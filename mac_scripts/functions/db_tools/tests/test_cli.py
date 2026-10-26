from __future__ import annotations

import io

import pytest

from db_tools import cli
from db_tools.cli import run_cli
from db_tools.runtime import Interrupted
from db_tools.tests.secrets_toml import write_secrets


def run(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    code = run_cli(argv, env={"HOME": "/Users/me"}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("argv", [[], ["help"], ["-h"], ["--help"]])
def test_help_exits_zero(argv: list[str]) -> None:
    code, out, err = run(argv)
    assert code == 0
    assert "dbt" in out
    assert err == ""


def test_help_lists_the_config_command() -> None:
    _code, out, _err = run(["help"])
    assert "config [path]" in out


def test_unknown_command_exits_one() -> None:
    code, _out, err = run(["frob"])
    assert code == 1
    assert err != ""


@pytest.mark.parametrize("raised", [KeyboardInterrupt, Interrupted])
def test_main_turns_an_unhandled_interrupt_into_exit_130(monkeypatch, raised, capsys) -> None:
    def interrupted(*_args, **_kwargs):
        raise raised()

    monkeypatch.setattr(cli, "run_cli", interrupted)
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 130
    assert "Traceback" not in capsys.readouterr().err


# --- dbt config ---------------------------------------------------------------------


def _config(tmp_path, argv: list[str]) -> tuple[int, str, str]:
    path = write_secrets(
        tmp_path / "secrets.toml",
        tunnels=("a|me|10.0.0.1|postgres|5432|db.internal|5432",),
        postgres_sources=("prod|postgresql://svc:s3cr3t@h:5432/app",),
        postgres_targets=("one|postgresql://svc:s3cr3t@t.example:5432/postgres",),
        mongo_sources=("m|mongodb://u:pw@h:27017|app,logs",),
    )
    out, err = io.StringIO(), io.StringIO()
    code = run_cli(["config", *argv], env={"DBT_SECRETS_FILE": str(path)}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_config_shows_every_section_with_credentials_masked(tmp_path) -> None:
    code, out, err = _config(tmp_path, [])

    assert code == 0
    assert err == ""
    assert out.startswith(f"Secrets file: {tmp_path / 'secrets.toml'}\n")
    assert "🔀 Tunnels (1):\n  1) a: me@10.0.0.1  postgres  :5432 -> db.internal:5432\n" in out
    assert "🐘 PostgreSQL sources (1):\n  1) prod → app  postgresql://***@h:5432/app\n" in out
    assert (
        "🎯 PostgreSQL targets (1):\n  1) one  postgresql://***@t.example:5432/postgres\n" in out
    )
    assert "🍃 MongoDB sources (1):\n  1) m → app,logs  mongodb://***@h:27017\n" in out
    assert "🎯 MongoDB targets (0):\n  (none)\n" in out
    assert "s3cr3t" not in out
    assert "pw@" not in out


def test_config_path_prints_only_the_location(tmp_path) -> None:
    code, out, err = _config(tmp_path, ["path"])
    assert (code, out, err) == (0, f"{tmp_path / 'secrets.toml'}\n", "")


def test_config_rejects_other_arguments(tmp_path) -> None:
    code, out, err = _config(tmp_path, ["edit"])
    assert (code, out) == (1, "")
    assert "unknown option: edit" in err
    assert "usage: dbt config [path]" in err
