from pathlib import Path

from shared.process import CommandResult

from git_tools.git import run_git


def test_run_git_prepends_executable_and_forwards_process_options() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> CommandResult:
        calls.append((argv, kwargs))
        return CommandResult(7, "out\n", "err\n")

    env = {"PATH": "/fake/bin"}
    result = run_git(["status", "--short"], cwd=Path("/repo"), env=env, runner=runner)

    assert result == CommandResult(7, "out\n", "err\n")
    assert calls == [
        (
            ["git", "status", "--short"],
            {"cwd": Path("/repo"), "env": env, "capture": True},
        )
    ]


def test_run_git_never_interpolates_shell_syntax() -> None:
    received: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: object) -> CommandResult:
        received.append(argv)
        return CommandResult(0)

    run_git(["show", "$(touch /tmp/should-not-exist)"], cwd="/repo", runner=runner)

    assert received == [["git", "show", "$(touch /tmp/should-not-exist)"]]
