from __future__ import annotations

import io
import re

import pytest

from git_tools.fetch import _sync_local_branch_with_origin_ff, run_fetch_command
from git_tools.ui import GitToolsUi
from shared.process import CommandResult


class FetchUi:
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


def _result(status: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(status, stdout, stderr)


def _git(
    overrides: dict[tuple[str, ...], CommandResult] | None = None,
) -> tuple[object, list[list[str]]]:
    calls: list[list[str]] = []
    responses = {
        ("rev-parse", "--git-dir"): _result(stdout=".git\n"),
        ("ls-remote", "--heads", "origin", "main"): _result(),
        ("ls-remote", "--heads", "origin", "develop"): _result(),
        ("fetch", "--all", "--prune"): _result(),
        ("rev-parse", "--abbrev-ref", "HEAD"): _result(stdout="feature\n"),
        ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"): _result(1),
        ("show-ref", "--verify", "--quiet", "refs/remotes/origin/develop"): _result(1),
        ("show-ref", "--verify", "--quiet", "refs/heads/main"): _result(1),
        ("show-ref", "--verify", "--quiet", "refs/heads/develop"): _result(1),
        ("branch", "-vv"): _result(),
        ("for-each-ref", "--format=%(refname:short)", "refs/heads"): _result(),
    }
    responses.update(overrides or {})

    def run(args: list[str], **_kwargs: object) -> CommandResult:
        calls.append(args)
        return responses.get(tuple(args), _result())

    return run, calls


def _primary_sync_git(
    overrides: dict[tuple[str, ...], CommandResult],
) -> tuple[object, list[list[str]]]:
    calls: list[list[str]] = []

    def run(args: list[str]) -> CommandResult:
        calls.append(args)
        return overrides[tuple(args)]

    return run, calls


def test_primary_sync_creates_missing_local_branch_in_exact_order() -> None:
    git, calls = _primary_sync_git(
        {
            ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"): _result(),
            ("show-ref", "--verify", "--quiet", "refs/heads/main"): _result(1),
            ("branch", "main", "origin/main"): _result(),
        }
    )
    ui = FetchUi()

    _sync_local_branch_with_origin_ff(git, ui, "main", "feature")

    assert calls == [
        ["show-ref", "--verify", "--quiet", "refs/remotes/origin/main"],
        ["show-ref", "--verify", "--quiet", "refs/heads/main"],
        ["branch", "main", "origin/main"],
    ]
    assert ui.events == [
        ("status", "Creating local 'main' from origin/main", "success"),
        ("status", "Created local 'main'", "success"),
    ]


def test_primary_sync_fast_forwards_local_branch_in_exact_order() -> None:
    git, calls = _primary_sync_git(
        {
            ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"): _result(),
            ("show-ref", "--verify", "--quiet", "refs/heads/main"): _result(),
            ("rev-parse", "refs/heads/main"): _result(stdout="local\n"),
            ("rev-parse", "refs/remotes/origin/main"): _result(stdout="remote\n"),
            ("merge-base", "--is-ancestor", "main", "origin/main"): _result(),
            ("update-ref", "refs/heads/main", "remote", "local"): _result(),
        }
    )
    ui = FetchUi()

    _sync_local_branch_with_origin_ff(git, ui, "main", "feature")

    assert calls == [
        ["show-ref", "--verify", "--quiet", "refs/remotes/origin/main"],
        ["show-ref", "--verify", "--quiet", "refs/heads/main"],
        ["rev-parse", "refs/heads/main"],
        ["rev-parse", "refs/remotes/origin/main"],
        ["merge-base", "--is-ancestor", "main", "origin/main"],
        ["update-ref", "refs/heads/main", "remote", "local"],
    ]
    assert ui.events[-1] == ("status", "Updated local 'main' to origin/main", "success")


def test_primary_sync_skips_diverged_local_branch_without_update() -> None:
    git, calls = _primary_sync_git(
        {
            ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"): _result(),
            ("show-ref", "--verify", "--quiet", "refs/heads/main"): _result(),
            ("rev-parse", "refs/heads/main"): _result(stdout="local\n"),
            ("rev-parse", "refs/remotes/origin/main"): _result(stdout="remote\n"),
            ("merge-base", "--is-ancestor", "main", "origin/main"): _result(1),
        }
    )
    ui = FetchUi()

    _sync_local_branch_with_origin_ff(git, ui, "main", "feature")

    assert calls == [
        ["show-ref", "--verify", "--quiet", "refs/remotes/origin/main"],
        ["show-ref", "--verify", "--quiet", "refs/heads/main"],
        ["rev-parse", "refs/heads/main"],
        ["rev-parse", "refs/remotes/origin/main"],
        ["merge-base", "--is-ancestor", "main", "origin/main"],
    ]
    assert ui.events == [
        ("status", "Local 'main' has local-only commits; skipping auto-sync", "warning"),
        ("detail", "Run: git switch main && git pull --ff-only origin main", "muted"),
    ]


def test_primary_sync_warns_when_fast_forward_update_fails() -> None:
    git, calls = _primary_sync_git(
        {
            ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"): _result(),
            ("show-ref", "--verify", "--quiet", "refs/heads/main"): _result(),
            ("rev-parse", "refs/heads/main"): _result(stdout="local\n"),
            ("rev-parse", "refs/remotes/origin/main"): _result(stdout="remote\n"),
            ("merge-base", "--is-ancestor", "main", "origin/main"): _result(),
            ("update-ref", "refs/heads/main", "remote", "local"): _result(1),
        }
    )
    ui = FetchUi()

    _sync_local_branch_with_origin_ff(git, ui, "main", "feature")

    assert calls == [
        ["show-ref", "--verify", "--quiet", "refs/remotes/origin/main"],
        ["show-ref", "--verify", "--quiet", "refs/heads/main"],
        ["rev-parse", "refs/heads/main"],
        ["rev-parse", "refs/remotes/origin/main"],
        ["merge-base", "--is-ancestor", "main", "origin/main"],
        ["update-ref", "refs/heads/main", "remote", "local"],
    ]
    assert ui.events[-1] == ("status", "Failed to update local 'main'", "warning")


def test_fetch_rejects_unknown_option_before_git_calls() -> None:
    git, calls = _git()
    ui = FetchUi()

    assert run_fetch_command(["--bogus"], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 1
    assert ui.errors == ["Unknown option for fetch: --bogus", "Usage: gt fetch [--sync-upstream]"]
    assert calls == []


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_fetch_help_aliases_print_plain_usage_without_git_calls(flag: str) -> None:
    git, calls = _git()
    ui = FetchUi()

    assert run_fetch_command([flag], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 0
    assert ui.lines == [
        "Usage: gt fetch [--sync-upstream]",
        "  --sync-upstream  Merge upstream/main into local main and push origin/main (main only)",
    ]
    assert calls == []


def test_fetch_rejects_non_repository_without_starting_frame() -> None:
    git, calls = _git({("rev-parse", "--git-dir"): _result(128, stderr="not a git repository")})
    ui = FetchUi()

    assert run_fetch_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 1
    assert ui.errors == ["Error: Not in a git repository"]
    assert ui.frames == []
    assert calls == [["rev-parse", "--git-dir"]]


def test_feature_branch_runs_cleanup_hooks_in_exact_order() -> None:
    git, calls = _git(
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): _result(stdout="feature-x\n"),
            ("branch", "-vv"): _result(
                stdout=(
                    "* feature-x   5678abc [origin/feature-x] latest\n"
                    "  old-feature 1234abc [origin/old-feature: gone] stale\n"
                )
            ),
            ("branch", "-d", "old-feature"): _result(),
            ("show-ref", "--verify", "--quiet", "refs/heads/main"): _result(),
            ("for-each-ref", "--format=%(refname:short)", "refs/heads"): _result(
                stdout="main\nfeature-x\nmerged-thing\n"
            ),
            ("merge-base", "--is-ancestor", "merged-thing", "main"): _result(),
            ("branch", "-d", "merged-thing"): _result(),
        }
    )
    ui = FetchUi()

    assert run_fetch_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 0
    assert ("status", "On branch 'feature-x'; no auto-sync performed", "muted") in ui.events
    assert ("status", "Deleted: old-feature", "success") in ui.events
    assert ("status", "Deleted merged branch: merged-thing", "success") in ui.events
    assert ui.frames == ["begin:Fetch and sync repository", "end"]
    assert calls == [
        ["rev-parse", "--git-dir"],
        ["ls-remote", "--heads", "origin", "main"],
        ["ls-remote", "--heads", "origin", "develop"],
        ["fetch", "--all", "--prune"],
        ["rev-parse", "--abbrev-ref", "HEAD"],
        ["show-ref", "--verify", "--quiet", "refs/remotes/origin/main"],
        ["show-ref", "--verify", "--quiet", "refs/remotes/origin/develop"],
        ["branch", "-vv"],
        ["branch", "-d", "old-feature"],
        ["show-ref", "--verify", "--quiet", "refs/heads/main"],
        ["show-ref", "--verify", "--quiet", "refs/heads/main"],
        ["for-each-ref", "--format=%(refname:short)", "refs/heads"],
        ["merge-base", "--is-ancestor", "merged-thing", "main"],
        ["branch", "-d", "merged-thing"],
    ]


def test_fetch_renders_multiple_gone_branches_as_frame_continuations() -> None:
    git, _ = _git(
        {
            ("branch", "-vv"): _result(
                stdout=(
                    "* feature-x 5678abc [origin/feature-x] latest\n"
                    "  old-feature 1234abc [origin/old-feature: gone] stale\n"
                    "  older-feature 9876def [origin/older-feature: gone] older\n"
                )
            )
        }
    )
    stdout = io.StringIO()

    assert run_fetch_command([], cwd="/repo", env={}, ui=GitToolsUi(stdout, io.StringIO()), run_git_fn=git) == 0
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", stdout.getvalue())
    assert "│  ■ Cleaning gone branches: old-feature\n│      older-feature" in plain


def test_fetch_sync_upstream_on_main_merges_then_pushes_origin() -> None:
    git, calls = _git(
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): _result(stdout="main\n"),
            ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"): _result(),
            ("pull", "--ff-only", "origin", "main"): _result(stdout="Already up to date.\n"),
            ("remote", "get-url", "upstream"): _result(stdout="git@github.com:upstream/repo.git\n"),
            ("ls-remote", "--heads", "upstream", "main"): _result(stdout="sha refs/heads/main\n"),
            ("diff", "--quiet"): _result(),
            ("diff", "--cached", "--quiet"): _result(),
            ("merge", "upstream/main", "--no-edit"): _result(),
            ("push", "origin", "main"): _result(),
        }
    )
    ui = FetchUi()

    assert run_fetch_command(["--sync-upstream"], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 0
    assert ("status", "Merged upstream changes successfully", "success") in ui.events
    assert ("status", "Pushed updated main to origin", "success") in ui.events
    assert ["remote", "get-url", "upstream"] in calls
    assert ["merge", "upstream/main", "--no-edit"] in calls
    assert ["push", "origin", "main"] in calls


def test_fetch_main_without_sync_upstream_does_not_probe_upstream() -> None:
    git, calls = _git(
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): _result(stdout="main\n"),
            ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"): _result(),
            ("pull", "--ff-only", "origin", "main"): _result(),
        }
    )
    ui = FetchUi()

    assert run_fetch_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 0
    assert ("status", "Skipping upstream sync (use 'gt fetch --sync-upstream' to enable)", "muted") in ui.events
    assert ["remote", "get-url", "upstream"] not in calls


