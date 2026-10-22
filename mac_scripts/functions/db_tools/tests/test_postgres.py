"""Tests for Postgres URL handling, client discovery, status, and cleanup."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

from db_tools.cli import run_cli
from db_tools.models import PostgresTarget
from db_tools.postgres import (
    PostgresTools,
    clear_postgres_backups,
    find_postgres_tools,
    parse_postgres_source_row,
    parse_postgres_target_row,
    parse_postgres_url,
    postgres_status,
    quote_identifier,
    quote_literal,
)
from db_tools.runtime import Runtime, SignalHooks
from shared.process import CommandResult

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

SOURCES = ("prod|postgresql://svc:secret@src.example:5432/app|app",)
TARGETS = (
    "one|postgresql://svc:secret@t1.example:5432/app?sslmode=require",
    "two|postgresql://svc:secret@t2.example:5432/other",
)

_LIST_ARGS = [PSQL, "-h", "t1.example", "-p", "5432", "-U", "svc", "-d", "postgres", "-At", "-c"]
_TERMINATE_ARGS = [PSQL, "-h", "t1.example", "-p", "5432", "-U", "svc", "-d", "postgres", "-t", "-c"]
_DROP_ARGS = [PSQL, "-h", "t1.example", "-p", "5432", "-U", "svc", "-d", "postgres", "-c"]


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
    envs: list[dict[str, str] | None] = field(default_factory=list)

    def run_command(self, argv, *, env=None, **_kwargs):
        self.calls.append(list(argv))
        self.envs.append(dict(env) if env is not None else None)
        query = argv[-1]
        host = argv[argv.index("-h") + 1]
        if query.startswith("SELECT datname FROM pg_database"):
            if host in self.list_fail_hosts:
                return CommandResult(1, "", "psql: connection refused")
            return CommandResult(0, BACKUP_NAME + "\n", "")
        if query.startswith("SELECT pg_terminate_backend"):
            return CommandResult(0, "", "")
        if query.startswith("DROP DATABASE"):
            return CommandResult(0, "", "")
        if query == "SELECT 1;":
            return CommandResult(self.probe_code, "1", "")
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


def _runtime(fake: FakePostgres) -> Runtime:
    return Runtime(
        run_command=fake.run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: fake.which.get(name),
        path_exists=lambda path: path in fake.files,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=datetime.now,
        signals=_StubSignals(),
    )


def _write_secrets(path: Path, sources: tuple[str, ...], targets: tuple[str, ...]) -> None:
    lines = ["typeset -a POSTGRES_SOURCES=(", *(f'  "{row}"' for row in sources), ")"]
    lines += ["typeset -a POSTGRES_TARGETS=(", *(f'  "{row}"' for row in targets), ")"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _drive(
    tmp_path,
    argv: list[str],
    *,
    sources: tuple[str, ...] = SOURCES,
    targets: tuple[str, ...] = TARGETS,
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
        dependencies=_runtime(fake),
    )
    return code, out.getvalue(), err.getvalue(), fake


# --- URL parsing and quoting --------------------------------------------------


def test_parse_url_keeps_password_colons_and_at_sign() -> None:
    url = parse_postgres_url("postgresql://user:p@ss:word@db.example:5432/app")
    assert (url.user, url.password) == ("user", "p@ss:word")
    assert (url.host, url.port, url.database) == ("db.example", "5432", "app")


def test_parse_url_defaults_database_to_postgres() -> None:
    assert parse_postgres_url("postgresql://user:pass@host:5432").database == "postgres"


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


def test_parse_secret_rows() -> None:
    source = parse_postgres_source_row("prod|postgresql://u:p@h:5432/db|db")
    assert source is not None
    assert (source.name, source.url, source.database) == (
        "prod",
        "postgresql://u:p@h:5432/db",
        "db",
    )
    target = parse_postgres_target_row("one|postgresql://u:p@h:5432/db")
    assert target is not None
    assert (target.name, target.url) == ("one", "postgresql://u:p@h:5432/db")
    assert parse_postgres_source_row("prod|postgresql://u:p@h:5432/db") is None
    assert parse_postgres_target_row("onlyname") is None


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
        [PSQL, "-h", "t1.example", "-p", "5432", "-U", "svc", "-d", "postgres", "-t", "-c", "SELECT 1;"]
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


def test_postgres_status_missing_client_counts_as_connected() -> None:
    assert (
        postgres_status(
            "postgresql://svc:secret@t1.example:5432/app",
            runtime=_runtime(FakePostgres(which={})),
        )
        is True
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
@pytest.mark.parametrize("flags", [("-c", "-n"), ("-n", "-c")])
def test_dry_run_does_not_drop(tmp_path, alias: str, flags: tuple[str, ...]) -> None:
    code, out, err, fake = _drive(tmp_path, ["sync", alias, *flags])

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
    code, out, err, fake = _drive(tmp_path, ["sync", "postgres", "-c", "-d", "1"])

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
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres", "-c", "-n", "-s", "1"])

    assert code == 0
    assert err == ""
    listings = fake.listings()
    assert [call[-1] for call in listings] == [SCOPED_QUERY, SCOPED_QUERY]


def test_destination_limits_cleanup_to_that_target(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres", "-c", "-n", "-d", "2"])

    assert code == 0
    assert err == ""
    assert fake.hosts(fake.listings()) == ["t2.example"]


def test_cleanup_without_destination_visits_every_target(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres", "-c", "-n"])

    assert code == 0
    assert err == ""
    assert fake.hosts(fake.listings()) == ["t1.example", "t2.example"]


def test_one_failed_target_still_visits_the_next_and_exits_one(tmp_path) -> None:
    code, out, err, fake = _drive(
        tmp_path,
        ["sync", "postgres", "-c", "-n"],
        list_fail_hosts=frozenset({"t2.example"}),
    )

    assert code == 1
    assert fake.hosts(fake.listings()) == ["t1.example", "t2.example"]
    assert "Failed to list PostgreSQL backup databases" in err
    assert "Dropped" not in out


def test_no_postgres_targets_exits_one(tmp_path) -> None:
    code, out, _err, fake = _drive(tmp_path, ["sync", "postgres", "-c"], targets=())

    assert code == 1
    assert fake.calls == []
    assert "No PostgreSQL targets" in out


def test_cleanup_never_unlinks_files(tmp_path, monkeypatch) -> None:
    def forbid(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("cleanup must not unlink files")

    monkeypatch.setattr(Path, "unlink", forbid)
    monkeypatch.setattr(os, "remove", forbid)
    keeper = tmp_path / "pg_dump.bin"
    keeper.write_text("x", encoding="utf-8")

    code, _out, err, _fake = _drive(tmp_path, ["sync", "postgres", "-c", "-d", "1"])

    assert code == 0
    assert err == ""
    assert keeper.exists()


def test_source_index_out_of_range_exits_one_without_psql(tmp_path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres", "-c", "-s", "2"])

    assert code == 1
    assert fake.calls == []
    assert "Invalid PostgreSQL source index" in err


@pytest.mark.parametrize("argv", [["-db", "1"], ["-c", "-n", "-db", "1"]])
def test_database_flag_is_accepted_and_ignored(tmp_path, argv: list[str]) -> None:
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres", *argv, "-c", "-n"])

    assert code == 0
    assert err == ""
    assert fake.hosts(fake.listings()) == ["t1.example", "t2.example"]


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


def test_sync_list_status_with_no_client_still_reports_connected(tmp_path) -> None:
    code, out, err, fake = _drive(tmp_path, ["sync", "list", "--status"], which={})

    assert code == 0
    assert err == ""
    assert fake.calls == []
    assert "🟢 1) prod → app | src.example" in out
    assert "🟢 1) one | t1.example [SSL: require]" in out
    assert "Summary: 🟢 3 connected  |  🔴 0 disconnected" in out


def test_sync_list_status_reports_failed_connection(tmp_path) -> None:
    code, out, _err, fake = _drive(tmp_path, ["sync", "list", "--status"], probe_code=1)

    assert code == 0
    assert "🔴 1) prod → app | src.example" in out
    assert "🔴 1) one | t1.example [SSL: require]" in out
    assert "Summary: 🟢 0 connected  |  🔴 3 disconnected" in out


def test_sync_list_skips_invalid_rows_with_a_warning(tmp_path) -> None:
    code, out, err, fake = _drive(tmp_path, ["sync", "list"], sources=("bad",))

    assert code == 0
    assert "bad" in err
    assert "(No PostgreSQL sources configured)" in out
