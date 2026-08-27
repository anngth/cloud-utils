from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from shared.process import CommandResult

from .git import run_git
from .ui import GitToolsUi

RunGit = Callable[..., CommandResult]

def _git_command(
    run_git_fn: RunGit,
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None,
) -> CommandResult:
    return run_git_fn(list(args), cwd=cwd, env=env)

def run_push_command(
    _args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None,
    ui: GitToolsUi,
    run_git_fn: RunGit = run_git,
) -> int:
    def git(args: Sequence[str]) -> CommandResult:
        return _git_command(run_git_fn, args, cwd=cwd, env=env)

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

    push_result = git(
        ["push", "--force-with-lease", target_remote, f"HEAD:{current_branch}"]
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
