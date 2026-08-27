from __future__ import annotations

from dataclasses import dataclass, field
import io

import pytest

from git_tools.cli import run_cli


@dataclass
class Dependencies:
    push_calls: list[list[str]] = field(default_factory=list)
    fetch_calls: list[list[str]] = field(default_factory=list)
    backup_calls: list[list[str]] = field(default_factory=list)
    push_code: int = 17
    fetch_code: int = 23
    backup_code: int = 29

    def run_push_command(self, args, **_kwargs) -> int:
        self.push_calls.append(list(args))
        return self.push_code

    def run_fetch_command(self, args, **_kwargs) -> int:
        self.fetch_calls.append(list(args))
        return self.fetch_code

    def run_backup_command(self, args, **_kwargs) -> int:
        self.backup_calls.append(list(args))
        return self.backup_code


def run(argv: list[str], deps: Dependencies | None = None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_cli(
        argv,
        cwd="/repo",
        env={"HOME": "/Users/me"},
        stdout=stdout,
        stderr=stderr,
        dependencies=deps,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_routes_push_with_unchanged_argv_slice() -> None:
    deps = Dependencies()
    code, stdout, stderr = run(["push"], deps)
    assert code == 17
    assert deps.push_calls == [[]]
    assert deps.fetch_calls == []
    assert deps.backup_calls == []
    assert stdout == stderr == ""


def test_routes_fetch_with_flags_and_unchanged_argv_slice() -> None:
    deps = Dependencies()
    code, _, _ = run(["fetch", "--sync-upstream"], deps)
    assert code == 23
    assert deps.fetch_calls == [["--sync-upstream"]]


def test_routes_backup_with_unchanged_argv_slice() -> None:
    deps = Dependencies()
    code, _, _ = run(["backup", "git@github.com:o/r.git"], deps)
    assert code == 29
    assert deps.backup_calls == [["git@github.com:o/r.git"]]


@pytest.mark.parametrize("alias", ["help", "-h", "--help"])
def test_help_aliases_exit_zero(alias: str) -> None:
    code, stdout, stderr = run([alias], Dependencies())
    assert code == 0
    assert "Usage: gt <command>" in stdout
    assert stderr == ""


def test_missing_command_prints_usage_and_exits_one() -> None:
    code, stdout, stderr = run([], Dependencies())
    assert code == 1
    assert "Usage: gt <command>" in stdout
    assert stderr == ""


def test_help_mentions_managed_backup_list_commands() -> None:
    code, stdout, stderr = run(["--help"], Dependencies())
    assert code == 0
    assert stderr == ""
    for text in (
        "backup",
        "backup --all",
        "backup stale",
        "backup add <ssh-url>",
        "backup remove <index|ssh-url>",
    ):
        assert text in stdout
    assert "-n" not in stdout
    assert "--new" not in stdout
    assert "backup <ssh-url>" not in stdout


def test_unknown_command_exits_one_and_renders_usage() -> None:
    code, stdout, stderr = run(["nope"], Dependencies())
    assert code == 1
    assert "Unknown command: nope" in stderr
    assert "Usage: gt <command>" in stdout
