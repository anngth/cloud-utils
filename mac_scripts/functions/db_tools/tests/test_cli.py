from __future__ import annotations

import io

import pytest

from db_tools.cli import run_cli


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
