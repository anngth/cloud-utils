from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import sys

from shared.process import CommandResult

from .git import run_git
from .ui import GitToolsUi

RunGit = Callable[..., CommandResult]
Confirm = Callable[[str], bool]

PUSH_USAGE = "Usage: gt push [-y | --yes]"
PROTECTED_BRANCHES = ("main", "develop")

def _git_command(
    run_git_fn: RunGit,
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None,
) -> CommandResult:
    return run_git_fn(list(args), cwd=cwd, env=env)

def _ask(question: str) -> bool:
    """Ask on the terminal; no TTY, EOF or Ctrl-C all count as "no"."""
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False

def _parse_args(args: Sequence[str], ui: GitToolsUi) -> tuple[bool, bool, bool]:
    yes = False
    for arg in args:
        if arg in ("-y", "--yes"):
            yes = True
            continue
        if arg in ("-h", "--help"):
            ui.line(PUSH_USAGE)
            ui.line(
                "  -y, --yes  Force push main/develop without asking "
                "(required when stdin is not a terminal)"
            )
            return False, True, False
        ui.error(f"Unknown option for push: {arg}")
        ui.usage_line(PUSH_USAGE)
        return False, False, True
    return yes, False, False

def run_push_command(
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None,
    ui: GitToolsUi,
    run_git_fn: RunGit = run_git,
    confirm: Confirm = _ask,
) -> int:
    def git(git_args: Sequence[str]) -> CommandResult:
        return _git_command(run_git_fn, git_args, cwd=cwd, env=env)

    yes, help_requested, invalid = _parse_args(args, ui)
    if help_requested:
        return 0
    if invalid:
        return 1

    repo = git(["rev-parse", "--git-dir"])
    if repo.returncode != 0:
        ui.error("Error: Not in a git repository")
        return 1

    branch_result = git(["rev-parse", "--abbrev-ref", "HEAD"])
    current_branch = branch_result.stdout.strip()
    if (
        branch_result.returncode != 0
        or not current_branch
        or current_branch == "HEAD"
    ):
        ui.error("Cannot push from detached HEAD")
        return 1

    ui.begin("Force push (safe)")
    ui.status(f"Current branch: {current_branch}")

    upstream_result = git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    tracking_branch = (
        upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""
    )
    target_remote = "origin"
    if tracking_branch:
        separator = tracking_branch.find("/")
        if separator > 0:
            target_remote = tracking_branch[:separator]
            ui.status(f"Tracking branch: {tracking_branch}")
            if tracking_branch[separator + 1 :] != current_branch:
                ui.status(
                    "Tracking branch name differs from local branch", tone="warning"
                )
                ui.detail(
                    f"Will push explicitly to {target_remote}/{current_branch}"
                )
    else:
        ui.status("No upstream tracking branch configured", tone="warning")
        ui.detail(f"Will push explicitly to origin/{current_branch}")

    remote_ref = f"refs/remotes/{target_remote}/{current_branch}"
    remote_ref_result = git(["show-ref", "--verify", "--quiet", remote_ref])
    if remote_ref_result.returncode == 0:
        remote_branch_result = git(
            [
                "ls-remote",
                "--exit-code",
                "--heads",
                target_remote,
                current_branch,
            ]
        )
        if remote_branch_result.returncode == 2:
            ui.status(
                f"Remote {target_remote}/{current_branch} no longer exists; "
                "pruning stale tracking ref"
            )
            prune_result = git(["fetch", target_remote, "--prune"])
            if prune_result.returncode == 0:
                ui.status("Pruned stale remote-tracking refs")
            else:
                ui.status(
                    "Failed to prune stale remote-tracking refs; continuing with push",
                    tone="warning",
                )
        elif remote_branch_result.returncode != 0:
            ui.status(
                f"Could not verify whether {target_remote}/{current_branch} exists; "
                "continuing with push",
                tone="warning",
            )

    if (
        current_branch in PROTECTED_BRANCHES
        and not yes
        and not confirm(f"Force push to {target_remote}/{current_branch}?")
    ):
        ui.status(
            f"Force push to {current_branch} cancelled (pass -y to skip the question)",
            tone="warning",
        )
        ui.end()
        return 1

    # --force-if-includes: a lease taken from a ref that `gt fetch` just updated
    # would otherwise overwrite remote commits that were never integrated locally.
    push_result = git(
        [
            "push",
            "--force-with-lease",
            "--force-if-includes",
            target_remote,
            f"HEAD:{current_branch}",
        ]
    )
    if push_result.returncode != 0:
        ui.error("Force push failed")
        if push_result.stdout.strip():
            ui.error_detail(push_result.stdout.strip())
        if push_result.stderr.strip():
            ui.error_detail(push_result.stderr.strip())
        ui.error_detail(
            "Tip: run 'git remote -v' and verify write permission to the remote"
        )
        return 1

    if push_result.stdout.strip():
        ui.detail(push_result.stdout.strip())
    if push_result.stderr.strip():
        ui.detail(push_result.stderr.strip())
    ui.status("Force push successful")
    ui.status("Fetching")
    fetch_result = git(["fetch"])
    if fetch_result.returncode == 0:
        ui.status("Fetch successful")
    else:
        ui.status("Fetch failed", tone="failure")
    ui.end()
    return 0
