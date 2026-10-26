from __future__ import annotations

import io

import pytest

from db_tools import cli
from db_tools.cli import run_cli
from db_tools.runtime import Interrupted


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
