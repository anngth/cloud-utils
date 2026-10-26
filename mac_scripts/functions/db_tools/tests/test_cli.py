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


def test_help_lists_every_command() -> None:
    _code, out, _err = run(["help"])
    names = ("clean", "completion", "config", "connect", "disconnect", "list", "status", "sync")
    nested = ("sync list", "sync postgres", "sync mongodb")  # the group's commands, one level in
    for name in names + nested:
        assert f"\n  {name} " in out
    assert "\n  test " not in out


@pytest.mark.parametrize(
    "argv",
    [["sync", "--help"], ["sync", "postgres", "-h"], ["sync", "mongo", "--help"], ["status", "-h"]],
)
def test_every_subcommand_has_its_own_help(argv: list[str]) -> None:
    code, out, err = run(argv)  # no secrets file is needed for help
    assert (code, err) == (0, "")
    assert out.startswith("Usage: dbt ")
    assert "-h, --help" in out


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
    assert "🔀 Tunnels (1):\n  1) a: postgres | :5432 -> db.internal:5432  me@10.0.0.1\n" in out
    assert "🐘 PostgreSQL Sources (1):\n  1) prod → app | postgresql://***@h:5432/app\n" in out
    assert (
        "🎯 PostgreSQL Targets (1):\n  1) one | postgresql://***@t.example:5432/postgres\n" in out
    )
    assert "🍃 MongoDB Sources (1):\n  1) m → app,logs | mongodb://***@h:27017\n" in out
    assert "🎯 MongoDB Targets (0):\n  (none)\n" in out
    assert "s3cr3t" not in out
    assert "pw@" not in out


def test_config_path_prints_only_the_location(tmp_path) -> None:
    code, out, err = _config(tmp_path, ["path"])
    assert (code, out, err) == (0, f"{tmp_path / 'secrets.toml'}\n", "")


def test_config_rejects_other_arguments(tmp_path) -> None:
    code, out, err = _config(tmp_path, ["frob"])
    assert (code, out) == (1, "")
    assert "'frob'" in err
    assert "Usage: dbt config" in err


def test_config_edit_opens_the_file_in_the_editor(tmp_path, monkeypatch) -> None:
    opened: list[object] = []
    monkeypatch.setattr(cli, "_edit", opened.append)
    code, out, err = _config(tmp_path, ["edit"])
    assert (code, out, err) == (0, "", "")
    assert opened == [tmp_path / "secrets.toml"]


def test_completion_prints_the_zsh_script() -> None:
    code, out, err = run(["completion", "zsh"])
    assert (code, err) == (0, "")
    assert "_DBT_COMPLETE=zsh_complete dbt" in out
    assert "compdef" in out
