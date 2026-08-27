from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import math
import re
import shutil
import sys
import tempfile
from typing import Literal, TextIO

import click

from .backup_list import (
    add_backup_repos,
    record_last_backup_at,
    record_last_checked_at,
    remove_backup_repo,
    set_selected_last,
)
from .config import (
    BackupRepoV4,
    BackupsDocumentV4,
    GtPaths,
    format_display_path,
    migrate_backups_document,
    read_backups_document,
    write_backups_document,
)
from .git import run_git
from .gitlab import (
    BACKUP_GROUP,
    assert_glab_ready,
    create_private_project,
    ensure_backup_group,
    group_exists,
    pick_preferred_default_branch,
    project_exists,
    project_ssh_url,
    project_web_url,
    protect_branch,
    set_default_branch,
)
from .refs_fingerprint import fingerprints_equal, parse_ls_remote_fingerprint
from .selector import SelectorItem, SelectorResult, SelectorState, run_selector
from .ssh_url import parse_ssh_git_url
from .stale import is_stale_repo
from .ui import GitToolsUi

ADD_HINT = "Use `gt backup add <ssh-url>` to add a repo first."
FORCE_ONLY_HINT = (
    "The --force flag is only valid for interactive backup, "
    "gt backup --all, and gt backup stale"
)
DRY_RUN_ONLY_HINT = (
    "The --dry-run flag is only valid for interactive backup, "
    "gt backup --all, and gt backup stale"
)
STALE_USAGE = (
    "Usage: gt backup stale [--days <n>] [--all] "
    "[-f|--force] [--dry-run]"
)

def _make_temp_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))

