"""Tests for Mongo host parsing, client discovery, status, and cleanup."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from shared.process import CommandResult

from db_tools.cli import run_cli
from db_tools.models import MongoTarget
from db_tools.mongo import (
    MONGO_DROP_SCRIPT,
    MONGO_EXISTS_SCRIPT,
    MONGO_LIST_SCRIPT,
    MONGO_PING_SCRIPT,
    clear_mongo_backups,
    find_mongo_tools,
    mongo_eval_argv,
    mongo_host,
    mongo_status,
    mongo_uri_database,
    parse_mongo_source_row,
    parse_mongo_target_row,
    perform_mongo_sync,
)
from db_tools.runtime import Interrupted, Runtime, SignalHooks

MONGOSH = "/usr/local/bin/mongosh"
MONGO = "/usr/local/bin/mongo"
MONGODUMP = "/usr/local/bin/mongodump"
MONGORESTORE = "/usr/local/bin/mongorestore"
PSQL = "/usr/local/bin/psql"
PG_DUMP = "/usr/local/bin/pg_dump"
PG_RESTORE = "/usr/local/bin/pg_restore"

PG_WHICH = {"psql": PSQL, "pg_dump": PG_DUMP, "pg_restore": PG_RESTORE}

FULL_WHICH = {
    "mongosh": MONGOSH,
    "mongo": MONGO,
    "mongodump": MONGODUMP,
    "mongorestore": MONGORESTORE,
}

EVIL_DATABASE = "a'); db.getSiblingDB('admin').dropDatabase(); //"
NO_DATABASE = "logs_backup_20260102_130000"
BACKUP_NAME = "app_backup_20260101_120000"
ALL_BACKUPS = f"{BACKUP_NAME}\n{NO_DATABASE}\n"

SOURCES = ("prod|mongodb://user:secret@cluster.example/?retryWrites=true|app,logs",)
TARGETS = (
    "one|mongodb://user:secret@127.0.0.1:27017",
    "two|mongodb://user:secret@t2.example:27017",
)


@dataclass
class FakeMongo:
    """Records argv/env pairs and answers mongosh calls without spawning."""

    which: dict[str, str] = field(default_factory=lambda: dict(FULL_WHICH))
    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str]] = field(default_factory=list)
    list_codes: dict[str, int] = field(default_factory=dict)  # host -> returncode
    drop_codes: dict[str, int] = field(default_factory=dict)  # database -> returncode
    ping_code: int = 0
    ping_by_host: dict[str, int] = field(default_factory=dict)  # host -> returncode
    list_stdout: str = ALL_BACKUPS
    exists_stdout: str = "true"  # fake answer for MONGO_EXISTS_SCRIPT
    exists_code: int = 0  # non-zero reads as a failed probe
    pipe_codes: list[int] = field(default_factory=list)  # piped commands, in order
    pipe_stderr: str = ""
    pipes: list[tuple[list[str], list[str]]] = field(default_factory=list)
    pipe_streams: list[bool] = field(default_factory=list)
    handler_at_pipe: list[object] = field(default_factory=list)

    def run_command(self, argv, *, env=None, input_text=None, **_kwargs):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        script = argv[4]
        if script == MONGO_LIST_SCRIPT:
            return CommandResult(
                self.list_codes.get(mongo_host(argv[1]), 0), self.list_stdout, ""
            )
        if script == MONGO_DROP_SCRIPT:
            database = (env or {}).get("DBT_MONGO_DB", "")
            stderr = "not authorized\n" if self.drop_codes.get(database) else ""
            return CommandResult(self.drop_codes.get(database, 0), "", stderr)
        if script == MONGO_PING_SCRIPT:
            code = self.ping_by_host.get(mongo_host(argv[1]), self.ping_code)
            return CommandResult(code, "1", "")
        if script == MONGO_EXISTS_SCRIPT:
            return CommandResult(self.exists_code, self.exists_stdout, "")
        raise AssertionError(f"unexpected command: {argv}")

    def run_pipe(self, left, right, *, stream: bool = False, **_kwargs):
        self.pipes.append((list(left), list(right)))
        self.pipe_streams.append(stream)
        code = self.pipe_codes.pop(0) if self.pipe_codes else 0
        return CommandResult(code, "", self.pipe_stderr if code else "")

    def drops(self) -> list[tuple[list[str], str]]:
        pairs = []
        for argv, env in zip(self.calls, self.envs, strict=True):
            if argv[4] == MONGO_DROP_SCRIPT:
                pairs.append((argv, (env or {}).get("DBT_MONGO_DB", "")))
        return pairs


def _runtime(
    fake: FakeMongo, *, now: datetime | None = None, signals: SignalHooks | None = None
) -> Runtime:
    signals = signals if signals is not None else SignalHooksStub()

    def run_pipe(*args, **kwargs):
        fake.handler_at_pipe.append(getattr(signals, "handler", None))
        return fake.run_pipe(*args, **kwargs)

    return Runtime(
        run_command=fake.run_command,
        run_pipe=run_pipe,
        which=lambda name: fake.which.get(name),
        path_exists=lambda _path: False,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=(lambda: now) if now is not None else datetime.now,
        signals=signals,
    )


class SignalHooksStub(SignalHooks):
    def bind(self, handler) -> None:  # pragma: no cover - cleanup binds nothing
        raise AssertionError("clear_mongo_backups must not bind signal handlers")


def _write_secrets(path: Path, sources: tuple[str, ...], targets: tuple[str, ...]) -> None:
    lines = ["typeset -a MONGO_SOURCES=(", *(f'  "{row}"' for row in sources), ")"]
    lines += ["typeset -a MONGO_TARGETS=(", *(f'  "{row}"' for row in targets), ")"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    fake = FakeMongo(**fake_args)
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
        dependencies=_runtime(fake, now=now, signals=SyncSignals()),
    )
    return code, out.getvalue(), err.getvalue(), fake


# --- fixed scripts and argv construction ---------------------------------------


def test_fixed_scripts_are_exact() -> None:
    assert MONGO_EXISTS_SCRIPT == (
        "db.getSiblingDB(process.env.DBT_MONGO_DB).getCollectionNames().length > 0"
    )
    assert MONGO_DROP_SCRIPT == (
        "db.getSiblingDB(process.env.DBT_MONGO_DB).dropDatabase()"
    )
    assert MONGO_LIST_SCRIPT == (
        'db.getMongo().getDBNames().filter((name) => '
        '/_backup_[0-9]{8}_[0-9]{6}$/.test(name)).sort().join("\\n")'
    )
    assert MONGO_PING_SCRIPT == "db.adminCommand('ping')"


def test_mongo_host_extracts_host_from_srv_and_plain_uris() -> None:
    uri = "mongodb+srv://user:pass@cluster.example/app?retryWrites=true"
    assert mongo_host(uri) == "cluster.example"
    uri_plain = "mongodb://user:pass@127.0.0.1:27017"
    assert mongo_host(uri_plain) == "127.0.0.1"


def test_mongo_host_strips_both_scheme_variants() -> None:
    assert mongo_host("mongodb://h.example:27017") == "h.example"
    assert mongo_host("mongodb+srv://h.example") == "h.example"


def test_mongo_host_without_credentials_keeps_the_host_part() -> None:
    # The zsh `cut -d@ -f2` yields "" for credential-less URIs; dbt keeps
    # the host segment instead so `sync list` never prints a missing host.
    assert mongo_host("mongodb+srv://cluster.example/app") == "cluster.example"


# --- client discovery ----------------------------------------------------------


def test_find_mongo_tools_prefers_mongosh_over_mongo() -> None:
    tools = find_mongo_tools(_runtime(FakeMongo()))
    assert tools is not None
    assert (tools.client, tools.mongodump, tools.mongorestore) == (
        MONGOSH,
        MONGODUMP,
        MONGORESTORE,
    )


def test_find_mongo_tools_ignores_the_legacy_mongo_shell() -> None:
    fake = FakeMongo(which={"mongo": MONGO, "mongodump": MONGODUMP, "mongorestore": MONGORESTORE})
    assert find_mongo_tools(_runtime(fake)) is None


@pytest.mark.parametrize(
    "which",
    [
        {"mongodump": MONGODUMP, "mongorestore": MONGORESTORE},  # no client
        {"mongosh": MONGOSH, "mongorestore": MONGORESTORE},  # no mongodump
        {"mongosh": MONGOSH, "mongodump": MONGODUMP},  # no mongorestore
    ],
)
def test_find_mongo_tools_needs_client_and_dump_tools(which: dict[str, str]) -> None:
    assert find_mongo_tools(_runtime(FakeMongo(which=which))) is None


@pytest.mark.parametrize(
    "which",
    [
        {"mongodump": MONGODUMP, "mongorestore": MONGORESTORE},
        {"mongosh": MONGOSH, "mongorestore": MONGORESTORE},
        {"mongosh": MONGOSH, "mongodump": MONGODUMP},
    ],
)
def test_cleanup_without_client_or_dump_tools_exits_one(
    which: dict[str, str],
) -> None:
    fake = FakeMongo(which=which)
    out = io.StringIO()
    err = io.StringIO()
    code = clear_mongo_backups(
        [MongoTarget(name="one", uri="mongodb://user:secret@127.0.0.1:27017")],
        dry_run=True,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )
    assert code == 1
    assert "Mongo" in err.getvalue()
    assert fake.calls == []


# --- database name safety ------------------------------------------------------


def test_database_name_stays_out_of_eval() -> None:
    exists_argv, exists_env = mongo_eval_argv(
        MONGOSH, "mongodb://u:p@h:27017", EVIL_DATABASE, MONGO_EXISTS_SCRIPT
    )
    assert exists_argv == [
        MONGOSH,
        "mongodb://u:p@h:27017",
        "--quiet",
        "--eval",
        MONGO_EXISTS_SCRIPT,
    ]
    assert exists_env == {"DBT_MONGO_DB": EVIL_DATABASE}
    assert exists_argv[4] == MONGO_EXISTS_SCRIPT
    assert not any("admin" in argument for argument in exists_argv)

    drop_argv, drop_env = mongo_eval_argv(
        MONGOSH, "mongodb://u:p@h:27017", EVIL_DATABASE, MONGO_DROP_SCRIPT
    )
    assert drop_argv == [
        MONGOSH,
        "mongodb://u:p@h:27017",
        "--quiet",
        "--eval",
        MONGO_DROP_SCRIPT,
    ]
    assert drop_env == {"DBT_MONGO_DB": EVIL_DATABASE}
    assert not any("admin" in argument for argument in drop_argv)


def test_list_script_carries_no_database_env() -> None:
    argv, env = mongo_eval_argv(MONGOSH, "mongodb://u:p@h:27017", None, MONGO_LIST_SCRIPT)
    assert argv == [MONGOSH, "mongodb://u:p@h:27017", "--quiet", "--eval", MONGO_LIST_SCRIPT]
    assert "DBT_MONGO_DB" not in MONGO_LIST_SCRIPT
    assert env == {}


# --- connection status ---------------------------------------------------------


def test_mongo_status_pings_with_the_fixed_script() -> None:
    fake = FakeMongo()
    uri = "mongodb+srv://user:secret@cluster.example/app"
    assert mongo_status(uri, runtime=_runtime(fake)) is True
    assert fake.calls == [[MONGOSH, uri, "--quiet", "--eval", MONGO_PING_SCRIPT]]
    assert fake.envs[0] == {}


def test_mongo_status_failed_ping_is_false() -> None:
    assert mongo_status("mongodb://u:p@h:27017", runtime=_runtime(FakeMongo(ping_code=1))) is False


def test_mongo_status_missing_client_is_unknown() -> None:
    fake = FakeMongo(which={"mongodump": MONGODUMP, "mongorestore": MONGORESTORE})
    assert mongo_status("mongodb://u:p@h:27017", runtime=_runtime(fake)) is None
    assert fake.calls == []


def test_mongo_status_never_runs_the_legacy_mongo_shell() -> None:
    fake = FakeMongo(which={"mongo": MONGO})
    assert mongo_status("mongodb://u:p@h:27017", runtime=_runtime(fake)) is None
    assert fake.calls == []


def test_cleanup_without_mongosh_names_the_legacy_shell_as_unsupported() -> None:
    fake = FakeMongo(which={"mongo": MONGO, "mongodump": MONGODUMP, "mongorestore": MONGORESTORE})
    out, err = io.StringIO(), io.StringIO()
    code = clear_mongo_backups(
        _targets("mongodb://user:secret@127.0.0.1:27017"),
        dry_run=True,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 1
    assert fake.calls == []
    assert "legacy mongo shell is not supported" in err.getvalue()


# --- secret row parsing --------------------------------------------------------


def test_parse_mongo_rows() -> None:
    source = parse_mongo_source_row("prod|mongodb://u:p@h:27017|app,logs")
    assert source is not None
    assert (source.name, source.uri, source.databases) == (
        "prod",
        "mongodb://u:p@h:27017",
        "app,logs",
    )
    target = parse_mongo_target_row("one|mongodb://u:p@h:27017")
    assert target is not None
    assert (target.name, target.uri) == ("one", "mongodb://u:p@h:27017")
    assert parse_mongo_source_row("prod|mongodb://u:p@h:27017") is None
    assert parse_mongo_target_row("onlyname") is None
    assert parse_mongo_target_row("name") is None


# --- cleanup (library level; `sync mongodb` flags land in Task 8) ---------------


def _targets(*uris: str) -> list[MongoTarget]:
    return [MongoTarget(name=f"t{index}", uri=uri) for index, uri in enumerate(uris, 1)]


def test_dry_run_prints_names_and_never_drops() -> None:
    fake = FakeMongo()
    out = io.StringIO()
    err = io.StringIO()
    code = clear_mongo_backups(
        _targets("mongodb://user:secret@127.0.0.1:27017"),
        dry_run=True,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 0
    assert err.getvalue() == ""
    assert len(fake.calls) == 1
    assert fake.calls[0] == [
        MONGOSH,
        "mongodb://user:secret@127.0.0.1:27017",
        "--quiet",
        "--eval",
        MONGO_LIST_SCRIPT,
    ]
    assert fake.envs[0] == {}
    assert BACKUP_NAME in out.getvalue()
    assert NO_DATABASE in out.getvalue()
    assert "dropDatabase" not in "".join(" ".join(call) for call in fake.calls)
    assert "Dry run only" in out.getvalue()


def test_real_clear_drops_every_backup_via_env_name() -> None:
    fake = FakeMongo()
    out = io.StringIO()
    err = io.StringIO()
    code = clear_mongo_backups(
        _targets("mongodb://user:secret@127.0.0.1:27017"),
        dry_run=False,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 0
    assert err.getvalue() == ""
    assert [(argv[1], database) for argv, database in fake.drops()] == [
        ("mongodb://user:secret@127.0.0.1:27017", BACKUP_NAME),
        ("mongodb://user:secret@127.0.0.1:27017", NO_DATABASE),
    ]
    drop_argv, _database = fake.drops()[0]
    assert drop_argv[-1] == MONGO_DROP_SCRIPT
    assert "Summary: dropped 2, failed 0" in out.getvalue()


def test_cleanup_drops_only_lines_shaped_like_backup_names() -> None:
    fake = FakeMongo(
        list_stdout=(
            "Warning: deprecated option in use\n"
            f"{BACKUP_NAME}\n"
            "admin\n"
            "app_backup_2026_bad\n"
            f"{NO_DATABASE}\n"
        )
    )
    out, err = io.StringIO(), io.StringIO()
    code = clear_mongo_backups(
        _targets("mongodb://user:secret@127.0.0.1:27017"),
        dry_run=False,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 0
    assert [database for _argv, database in fake.drops()] == [BACKUP_NAME, NO_DATABASE]
    assert "ignored non-backup output line: admin" in err.getvalue()
    assert "ignored non-backup output line: Warning: deprecated" in err.getvalue()
    assert "admin" not in out.getvalue()


def test_cleanup_with_only_non_backup_output_drops_nothing() -> None:
    fake = FakeMongo(list_stdout="Warning: something\nadmin\n")
    out, err = io.StringIO(), io.StringIO()
    code = clear_mongo_backups(
        _targets("mongodb://user:secret@127.0.0.1:27017"),
        dry_run=False,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 0
    assert fake.drops() == []
    assert "No MongoDB backup databases found" in out.getvalue()


def test_one_failed_drop_still_visits_the_next_and_exits_one() -> None:
    fake = FakeMongo(drop_codes={BACKUP_NAME: 1})
    out = io.StringIO()
    err = io.StringIO()
    code = clear_mongo_backups(
        _targets("mongodb://user:secret@127.0.0.1:27017"),
        dry_run=False,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 1
    assert len(fake.drops()) == 2
    assert "Failed to drop: app_backup_20260101_120000" in out.getvalue()
    assert "Summary: dropped 1, failed 1" in out.getvalue()


def test_failed_list_still_visits_the_next_target() -> None:
    fake = FakeMongo(list_codes={"127.0.0.1": 1}, list_stdout="")
    out = io.StringIO()
    err = io.StringIO()
    code = clear_mongo_backups(
        _targets("mongodb://user:secret@127.0.0.1:27017", "mongodb://user:s@t2.example:27017"),
        dry_run=True,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 1
    listed = [call for call in fake.calls if call[4] == MONGO_LIST_SCRIPT]
    assert [mongo_host(call[1]) for call in listed] == ["127.0.0.1", "t2.example"]
    assert "Failed to list MongoDB backup databases" in err.getvalue()


def test_empty_target_output_reports_no_backups() -> None:
    fake = FakeMongo(list_stdout="")
    out = io.StringIO()
    err = io.StringIO()
    code = clear_mongo_backups(
        _targets("mongodb://user:secret@127.0.0.1:27017"),
        dry_run=False,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 0
    assert err.getvalue() == ""
    assert "No MongoDB backup databases found" in out.getvalue()
    assert len(fake.calls) == 1


def test_no_mongo_targets_returns_zero() -> None:
    fake = FakeMongo()
    out = io.StringIO()
    err = io.StringIO()
    code = clear_mongo_backups(
        [],
        dry_run=False,
        runtime=_runtime(fake),
        stdout=out,
        stderr=err,
    )

    assert code == 0
    assert fake.calls == []
    assert err.getvalue() == ""


# --- sync list / ls through run_cli ---------------------------------------------


@pytest.mark.parametrize("alias", ["list", "ls"])
def test_sync_list_prints_mongo_sections_with_1_based_indexes(tmp_path, alias: str) -> None:
    code, out, err, fake = _drive(tmp_path, ["sync", alias])

    assert code == 0
    assert err == ""
    assert fake.calls == []
    assert "🍃 MongoDB Sources (1):" in out
    assert "  1) prod → app,logs | cluster.example" in out
    assert "🎯 MongoDB Targets (2):" in out
    assert "  1) one | 127.0.0.1" in out
    assert "  2) two | t2.example" in out
    assert "(No MongoDB targets configured)" not in out


def test_sync_list_persists_postgres_sections_and_no_client_calls(tmp_path) -> None:
    code, out, _err, fake = _drive(tmp_path, ["sync", "list"])

    assert code == 0
    assert fake.calls == []
    assert "🐘 PostgreSQL Sources (0):" in out
    assert "  (No PostgreSQL sources configured)" in out


def test_sync_list_status_pings_each_source_and_target(tmp_path) -> None:
    code, out, err, fake = _drive(tmp_path, ["sync", "list", "--status"])

    assert code == 0
    assert err == ""
    uris = ("mongodb://user:secret@cluster.example/?retryWrites=true",
            "mongodb://user:secret@127.0.0.1:27017",
            "mongodb://user:secret@t2.example:27017")
    assert fake.calls == [
        [MONGOSH, uri, "--quiet", "--eval", MONGO_PING_SCRIPT] for uri in uris
    ]
    assert all(env == {} for env in fake.envs)
    assert "🟢 1) prod → app,logs | cluster.example" in out
    assert "🟢 1) one | 127.0.0.1" in out
    assert "🟢 2) two | t2.example" in out
    assert "Summary: 🟢 3 connected  |  🔴 0 disconnected" in out


def test_sync_list_status_failed_ping_is_marked_disconnected(tmp_path) -> None:
    code, out, _err, _fake = _drive(tmp_path, ["sync", "list", "--status"], ping_code=1)

    assert code == 0
    assert "🔴 1) prod → app,logs | cluster.example" in out
    assert "🟢 1)" not in out
    assert "Summary: 🟢 0 connected  |  🔴 3 disconnected" in out


def test_sync_list_status_with_no_mongo_client_reports_unknown(tmp_path) -> None:
    code, out, err, fake = _drive(
        tmp_path, ["sync", "list", "--status"], which={"mongodump": MONGODUMP}
    )

    assert code == 0
    assert err == ""
    assert fake.calls == []
    assert "⚪ 1) prod → app,logs | cluster.example" in out
    assert "⚪ 3 unknown (client not installed)" in out


def test_sync_list_skips_invalid_mongo_rows_with_a_warning(tmp_path) -> None:
    code, out, err, _fake = _drive(tmp_path, ["sync", "list"], sources=("bad",))

    assert code == 0
    assert "bad" in err
    assert "🍃 MongoDB Sources (0):" in out
    assert "  (No MongoDB sources configured)" in out


# --- sync flow (library level; CLI selection below) ------------------------------

# The brief's test clock; `app` then backs up as this name.
BACKUP_CLOCK = datetime(2026, 10, 22, 15, 4, 5)
SYNC_BACKUP = "app_backup_20261022_150405"

SOURCE_URI = SOURCES[0].split("|")[1]
TARGET_URI = TARGETS[0].split("|")[1]


class SyncSignals(SignalHooks):
    """A signals stub whose bind only stores the handler (never the OS)."""

    def __init__(self) -> None:
        self.handler = None
        self.bound: list[object] = []

    def bind(self, handler) -> None:
        self.bound.append(handler)
        self.handler = handler


def _sync_runtime(fake: FakeMongo, *, now: datetime = BACKUP_CLOCK):
    """Runtime with a fixed clock and a handler snapshot on every pipe."""
    signals = SyncSignals()

    def run_pipe(*args, **kwargs):
        fake.handler_at_pipe.append(signals.handler)
        return fake.run_pipe(*args, **kwargs)

    runtime = Runtime(
        run_command=fake.run_command,
        run_pipe=run_pipe,
        which=lambda name: fake.which.get(name),
        path_exists=lambda _path: False,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=lambda: now,
        signals=signals,
    )
    return runtime, signals


def _sync(fake: FakeMongo, *, verbose: bool = False):
    runtime, signals = _sync_runtime(fake)
    out, err = io.StringIO(), io.StringIO()
    code = perform_mongo_sync(
        SOURCE_URI,
        "app",
        TARGET_URI,
        verbose=verbose,
        runtime=runtime,
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue(), signals


def test_source_ping_failure_returns_one_without_a_pipe() -> None:
    fake = FakeMongo(ping_code=1)
    code, _out, err, _signals = _sync(fake)

    assert code == 1
    assert fake.pipes == []
    assert "source connection failed" in err
    assert len(fake.calls) == 1  # only the source ping ran


def test_target_ping_failure_returns_one_without_a_pipe() -> None:
    fake = FakeMongo(ping_by_host={"127.0.0.1": 1})
    code, _out, err, _signals = _sync(fake)

    assert code == 1
    assert fake.pipes == []
    assert "target connection failed" in err
    assert [mongo_host(call[1]) for call in fake.calls] == ["cluster.example", "127.0.0.1"]


def test_missing_mongodump_returns_one() -> None:
    fake = FakeMongo(which={"mongosh": MONGOSH, "mongorestore": MONGORESTORE})
    code, _out, err, _signals = _sync(fake)

    assert code == 1
    assert fake.pipes == []
    assert "MongoDB" in err


def test_exists_true_backs_up_syncs_and_drops_backup() -> None:
    fake = FakeMongo()
    code, out, err, signals = _sync(fake)

    assert code == 0
    assert err == ""
    assert fake.pipes == [
        (
            [MONGODUMP, f"--uri={TARGET_URI}", "--db=app", "--archive", "--gzip"],
            [
                MONGORESTORE,
                f"--uri={TARGET_URI}",
                "--archive",
                "--gzip",
                "--nsFrom=app.*",
                f"--nsTo={SYNC_BACKUP}.*",
            ],
        ),
        (
            [MONGODUMP, f"--uri={SOURCE_URI}", "--db=app", "--archive", "--gzip"],
            [
                MONGORESTORE,
                f"--uri={TARGET_URI}",
                "--drop",
                "--archive",
                "--gzip",
                "--nsFrom=app.*",
                "--nsTo=app.*",
            ],
        ),
    ]
    # Ping source, ping target, probe the target database, drop the backup.
    assert fake.envs == [
        {},
        {},
        {"DBT_MONGO_DB": "app"},
        {"DBT_MONGO_DB": SYNC_BACKUP},
    ]
    assert [database for _argv, database in fake.drops()] == [SYNC_BACKUP]
    assert SYNC_BACKUP in out
    # The handler is bound only once the backup pipe succeeded ...
    assert fake.handler_at_pipe == [None, signals.bound[0]]
    assert signals.bound[0] is not None
    # ... and it is gone again before this function returns.
    assert signals.handler is None


def test_backup_pipe_failure_returns_one_before_sync() -> None:
    fake = FakeMongo(pipe_codes=[1])
    code, _out, _err, signals = _sync(fake)

    assert code == 1
    assert len(fake.pipes) == 1  # backup only; the sync pipe never starts
    assert fake.drops() == []
    assert fake.handler_at_pipe == [None]
    assert signals.handler is None


def test_exists_false_skips_backup_and_runs_sync() -> None:
    fake = FakeMongo(exists_stdout="false")
    code, out, _err, signals = _sync(fake)

    assert code == 0
    assert "skipping backup" in out
    assert len(fake.pipes) == 1  # only the sync pipe
    _sync_left, sync_right = fake.pipes[0]
    assert "--drop" in sync_right
    assert "--nsFrom=app.*" in sync_right
    assert "--nsTo=app.*" in sync_right
    # No backup database exists, so no handler is ever bound.
    assert signals.bound == []
    assert signals.handler is None
    assert fake.drops() == []


def test_probe_failure_fails_closed_before_any_pipe() -> None:
    """A failed probe must never read as "no backup needed" (fail closed)."""
    fake = FakeMongo(exists_code=1)
    code, _out, err, signals = _sync(fake)

    assert code == 1
    assert fake.pipes == []  # neither the backup nor the sync pipe ran
    assert "127.0.0.1" in err  # the message names the target host
    assert "not attempting a sync" in err
    assert signals.bound == []  # nothing was bound: the sync never started
    assert fake.drops() == []
    # Ping source, ping target, failed probe: three commands and no more.
    assert len(fake.calls) == 3
    assert fake.envs == [{}, {}, {"DBT_MONGO_DB": "app"}]


@pytest.mark.parametrize("verbose", [True, False])
def test_pipe_stream_flag_matches_verbose(verbose: bool) -> None:
    fake = FakeMongo()
    code, _out, _err, _signals = _sync(fake, verbose=verbose)

    assert code == 0
    assert fake.pipe_streams == [verbose, verbose]


def test_sync_failure_restores_backup() -> None:
    fake = FakeMongo(pipe_codes=[0, 1])
    code, out, _err, _signals = _sync(fake)

    assert code == 1
    assert len(fake.pipes) == 3
    restore_left, restore_right = fake.pipes[2]
    assert restore_left == [
        MONGODUMP,
        f"--uri={TARGET_URI}",
        f"--db={SYNC_BACKUP}",
        "--archive",
        "--gzip",
    ]
    assert restore_right == [
        MONGORESTORE,
        f"--uri={TARGET_URI}",
        "--drop",
        "--archive",
        "--gzip",
        f"--nsFrom={SYNC_BACKUP}.*",
        "--nsTo=app.*",
    ]
    # The restored backup is removed again.
    assert [database for _argv, database in fake.drops()] == [SYNC_BACKUP]
    assert f"Restoring backup database '{SYNC_BACKUP}'" in out


def test_restore_failure_keeps_backup() -> None:
    fake = FakeMongo(pipe_codes=[0, 1, 1])
    code, out, _err, _signals = _sync(fake)

    assert code == 1
    # The restore failed, so the backup database must not be dropped.
    assert fake.drops() == []
    assert f"available as '{SYNC_BACKUP}'" in out


def test_success_drops_backup_and_exit_zero() -> None:
    fake = FakeMongo()
    code, out, _err, _signals = _sync(fake)

    assert code == 0
    assert "Removing backup database" in out
    assert "Backup removed" in out


def test_failed_backup_drop_warns_but_exits_zero() -> None:
    fake = FakeMongo(drop_codes={SYNC_BACKUP: 1})
    code, out, _err, _signals = _sync(fake)

    assert code == 0
    assert SYNC_BACKUP in out
    assert "⚠️" in out


def test_backup_pipe_failure_prints_the_cause() -> None:
    fake = FakeMongo(pipe_codes=[1], pipe_stderr="Failed: error connecting to db server\n")
    code, out, _err, _signals = _sync(fake)

    assert code == 1
    assert "Failed to backup target database" in out
    assert "Error: Failed: error connecting to db server" in out


def test_sync_pipe_failure_prints_the_cause_before_restoring() -> None:
    fake = FakeMongo(pipe_codes=[0, 1], pipe_stderr="2026-10-26 Failed: not authorized\n")
    code, out, _err, _signals = _sync(fake)

    assert code == 1
    assert "MongoDB sync failed" in out
    assert "Error: 2026-10-26 Failed: not authorized" in out
    assert out.index("Error: 2026-10-26") < out.index("Restoring backup database")


def test_restore_pipe_failure_prints_the_cause() -> None:
    fake = FakeMongo(pipe_codes=[0, 1, 1], pipe_stderr="Failed: restore broke\n")
    code, out, _err, _signals = _sync(fake)

    assert code == 1
    assert "Failed to restore backup" in out
    assert out.count("Error: Failed: restore broke") == 2


def test_pipe_failure_without_captured_stderr_points_at_verbose() -> None:
    fake = FakeMongo(pipe_codes=[1])
    code, out, _err, _signals = _sync(fake)

    assert code == 1
    assert "Run again with --verbose" in out


def test_interrupt_during_sync_pipe_restores_after_the_pipe_unwound() -> None:
    fake = FakeMongo()
    runtime, signals = _sync_runtime(fake)
    base = runtime.run_pipe
    order: list[str] = []

    def run_pipe(left, right, **kwargs):
        order.append(right[-1])
        if len(fake.pipes) == 1:  # the sync pipe
            fake.pipes.append((list(left), list(right)))
            raise Interrupted()
        return base(left, right, **kwargs)

    runtime.run_pipe = run_pipe
    out = io.StringIO()
    code = perform_mongo_sync(
        SOURCE_URI, "app", TARGET_URI, verbose=False,
        runtime=runtime, stdout=out, stderr=io.StringIO(),
    )

    assert code == 130
    assert order == [f"--nsTo={SYNC_BACKUP}.*", "--nsTo=app.*", "--nsTo=app.*"]
    assert "MongoDB sync interrupted" in out.getvalue()
    assert "Backup restored successfully" in out.getvalue()
    # Unbound before the restore ran.
    assert signals.bound[-1] is None
    assert signals.handler is None


def test_interrupt_before_any_backup_propagates_with_nothing_bound() -> None:
    fake = FakeMongo(exists_stdout="false")
    runtime, signals = _sync_runtime(fake)

    def run_pipe(*_args, **_kwargs):
        raise Interrupted()

    runtime.run_pipe = run_pipe
    with pytest.raises(Interrupted):
        perform_mongo_sync(
            SOURCE_URI, "app", TARGET_URI, verbose=False,
            runtime=runtime, stdout=io.StringIO(), stderr=io.StringIO(),
        )
    assert signals.bound == []


def test_signal_handler_restores_backup_and_returns_130() -> None:
    fake = FakeMongo()
    _code, _out, _err, signals = _sync(fake)

    # The handler was live during the sync pipe: an interrupt there restores.
    handler = fake.handler_at_pipe[1]
    assert handler is not None
    assert handler is signals.bound[0]
    assert handler() == 130
    restore_left, restore_right = fake.pipes[2]
    assert restore_left == [
        MONGODUMP,
        f"--uri={TARGET_URI}",
        f"--db={SYNC_BACKUP}",
        "--archive",
        "--gzip",
    ]
    assert restore_right == [
        MONGORESTORE,
        f"--uri={TARGET_URI}",
        "--drop",
        "--archive",
        "--gzip",
        f"--nsFrom={SYNC_BACKUP}.*",
        "--nsTo=app.*",
    ]


def test_interrupt_without_backup_signals_nothing_to_restore() -> None:
    fake = FakeMongo(exists_stdout="false")
    _code, _out, _err, signals = _sync(fake)

    # Unused except by this test's unpacking; signals carries the assertion.
    assert signals.handler is None


# --- sync mongodb · run_cli selection ---------------------------------------------


def test_sync_mongodb_syncs_the_selected_database(tmp_path) -> None:
    code, _out, err, fake = _drive(
        tmp_path,
        ["sync", "mongodb", "-s", "1", "-d", "1", "-db", "2"],
        now=BACKUP_CLOCK,
    )

    assert code == 0
    assert err == ""
    assert len(fake.pipes) == 2
    sync_left, sync_right = fake.pipes[1]
    assert "--db=logs" in sync_left
    assert "--nsFrom=logs.*" in sync_right
    assert "--nsTo=logs.*" in sync_right
    assert {"DBT_MONGO_DB": "logs"} in fake.envs
    assert [database for _argv, database in fake.drops()] == [
        "logs_backup_20261022_150405"
    ]


def test_sync_mongodb_defaults_to_the_first_database(tmp_path) -> None:
    code, _out, err, fake = _drive(
        tmp_path, ["sync", "mongodb", "-s", "1", "-d", "1"], now=BACKUP_CLOCK
    )

    assert code == 0
    assert err == ""
    sync_left, _sync_right = fake.pipes[1]
    assert "--db=app" in sync_left


def test_sync_mongodb_database_index_out_of_range_exits_one_without_ping(tmp_path) -> None:
    code, _out, err, fake = _drive(
        tmp_path, ["sync", "mongodb", "-s", "1", "-d", "1", "-db", "3"]
    )

    assert code == 1
    assert fake.calls == []  # no ping, no probe
    assert "Invalid MongoDB database index: 3" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["sync", "mongodb"],
        ["sync", "mongodb", "-s", "1"],
        ["sync", "mongodb", "-d", "1"],
    ],
)
def test_sync_mongodb_requires_source_and_destination(tmp_path, argv: list[str]) -> None:
    code, _out, err, fake = _drive(tmp_path, argv)

    assert code == 1
    assert fake.calls == []
    assert err != ""


def test_sync_mongodb_rejects_the_clear_flag(tmp_path) -> None:
    code, _out, _err, fake = _drive(tmp_path, ["sync", "mongodb", "-c"])

    assert code == 1
    assert fake.calls == []
    assert fake.pipes == []


def test_sync_mongodb_without_mongodump_returns_one(tmp_path) -> None:
    which = {"mongosh": MONGOSH, "mongorestore": MONGORESTORE}
    code, _out, err, fake = _drive(
        tmp_path, ["sync", "mongodb", "-s", "1", "-d", "1"], now=BACKUP_CLOCK, which=which
    )

    assert code == 1
    assert fake.pipes == []
    assert "MongoDB" in err


# --- sync -c / --clear-backups · combined cleanup ----------------------------------

PG_TARGETS = ("pg1|postgresql://u:p@pgt.example:5432/db",)


@dataclass
class FakeAll(FakeMongo):
    """The Mongo fake plus psql dispatch; shared timeline in `calls`."""

    which: dict[str, str] = field(default_factory=lambda: {**FULL_WHICH, **PG_WHICH})
    pg_list_stdout: str = "old_backup_20260101_120000\n"
    pg_list_fail_hosts: frozenset[str] = frozenset()

    def run_command(self, argv, *, env=None, input_text=None, **kwargs):
        if argv[0] == PSQL:
            self.calls.append(list(argv))  # keep one ordered timeline
            self.envs.append(dict(env or {}))
            query = argv[-1]
            host = argv[argv.index("-h") + 1]
            if query.startswith("SELECT datname FROM pg_database"):
                if host in self.pg_list_fail_hosts:
                    return CommandResult(1, "", "psql: connection refused")
                return CommandResult(0, self.pg_list_stdout, "")
            if query.startswith("SELECT pg_terminate_backend"):
                return CommandResult(0, "", "")
            if query.startswith("DROP DATABASE"):
                return CommandResult(0, "", "")
            raise AssertionError(f"unexpected psql command: {argv}")
        return super().run_command(argv, env=env, input_text=input_text, **kwargs)


def _write_all_secrets(
    path: Path,
    pg_sources: tuple[str, ...],
    pg_targets: tuple[str, ...],
    mongo_sources: tuple[str, ...],
    mongo_targets: tuple[str, ...],
) -> None:
    lines: list[str] = []
    for name, rows in (
        ("POSTGRES_SOURCES", pg_sources),
        ("POSTGRES_TARGETS", pg_targets),
        ("MONGO_SOURCES", mongo_sources),
        ("MONGO_TARGETS", mongo_targets),
    ):
        lines.append(f"typeset -a {name}=(")
        lines.extend(f'  "{row}"' for row in rows)
        lines.append(")")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _drive_all(
    tmp_path,
    argv: list[str],
    *,
    pg_sources: tuple[str, ...] = (),
    pg_targets: tuple[str, ...] = PG_TARGETS,
    mongo_sources: tuple[str, ...] = SOURCES,
    mongo_targets: tuple[str, ...] = TARGETS,
    **fake_args,
):
    path = tmp_path / "dbt_secrets"
    _write_all_secrets(path, pg_sources, pg_targets, mongo_sources, mongo_targets)
    fake = FakeAll(**fake_args)
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


def _pg_list_index(fake: FakeAll) -> int:
    return next(
        index
        for index, argv in enumerate(fake.calls)
        if argv[0] == PSQL and argv[-1].startswith("SELECT datname FROM pg_database")
    )


def _mongo_list_index(fake: FakeAll) -> int:
    return next(
        index
        for index, argv in enumerate(fake.calls)
        if argv[0] == MONGOSH and argv[-1] == MONGO_LIST_SCRIPT
    )


def test_sync_clear_lists_postgres_backups_before_mongo(tmp_path) -> None:
    code, _out, err, fake = _drive_all(tmp_path, ["sync", "-c"])

    assert code == 0
    assert err == ""
    pg_first = _pg_list_index(fake)
    mongo_first = _mongo_list_index(fake)
    assert pg_first < mongo_first
    # No Postgres source filter is applied to the listing query.
    query = fake.calls[pg_first][-1]
    assert "_backup_[0-9]{8}_[0-9]{6}" in query
    assert "left(datname" not in query


def test_sync_clear_with_clear_backups_spelling(tmp_path) -> None:
    code, _out, _err, fake = _drive_all(tmp_path, ["sync", "--clear-backups"])

    assert code == 0
    assert _mongo_list_index(fake) >= 0


def test_sync_clear_runs_mongo_after_postgres_failure(tmp_path) -> None:
    code, _out, err, fake = _drive_all(
        tmp_path, ["sync", "-c"], pg_list_fail_hosts=frozenset({"pgt.example"})
    )

    assert code == 1
    assert _mongo_list_index(fake) >= 0  # the Mongo half still ran
    assert "Failed to list" in err


def test_sync_clear_without_postgres_targets_still_cleans_mongo(tmp_path) -> None:
    code, out, err, fake = _drive_all(tmp_path, ["sync", "-c"], pg_targets=())

    assert code == 0
    assert err == ""
    assert not any(argv[0] == PSQL for argv in fake.calls)
    assert _mongo_list_index(fake) >= 0
    assert [database for _argv, database in fake.drops()] == [BACKUP_NAME, NO_DATABASE] * 2
    assert "No PostgreSQL targets configured" in out


def test_sync_clear_without_any_targets_exits_one(tmp_path) -> None:
    code, out, _err, fake = _drive_all(
        tmp_path, ["sync", "-c"], pg_targets=(), mongo_targets=()
    )

    assert code == 1
    assert fake.calls == []
    assert "No PostgreSQL or MongoDB targets configured" in out


def test_sync_clear_without_mongo_targets_returns_zero(tmp_path) -> None:
    code, _out, _err, fake = _drive_all(tmp_path, ["sync", "-c"], mongo_targets=())

    assert code == 0
    assert _pg_list_index(fake) >= 0
    assert not any(argv[0] == MONGOSH for argv in fake.calls)


def test_sync_clear_applies_dry_run_to_both_halves(tmp_path) -> None:
    code, out, err, fake = _drive_all(tmp_path, ["sync", "-c", "-n"])

    assert code == 0
    assert err == ""
    assert out.count("Dry run only; no databases were dropped") == 3  # 1 Postgres + 2 Mongo targets
    assert fake.drops() == []
    assert not any("DROP DATABASE" in " ".join(argv) for argv in fake.calls)


# --- a URI path must not name another database ----------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("mongodb://user:secret@cluster.example/admin?authSource=x", "admin"),
        ("mongodb+srv://user:p@ss@cluster.example/app", "app"),
        ("mongodb://cluster.example:27017/", ""),
        ("mongodb://cluster.example:27017", ""),
        ("mongodb://cluster.example/?retryWrites=true", ""),
    ],
)
def test_mongo_uri_database_reads_the_path(uri: str, expected: str) -> None:
    assert mongo_uri_database(uri) == expected


@pytest.mark.parametrize("side", ["source", "target"])
def test_sync_rejects_a_uri_naming_another_database(side: str) -> None:
    fake = FakeMongo()
    runtime, signals = _sync_runtime(fake)
    named = "mongodb://user:secret@cluster.example/admin?retryWrites=true"
    source = named if side == "source" else SOURCE_URI
    target = named if side == "target" else TARGET_URI
    out, err = io.StringIO(), io.StringIO()

    code = perform_mongo_sync(
        source, "app", target, verbose=False, runtime=runtime, stdout=out, stderr=err
    )

    assert code == 1
    assert fake.calls == [] and fake.pipes == []
    assert f"{side} URI names database 'admin'" in err.getvalue()
    assert "?authSource=admin" in err.getvalue()
    assert signals.bound == []


def test_sync_accepts_a_uri_naming_the_sync_database() -> None:
    fake = FakeMongo()
    runtime, _signals = _sync_runtime(fake)
    source = "mongodb://user:secret@cluster.example/app?retryWrites=true"

    code = perform_mongo_sync(
        source, "app", TARGET_URI, verbose=False,
        runtime=runtime, stdout=io.StringIO(), stderr=io.StringIO(),
    )

    assert code == 0
    assert len(fake.pipes) == 2
