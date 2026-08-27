from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from shared.process import CommandResult

from .git import run_git
from .ui import GitToolsUi

FETCH_USAGE = "Usage: gt fetch [--sync-upstream]"
PROTECTED_BRANCHES = ("main", "master", "develop")
RunGit = Callable[..., CommandResult]

def _git_command(
    run_git_fn: RunGit,
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None,
) -> CommandResult:
    return run_git_fn(list(args), cwd=cwd, env=env)

def _refresh_primary_remote_refs(
    git: Callable[[Sequence[str]], CommandResult], ui: GitToolsUi
) -> None:
    for branch in ("main", "develop"):
        ls_remote = git(["ls-remote", "--heads", "origin", branch])
        if not ls_remote.stdout.strip():
            continue
        ui.status(f"Fetching origin/{branch} without checkout")
        fetched = git(["fetch", "origin", branch])
        if fetched.returncode == 0:
            ui.status(f"Successfully fetched origin/{branch}")
        else:
            ui.status(f"Failed to fetch origin/{branch}", tone="warning")

def _fetch_and_prune(
    git: Callable[[Sequence[str]], CommandResult], ui: GitToolsUi
) -> bool:
    ui.status("Fetching from all remotes")
    all_remotes = git(["fetch", "--all", "--prune"])
    if all_remotes.returncode == 0:
        ui.status("Fetch & prune successful")
        return True

    ui.status(
        "Failed to fetch from all remotes; trying origin only", tone="warning"
    )
    origin = git(["fetch", "origin", "--prune"])
    if origin.returncode == 0:
        ui.status("Fetch origin successful")
        return True

    ui.error("Failed to fetch")
    return False

def _sync_local_branch_with_origin_ff(
    git: Callable[[Sequence[str]], CommandResult],
    ui: GitToolsUi,
    branch: str,
    current_branch: str,
) -> None:
    remote_ref = git(
        ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"]
    )
    if remote_ref.returncode != 0 or current_branch == branch:
        return

    local_ref = git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
    if local_ref.returncode != 0:
        ui.status(f"Creating local '{branch}' from origin/{branch}")
        created = git(["branch", branch, f"origin/{branch}"])
        if created.returncode == 0:
            ui.status(f"Created local '{branch}'")
        else:
            ui.status(f"Failed to create local '{branch}'", tone="warning")
        return

    local_sha = git(["rev-parse", f"refs/heads/{branch}"]).stdout.strip()
    remote_sha = git(["rev-parse", f"refs/remotes/origin/{branch}"]).stdout.strip()
    if not local_sha or not remote_sha or local_sha == remote_sha:
        return

    ancestor = git(["merge-base", "--is-ancestor", branch, f"origin/{branch}"])
    if ancestor.returncode == 0:
        ui.status(f"Fast-forwarding local '{branch}' to origin/{branch}")
        updated = git(
            ["update-ref", f"refs/heads/{branch}", remote_sha, local_sha]
        )
        if updated.returncode == 0:
            ui.status(f"Updated local '{branch}' to origin/{branch}")
        else:
            ui.status(f"Failed to update local '{branch}'", tone="warning")
    else:
        ui.status(
            f"Local '{branch}' has local-only commits; skipping auto-sync",
            tone="warning",
        )
        ui.detail(f"Run: git switch {branch} && git pull --ff-only origin {branch}")

def _sync_with_origin(
    git: Callable[[Sequence[str]], CommandResult], ui: GitToolsUi, branch: str
) -> bool:
    remote_ref = git(
        ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"]
    )
    if remote_ref.returncode != 0:
        ui.status(f"origin/{branch} not found; skipping sync", tone="muted")
        return True

    ui.status(f"Syncing {branch} branch")
    pull = git(["pull", "--ff-only", "origin", branch])
    if pull.returncode == 0:
        ui.status(f"Pulled from origin/{branch}")
        return True
    ui.error("Pull failed (non fast-forward or uncommitted changes)")
    return False

def _sync_with_upstream(
    git: Callable[[Sequence[str]], CommandResult], ui: GitToolsUi
) -> None:
    upstream_remote = git(["remote", "get-url", "upstream"])
    if upstream_remote.returncode != 0:
        return

    current_branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if current_branch != "main":
        return

    upstream_main = git(["ls-remote", "--heads", "upstream", "main"])
    if not upstream_main.stdout.strip():
        return

    dirty = git(["diff", "--quiet"])
    dirty_cached = git(["diff", "--cached", "--quiet"])
    if dirty.returncode != 0 or dirty_cached.returncode != 0:
        ui.status(
            "You have uncommitted changes; skipping upstream merge", tone="warning"
        )
        ui.detail(
            "Commit or stash your changes, then run 'gt fetch' again to merge upstream"
        )
        return

    ui.status("Merging upstream/main into main")
    merged = git(["merge", "upstream/main", "--no-edit"])
    if merged.returncode != 0:
        ui.error("Merge failed; there might be conflicts")
        ui.error_detail("Resolve conflicts manually, then run 'git add .' and 'git commit'")
        return

    ui.status("Merged upstream changes successfully")
    ui.status("Pushing updated main to origin")
    pushed = git(["push", "origin", "main"])
    if pushed.returncode == 0:
        ui.status("Pushed updated main to origin")
    else:
        ui.status(
            "Failed to push to origin, but local merge was successful", tone="warning"
        )