def test_fetch_skips_dirty_upstream_merge() -> None:
    git, calls = _git(
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): _result(stdout="main\n"),
            ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"): _result(),
            ("pull", "--ff-only", "origin", "main"): _result(),
            ("remote", "get-url", "upstream"): _result(stdout="git@github.com:upstream/repo.git\n"),
            ("ls-remote", "--heads", "upstream", "main"): _result(stdout="sha refs/heads/main\n"),
            ("diff", "--quiet"): _result(1),
        }
    )
    ui = FetchUi()

    assert run_fetch_command(["--sync-upstream"], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 0
    assert ("status", "You have uncommitted changes; skipping upstream merge", "warning") in ui.events
    assert not any(call[0] == "merge" for call in calls)


def test_fetch_stops_before_cleanup_when_all_and_origin_fetch_fail() -> None:
    git, calls = _git(
        {
            ("fetch", "--all", "--prune"): _result(1, stderr="network down"),
            ("fetch", "origin", "--prune"): _result(1, stderr="network down"),
        }
    )
    ui = FetchUi()

    assert run_fetch_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 1
    assert ui.errors == ["Failed to fetch"]
    assert ["branch", "-vv"] not in calls
    assert ui.frames == ["begin:Fetch and sync repository"]


def test_fetch_refreshes_primary_refs_before_fetching_all_remotes() -> None:
    git, calls = _git(
        {
            ("ls-remote", "--heads", "origin", "main"): _result(stdout="sha refs/heads/main\n"),
            ("ls-remote", "--heads", "origin", "develop"): _result(stdout="sha refs/heads/develop\n"),
            ("fetch", "origin", "main"): _result(),
            ("fetch", "origin", "develop"): _result(),
        }
    )
    ui = FetchUi()

    assert run_fetch_command([], cwd="/repo", env={}, ui=ui, run_git_fn=git) == 0
    assert calls.index(["fetch", "origin", "main"]) < calls.index(["fetch", "--all", "--prune"])
    assert calls.index(["fetch", "origin", "develop"]) < calls.index(["fetch", "--all", "--prune"])
