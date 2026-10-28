from __future__ import annotations

import io

import pytest
from shared.process import CommandResult

from git_tools import push
from git_tools.push import run_push_command


class PushUi:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.error_details: list[str] = []
        self.events: list[tuple[str, str, str]] = []
        self.frames: list[str] = []
        self.lines: list[str] = []

    def begin(self, message: str) -> None:
        self.frames.append(f"begin:{message}")

    def end(self) -> None:
        self.frames.append("end")

    def error(self, message: str) -> None:
        self.errors.append(message)

    def error_detail(self, message: str) -> None:
        self.error_details.append(message)

    def status(self, message: str, *, tone: str = "success") -> None:
        self.events.append(("status", message, tone))

    def detail(self, message: str, *, tone: str = "muted") -> None:
        self.events.append(("detail", message, tone))

    def usage_line(self, message: str) -> None:
        self.errors.append(message)

    def line(self, message: str = "") -> None:
        self.lines.append(message)


def _result(status: int = 0, stdout: str = "") -> CommandResult:
    return CommandResult(status, stdout, "")


def _git(branch: str = "feature", overrides: dict[tuple[str, ...], CommandResult] | None = None):
    calls: list[list[str]] = []
    responses = {
        ("rev-parse", "--git-dir"): _result(stdout=".git\n"),
        ("rev-parse", "--abbrev-ref", "HEAD"): _result(stdout=f"{branch}\n"),
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): _result(stdout=f"origin/{branch}\n"),
        ("show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"): _result(1),
        **(overrides or {}),
    }

    def run_git(args, *, cwd, env):
        calls.append(list(args))
        return responses.get(tuple(args), _result())

    return run_git, calls


def _push_call(branch: str, remote: str = "origin") -> list[str]:
    return ["push", "--force-with-lease", "--force-if-includes", remote, f"HEAD:{branch}"]


def _never_asked(_question: str) -> bool:
    raise AssertionError("asked for confirmation")


def _run(args, run_git, *, confirm=_never_asked):
    ui = PushUi()
    status = run_push_command(args, cwd=".", env=None, ui=ui, run_git_fn=run_git, confirm=confirm)
    return status, ui


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_prints_usage_without_git_calls(flag: str) -> None:
    run_git, calls = _git()
    status, ui = _run([flag], run_git)
    assert status == 0
    assert calls == []
    assert ui.lines[0] == "Usage: gt push [-y | --yes]"


def test_unknown_option_is_rejected_before_git_calls() -> None:
    run_git, calls = _git()
    status, ui = _run(["--force"], run_git)
    assert status == 1
    assert calls == []
    assert ui.errors == ["Unknown option for push: --force", "Usage: gt push [-y | --yes]"]


def test_feature_branch_pushes_with_force_if_includes_without_asking() -> None:
    run_git, calls = _git()
    status, ui = _run([], run_git)
    assert status == 0
    assert _push_call("feature") in calls
    assert calls[-1] == ["fetch"]
    assert ui.frames == ["begin:Force push (safe)", "end"]


@pytest.mark.parametrize("branch", ["main", "develop"])
def test_protected_branch_declined_does_not_push(branch: str) -> None:
    run_git, calls = _git(branch)
    questions: list[str] = []

    def decline(question: str) -> bool:
        questions.append(question)
        return False

    status, ui = _run([], run_git, confirm=decline)
    assert status == 1
    assert questions == [f"Force push to origin/{branch}?"]
    assert not any(call[0] == "push" for call in calls)
    assert ui.events[-1][2] == "warning"
    assert ui.frames[-1] == "end"


def test_protected_branch_confirmed_pushes() -> None:
    run_git, calls = _git("main")
    status, _ui = _run([], run_git, confirm=lambda _q: True)
    assert status == 0
    assert _push_call("main") in calls


@pytest.mark.parametrize("flag", ["-y", "--yes"])
def test_yes_skips_the_question_on_protected_branch(flag: str) -> None:
    run_git, calls = _git("main")
    status, _ui = _run([flag], run_git)
    assert status == 0
    assert _push_call("main") in calls


def test_not_a_repository_fails() -> None:
    run_git, calls = _git(overrides={("rev-parse", "--git-dir"): _result(128)})
    status, ui = _run([], run_git)
    assert status == 1
    assert ui.errors == ["Error: Not in a git repository"]
    assert len(calls) == 1


def test_detached_head_fails_without_pushing() -> None:
    run_git, calls = _git("HEAD")
    status, ui = _run([], run_git)
    assert status == 1
    assert ui.errors == ["Cannot push from detached HEAD"]
    assert not any(call[0] == "push" for call in calls)


def test_without_upstream_pushes_to_origin() -> None:
    run_git, calls = _git(
        overrides={("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): _result(128)}
    )
    status, ui = _run([], run_git)
    assert status == 0
    assert _push_call("feature") in calls
    assert ("status", "No upstream tracking branch configured", "warning") in ui.events


def test_tracking_branch_on_other_remote_and_name_pushes_to_current_name() -> None:
    run_git, calls = _git(
        overrides={
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): _result(stdout="fork/other\n"),
        }
    )
    status, ui = _run([], run_git)
    assert status == 0
    assert _push_call("feature", "fork") in calls
    assert ("detail", "Will push explicitly to fork/feature", "muted") in ui.events


def test_stale_tracking_ref_is_pruned_before_push() -> None:
    run_git, calls = _git(
        overrides={
            ("show-ref", "--verify", "--quiet", "refs/remotes/origin/feature"): _result(),
            ("ls-remote", "--exit-code", "--heads", "origin", "feature"): _result(2),
        }
    )
    status, _ui = _run([], run_git)
    assert status == 0
    assert calls.index(["fetch", "origin", "--prune"]) < calls.index(_push_call("feature"))


def test_push_failure_reports_output_and_skips_fetch() -> None:
    run_git, calls = _git(overrides={tuple(_push_call("feature")): CommandResult(1, "rejected", "stale info")})
    status, ui = _run([], run_git)
    assert status == 1
    assert ui.errors == ["Force push failed"]
    assert ui.error_details[:2] == ["rejected", "stale info"]
    assert calls[-1] == _push_call("feature")


def test_ask_without_terminal_is_no(monkeypatch) -> None:
    monkeypatch.setattr(push.sys, "stdin", io.StringIO("y\n"))
    assert push._ask("Force push?") is False


@pytest.mark.parametrize(("answer", "expected"), [("y\n", True), ("YES\n", True), ("\n", False), ("n\n", False)])
def test_ask_on_terminal_reads_the_answer(monkeypatch, answer: str, expected: bool) -> None:
    stdin = io.StringIO(answer)
    stdin.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(push.sys, "stdin", stdin)
    monkeypatch.setattr("builtins.input", lambda _prompt: stdin.readline())
    assert push._ask("Force push?") is expected


def test_ask_treats_eof_as_no(monkeypatch) -> None:
    stdin = io.StringIO("")
    stdin.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(push.sys, "stdin", stdin)

    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert push._ask("Force push?") is False