def _cleanup_gone_branches(
    git: Callable[[Sequence[str]], CommandResult], ui: GitToolsUi
) -> None:
    branches = git(["branch", "-vv"]).stdout.split("\n")
    gone_branches = [
        line.strip().split()[0]
        for line in branches
        if ": gone]" in line and not line.startswith("*") and line.strip()
    ]
    if not gone_branches:
        return

    ui.status(f"Cleaning gone branches: {'\n'.join(gone_branches)}")
    for branch in gone_branches:
        deleted = git(["branch", "-d", branch])
        if deleted.returncode == 0:
            ui.status(f"Deleted: {branch}")
        else:
            ui.status(f"Skipped (not merged): {branch}", tone="warning")

def _get_cleanup_base_branch(
    git: Callable[[Sequence[str]], CommandResult], current_branch: str
) -> str:
    if current_branch in ("main", "develop"):
        return current_branch
    if git(["show-ref", "--verify", "--quiet", "refs/heads/main"]).returncode == 0:
        return "main"
    if git(["show-ref", "--verify", "--quiet", "refs/heads/develop"]).returncode == 0:
        return "develop"
    return current_branch

def _cleanup_merged_branches(
    git: Callable[[Sequence[str]], CommandResult],
    ui: GitToolsUi,
    base_branch: str,
    current_branch: str,
) -> None:
    if not base_branch:
        return
    base_ref = git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{base_branch}"]
    )
    if base_ref.returncode != 0:
        return

    branches = [
        name
        for line in git(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads"]
        ).stdout.split("\n")
        if (name := line.strip()) and name not in PROTECTED_BRANCHES
    ]
    if not branches:
        return

    ui.status(f"Cleaning merged branches (base: {base_branch})")
    cleaned_any = False
    for branch in branches:
        if branch == current_branch:
            continue
        ancestor = git(["merge-base", "--is-ancestor", branch, base_branch])
        if ancestor.returncode != 0:
            continue
        deleted = git(["branch", "-d", branch])
        if deleted.returncode == 0:
            ui.status(f"Deleted merged branch: {branch}")
            cleaned_any = True
    if not cleaned_any:
        ui.status("No merged local branches to clean", tone="muted")

def _parse_args(args: Sequence[str], ui: GitToolsUi) -> tuple[bool, bool, bool]:
    sync_upstream = False
    for arg in args:
        if arg == "--sync-upstream":
            sync_upstream = True
            continue
        if arg in ("-h", "--help"):
            ui.line(FETCH_USAGE)
            ui.line(
                "  --sync-upstream  Merge upstream/main into local main and push "
                "origin/main (main only)"
            )
            return False, True, False
        ui.error(f"Unknown option for fetch: {arg}")
        ui.usage_line(FETCH_USAGE)
        return False, False, True
    return sync_upstream, False, False

def run_fetch_command(
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None,
    ui: GitToolsUi,
    run_git_fn: RunGit = run_git,
) -> int:
    def git(git_args: Sequence[str]) -> CommandResult:
        return _git_command(run_git_fn, git_args, cwd=cwd, env=env)

    sync_upstream, help_requested, invalid = _parse_args(args, ui)
    if help_requested:
        return 0
    if invalid:
        return 1

    repo = git(["rev-parse", "--git-dir"])
    if repo.returncode != 0:
        ui.error("Error: Not in a git repository")
        return 1

    ui.begin("Fetch and sync repository")
    _refresh_primary_remote_refs(git, ui)
    if not _fetch_and_prune(git, ui):
        return 1

    current_branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    _sync_local_branch_with_origin_ff(git, ui, "main", current_branch)
    _sync_local_branch_with_origin_ff(git, ui, "develop", current_branch)

    if current_branch in ("main", "develop"):
        if not _sync_with_origin(git, ui, current_branch):
            return 1
        if current_branch == "main":
            if sync_upstream:
                _sync_with_upstream(git, ui)
            else:
                ui.status(
                    "Skipping upstream sync (use 'gt fetch --sync-upstream' to enable)",
                    tone="muted",
                )
    else:
        ui.status(
            f"On branch '{current_branch}'; no auto-sync performed", tone="muted"
        )

    _cleanup_gone_branches(git, ui)
    cleanup_base = _get_cleanup_base_branch(git, current_branch)
    _cleanup_merged_branches(git, ui, cleanup_base, current_branch)
    ui.end()
    return 0
