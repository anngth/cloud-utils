"""Tests for Postgres URL handling, client discovery, status, and cleanup."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from shared.process import CommandResult

from db_tools import cli
from db_tools.cli import run_cli
from db_tools.models import PostgresTarget
from db_tools.postgres import (
    PostgresTools,
    _strip_transaction_timeout,
    clear_postgres_backups,
    find_postgres_tools,
    parse_postgres_url,
    postgres_status,
    quote_identifier,
    quote_literal,
)
from db_tools.runtime import Interrupted, Runtime, SignalHooks
from db_tools.tests.secrets_toml import write_secrets

PSQL = "/usr/bin/psql"
PG_DUMP = "/usr/bin/pg_dump"
PG_RESTORE = "/usr/bin/pg_restore"
LIBPQ = "/opt/homebrew/opt/libpq/bin"
PG18 = "/opt/homebrew/opt/postgresql@18/bin"

BACKUP_NAME = 'weird"name_backup_20260101_120000'
LIST_QUERY = (
    "SELECT datname FROM pg_database WHERE datistemplate = false"
    " AND datname ~ '_backup_[0-9]{8}_[0-9]{6}$' ORDER BY datname;"
)
SCOPED_QUERY = (
    "SELECT datname FROM pg_database WHERE datistemplate = false"
    " AND datname ~ '_backup_[0-9]{8}_[0-9]{6}$'"
    " AND left(datname, length('app_backup_')) = 'app_backup_' ORDER BY datname;"
)
TERMINATE_SQL = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
    " WHERE datname = 'weird\"name_backup_20260101_120000'"
    " AND pid <> pg_backend_pid();"
)
DROP_SQL = 'DROP DATABASE IF EXISTS "weird""name_backup_20260101_120000";'

# --- sync fixtures ----------------------------------------------------------------

NOW = datetime(2026, 10, 22, 15, 4, 5)
SYNC_BACKUP = "app_backup_20261022_150405"
SYNC_ARGV = ["sync", "postgres", "-s", "1", "-d", "1"]
SYNC_EXISTS_SQL = "SELECT 1 FROM pg_database WHERE datname='app';"
SYNC_TERMINATE_SQL = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
    " WHERE datname = 'app' AND pid <> pg_backend_pid();"
)
SYNC_RENAME_TO_BACKUP = f'ALTER DATABASE "app" RENAME TO "{SYNC_BACKUP}";'
SYNC_CREATE_SQL = 'CREATE DATABASE "app";'
SYNC_RENAME_BACK_SQL = f'ALTER DATABASE "{SYNC_BACKUP}" RENAME TO "app";'
SYNC_DROP_NEW_SQL = 'DROP DATABASE IF EXISTS "app";'
SYNC_DROP_BACKUP_SQL = f'DROP DATABASE IF EXISTS "{SYNC_BACKUP}";'
SYNC_TABLE_SQL = (
    "SELECT COUNT(*) FROM information_schema.tables"
    " WHERE table_schema NOT IN ('pg_catalog', 'information_schema');"
)
SYNC_SCHEMA_SQL = (
    "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
    " GRANT ALL ON SCHEMA public TO postgres;"
    " GRANT ALL ON SCHEMA public TO public;"
)
DEFAULT_DUMP_OUTPUT = (
    "SET transaction_timeout = 0;\n"
    "CREATE TABLE public.items (id int);\n"
    "ALTER DATABASE dbt SET transaction_timeout = 100;\n"
)
SUPABASE_TARGETS = (
    "one|postgresql://svc:secret@db.supabase.co:5432/postgres?sslmode=require",
)
POSTGRES_DB_TARGETS = ("one|postgresql://svc:secret@plain.example:5432/postgres",)

SOURCES = ("prod|postgresql://svc:secret@src.example:5432/app|app",)
TARGETS = (
    "one|postgresql://svc:secret@t1.example:5432/app?sslmode=require",
    "two|postgresql://svc:secret@t2.example:5432/other",
)

_CONN_ARGS = [PSQL, "-h", "t1.example", "-p", "5432", "-U", "svc", "-w", "-d", "postgres"]
_LIST_ARGS = [*_CONN_ARGS, "-At", "-c"]
_TERMINATE_ARGS = [*_CONN_ARGS, "-t", "-c"]
_DROP_ARGS = [*_CONN_ARGS, "-c"]


@dataclass
class FakePostgres:
    """Records argv/env pairs and answers psql queries without spawning."""

    list_fail_hosts: frozenset[str] = field(default_factory=frozenset)
    probe_code: int = 0
    which: dict[str, str] = field(
        default_factory=lambda: {"psql": PSQL, "pg_dump": PG_DUMP, "pg_restore": PG_RESTORE}
    )
    files: frozenset[str] = field(default_factory=frozenset)
    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str]] = field(default_factory=list)
    # Next matching substring wins once, then is removed (for run stories).
    responses: tuple[tuple[str, CommandResult], ...] = ()
    version_stdout: str = "PostgreSQL 16.4 (Homebrew)"
    table_count: str = "1"
    db_exists_stdout: str = "1"
    dump_stdout: str = DEFAULT_DUMP_OUTPUT
    restore_stderr: str = ""  # stderr for psql restores fed through stdin
    inputs: list[str | None] = field(default_factory=list)
    input_files: list[str | None] = field(default_factory=list)
    streams: list[bool] = field(default_factory=list)

    def run_command(self, argv, *, env=None, input_text=None, input_file=None, **kwargs):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        self.input_files.append(input_file)
        self.streams.append(bool(kwargs.get("stream", False)))
        stdin_text = input_text
        if stdin_text is None and input_file is not None:
            stdin_text = Path(input_file).read_text(encoding="utf-8")
        self.inputs.append(stdin_text)
        compiled = " ".join(argv) + "\n" + (stdin_text or "")
        for index, (needle, result) in enumerate(self.responses):
            if needle in compiled:
                self.responses = self.responses[:index] + self.responses[index + 1 :]
                return result
        query = argv[-1]
        host = argv[argv.index("-h") + 1]
        if query.startswith("SELECT datname FROM pg_database"):
            if host in self.list_fail_hosts:
                return CommandResult(1, "", "psql: connection refused")
            return CommandResult(0, BACKUP_NAME + "\n", "")
        if query.startswith("SELECT pg_terminate_backend"):
            return CommandResult(0, "", "")
        if query.startswith("SELECT 1 FROM pg_database"):
            return CommandResult(0, self.db_exists_stdout + "\n", "")
        if query == "SELECT version();":
            return CommandResult(0, self.version_stdout + "\n", "")
        if query.startswith("SELECT COUNT(*) FROM information_schema.tables"):
            return CommandResult(0, self.table_count + "\n", "")
        if query.startswith("DROP DATABASE"):
            return CommandResult(0, "", "")
        if query.startswith(("ALTER DATABASE", "CREATE DATABASE")):
            return CommandResult(0, "", "")
        if query.startswith("DROP SCHEMA"):
            return CommandResult(0, "", "")
        if query == "SELECT 1;":
            return CommandResult(self.probe_code, "1", "")
        if argv[0] == PG_DUMP:  # dump output has no SQL argument
            if "-f" in argv:  # the dump went straight into a file
                Path(argv[argv.index("-f") + 1]).write_text(
                    self.dump_stdout, encoding="utf-8"
                )
                return CommandResult(0, "", "")
            return CommandResult(0, self.dump_stdout, "")
        if argv[0] == PG_RESTORE:
            return CommandResult(0, "", "")
        if stdin_text is not None:  # psql restore through stdin or a file
            return CommandResult(0, "", self.restore_stderr)
        raise AssertionError(f"unexpected command: {argv}")

    def listings(self) -> list[list[str]]:
        return [
            call
            for call in self.calls
            if call[-1].startswith("SELECT datname FROM pg_database")
        ]

    def hosts(self, calls: list[list[str]]) -> list[str]:
        return [call[call.index("-h") + 1] for call in calls]


class _StubSignals(SignalHooks):
    def bind(self, handler) -> None:
        self.handler = handler  # tests must not touch real signal handlers


def _runtime(fake: FakePostgres, *, now: datetime | None = None) -> Runtime:
    return Runtime(
        run_command=fake.run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: fake.which.get(name),
        path_exists=lambda path: path in fake.files,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=(lambda: now) if now is not None else datetime.now,
        signals=_StubSignals(),
    )


def _write_secrets(path: Path, sources: tuple[str, ...], targets: tuple[str, ...]) -> None:
    write_secrets(path, postgres_sources=sources, postgres_targets=targets)


def _drive(
    tmp_path,
    argv: list[str],
    *,
    sources: tuple[str, ...] = SOURCES,
    targets: tuple[str, ...] = TARGETS,
    now: datetime | None = None,
    **fake_args,
):
    path = tmp_path / "dbt_secrets"
    _write_secrets(path, sources, targets)
    fake = FakePostgres(**fake_args)
    out = io.StringIO()
    err = io.StringIO()
    code = run_cli(
        argv,
        env={
            "HOME": str(tmp_path / "home"),
            "CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg"),
            "DBT_SECRETS_FILE": str(path),
        },
        stdout=out,
        stderr=err,
        dependencies=_runtime(fake, now=now),
    )
    return code, out.getvalue(), err.getvalue(), fake


# --- URL parsing and quoting --------------------------------------------------


def test_parse_url_keeps_password_colons_and_at_sign() -> None:
    url = parse_postgres_url("postgresql://user:p@ss:word@db.example:5432/app")
    assert (url.user, url.password) == ("user", "p@ss:word")
    assert (url.host, url.port, url.database) == ("db.example", "5432", "app")


def test_parse_url_without_a_path_has_no_database() -> None:
    assert parse_postgres_url("postgresql://user:pass@host:5432").database == ""
    assert parse_postgres_url("postgresql://user:pass@host:5432/").database == ""


def test_parse_url_query_without_a_path_is_not_a_database() -> None:
    url = parse_postgres_url("postgresql://u:p@h:5432?sslmode=require")
    assert (url.host, url.port, url.database, url.sslmode_require) == ("h", "5432", "", True)


def test_target_url_without_a_database_syncs_into_the_source_name(tmp_path) -> None:
    code, out, err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        targets=("one|postgresql://svc:secret@t1.example:5432?sslmode=require",),
    )

    assert code == 0
    assert err == ""
    assert "Target database: app\n" in out
    maintenance = [call for call in fake.calls if call[0] == PSQL and "-c" in call]
    assert {call[call.index("-d") + 1] for call in maintenance} == {"postgres", "app"}
    assert [call[-1] for call in fake.calls if call[-1].startswith("CREATE DATABASE")] == [
        SYNC_CREATE_SQL
    ]


def test_sslmode_require_is_true_only_for_that_substring() -> None:
    require = parse_postgres_url("postgresql://u:p@h:5432/db?sslmode=require")
    plain = parse_postgres_url("postgresql://u:p@h:5432/db")
    other = parse_postgres_url("postgresql://u:p@h:5432/db?sslmode=verify-full")
    assert require.sslmode_require is True
    assert plain.sslmode_require is False
    assert other.sslmode_require is False


def test_quote_identifier_and_literal_double_the_quotes() -> None:
    assert quote_identifier('we"ird') == '"we""ird"'
    assert quote_literal("a'b") == "'a''b'"


# --- client discovery ---------------------------------------------------------


def test_find_postgres_tools_prefers_path() -> None:
    tools = find_postgres_tools(_runtime(FakePostgres()))
    assert tools == PostgresTools(
        psql=PSQL, pg_dump=PG_DUMP, pg_restore=PG_RESTORE, source="PATH"
    )


def test_find_postgres_tools_falls_back_to_libpq_then_postgresql_18() -> None:
    libpq = FakePostgres(
        which={},
        files=frozenset(
            f"{LIBPQ}/{name}" for name in ("psql", "pg_dump", "pg_restore")
        ),
    )
    tools = find_postgres_tools(_runtime(libpq))
    assert tools is not None
    assert (tools.psql, tools.source) == (f"{LIBPQ}/psql", "Homebrew libpq")

    pg18 = FakePostgres(
        which={},
        files=frozenset(f"{PG18}/{name}" for name in ("psql", "pg_dump", "pg_restore")),
    )
    tools = find_postgres_tools(_runtime(pg18))
    assert tools is not None
    assert (tools.psql, tools.source) == (f"{PG18}/psql", "Homebrew postgresql@18")


def test_find_postgres_tools_partial_fallback_sets_do_not_win() -> None:
    mixed = FakePostgres(
        which={},  # nothing on PATH
        files=frozenset(
            [
                f"{LIBPQ}/psql",  # libpq incomplete
                f"{PG18}/psql",
                f"{PG18}/pg_dump",
            ]  # postgresql@18 incomplete
        ),
    )
    assert find_postgres_tools(_runtime(mixed)) is None


def test_cleanup_without_tools_mentions_brew_and_exits_one() -> None:
    fake = FakePostgres(which={})
    out = io.StringIO()
    err = io.StringIO()
    code = clear_postgres_backups(
        [PostgresTarget("one", TARGETS[0].split("|", 1)[1])],
        source_database=None,
        dry_run=False,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )
    assert code == 1
    assert "brew install libpq" in err.getvalue()
    assert "postgresql@18" in err.getvalue()
    assert fake.calls == []


# --- connection status --------------------------------------------------------


def test_postgres_status_probes_select_one_with_pgpassword_env() -> None:
    fake = FakePostgres()
    url = "postgresql://svc:secret@t1.example:5432/app?sslmode=require"
    assert postgres_status(url, runtime=_runtime(fake)) is True
    assert fake.calls == [
        [*_TERMINATE_ARGS, "SELECT 1;"]
    ]
    assert fake.envs[0]["PGPASSWORD"] == "secret"
    assert fake.envs[0]["PGSSLMODE"] == "require"
    assert "secret" not in " ".join(fake.calls[0])


def test_postgres_status_omits_pgsslmode_without_require() -> None:
    fake = FakePostgres()
    assert postgres_status(
        "postgresql://svc:secret@t1.example:5432/app", runtime=_runtime(fake)
    ) is True
    assert fake.envs[0]["PGPASSWORD"] == "secret"
    assert "PGSSLMODE" not in fake.envs[0]


def test_postgres_status_missing_client_is_unknown() -> None:
    assert (
        postgres_status(
            "postgresql://svc:secret@t1.example:5432/app",
            runtime=_runtime(FakePostgres(which={})),
        )
        is None
    )


def test_postgres_status_failed_probe_is_false() -> None:
    assert (
        postgres_status(
            "postgresql://svc:secret@t1.example:5432/app",
            runtime=_runtime(FakePostgres(probe_code=1)),
        )
        is False
    )


# --- cleanup through run_cli ---------------------------------------------------


@pytest.mark.parametrize("alias", ["postgres", "postgresql", "pg"])
def test_dry_run_does_not_drop(tmp_path, alias: str) -> None:
    code, out, err, fake = _drive(tmp_path, ["clean", alias, "-n"])

    assert code == 0
    assert err == ""
    listings = fake.listings()
    assert [fake.hosts(listings), [call[-1] for call in listings]] == [
        ["t1.example", "t2.example"],
        [LIST_QUERY, LIST_QUERY],
    ]
    assert listings[0][:-1] == _LIST_ARGS
    assert all("DROP DATABASE" not in " ".join(call) for call in fake.calls)
    assert "secret" not in " ".join(" ".join(call) for call in fake.calls)
    assert 'weird"name_backup_20260101_120000' in out
    assert fake.envs[0]["PGPASSWORD"] == "secret"
    assert fake.envs[0]["PGSSLMODE"] == "require"
    assert "PGSSLMODE" not in fake.envs[1]


def test_drop_terminates_then_drops_the_quoted_backup(tmp_path) -> None:
    code, out, err, fake = _drive(tmp_path, ["clean", "postgres", "-d", "1"])

    assert code == 0
    assert err == ""
    assert len(fake.calls) == 3
    list_call, terminate, drop = fake.calls
    assert list_call[:-1] == _LIST_ARGS
    assert list_call[-1] == LIST_QUERY
    assert terminate[:-1] == _TERMINATE_ARGS
    assert terminate[-1] == TERMINATE_SQL
    assert drop[:-1] == _DROP_ARGS
    assert drop[-1] == DROP_SQL
    assert "Summary: dropped 1, failed 0" in out


def test_source_database_scopes_the_listing(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["clean", "postgres", "-n", "-s", "1"])

    assert code == 0
    assert err == ""
    listings = fake.listings()
    assert [call[-1] for call in listings] == [SCOPED_QUERY, SCOPED_QUERY]


def test_destination_limits_cleanup_to_that_target(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["clean", "postgres", "-n", "-d", "2"])

    assert code == 0
    assert err == ""
    assert fake.hosts(fake.listings()) == ["t2.example"]


def test_cleanup_without_destination_visits_every_target(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["clean", "postgres", "-n"])

    assert code == 0
    assert err == ""
    assert fake.hosts(fake.listings()) == ["t1.example", "t2.example"]


def test_one_failed_target_still_visits_the_next_and_exits_one(tmp_path) -> None:
    code, out, err, fake = _drive(
        tmp_path,
        ["clean", "postgres", "-n"],
        list_fail_hosts=frozenset({"t2.example"}),
    )

    assert code == 1
    assert fake.hosts(fake.listings()) == ["t1.example", "t2.example"]
    assert "Failed to list PostgreSQL backup databases" in err
    assert "Dropped" not in out


def test_no_postgres_targets_returns_zero(tmp_path) -> None:
    code, out, _err, fake = _drive(tmp_path, ["clean", "postgres"], targets=())

    assert code == 0
    assert fake.calls == []
    assert "No PostgreSQL targets" in out


def test_cleanup_never_unlinks_files(tmp_path, monkeypatch) -> None:
    def forbid(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("cleanup must not unlink files")

    monkeypatch.setattr(Path, "unlink", forbid)
    monkeypatch.setattr(os, "remove", forbid)
    keeper = tmp_path / "pg_dump.bin"
    keeper.write_text("x", encoding="utf-8")

    code, _out, err, _fake = _drive(tmp_path, ["clean", "postgres", "-d", "1"])

    assert code == 0
    assert err == ""
    assert keeper.exists()


def test_source_index_out_of_range_exits_one_without_psql(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["clean", "postgres", "-s", "2"])

    assert code == 1
    assert fake.calls == []
    assert "Unknown PostgreSQL source: 2" in err
    assert "Available sources: 1) prod" in err


def test_clean_scopes_by_source_and_target_name(tmp_path) -> None:
    code, _out, err, fake = _drive(
        tmp_path, ["clean", "postgres", "-n", "--from", "prod", "--to", "two"]
    )

    assert code == 0
    assert err == ""
    assert fake.hosts(fake.listings()) == ["t2.example"]
    assert [call[-1] for call in fake.listings()] == [SCOPED_QUERY]


def test_clean_rejects_from_and_to_for_mongodb(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["clean", "mongodb", "--to", "one"])

    assert code == 1
    assert fake.calls == []
    assert "--from/--to only apply to PostgreSQL" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["sync", "postgres", "-c", "-x"],
        ["sync", "postgres"],
        ["sync", "list", "--full"],
        ["sync", "postgres", "-c", "-s"],  # missing -s value
    ],
)
def test_bad_flag_or_usage_exits_one_without_calls(tmp_path, argv: list[str]) -> None:
    code, _out, err, fake = _drive(tmp_path, argv)

    assert code == 1
    assert err != ""
    assert fake.calls == []


# --- sync list / ls ------------------------------------------------------------


@pytest.mark.parametrize("alias", ["list", "ls"])
def test_sync_list_prints_sections_with_1_based_indexes(tmp_path, alias: str) -> None:
    code, out, err, fake = _drive(tmp_path, ["sync", alias])

    assert code == 0
    assert err == ""
    assert fake.calls == []
    assert "🐘 PostgreSQL Sources (1):" in out
    assert "  1) prod → app | src.example" in out
    assert "🎯 PostgreSQL Targets (2):" in out
    assert "  1) one | t1.example [SSL: require]" in out
    assert "  2) two | t2.example [SSL: disable]" in out


def test_sync_list_status_with_no_client_reports_unknown(tmp_path) -> None:
    code, out, err, fake = _drive(tmp_path, ["sync", "list", "--status"], which={})

    assert code == 0
    assert err == ""
    assert fake.calls == []
    assert "⚪ 1) prod → app | src.example" in out
    assert "⚪ 1) one | t1.example [SSL: require]" in out
    assert "⚪ 3 unknown (client not installed)" in out


def test_sync_list_status_reports_failed_connection(tmp_path) -> None:
    code, out, _err, _fake = _drive(tmp_path, ["sync", "list", "--status"], probe_code=1)

    assert code == 0
    assert "🔴 1) prod → app | src.example" in out
    assert "🔴 1) one | t1.example [SSL: require]" in out
    assert "Summary: 🟢 0 connected  |  🔴 3 disconnected" in out


def test_sync_list_rejects_the_file_when_an_entry_is_invalid(tmp_path) -> None:
    code, out, err, _fake = _drive(tmp_path, ["sync", "list"], sources=("bad",))

    assert code == 1
    assert "postgres.sources[1] (bad): missing field 'url'" in err
    assert out == ""


# --- sync: plain target ---------------------------------------------------------


def test_existing_target_renamed_created_and_backup_dropped(tmp_path) -> None:
    code, out, err, fake = _drive(tmp_path, SYNC_ARGV, now=NOW)

    assert code == 0
    assert err == ""
    assert [fake.calls[0][-1], fake.calls[1][-1]] == [SYNC_EXISTS_SQL, SYNC_TERMINATE_SQL]
    assert fake.calls[2][-1] == SYNC_RENAME_TO_BACKUP
    assert fake.calls[3][-1] == SYNC_CREATE_SQL
    version_calls = [call for call in fake.calls if call[-1] == "SELECT version();"]
    assert fake.hosts(version_calls) == ["src.example", "t1.example"]
    dump = [call for call in fake.calls if call[0] == PG_DUMP]
    assert len(dump) == 1
    assert dump[0][:-2] == [
        PG_DUMP,
        "-h",
        "src.example",
        "-p",
        "5432",
        "-U",
        "svc",
        "-w",
        "-d",
        "app",
        "--no-owner",
        "--no-acl",
        "--clean",
        "--if-exists",
    ]
    assert dump[0][-2] == "-f"  # the plain dump went straight into a file
    restored = [index for index, value in enumerate(fake.inputs) if value is not None]
    assert restored == [7]
    assert fake.calls[7] == [
        *_CONN_ARGS[:-1], "app", "-v", "ON_ERROR_STOP=1", "-q"
    ]
    assert fake.inputs[7] == "CREATE TABLE public.items (id int);\n"
    counted = [call for call in fake.calls if call[-1].startswith("SELECT COUNT(*)")]
    assert [counted[0][counted[0].index("-d") + 1]] == ["app"]
    assert counted[0][:-1] == [*_CONN_ARGS[:-1], "app", "-t", "-c"]
    drop = [call for call in fake.calls if call[-1].startswith("DROP DATABASE")]
    assert drop == [[*_DROP_ARGS, SYNC_DROP_BACKUP_SQL]]
    assert "app_backup_20261022_150405" in out
    assert "synced successfully" in out


def test_missing_target_just_creates_a_fresh_database(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, SYNC_ARGV, now=NOW, db_exists_stdout="0")

    assert code == 0
    assert err == ""
    assert [call[-1] for call in fake.calls if call[-1].startswith("ALTER DATABASE")] == []
    created = [call[-1] for call in fake.calls if call[-1].startswith("CREATE DATABASE")]
    assert created == [SYNC_CREATE_SQL]
    assert [call[-1] for call in fake.calls if call[-1].startswith("DROP DATABASE")] == []


def test_create_failure_renames_the_backup_back(tmp_path) -> None:
    code, _out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        responses=(
            ("CREATE DATABASE", CommandResult(1, "", "ERROR: permission denied")),
        ),
    )

    assert code == 1
    assert SYNC_RENAME_BACK_SQL in [call[-1] for call in fake.calls]
    assert all(call[0] == PSQL for call in fake.calls)
    assert all(value is None for value in fake.inputs)


def test_rename_back_failure_keeps_the_backup(tmp_path) -> None:
    code, _out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        responses=(
            ("CREATE DATABASE", CommandResult(1, "", "ERROR: permission denied")),
            ('RENAME TO "app";', CommandResult(1, "", "ERROR: object is in use")),
        ),
    )

    assert code == 1
    assert fake.calls[-1][-1] == SYNC_RENAME_BACK_SQL
    assert SYNC_DROP_BACKUP_SQL not in [call[-1] for call in fake.calls]


def test_version_16_strips_transaction_timeout(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, SYNC_ARGV, now=NOW)

    assert code == 0
    assert err == ""
    dump = next(call for call in fake.calls if call[0] == PG_DUMP)
    assert dump[-6:-2] == ["--no-owner", "--no-acl", "--clean", "--if-exists"]
    assert "-Fc" not in dump
    assert dump[-2] == "-f"
    restored = [index for index, value in enumerate(fake.inputs) if value is not None]
    assert restored == [7]
    assert fake.calls[restored[0]][-1] == "-q"
    assert fake.inputs[restored[0]] == "CREATE TABLE public.items (id int);\n"


def test_version_16_restore_streams_through_temp_files(tmp_path, monkeypatch) -> None:
    """The dump and filtered SQL live in files, never in memory payloads."""
    removed = _track_unlink(monkeypatch)
    code, _out, err, fake = _drive(tmp_path, SYNC_ARGV, now=NOW)

    assert code == 0
    assert err == ""
    dump = next(call for call in fake.calls if call[0] == PG_DUMP)
    dump_path = dump[dump.index("-f") + 1]
    restore = next(
        index
        for index, call in enumerate(fake.calls)
        if call[0] == PSQL and "-q" in call
    )
    sql_path = fake.input_files[restore]
    assert sql_path is not None
    # pg_dump wrote into one temp file; psql read the filtered copy of
    # another, so no argv or stdin payload ever carried the whole dump.
    assert sql_path != dump_path
    assert fake.inputs[restore] == "CREATE TABLE public.items (id int);\n"
    assert {dump_path, sql_path} <= set(removed)
    assert not Path(dump_path).exists()
    assert not Path(sql_path).exists()


@pytest.mark.parametrize("version_stdout", ["", "PostgreSQL 17.1"])
def test_unreadable_version_uses_custom_format(tmp_path, version_stdout) -> None:
    code, _out, err, fake = _drive(
        tmp_path, SYNC_ARGV, now=NOW, version_stdout=version_stdout
    )

    assert code == 0
    assert err == ""
    dump = next(call for call in fake.calls if call[0] == PG_DUMP)
    assert dump[:10] == [
        PG_DUMP, "-h", "src.example", "-p", "5432", "-U", "svc", "-w", "-d", "app"
    ]
    assert dump[10] == "-Fc"
    dump_file = dump[dump.index("-f") + 1]
    assert [call for call in fake.calls if call[0] == PG_RESTORE] == [
        [
            PG_RESTORE,
            "-h",
            "t1.example",
            "-p",
            "5432",
            "-U",
            "svc",
            "-w",
            "-d",
            "app",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            dump_file,
        ]
    ]
    assert all(value is None for value in fake.inputs)
    assert "-q" not in " ".join(" ".join(call) for call in fake.calls)


@pytest.mark.parametrize("version_stdout", ["PostgreSQL 16.4", ""])
def test_verbose_sets_debug_flags_and_omits_qs(tmp_path, version_stdout) -> None:
    code, _out, err, fake = _drive(
        tmp_path, [*SYNC_ARGV, "-v"], now=NOW, version_stdout=version_stdout
    )

    assert code == 0
    assert err == ""
    dump = next(call for call in fake.calls if call[0] == PG_DUMP)
    assert dump[1] == "-v"
    if version_stdout:  # SQL path: psql stays chatty
        assert [value for value in fake.inputs if value is not None] != []
        assert all("-q" not in call for call in fake.calls)
    else:  # custom path: pg_restore goes verbose
        restore = next(call for call in fake.calls if call[0] == PG_RESTORE)
        assert restore[1] == "--verbose"
        assert [value for value in fake.inputs if value is not None] == []


@pytest.mark.parametrize("version_stdout", ["PostgreSQL 16.4", "PostgreSQL 17.1"])
def test_verbose_streams_the_dump_and_restore_live(tmp_path, version_stdout) -> None:
    code, _out, _err, fake = _drive(
        tmp_path, [*SYNC_ARGV, "-v"], now=NOW, version_stdout=version_stdout
    )

    assert code == 0
    streamed = [call[0] for call, stream in zip(fake.calls, fake.streams, strict=True) if stream]
    assert PG_DUMP in streamed
    assert PG_RESTORE in streamed or any(
        call[0] == PSQL and stream and "-c" not in call
        for call, stream in zip(fake.calls, fake.streams, strict=True)
    )
    assert not any(
        stream for call, stream in zip(fake.calls, fake.streams, strict=True) if "-c" in call
    )


def test_without_verbose_nothing_streams(tmp_path) -> None:
    code, _out, _err, fake = _drive(tmp_path, SYNC_ARGV, now=NOW)

    assert code == 0
    assert fake.streams == [False] * len(fake.calls)


def test_supabase_verbose_streams_the_target_backup_dump(tmp_path) -> None:
    code, _out, _err, fake = _drive(
        tmp_path, [*SYNC_ARGV, "-v"], now=NOW, targets=SUPABASE_TARGETS, version_stdout=""
    )

    assert code == 0
    pairs = zip(fake.calls, fake.streams, strict=True)
    dumps = [stream for call, stream in pairs if call[0] == PG_DUMP]
    assert dumps == [True, True]


def test_dump_failure_prints_the_cause_and_exit_code(tmp_path) -> None:
    code, out, _err, _fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        responses=(
            (
                "usr/bin/pg_dump",
                CommandResult(
                    2, "", "pg_dump: error: connection failed\npg_dump: detail: no route\n"
                ),
            ),
        ),
    )

    assert code == 1
    assert "Dump failed (exit code 2)" in out
    assert "pg_dump: error: connection failed" in out
    assert "pg_dump: detail: no route" in out
    assert "Run again with --verbose" in out
    assert out.index("Dump failed") < out.index("Restoring backup database")


def test_restore_failure_prints_the_cause(tmp_path) -> None:
    code, out, _err, _fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        responses=(("-q", CommandResult(3, "", "psql: error: broken pipe")),),
    )

    assert code == 1
    assert "Restore failed (exit code 3)" in out
    assert "psql: error: broken pipe" in out


def test_custom_format_restore_failure_prints_the_cause(tmp_path) -> None:
    code, out, _err, _fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        version_stdout="PostgreSQL 17.1",
        responses=(("usr/bin/pg_restore", CommandResult(1, "", "pg_restore: error: boom")),),
    )

    assert code == 1
    assert "Restore failed (exit code 1)" in out
    assert "pg_restore: error: boom" in out


def test_verbose_failure_does_not_point_at_verbose_again(tmp_path) -> None:
    code, out, _err, _fake = _drive(
        tmp_path,
        [*SYNC_ARGV, "-v"],
        now=NOW,
        responses=(("usr/bin/pg_dump", CommandResult(1, "", "pg_dump: error: x")),),
    )

    assert code == 1
    assert "Dump failed" in out
    assert "Run again with --verbose" not in out


# --- transaction_timeout filter --------------------------------------------------


def _strip(tmp_path, data: bytes) -> bytes:
    source = tmp_path / "dump.sql"
    target = tmp_path / "filtered.sql"
    source.write_bytes(data)
    _strip_transaction_timeout(str(source), str(target))
    return target.read_bytes()


def test_strip_removes_only_transaction_timeout_statements(tmp_path) -> None:
    data = (
        b"SET statement_timeout = 0;\n"
        b"SET transaction_timeout = 0;\n"
        b"  SET transaction_timeout = 0;\n"
        b"ALTER DATABASE app SET transaction_timeout = 100;\n"
        b"ALTER ROLE svc SET transaction_timeout TO '1min';\n"
        b"CREATE TABLE public.items (id int);\n"
    )
    assert _strip(tmp_path, data) == (
        b"SET statement_timeout = 0;\nCREATE TABLE public.items (id int);\n"
    )


def test_strip_keeps_copy_data_that_mentions_transaction_timeout(tmp_path) -> None:
    data = (
        b"COPY public.notes (id, body) FROM stdin;\n"
        b"1\tplease raise transaction_timeout on prod\n"
        b"2\tSET transaction_timeout is what the docs say\n"
        b"\\.\n"
    )
    assert _strip(tmp_path, data) == data


def test_strip_passes_non_utf8_bytes_through_unchanged(tmp_path) -> None:
    data = b"SET transaction_timeout = 0;\nINSERT INTO t VALUES ('caf\xe9');\n"
    assert _strip(tmp_path, data) == b"INSERT INTO t VALUES ('caf\xe9');\n"


def test_restore_error_keeps_backup_and_exits_one(tmp_path) -> None:
    code, out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        restore_stderr='psql: ERROR: role "svc" does not exist',
    )

    assert code == 1
    assert SYNC_DROP_BACKUP_SQL not in [call[-1] for call in fake.calls]
    assert "completed with warnings" in out


def test_zero_table_count_keeps_backup_and_exits_one(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, SYNC_ARGV, now=NOW, table_count="0")

    assert code == 1
    assert err == ""
    assert SYNC_DROP_BACKUP_SQL not in [call[-1] for call in fake.calls]
    assert fake.inputs[-2] == "CREATE TABLE public.items (id int);\n"  # ran the restore


def test_dump_failure_drops_new_database_and_renames_backup_back(tmp_path) -> None:
    code, _out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        responses=(("usr/bin/pg_dump", CommandResult(1, "", "pg_dump: error: no route")),),
    )

    assert code == 1
    calls = [call[-1] for call in fake.calls]
    assert calls[-2] == SYNC_DROP_NEW_SQL
    assert calls[-1] == SYNC_RENAME_BACK_SQL
    assert [call for call in fake.calls if call[0] == PG_RESTORE] == []


def test_restore_failure_drops_new_database_and_renames_backup_back(tmp_path) -> None:
    code, _out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        responses=(("-q", CommandResult(1, "", "psql: error: broken pipe")),),
    )

    assert code == 1
    calls = [call[-1] for call in fake.calls]
    assert calls[-2] == SYNC_DROP_NEW_SQL
    assert calls[-1] == SYNC_RENAME_BACK_SQL
    assert [call for call in fake.calls if call[-1].startswith("SELECT COUNT(*)")] == []


# --- sync: Supabase and `postgres` targets ---------------------------------------


def _track_unlink(monkeypatch) -> list[str]:
    removed: list[str] = []
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        removed.append(str(self))
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    return removed


def test_supabase_dumps_file_cleans_schema_and_deletes_it(tmp_path, monkeypatch) -> None:
    removed = _track_unlink(monkeypatch)
    code, _out, err, fake = _drive(
        tmp_path, SYNC_ARGV, now=NOW, targets=SUPABASE_TARGETS
    )

    assert code == 0
    assert err == ""
    backup_dump = next(call for call in fake.calls if call[0] == PG_DUMP)
    assert backup_dump[:11] == [
        PG_DUMP,
        "-h",
        "db.supabase.co",
        "-p",
        "5432",
        "-U",
        "svc",
        "-w",
        "-d",
        "postgres",
        "-Fc",
    ]
    assert "--no-owner" in backup_dump and "--no-acl" in backup_dump
    dump_file = backup_dump[backup_dump.index("-f") + 1]
    clean_calls = [call for call in fake.calls if call[-1].startswith("DROP SCHEMA")]
    assert clean_calls[0][-1] == SYNC_SCHEMA_SQL
    assert clean_calls[0][clean_calls[0].index("-d") + 1] == "postgres"
    assert fake.calls.index(backup_dump) < fake.calls.index(clean_calls[0])
    assert [call for call in fake.calls if call[-1].startswith("ALTER DATABASE")] == []
    assert dump_file in removed
    assert not Path(dump_file).exists()


def test_target_database_postgres_dumps_file_before_schema_cleanup(tmp_path) -> None:
    code, out, err, fake = _drive(
        tmp_path, SYNC_ARGV, now=NOW, targets=POSTGRES_DB_TARGETS
    )

    assert code == 0
    assert err == ""
    assert "Supabase or uses 'postgres' database" in out
    backup_dump = next(call for call in fake.calls if call[0] == PG_DUMP)
    assert backup_dump[2] == "plain.example"
    clean_calls = [call for call in fake.calls if call[-1].startswith("DROP SCHEMA")]
    assert fake.calls.index(backup_dump) < fake.calls.index(clean_calls[0])
    assert [call for call in fake.calls if call[-1].startswith("ALTER DATABASE")] == []


def test_supabase_dump_failure_deletes_file_and_skips_schema_drop(
    tmp_path, monkeypatch
) -> None:
    removed = _track_unlink(monkeypatch)
    code, _out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        targets=SUPABASE_TARGETS,
        responses=(("usr/bin/pg_dump", CommandResult(1, "", "pg_dump: error: denied")),),
    )

    assert code == 1
    assert [call for call in fake.calls if call[-1].startswith("DROP SCHEMA")] == []
    assert removed
    assert fake.calls[-1][-1] != SYNC_SCHEMA_SQL


def test_supabase_schema_cleanup_failure_restores_dump_file(tmp_path, monkeypatch) -> None:
    removed = _track_unlink(monkeypatch)
    code, out, err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        targets=SUPABASE_TARGETS,
        responses=(("DROP SCHEMA", CommandResult(1, "", "ERROR: permission denied")),),
    )

    assert code == 1
    assert err == ""
    backup_dump = next(call for call in fake.calls if call[0] == PG_DUMP)
    dump_file = backup_dump[backup_dump.index("-f") + 1]
    restore = [call for call in fake.calls if call[0] == PG_RESTORE]
    assert restore == [
        [
            PG_RESTORE,
            "-h",
            "db.supabase.co",
            "-p",
            "5432",
            "-U",
            "svc",
            "-w",
            "-d",
            "postgres",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            dump_file,
        ]
    ]
    assert restore[0][restore[0].index("-d") + 1] == "postgres"
    assert dump_file in removed
    assert "Failed to clean target database before restore" in out
    assert not Path(dump_file).exists()


def test_supabase_file_restore_failure_keeps_file_and_prints_path(
    tmp_path, monkeypatch
) -> None:
    removed = _track_unlink(monkeypatch)
    code, out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        targets=SUPABASE_TARGETS,
        responses=(
            (
                "usr/bin/pg_restore",
                CommandResult(1, "", "pg_restore: error: damaged archive"),
            ),
            ("DROP SCHEMA", CommandResult(1, "", "ERROR: permission denied")),
        ),
    )

    assert code == 1
    backup_dump = next(call for call in fake.calls if call[0] == PG_DUMP)
    dump_file = backup_dump[backup_dump.index("-f") + 1]
    assert dump_file not in removed
    assert dump_file in out
    assert Path(dump_file).exists()
    assert "Failed to clean target database before restore" in out


# --- sync: interrupt ------------------------------------------------------------


def test_interrupt_after_rename_restores_and_exits_130(tmp_path) -> None:
    path = tmp_path / "dbt_secrets"
    _write_secrets(path, SOURCES, TARGETS)
    fake = FakePostgres()
    out, err = io.StringIO(), io.StringIO()
    signals = _StubSignals()
    handlers_seen: list[object] = []
    triggered: list[bool] = []
    base = fake.run_command

    def run_command(argv, **kwargs):
        joined = " ".join(argv)
        if joined.startswith(PG_DUMP) and not triggered:
            triggered.append(True)
            assert signals.handler is not None
            raise Interrupted()
        if "ALTER DATABASE" in joined:
            handlers_seen.append(signals.handler)
        return base(argv, **kwargs)

    runtime = Runtime(
        run_command=run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: fake.which.get(name),
        path_exists=lambda value: value in fake.files,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=lambda: NOW,
        signals=signals,
    )

    code = run_cli(
        SYNC_ARGV,
        env={"DBT_SECRETS_FILE": str(path)},
        stdout=out,
        stderr=err,
        dependencies=runtime,
    )

    assert code == 130
    assert handlers_seen[0] is None  # nothing bound when the rename runs
    assert handlers_seen[-1] is None  # unbound before the rollback
    assert "PostgreSQL sync interrupted" in out.getvalue()
    assert fake.calls[-2][-1] == SYNC_DROP_NEW_SQL
    assert fake.calls[-1][-1] == SYNC_RENAME_BACK_SQL
    assert [call for call in fake.calls if call[0] == PG_RESTORE] == []
    assert signals.handler is None


def test_interrupt_before_rollback_is_armed_propagates(tmp_path) -> None:
    path = tmp_path / "dbt_secrets"
    _write_secrets(path, SOURCES, TARGETS)
    fake = FakePostgres()
    signals = _StubSignals()
    base = fake.run_command

    def run_command(argv, **kwargs):
        if argv[-1] == SYNC_EXISTS_SQL:
            raise Interrupted()
        return base(argv, **kwargs)

    runtime = Runtime(
        run_command=run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: fake.which.get(name),
        path_exists=lambda value: value in fake.files,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=lambda: NOW,
        signals=signals,
    )

    with pytest.raises(Interrupted):
        run_cli(
            SYNC_ARGV,
            env={"DBT_SECRETS_FILE": str(path)},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=runtime,
        )
    assert not any(call[-1].startswith("ALTER DATABASE") for call in fake.calls)
    assert signals.handler is None


def test_unexpected_error_mid_sync_leaves_no_handler_bound(tmp_path) -> None:
    path = tmp_path / "dbt_secrets"
    _write_secrets(path, SOURCES, TARGETS)
    fake = FakePostgres()
    signals = _StubSignals()
    base = fake.run_command

    def run_command(argv, **kwargs):
        if argv[0] == PG_DUMP:
            raise OSError("disk full")
        return base(argv, **kwargs)

    runtime = Runtime(
        run_command=run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: fake.which.get(name),
        path_exists=lambda value: value in fake.files,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=lambda: NOW,
        signals=signals,
    )

    with pytest.raises(OSError):
        run_cli(
            SYNC_ARGV,
            env={"DBT_SECRETS_FILE": str(path)},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=runtime,
        )
    assert signals.handler is None


def test_interrupt_after_supabase_dump_restores_file(tmp_path) -> None:
    path = tmp_path / "dbt_secrets"
    _write_secrets(path, SOURCES, SUPABASE_TARGETS)
    fake = FakePostgres(version_stdout="")
    out, err = io.StringIO(), io.StringIO()
    signals = _StubSignals()
    triggered: list[bool] = []
    base = fake.run_command

    def run_command(argv, **kwargs):
        joined = " ".join(argv)
        if "DROP SCHEMA" in joined and not triggered:
            triggered.append(True)
            assert signals.handler is not None
            raise Interrupted()
        return base(argv, **kwargs)

    runtime = Runtime(
        run_command=run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: fake.which.get(name),
        path_exists=lambda value: value in fake.files,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=lambda: NOW,
        signals=signals,
    )

    code = run_cli(
        SYNC_ARGV,
        env={"DBT_SECRETS_FILE": str(path)},
        stdout=out,
        stderr=err,
        dependencies=runtime,
    )

    assert code == 130
    dump_argv = next(call for call in fake.calls if call[0] == PG_DUMP)
    dump_file = dump_argv[dump_argv.index("-f") + 1]
    restore = [call for call in fake.calls if call[0] == PG_RESTORE][-1]
    assert restore[-1] == dump_file
    assert restore[restore.index("-d") + 1] == "postgres"
    assert "Restoring dump backup" in out.getvalue()
    assert not any(call[-1].startswith("ALTER DATABASE") for call in fake.calls)
    assert signals.handler is None
    assert not Path(dump_file).exists()  # the freed handler cleaned the file up


# --- sync: cli flags -------------------------------------------------------------


def test_password_is_only_in_pgpassword(tmp_path) -> None:
    bad = "p'; touch pwned; echo '"
    code, _out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        sources=(f"prod|postgresql://svc:{bad}@src.example:5432/app|app",),
    )

    assert code == 0
    joined = " ".join(" ".join(call) for call in fake.calls)
    assert bad not in joined
    assert bad not in " ".join(value or "" for value in fake.inputs)
    carriers = [one_env for one_env in fake.envs if one_env.get("PGPASSWORD") == bad]
    assert carriers  # the source password rides only in PGPASSWORD
    assert all("PGPASSWORD" in one_env for one_env in fake.envs)
    assert not (tmp_path / "pwned").exists()


@pytest.mark.parametrize(
    "argv",
    [["sync", "postgres", "-s", "1"], ["sync", "postgres", "-d", "1"], ["sync", "postgres"]],
)
def test_sync_requires_source_and_destination(tmp_path, argv: list[str]) -> None:
    code, _out, err, fake = _drive(tmp_path, argv)

    assert code == 1
    assert err != ""
    assert [call for call in fake.calls if call[0] == PG_DUMP] == []


@pytest.mark.parametrize("argset", [("-s", "2"), ("-d", "3")])
def test_sync_out_of_range_index_exits_one_without_psql(tmp_path, argset) -> None:
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres", *argset])

    assert code == 1
    assert err != ""
    assert fake.calls == []


def test_database_flag_is_rejected_for_postgres(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, [*SYNC_ARGV, "--db", "2"], now=NOW)

    assert code == 1
    assert "No such option" in err
    assert fake.calls == []


def test_sync_accepts_row_names(tmp_path) -> None:
    code, out, err, fake = _drive(
        tmp_path, ["sync", "postgres", "--from", "prod", "--to", "one"], now=NOW
    )

    assert code == 0
    assert err == ""
    assert [call for call in fake.calls if call[0] == PG_DUMP]
    assert "  To: one → Database: app\n" in out
    assert "  Backup: app_backup_YYYYMMDD_HHMMSS (renamed, dropped on success)\n" in out


def test_sync_unknown_name_lists_the_choices(tmp_path) -> None:
    code, _out, err, fake = _drive(
        tmp_path, ["sync", "postgres", "--from", "prod", "--to", "three"]
    )

    assert code == 1
    assert fake.calls == []
    assert "Unknown PostgreSQL target: three" in err
    assert "Available targets: 1) one, 2) two" in err


def test_sync_dry_run_prints_the_plan_and_changes_nothing(tmp_path) -> None:
    code, out, err, fake = _drive(tmp_path, [*SYNC_ARGV, "-n"], targets=SUPABASE_TARGETS)

    assert (code, err) == (0, "")
    assert fake.calls == []
    assert "  To: one → Database: postgres\n" in out
    assert "  Backup: a temporary dump file" in out
    assert out.endswith("Dry run: nothing was changed\n")


def test_sync_without_target_outside_a_terminal_explains(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres", "--from", "prod"])

    assert code == 1
    assert fake.calls == []
    assert "--to is required outside a terminal" in err
    assert "dbt sync list" in err


def test_sync_in_a_terminal_picks_the_rows_and_asks_before_overwriting(
    tmp_path, monkeypatch
) -> None:
    picked: list[str] = []
    questions: list[str] = []

    def pick(title, items, _app):
        picked.append(title)
        return items[0].value

    def confirm(question):
        questions.append(question)
        return True

    monkeypatch.setattr(cli._App, "interactive", property(lambda _self: True))
    monkeypatch.setattr(cli, "_pick", pick)
    monkeypatch.setattr(cli, "_confirm", confirm)
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres"], now=NOW)

    assert (code, err) == (0, "")
    assert picked == ["Select the PostgreSQL target", "Select the PostgreSQL source"]
    assert questions == ["Overwrite database 'app' on 'one' with 'app' from 'prod'?"]
    assert [call for call in fake.calls if call[0] == PG_DUMP]


def test_sync_declined_confirmation_cancels(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli._App, "interactive", property(lambda _self: True))
    monkeypatch.setattr(cli, "_confirm", lambda _question: False)
    code, out, _err, fake = _drive(tmp_path, SYNC_ARGV, now=NOW)

    assert code == 1
    assert fake.calls == []
    assert out.endswith("Cancelled\n")


def test_sync_yes_skips_the_confirmation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli._App, "interactive", property(lambda _self: True))
    monkeypatch.setattr(cli, "_confirm", lambda _q: (_ for _ in ()).throw(AssertionError("asked")))
    code, _out, err, fake = _drive(tmp_path, [*SYNC_ARGV, "-y"], now=NOW)

    assert (code, err) == (0, "")
    assert [call for call in fake.calls if call[0] == PG_DUMP]


@pytest.mark.parametrize(
    "sources",
    [
        ("prod|postgresql://svc:p@src.example:5432/app|",),  # empty database
        ("prod|postgresql://svc:p@src.example:5432",),  # no database anywhere
    ],
)
def test_invalid_source_entry_blocks_sync(tmp_path, sources) -> None:
    code, _out, err, fake = _drive(tmp_path, SYNC_ARGV, sources=sources, now=NOW)

    assert code == 1
    assert err != ""
    assert fake.calls == []


def test_quiet_flag_is_rejected(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, [*SYNC_ARGV, "--quiet"])

    assert code == 1
    assert err != ""
    assert [call for call in fake.calls if call[0] == PG_DUMP] == []


@pytest.mark.parametrize("alias", ["postgres", "postgresql", "pg"])
def test_sync_aliases_all_reach_pg_dump(tmp_path, alias: str) -> None:
    code, _out, err, fake = _drive(
        tmp_path, ["sync", alias, "-s", "1", "-d", "1"], now=NOW
    )

    assert code == 0
    assert err == ""
    assert [call for call in fake.calls if call[0] == PG_DUMP]


# --- clients never prompt, never wait forever ----------------------------------


@pytest.mark.parametrize(
    ("targets", "version_stdout"),
    [(TARGETS, "PostgreSQL 16.4"), (SUPABASE_TARGETS, "PostgreSQL 17.1")],
)
def test_every_client_call_passes_the_no_password_flag(tmp_path, targets, version_stdout) -> None:
    code, _out, _err, fake = _drive(
        tmp_path, SYNC_ARGV, now=NOW, targets=targets, version_stdout=version_stdout
    )

    assert code == 0
    clients = [call for call in fake.calls if call[0] in (PSQL, PG_DUMP, PG_RESTORE)]
    assert {call[0] for call in clients} >= {PSQL, PG_DUMP}
    assert all("-w" in call for call in clients)


def test_child_env_sets_a_connect_timeout(monkeypatch) -> None:
    monkeypatch.delenv("PGCONNECT_TIMEOUT", raising=False)
    fake = FakePostgres()
    postgres_status("postgresql://svc:secret@t1.example:5432/app", runtime=_runtime(fake))
    assert fake.envs[0]["PGCONNECT_TIMEOUT"] == "10"


def test_child_env_keeps_the_callers_connect_timeout(monkeypatch) -> None:
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "30")
    fake = FakePostgres()
    postgres_status("postgresql://svc:secret@t1.example:5432/app", runtime=_runtime(fake))
    assert fake.envs[0]["PGCONNECT_TIMEOUT"] == "30"


# --- probes connect where the sync will ----------------------------------------


def test_status_probes_sources_on_their_database_and_targets_on_postgres(tmp_path) -> None:
    code, _out, _err, fake = _drive(tmp_path, ["sync", "list", "--status"])

    assert code == 0
    probes = [call for call in fake.calls if call[-1] == "SELECT 1;"]
    # probes run concurrently, so only the multiset of databases is fixed
    assert sorted(call[call.index("-d") + 1] for call in probes) == ["app", "postgres", "postgres"]


def test_source_version_probe_uses_the_source_database(tmp_path) -> None:
    code, _out, _err, fake = _drive(tmp_path, SYNC_ARGV, now=NOW)

    assert code == 0
    versions = [call for call in fake.calls if call[-1] == "SELECT version();"]
    assert [(call[2], call[call.index("-d") + 1]) for call in versions] == [
        ("src.example", "app"),
        ("t1.example", "postgres"),
    ]


# --- the exists probe fails closed ----------------------------------------------


def test_exists_probe_failure_aborts_before_any_change(tmp_path) -> None:
    code, out, _err, fake = _drive(
        tmp_path,
        SYNC_ARGV,
        now=NOW,
        responses=(
            ("SELECT 1 FROM pg_database", CommandResult(2, "", "psql: error: connection refused")),
        ),
    )

    assert code == 1
    assert "Failed to check whether database 'app' exists" in out
    assert "connection refused" in out
    changes = [
        call[-1]
        for call in fake.calls
        if call[-1].startswith(("ALTER DATABASE", "CREATE DATABASE", "DROP"))
    ]
    assert changes == []
    assert [call for call in fake.calls if call[0] == PG_DUMP] == []


# --- plain-SQL restores stop on the first error --------------------------------


@pytest.mark.parametrize("verbose", [False, True])
def test_plain_sql_restore_runs_with_on_error_stop(tmp_path, verbose: bool) -> None:
    argv = [*SYNC_ARGV, "--verbose"] if verbose else SYNC_ARGV
    code, _out, _err, fake = _drive(tmp_path, argv, now=NOW)

    assert code == 0
    restore = next(
        call for index, call in enumerate(fake.calls) if fake.inputs[index] is not None
    )
    assert restore[restore.index("-v") + 1] == "ON_ERROR_STOP=1"
    assert ("-q" in restore) is not verbose


# --- no temp file survives an early interrupt -----------------------------------


def test_interrupt_during_target_backup_dump_discards_the_file(tmp_path, monkeypatch) -> None:
    removed = _track_unlink(monkeypatch)
    path = tmp_path / "dbt_secrets"
    _write_secrets(path, SOURCES, SUPABASE_TARGETS)
    fake = FakePostgres()
    signals = _StubSignals()
    dump_files: list[str] = []
    base = fake.run_command

    def run_command(argv, **kwargs):
        if argv[0] == PG_DUMP:
            dump_files.append(argv[argv.index("-f") + 1])
            assert signals.handler is None  # the target is not preserved yet
            raise Interrupted()
        return base(argv, **kwargs)

    runtime = Runtime(
        run_command=run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: fake.which.get(name),
        path_exists=lambda value: value in fake.files,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=lambda: NOW,
        signals=signals,
    )

    with pytest.raises(Interrupted):
        run_cli(
            SYNC_ARGV,
            env={"DBT_SECRETS_FILE": str(path)},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=runtime,
        )
    assert dump_files and dump_files[0] in removed
    assert not Path(dump_files[0]).exists()
    assert not any(call[-1].startswith("DROP SCHEMA") for call in fake.calls)


# --- warnings never quote credentials ----------------------------------------------


def test_invalid_entry_error_masks_the_password(tmp_path) -> None:
    code, _out, err, _fake = _drive(
        tmp_path, ["sync", "list"], sources=("bad|http://svc:s3cr3t@h:5432/db",)
    )

    assert code == 1
    assert "postgres.sources[1] (bad): 'url' must start with postgresql://" in err
    assert "http://***@h:5432/db" in err
    assert "s3cr3t" not in err


# --- status probes run concurrently -------------------------------------------------


def test_status_probes_run_concurrently(tmp_path) -> None:
    import threading

    path = tmp_path / "dbt_secrets"
    _write_secrets(path, SOURCES, TARGETS)
    fake = FakePostgres()
    base = fake.run_command
    barrier = threading.Barrier(3, timeout=5)  # one source + two targets

    def run_command(argv, **kwargs):
        if argv[-1] == "SELECT 1;":
            barrier.wait()  # sequential probes would time out here
        return base(argv, **kwargs)

    runtime = _runtime(fake)
    runtime.run_command = run_command
    out = io.StringIO()
    code = run_cli(
        ["sync", "list", "--status"],
        env={"DBT_SECRETS_FILE": str(path)},
        stdout=out,
        stderr=io.StringIO(),
        dependencies=runtime,
    )

    assert code == 0
    assert "Summary: 🟢 3 connected" in out.getvalue()


# --- tunnel hint on a refused target --------------------------------------------------


def test_exists_probe_failure_on_localhost_explains_the_tunnel(tmp_path) -> None:
    path = write_secrets(
        tmp_path / "secrets.toml",
        tunnels=("oracle|me|10.0.0.1|postgres|5432|127.0.0.1|5432",),
        postgres_sources=(SOURCES[0],),
        postgres_targets=("local|postgresql://svc:secret@localhost:5432",),
    )
    fake = FakePostgres(
        responses=(("SELECT 1 FROM pg_database", CommandResult(2, "", "connection refused")),)
    )
    base = fake.run_command

    def run_command(argv, **kwargs):
        if argv[0] == "lsof":
            return CommandResult(0, "p41\nn127.0.0.1:5432\n", "")
        if argv[0] == "ps":
            return CommandResult(0, "ssh -f -N -L 5432:127.0.0.1:5432 me@10.0.0.1\n", "")
        return base(argv, **kwargs)

    runtime = _runtime(fake, now=NOW)
    runtime.run_command = run_command
    out = io.StringIO()
    code = run_cli(
        SYNC_ARGV,
        env={"DBT_SECRETS_FILE": str(path)},
        stdout=out,
        stderr=io.StringIO(),
        dependencies=runtime,
    )

    assert code == 1
    assert "Failed to check whether database 'app' exists" in out.getvalue()
    assert (
        "tunnel postgres (oracle) is UP, so 10.0.0.1 refused the forward to 127.0.0.1:5432"
        in out.getvalue()
    )