def _remove_tree(path: str | Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass

def _has_command(name: str) -> bool:
    return shutil.which(name) is not None

@dataclass(slots=True)
class BackupContext:
    paths: GtPaths
    ui: GitToolsUi
    cwd: str | Path = field(default_factory=Path.cwd)
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    has_command: Callable[..., object] = _has_command
    assert_glab_ready: Callable[..., object] = assert_glab_ready
    ensure_backup_group: Callable[..., object] = ensure_backup_group
    group_exists: Callable[..., object] = group_exists
    project_exists: Callable[..., object] = project_exists
    create_private_project: Callable[..., object] = create_private_project
    pick_preferred_default_branch: Callable[..., object] = (
        pick_preferred_default_branch
    )
    set_default_branch: Callable[..., object] = set_default_branch
    protect_branch: Callable[..., object] = protect_branch
    run_git: Callable[..., object] = run_git
    make_temp_dir: Callable[[str], str | Path] = _make_temp_dir
    remove_tree: Callable[[str | Path], object] = _remove_tree
    add_backup_repos: Callable[..., object] = add_backup_repos
    remove_backup_repo: Callable[..., object] = remove_backup_repo
    record_last_backup_at: Callable[..., object] = record_last_backup_at
    record_last_checked_at: Callable[..., object] = record_last_checked_at
    set_selected_last: Callable[..., object] = set_selected_last
    read_backups_document: Callable[..., object] = read_backups_document
    migrate_backups_document: Callable[..., object] = migrate_backups_document
    write_backups_document: Callable[..., object] = write_backups_document
    run_selector: Callable[..., SelectorResult] = run_selector
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

@dataclass(frozen=True, slots=True)
class BackupResult:
    status: Literal["ok", "skip", "fail"]
    source_url: str
    destination_url: str | None = None
    error: str | None = None

@dataclass(frozen=True, slots=True)
class _BackupSelectorItem(SelectorItem):
    last_backup_at: str | None = None
    last_checked_at: str | None = None

@dataclass(frozen=True, slots=True)
class _LoadedRepos:
    repos: tuple[BackupRepoV4, ...]
    document: BackupsDocumentV4
    migrated: bool

def _failed(source_url: str, error: str | None, fallback: str) -> BackupResult:
    return BackupResult("fail", source_url, error=error or fallback)

def _git_error(result, fallback: str) -> str:
    return result.stderr.strip() or result.stdout.strip() or fallback

def backup_one_repo(
    source_url: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    context: BackupContext,
) -> BackupResult:
    try:
        parsed = parse_ssh_git_url(source_url)
    except ValueError as error:
        return BackupResult("fail", source_url, error=str(error))

    if not context.has_command("git"):
        return _failed(
            source_url, None, "git is not installed or not available on PATH"
        )
    ready = context.assert_glab_ready(
        has_command=context.has_command,
        env=context.env,
    )
    if not ready.ok:
        return _failed(source_url, ready.error, "glab is not ready")

    name = parsed.project_name
    project_path = f"{BACKUP_GROUP}/{name}"
    web_url = project_web_url(BACKUP_GROUP, name)
    destination = project_ssh_url(BACKUP_GROUP, name)
    context.ui.section(f"{source_url} → {project_path}")

    if dry_run:
        group = context.group_exists(BACKUP_GROUP)
        if not group.ok:
            return _failed(
                source_url, group.error, "could not check GitLab group"
            )
        existing = context.project_exists(BACKUP_GROUP, name)
        if not existing.ok:
            return _failed(
                source_url, existing.error, "could not check GitLab project"
            )
        if not existing.exists:
            if existing.inactive:
                context.ui.status(
                    f"Would recreate inactive backup {project_path}"
                )
            else:
                context.ui.status(f"Would create {project_path}")
            context.ui.status("Would mirror")
            return BackupResult("ok", source_url, web_url)

        context.ui.status(f"Would update existing backup {project_path}")
        source_refs = context.run_git(
            ["ls-remote", source_url], cwd=context.cwd, env=context.env
        )
        if source_refs.returncode != 0:
            return _failed(
                source_url,
                _git_error(source_refs, "git ls-remote source failed"),
                "git ls-remote source failed",
            )
        destination_refs = context.run_git(
            ["ls-remote", destination], cwd=context.cwd, env=context.env
        )
        if destination_refs.returncode != 0:
            return _failed(
                source_url,
                _git_error(
                    destination_refs, "git ls-remote destination failed"
                ),
                "git ls-remote destination failed",
            )
        if fingerprints_equal(
            parse_ls_remote_fingerprint(source_refs.stdout),
            parse_ls_remote_fingerprint(destination_refs.stdout),
        ):
            context.ui.status("Would skip (unchanged)", tone="muted")
            return BackupResult("skip", source_url, web_url)
        context.ui.status("Would mirror")
        return BackupResult("ok", source_url, web_url)

    group = context.ensure_backup_group(BACKUP_GROUP)
    if not group.ok:
        return _failed(
            source_url,
            group.error,
            "could not ensure GitLab backup group",
        )
    if group.created:
        context.ui.status(f"Created group {BACKUP_GROUP}")

    existing = context.project_exists(BACKUP_GROUP, name)
    if not existing.ok:
        return _failed(
            source_url, existing.error, "could not check GitLab project"
        )
    if existing.exists:
        context.ui.status(f"Updating existing backup {project_path}")
    else:
        if existing.inactive:
            context.ui.status(
                "Previous backup pending deletion (inactive); "
                f"creating {project_path}"
            )
        created = context.create_private_project(BACKUP_GROUP, name)
        if not created.ok:
            return _failed(
                source_url, created.error, "failed to create GitLab project"
            )
        context.ui.status(f"Created {project_path}")

    if existing.exists and not force:
        source_refs = context.run_git(
            ["ls-remote", source_url], cwd=context.cwd, env=context.env
        )
        if source_refs.returncode != 0:
            return _failed(
                source_url,
                _git_error(source_refs, "git ls-remote source failed"),
                "git ls-remote source failed",
            )
        destination_refs = context.run_git(
            ["ls-remote", destination], cwd=context.cwd, env=context.env
        )
        if destination_refs.returncode != 0:
            return _failed(
                source_url,
                _git_error(
                    destination_refs, "git ls-remote destination failed"
                ),
                "git ls-remote destination failed",
            )
        if fingerprints_equal(
            parse_ls_remote_fingerprint(source_refs.stdout),
            parse_ls_remote_fingerprint(destination_refs.stdout),
        ):
            context.ui.status("Unchanged; skipping mirror", tone="muted")
            return BackupResult("skip", source_url, web_url)

    temp_root = Path(context.make_temp_dir("gt-backup-"))
    mirror_dir = temp_root / "mirror.git"
    try:
        context.ui.status(
            f"Cloning source to {temp_root.name}/mirror.git"
        )
        cloned = context.run_git(
            ["clone", "--mirror", source_url, str(mirror_dir)],
            cwd=context.cwd,
            env=context.env,
        )
        if cloned.returncode != 0:
            return _failed(
                source_url,
                _git_error(cloned, "git clone --mirror failed"),
                "git clone --mirror failed",
            )
        context.ui.status("Clone complete")

        context.ui.status(f"Pushing all branches + tags → {project_path}")
        pushed = context.run_git(
            [
                "push",
                "--prune",
                destination,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
            ],
            cwd=mirror_dir,
            env=context.env,
        )
        if pushed.returncode != 0:
            return _failed(
                source_url,
                _git_error(pushed, "git push failed"),
                "git push failed",
            )
        context.ui.status("Pushed all branches + tags")

        preferred = context.pick_preferred_default_branch(
            mirror_dir, run_git_fn=context.run_git
        )
        if preferred:
            updated = context.set_default_branch(
                BACKUP_GROUP, name, preferred
            )
            if updated.ok:
                context.ui.status(f"Default branch {preferred}")
            else:
                context.ui.status(
                    f"Could not set default branch to {preferred}: "
                    f"{updated.error or 'unknown error'}",
                    tone="warning",
                )

        for branch in ("main", "develop"):
            present = context.run_git(
                [
                    "show-ref",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{branch}",
                ],
                cwd=mirror_dir,
            )
            if present.returncode != 0:
                continue
            protected = context.protect_branch(BACKUP_GROUP, name, branch)
            if protected.ok:
                context.ui.status(
                    f"{branch} already protected"
                    if protected.already_protected
                    else f"Protected {branch}"
                )
            else:
                context.ui.status(
                    f"Could not protect {branch}: "
                    f"{protected.error or 'unknown error'}",
                    tone="warning",
                )
    finally:
        context.remove_tree(temp_root)

    return BackupResult("ok", source_url, web_url)

def run_backup_batch(
    urls: Sequence[str],
    *,
    context: BackupContext,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    results: list[BackupResult] = []
    if dry_run:
        context.ui.status("Dry run (no changes)")

    for url in urls:
        result = backup_one_repo(
            url, force=force, dry_run=dry_run, context=context
        )
        if result.status == "fail" or dry_run:
            results.append(result)
            continue

        if result.status == "skip":
            recorded = context.record_last_checked_at(
                context.paths, url, now=context.now()
            )
            if not recorded.ok:
                results.append(
                    BackupResult(
                        "fail",
                        url,
                        error=(
                            "Backup skipped but failed to save lastCheckedAt: "
                            f"{recorded.error}"
                        ),
                    )
                )
                continue
        else:
            recorded = context.record_last_backup_at(
                context.paths, url, now=context.now()
            )
            if not recorded.ok:
                results.append(
                    BackupResult(
                        "fail",
                        url,
                        error=(
                            "Backup succeeded but failed to save lastBackupAt: "
                            f"{recorded.error}"
                        ),
                    )
                )
                continue
        results.append(result)

    context.ui.section("Backup summary")
    for result in results:
        if result.status == "ok":
            context.ui.item(f"ok  {result.source_url}")
            context.ui.detail(
                "→ would mirror" if dry_run else f"→ {result.destination_url}"
            )
        elif result.status == "skip":
            context.ui.item(f"skip  {result.source_url}", tone="muted")
            context.ui.detail(
                "→ would skip (unchanged)" if dry_run else "→ unchanged"
            )
        else:
            context.ui.item(f"fail  {result.source_url}", tone="failure")
            context.ui.detail(f"— {result.error}", tone="failure")
    context.ui.list_end()
    return 1 if any(result.status == "fail" for result in results) else 0

_TOKENIZER = click.Command(
    "backup",
    params=[
        click.Argument(
            ["tokens"], nargs=-1, type=click.UNPROCESSED, required=False
        )
    ],
    add_help_option=False,
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
    },
)

def _tokens(args: Sequence[str]) -> tuple[str, ...]:
    sentinel = "__GT_BACKUP_LITERAL_DOUBLE_DASH__"
    values = tuple(args)
    while sentinel in values:
        sentinel += "_"
    escaped = [sentinel if value == "--" else value for value in values]
    context = _TOKENIZER.make_context("backup", escaped)
    try:
        parsed = tuple(context.params.get("tokens", ())) + tuple(context.args)
        return tuple("--" if value == sentinel else value for value in parsed)
    finally:
        context.close()

def _start_backup_frame(
    context: BackupContext, list_path: str, *, dry_run: bool
) -> None:
    context.ui.title("REPO BACKUP")
    context.ui.step(
        "Dry run: backup repositories"
        if dry_run
        else "Backup repositories"
    )
    context.ui.detail(list_path)

def _load_repos(context: BackupContext) -> _LoadedRepos | None:
    read = context.read_backups_document(context.paths.backups_file)
    if not read.ok:
        if read.missing:
            context.ui.error(f"No backups list found. {ADD_HINT}")
        else:
            context.ui.error(read.error)
        return None
    migrated = context.migrate_backups_document(read.document)
    if not migrated.ok:
        context.ui.error(migrated.error)
        return None
    if not migrated.document.repos:
        context.ui.error(f"Backups list is empty. {ADD_HINT}")
        return None
    return _LoadedRepos(
        repos=tuple(migrated.document.repos),
        document=migrated.document,
        migrated=migrated.migrated,
    )

def _persist_migration(
    loaded: _LoadedRepos, context: BackupContext
) -> bool:
    if not loaded.migrated:
        return True
    written = context.write_backups_document(
        context.paths.backups_file, loaded.document
    )
    if not written.ok:
        context.ui.error(written.error)
        return False
    return True

def _is_tty(stream: object) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False

def _selector_items(repos: Sequence[BackupRepoV4]) -> tuple[_BackupSelectorItem, ...]:
    return tuple(
        _BackupSelectorItem(
            label=repo.url,
            value=repo.url,
            last_backup_at=repo.last_backup_at,
            last_checked_at=repo.last_checked_at,
        )
        for repo in repos
    )

def _select_and_backup(
    repos: Sequence[BackupRepoV4],
    *,
    context: BackupContext,
    heading: str,
    tty_error: str,
    force: bool,
    dry_run: bool,
    list_path: str,
) -> int:
    if not _is_tty(context.stdin):
        context.ui.error(tty_error)
        return 1

    items = _selector_items(repos)
    initial = tuple(repo.url for repo in repos if repo.selected_last)
    selection = context.run_selector(
        items,
        initial=initial,
        multiple=True,
        input=context.stdin,
        output=context.stdout,
        render=lambda state: context.ui.render_backup_selector(
            heading, state, list_path=list_path
        ),
    )
    if selection.kind == "cancel":
        context.ui.cancelled_backup_selector(
            heading, selection.state, list_path=list_path
        )
        return 1
    if not selection.selected:
        context.ui.error("No repos selected")
        return 1
    selected = list(selection.selected)
    if not dry_run:
        saved = context.set_selected_last(context.paths, selected)
        if not saved.ok:
            context.ui.error(f"Failed to save selection: {saved.error}")
            return 1
    _start_backup_frame(context, list_path, dry_run=dry_run)
    return run_backup_batch(
        selected,
        context=context,
        force=force,
        dry_run=dry_run,
    )

def _run_add(args: tuple[str, ...], context: BackupContext) -> int:
    if any(arg in ("-f", "--force") for arg in args):
        context.ui.error(FORCE_ONLY_HINT)
        return 1
    if "--dry-run" in args:
        context.ui.error(DRY_RUN_ONLY_HINT)
        return 1
    urls = list(args)
    if not urls:
        context.ui.error("Usage: gt backup add <ssh-url> [<ssh-url> ...]")
        return 1
    result = context.add_backup_repos(context.paths, urls)
    if result.error and not result.added:
        context.ui.error(result.error)
        return 1
    if result.added:
        context.ui.title("REPO BACKUP")
        context.ui.step("Add repositories")
    if result.error:
        context.ui.error(result.error)
    for item in result.added:
        context.ui.success(f"Added {item.url} at index {item.index}")
    for failure in result.failures:
        context.ui.error(f"{failure.url}: {failure.error}")
    if result.added:
        context.ui.item(
            format_display_path(
                context.paths.backups_file,
                home=context.env.get("HOME"),
            )
        )
        context.ui.list_end()
    return 0 if result.ok else 1

def _run_remove(args: tuple[str, ...], context: BackupContext) -> int:
    if any(arg in ("-f", "--force") for arg in args):
        context.ui.error(FORCE_ONLY_HINT)
        return 1
    if "--dry-run" in args:
        context.ui.error(DRY_RUN_ONLY_HINT)
        return 1
    if len(args) != 1 or not args[0]:
        context.ui.error("Usage: gt backup remove <index|ssh-url>")
        return 1
    result = context.remove_backup_repo(context.paths, args[0])
    if not result.ok:
        context.ui.error(result.error)
        return 1
    context.ui.title("REPO BACKUP")
    context.ui.step("Remove repository")
    context.ui.success(f"Removed {result.removed}")
    context.ui.item(
        format_display_path(
            context.paths.backups_file,
            home=context.env.get("HOME"),
        )
    )
    context.ui.list_end()
    return 0

def _parse_stale_options(
    args: tuple[str, ...], context: BackupContext
) -> tuple[bool, float, bool, bool] | None:
    all_repos = False
    days = 7
    force = False
    dry_run = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--all":
            all_repos = True
        elif arg == "--days":
            index += 1
            if index >= len(args) or args[index].startswith("-"):
                context.ui.error(STALE_USAGE)
                return None
            raw = args[index]
            parsed_days = _parse_js_positive_integer(raw)
            if parsed_days is None:
                context.ui.error(
                    f"Invalid --days value: {raw} (must be a positive integer)"
                )
                return None
            days = parsed_days
        elif arg in ("-f", "--force"):
            force = True
        elif arg == "--dry-run":
            dry_run = True
        elif arg.startswith("-"):
            context.ui.error(f"Unknown flag: {arg}")
            return None
        else:
            context.ui.error(STALE_USAGE)
            return None
        index += 1
    return all_repos, days, force, dry_run

_JS_DECIMAL_RE = re.compile(
    r"[+]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
    re.ASCII,
)

_ECMASCRIPT_TRIM_CHARS = (
    "\u0009\u000b\u000c\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff\u000a\u000d"
)

def _parse_js_positive_integer(raw: str) -> float | None:
    text = raw.strip(_ECMASCRIPT_TRIM_CHARS)
    try:
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", text):
            value = float(int(text[2:], 16))
        elif re.fullmatch(r"0[bB][01]+", text):
            value = float(int(text[2:], 2))
        elif re.fullmatch(r"0[oO][0-7]+", text):
            value = float(int(text[2:], 8))
        elif _JS_DECIMAL_RE.fullmatch(text):
            value = float(text)
        else:
            return None
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 and value.is_integer() else None

def _run_stale(args: tuple[str, ...], context: BackupContext) -> int:
    parsed = _parse_stale_options(args, context)
    if parsed is None:
        return 1
    all_repos, days, force, dry_run = parsed
    if force and dry_run:
        context.ui.error("Cannot combine --force and --dry-run")
        return 1
    loaded = _load_repos(context)
    if loaded is None:
        return 1
    if all_repos and not dry_run and not _persist_migration(loaded, context):
        return 1
    now = context.now()
    stale = [
        repo
        for repo in loaded.repos
        if is_stale_repo(repo.model_dump(by_alias=True), now=now, days=days)
    ]
    list_path = format_display_path(
        context.paths.backups_file, home=context.env.get("HOME")
    )
    if not stale:
        _start_backup_frame(context, list_path, dry_run=dry_run)
        context.ui.status("No stale repos", tone="muted")
        context.ui.list_end()
        return 0
    if all_repos:
        _start_backup_frame(context, list_path, dry_run=dry_run)
        return run_backup_batch(
            [repo.url for repo in stale],
            context=context,
            force=force,
            dry_run=dry_run,
        )
    return _select_and_backup(
        stale,
        context=context,
        heading="Select stale repos to backup",
        tty_error=(
            "A terminal is required to select stale repos interactively. "
            "Use `gt backup stale --all` to back up every stale repo without selecting."
        ),
        force=force,
        dry_run=dry_run,
        list_path=list_path,
    )

def run_backup_command(
    args: Sequence[str] = (), *, context: BackupContext
) -> int:
    tokens = _tokens(args)
    if tokens and tokens[0] == "add":
        return _run_add(tokens[1:], context)
    if tokens and tokens[0] == "remove":
        return _run_remove(tokens[1:], context)
    if tokens and tokens[0] == "stale":
        return _run_stale(tokens[1:], context)

    all_repos = False
    force = False
    dry_run = False
    for arg in tokens:
        if arg == "--all":
            all_repos = True
        elif arg in ("-f", "--force"):
            force = True
        elif arg == "--dry-run":
            dry_run = True
        elif arg.startswith("-"):
            context.ui.error(f"Unknown flag: {arg}")
            return 1
        else:
            context.ui.error(
                "gt backup one-shot URL is retired; use gt backup add / "
                "gt backup / gt backup --all"
            )
            return 1
    if force and dry_run:
        context.ui.error("Cannot combine --force and --dry-run")
        return 1
    loaded = _load_repos(context)
    if loaded is None:
        return 1
    list_path = format_display_path(
        context.paths.backups_file, home=context.env.get("HOME")
    )
    if all_repos:
        if not dry_run and not _persist_migration(loaded, context):
            return 1
        _start_backup_frame(context, list_path, dry_run=dry_run)
        return run_backup_batch(
            [repo.url for repo in loaded.repos],
            context=context,
            force=force,
            dry_run=dry_run,
        )
    return _select_and_backup(
        loaded.repos,
        context=context,
        heading="Select repos to backup",
        tty_error=(
            "A terminal is required to select repos interactively. "
            "Use `gt backup --all` to back up every listed repo without selecting."
        ),
        force=force,
        dry_run=dry_run,
        list_path=list_path,
    )
