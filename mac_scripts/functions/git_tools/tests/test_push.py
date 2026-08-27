from __future__ import annotations

import io
import re

from git_tools.push import run_push_command
from git_tools.ui import GitToolsUi
from shared.process import CommandResult


class PushUi:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.error_details: list[str] = []
        self.events: list[tuple[str, str, str]] = []
        self.frames: list[str] = []

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


def _result(status: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(status, stdout, stderr)


def test_push_rejects_non_repository_without_starting_frame() -> None:
    calls: list[list[str]] = []

    def git(args: list[str], **_kwargs: object) -> CommandResult:
        calls.append(args)
        return _result(128, stderr="not a git repository")

    ui = PushUi()

    assert run_push_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 1
    assert ui.errors == ["Error: Not in a git repository"]
    assert ui.frames == []
    assert calls == [["rev-parse", "--git-dir"]]


def test_push_rejects_detached_head() -> None:
    calls: list[list[str]] = []

    def git(args: list[str], **_kwargs: object) -> CommandResult:
        calls.append(args)
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return _result(stdout="HEAD\n")
        return _result()

    ui = PushUi()

    assert run_push_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 1
    assert ui.errors == ["Cannot push from detached HEAD"]
    assert ui.frames == []
    assert calls == [
        ["rev-parse", "--git-dir"],
        ["rev-parse", "--abbrev-ref", "HEAD"],
    ]


def test_push_prunes_stale_tracking_ref_force_pushes_and_fetches_in_order() -> None:
    calls: list[list[str]] = []
    responses = {
        ("rev-parse", "--abbrev-ref", "HEAD"): _result(stdout="feature\n"),
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): _result(
            stdout="upstream/feature\n"
        ),
        ("show-ref", "--verify", "--quiet", "refs/remotes/upstream/feature"): _result(),
        ("ls-remote", "--exit-code", "--heads", "upstream", "feature"): _result(2),
        ("fetch", "upstream", "--prune"): _result(),
        ("push", "--force-with-lease", "upstream", "HEAD:feature"): _result(
            stderr="To origin\n * [new branch] HEAD -> feature\n"
        ),
    }

    def git(args: list[str], **_kwargs: object) -> CommandResult:
        calls.append(args)
        return responses.get(tuple(args), _result())

    ui = PushUi()

    assert run_push_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 0
    assert ("detail", "To origin\n * [new branch] HEAD -> feature", "muted") in ui.events
    assert ui.frames == ["begin:Force push (safe)", "end"]
    assert calls == [
        ["rev-parse", "--git-dir"],
        ["rev-parse", "--abbrev-ref", "HEAD"],
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        ["show-ref", "--verify", "--quiet", "refs/remotes/upstream/feature"],
        ["ls-remote", "--exit-code", "--heads", "upstream", "feature"],
        ["fetch", "upstream", "--prune"],
        ["push", "--force-with-lease", "upstream", "HEAD:feature"],
        ["fetch"],
    ]


def test_push_renders_each_multiline_success_stream_line_as_framed_detail() -> None:
    stdout = io.StringIO()
    ui = GitToolsUi(stdout, io.StringIO())

    def git(args: list[str], **_kwargs: object) -> CommandResult:
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return _result(stdout="feature\n")
        if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return _result(1)
        if args == ["show-ref", "--verify", "--quiet", "refs/remotes/origin/feature"]:
            return _result(1)
        if args == ["push", "--force-with-lease", "origin", "HEAD:feature"]:
            return _result(
                stdout="remote: preparing\nremote: updating refs\n",
                stderr="To github.com:org/app.git\n   abc..def  feature -> feature\n",
            )
        return _result()

    assert run_push_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 0
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", stdout.getvalue())
    assert (
        "│      remote: preparing\n│      remote: updating refs\n"
        "│      To github.com:org/app.git\n│         abc..def  feature -> feature"
    ) in plain


def test_push_failure_reports_details_and_remotes_permission_tip() -> None:
    def git(args: list[str], **_kwargs: object) -> CommandResult:
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return _result(stdout="feature\n")
        if args[0] == "push":
            return _result(1, stderr="permission denied")
        return _result()

    ui = PushUi()

    assert run_push_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 1
    assert ui.errors == ["Force push failed"]
    assert ui.error_details == [
        "permission denied",
        "Tip: run 'git remote -v' and verify write permission to the remote",
    ]
    assert ("status", "No upstream tracking branch configured", "warning") in ui.events
    assert ui.frames == ["begin:Force push (safe)"]
