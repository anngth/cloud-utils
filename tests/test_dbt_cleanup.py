"""dbt must treat secrets and backup names as data when it drops databases."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from db_tools.cli import run_cli
from db_tools.mongo import MONGO_DROP_SCRIPT, mongo_eval_argv
from db_tools.postgres import parse_postgres_url
from db_tools.runtime import Runtime, SignalHooks
from db_tools.secrets import parse_secrets_text
from db_tools.tests.secrets_toml import write_secrets
from shared.process import CommandResult

BACKUP_NAME = 'weird"name_backup_20260101_120000'
DROP_SQL = 'DROP DATABASE IF EXISTS "weird""name_backup_20260101_120000";'

SOURCES = ("prod|postgresql://svc:secret@src.example:5432/app|app",)
TARGETS = ("one|postgresql://svc:secret@t1.example:5432/app?sslmode=require",)


@dataclass
class FakePostgres:
    """Records argv/env pairs and answers psql queries without spawning."""

    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str] | None] = field(default_factory=list)

    def run_command(self, argv, *, env=None, **_kwargs):
        self.calls.append(list(argv))
        self.envs.append(dict(env) if env is not None else None)
        query = argv[-1]
        if query.startswith("SELECT datname FROM pg_database"):
            return CommandResult(0, BACKUP_NAME + "\n", "")
        if query.startswith("SELECT pg_terminate_backend"):
            return CommandResult(0, "", "")
        if query.startswith("DROP DATABASE"):
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected command: {argv}")


class _StubSignals(SignalHooks):
    def bind(self, handler) -> None:
        self.handler = handler  # tests must not touch real signal handlers


def _runtime(fake: FakePostgres) -> Runtime:
    return Runtime(
        run_command=fake.run_command,
        run_pipe=lambda *_args, **_kwargs: CommandResult(0, "", ""),
        which=lambda name: {
            "psql": "/usr/bin/psql",
            "pg_dump": "/usr/bin/pg_dump",
            "pg_restore": "/usr/bin/pg_restore",
        }.get(name),
        path_exists=lambda _path: False,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=datetime.now,
        signals=_StubSignals(),
    )


def _drive(tmp_path: Path, argv: list[str]) -> tuple[int, str, str, FakePostgres]:
    path = write_secrets(
        tmp_path / "secrets.toml", postgres_sources=SOURCES, postgres_targets=TARGETS
    )
    fake = FakePostgres()
    out = io.StringIO()
    err = io.StringIO()
    code = run_cli(
        argv,
        env={"DBT_SECRETS_FILE": str(path)},
        stdout=out,
        stderr=err,
        dependencies=_runtime(fake),
    )
    return code, out.getvalue(), err.getvalue(), fake


# --- secrets and shell metacharacters ------------------------------------------


def test_postgres_url_password_is_data_not_shell(tmp_path: Path) -> None:
    sentinel = tmp_path / "pwned"
    password = f"x'; touch {sentinel}; echo 'x"
    url = f"postgresql://user:{password}@127.0.0.1:5432/app"

    parsed = parse_postgres_url(url)

    assert not sentinel.exists()
    assert parsed.password == password


def test_vps_user_is_data_not_shell(tmp_path: Path) -> None:
    sentinel = tmp_path / "pwned"
    user = f"admin'; touch {sentinel}; echo '"
    text = (
        '[[tunnels]]\nname = "vps"\nuser = "' + user + '"\nhost = "10.0.0.1"\n'
        'service = "postgres"\nlocal_port = 5432\ntarget_host = "127.0.0.1"\ntarget_port = 5432\n'
    )

    secrets = parse_secrets_text(text)

    assert not sentinel.exists()
    (tunnel,) = secrets.tunnels
    assert tunnel.user == user


def test_mongo_keeps_database_name_out_of_the_javascript() -> None:
    name = "a'); db.getSiblingDB('admin').dropDatabase(); //"

    argv, env = mongo_eval_argv(
        "mongosh", "mongodb://127.0.0.1:27017", name, MONGO_DROP_SCRIPT
    )

    assert env == {"DBT_MONGO_DB": name}
    script = argv[-1]
    assert script == MONGO_DROP_SCRIPT
    assert "process.env.DBT_MONGO_DB" in script
    assert "admin" not in script
    assert name not in " ".join(argv[:-1])


# --- backup cleanup through run_cli ---------------------------------------------


def test_invalid_postgres_index_returns_one_without_psql(tmp_path: Path) -> None:
    code, _out, err, fake = _drive(tmp_path, ["sync", "postgres", "-s", "2", "-d", "1"])

    assert code == 1
    assert fake.calls == []
    assert "Unknown PostgreSQL source: 2" in err


def test_dry_run_lists_postgres_backups_without_dropping_them(tmp_path: Path) -> None:
    code, out, err, fake = _drive(tmp_path, ["clean", "postgres", "-n"])

    assert code == 0
    assert err == ""
    sql = " ".join(" ".join(call) for call in fake.calls)
    assert "SELECT datname" in sql
    assert "DROP DATABASE" not in sql
    assert BACKUP_NAME in out
    assert fake.envs[0]["PGPASSWORD"] == "secret"


def test_postgres_cleanup_quotes_backup_names_in_drop_statements(
    tmp_path: Path,
) -> None:
    code, _out, _err, fake = _drive(tmp_path, ["clean", "postgres"])

    assert code == 0
    assert DROP_SQL in [call[-1] for call in fake.calls]
    terminates = [
        call
        for call in fake.calls
        if call[-1].startswith("SELECT pg_terminate_backend")
        and "weird\"name" in call[-1]
    ]
    assert terminates  # the connection is closed before the drop
