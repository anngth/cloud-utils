"""Tests for Mongo host parsing, client discovery, status, and cleanup."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

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
    parse_mongo_source_row,
    parse_mongo_target_row,
)
from db_tools.runtime import Runtime
from shared.process import CommandResult

MONGOSH = "/usr/local/bin/mongosh"
MONGO = "/usr/local/bin/mongo"
MONGODUMP = "/usr/local/bin/mongodump"
MONGORESTORE = "/usr/local/bin/mongorestore"

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

SOURCES = ("prod|mongodb://user:secret@cluster.example/app?retryWrites=true|app,logs",)
TARGETS = (
    "one|mongodb://user:secret@127.0.0.1:27017",
    "two|mongodb://user:secret@t2.example:27017",
)


@dataclass
class FakeMongo:
    """Records argv/env pairs and answers mongosh calls without spawning."""

    which: dict[str, str] = field(default_factory=lambda: dict(FULL_WHICH))
    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str] | None] = field(default_factory=list)
    list_codes: dict[str, int] = field(default_factory=dict)  # host -> returncode
    drop_codes: dict[str, int] = field(default_factory=dict)  # database -> returncode
    ping_code: int = 0
    list_stdout: str = ALL_BACKUPS

    def run_command(self, argv, *, env=None, input_text=None, **_kwargs):
        self.calls.append(list(argv))
        self.envs.append(dict(env) if env is not None else None)
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
            return CommandResult(self.ping_code, "1", "")
        raise AssertionError(f"unexpected command: {argv}")

    def drops(self) -> list[tuple[list[str], str]]:
        pairs = []
        for argv, env in zip(self.calls, self.envs, strict=True):
            if argv[4] == MONGO_DROP_SCRIPT:
                pairs.append((argv, (env or {}).get("DBT_MONGO_DB", "")))
        return pairs


def _runtime(fake: FakeMongo) -> Runtime:
    return Runtime(
        run_command=fake.run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: fake.which.get(name),
        path_exists=lambda _path: False,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=datetime.now,
        signals=SignalHooksStub(),
    )


class SignalHooksStub:
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
        dependencies=_runtime(fake),
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


def test_find_mongo_tools_falls_back_to_legacy_mongo() -> None:
    fake = FakeMongo(which={"mongo": MONGO, "mongodump": MONGODUMP, "mongorestore": MONGORESTORE})
    tools = find_mongo_tools(_runtime(fake))
    assert tools is not None
    assert tools.client == MONGO


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


def test_mongo_status_missing_client_counts_as_connected() -> None:
    fake = FakeMongo(which={"mongodump": MONGODUMP, "mongorestore": MONGORESTORE})
    assert mongo_status("mongodb://u:p@h:27017", runtime=_runtime(fake)) is True
    assert fake.calls == []


def test_mongo_status_uses_legacy_mongo_when_mongosh_is_absent() -> None:
    fake = FakeMongo(which={"mongo": MONGO})
    assert mongo_status("mongodb://u:p@h:27017", runtime=_runtime(fake)) is True
    assert fake.calls[0][0] == MONGO


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
    uris = ("mongodb://user:secret@cluster.example/app?retryWrites=true",
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
    code, out, _err, fake = _drive(tmp_path, ["sync", "list", "--status"], ping_code=1)

    assert code == 0
    assert "🔴 1) prod → app,logs | cluster.example" in out
    assert "🟢 1)" not in out
    assert "Summary: 🟢 0 connected  |  🔴 3 disconnected" in out


def test_sync_list_status_with_no_mongo_client_reports_connected(tmp_path) -> None:
    which = {"mongodump": MONGODUMP, "mongorestore": MONGORESTORE}
    code, out, err, fake = _drive(tmp_path, ["sync", "list", "--status"], which=which)

    assert code == 0
    assert err == ""
    assert fake.calls == []
    assert "🟢 1) prod → app,logs | cluster.example" in out
    assert "Summary: 🟢 3 connected  |  🔴 0 disconnected" in out


def test_sync_list_skips_invalid_mongo_rows_with_a_warning(tmp_path) -> None:
    code, out, err, _fake = _drive(tmp_path, ["sync", "list"], sources=("bad",))

    assert code == 0
    assert "bad" in err
    assert "🍃 MongoDB Sources (0):" in out
    assert "  (No MongoDB sources configured)" in out
