from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
from pathlib import Path

import pytest

from git_tools.backup import (
    BackupContext,
    BackupResult,
    backup_one_repo,
    run_backup_batch,
    run_backup_command,
)
from git_tools.config import GtPaths, resolve_gt_paths
from git_tools.gitlab import (
    ActionResult,
    BACKUP_GROUP,
    EnsureGroupResult,
    ExistsResult,
    ProtectResult,
    ReadyResult,
    project_ssh_url,
    project_web_url,
)
from git_tools.selector import SelectorResult, SelectorState
from git_tools.ui import GitToolsUi
from shared.process import CommandResult


SOURCE = "git@github.com:org/app.git"
SOURCE_B = "git@github.com:org/other.git"
BASE_NAME = "org-app"
BASE_NAME_B = "org-other"
FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
RECENT_CHECKED = "2026-08-07T12:00:00.000Z"


class InputStub:
    def __init__(self, tty: bool = True) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


class UiSpy:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.statuses: list[str] = []
        self.items: list[str] = []
        self.titles: list[str] = []
        self.ends: list[str] = []
        self.events: list[tuple[str, str, str | None]] = []
        self.cancelled_calls: list[tuple[str, object, object | None]] = []

    def error(self, message: object) -> None:
        self.errors.append(str(message))

    def title(self, message: object) -> None:
        text = str(message)
        self.titles.append(text)
        self.events.append(("title", text, None))

    def step(self, message: object) -> None:
        text = str(message)
        self.statuses.append(text)
        self.events.append(("step", text, None))

    def section(self, message: object) -> None:
        self.events.append(("section", str(message), None))

    def status(
        self, message: object, *, tone: str = "success", marker: str | None = None
    ) -> None:
        del marker
        text = str(message)
        self.statuses.append(text)
        self.events.append(("status", text, tone))

    def success(self, message: object) -> None:
        text = str(message)
        self.statuses.append(text)
        self.events.append(("status", text, "success"))

    def item(
        self, message: object, *, tone: str = "success", marker: str | None = None
    ) -> None:
        text = str(message)
        self.items.append(text)
        self.events.append(("item", text, tone if marker is None else marker))

    def detail(self, message: object, *, tone: str = "muted") -> None:
        text = str(message)
        self.items.append(text)
        self.events.append(("detail", text, tone))

    def list_end(self, message: object = "") -> None:
        text = str(message)
        self.ends.append(text)
        self.events.append(("list_end", text, None))

    def render_backup_selector(self, *_args, **_kwargs) -> None:
        pass

    def cancelled_backup_selector(
        self, heading: object, state: object, *, list_path=None, **_kwargs
    ) -> None:
        self.cancelled_calls.append((str(heading), state, list_path))


@dataclass
class Harness:
    context: BackupContext
    ui: UiSpy
    paths: GtPaths
    created: list[str]
    removed: list[Path]


def seed_repos(paths: GtPaths, repos, *, version: int = 4) -> None:
    paths.gt_dir.mkdir(parents=True, exist_ok=True)
    if version == 1:
        document = {"version": 1, "repos": list(repos)}
    else:
        normalized = []
        for repo in repos:
            value = {"url": repo} if isinstance(repo, str) else dict(repo)
            normalized.append(
                {
                    "url": value["url"],
                    "lastBackupAt": value.get("lastBackupAt"),
                    "lastCheckedAt": value.get("lastCheckedAt"),
                    "selectedLast": value.get("selectedLast", False),
                }
            )
        document = {"version": 4, "repos": normalized}
    paths.backups_file.write_text(json.dumps(document, indent=2) + "\n")


def make_harness(tmp_path: Path, **overrides) -> Harness:
    env = {"CLOUD_UTILS_CONFIG_DIR": str(tmp_path), "HOME": "/Users/me"}
    paths = resolve_gt_paths(env)
    ui = UiSpy()
    created: list[str] = []
    removed: list[Path] = []
    counter = 0

    def create_project(_group: str, name: str) -> ActionResult:
        created.append(name)
        return ActionResult(True)

    def make_temp(prefix: str) -> Path:
        nonlocal counter
        counter += 1
        return Path("/tmp") / f"{prefix.rstrip('-')}-{counter}"

    defaults = dict(
        paths=paths,
        ui=ui,
        cwd=Path("/repo"),
        env=env,
        stdin=InputStub(True),
        stdout=io.StringIO(),
        has_command=lambda name: name in {"git", "glab"},
        assert_glab_ready=lambda **_kwargs: ReadyResult(True),
        ensure_backup_group=lambda _group: EnsureGroupResult(True),
        group_exists=lambda _group: ExistsResult(True, exists=True),
        project_exists=lambda _group, _name: ExistsResult(True),
        create_private_project=create_project,
        pick_preferred_default_branch=lambda _repo, **_kwargs: "main",
        set_default_branch=lambda _group, _name, _branch: ActionResult(True),
        protect_branch=lambda _group, _name, _branch: ProtectResult(True),
        run_git=lambda _args, **_kwargs: CommandResult(0),
        make_temp_dir=make_temp,
        remove_tree=lambda path: removed.append(Path(path)),
        now=lambda: FIXED_NOW,
    )
    defaults.update(overrides)
    context = BackupContext(**defaults)
    return Harness(context, ui, paths, created, removed)


def event_index(ui: UiSpy, kind: str, fragment: str) -> int:
    return next(
        index
        for index, (event_kind, message, _tone) in enumerate(ui.events)
        if event_kind == kind and fragment in message
    )


def assert_backup_frame_before_repo(ui: UiSpy, *, dry_run: bool = False) -> None:
    title = event_index(ui, "title", "REPO BACKUP")
    step = event_index(
        ui,
        "step",
        "Dry run: backup repositories" if dry_run else "Backup repositories",
    )
    path = event_index(ui, "detail", "backups.json")
    repo = event_index(ui, "section", f" → {BACKUP_GROUP}/")
    assert title < step < path < repo


def read_disk(paths: GtPaths) -> dict:
    return json.loads(paths.backups_file.read_text())


def test_backup_one_repo_rejects_bad_url_before_external_calls(tmp_path: Path) -> None:
    calls: list[str] = []
    h = make_harness(
        tmp_path,
        has_command=lambda name: calls.append(name) or True,
    )
    result = backup_one_repo("https://github.com/org/app.git", context=h.context)
    assert result.status == "fail"
    assert result.destination_url is None
    assert "HTTPS" in (result.error or "")
    assert calls == []


def test_backup_one_repo_fails_when_git_missing(tmp_path: Path) -> None:
    h = make_harness(tmp_path, has_command=lambda name: name == "glab")
    result = backup_one_repo(SOURCE, context=h.context)
    assert result.status == "fail"
    assert result.error == "git is not installed or not available on PATH"


def test_backup_one_repo_fails_when_glab_is_not_ready(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        assert_glab_ready=lambda **_kwargs: ReadyResult(False, "auth required"),
    )
    result = backup_one_repo(SOURCE, context=h.context)
    assert result == BackupResult("fail", SOURCE, None, "auth required")


def test_backup_one_repo_fails_when_group_ensure_fails_before_create(
    tmp_path: Path,
) -> None:
    h = make_harness(
        tmp_path,
        ensure_backup_group=lambda _group: EnsureGroupResult(
            False, error="failed to create GitLab subgroup"
        ),
    )
    result = backup_one_repo(SOURCE, context=h.context)
    assert result.status == "fail"
    assert h.created == []
    assert "subgroup" in (result.error or "")


def test_backup_one_repo_ensures_group_before_project_lookup(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    h = make_harness(
        tmp_path,
        ensure_backup_group=lambda group: calls.append(("ensure", group))
        or EnsureGroupResult(True, created=True),
        project_exists=lambda group, name: calls.append(("exists", group, name))
        or ExistsResult(True),
    )
    result = backup_one_repo(SOURCE, context=h.context)
    assert result.status == "ok"
    assert calls[:2] == [
        ("ensure", BACKUP_GROUP),
        ("exists", BACKUP_GROUP, BASE_NAME),
    ]


def test_backup_one_repo_preserves_complete_mutation_order(tmp_path: Path) -> None:
    calls: list[str] = []

    def git(args, **_kwargs):
        calls.append(f"git:{args[0]}:{args[-1]}")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        ensure_backup_group=lambda _group: calls.append("ensure")
        or EnsureGroupResult(True),
        project_exists=lambda *_args: calls.append("project")
        or ExistsResult(True),
        create_private_project=lambda *_args: calls.append("create")
        or ActionResult(True),
        run_git=git,
        pick_preferred_default_branch=lambda *_args, **_kwargs: calls.append("pick")
        or "main",
        set_default_branch=lambda *_args: calls.append("default")
        or ActionResult(True),
        protect_branch=lambda _group, _name, branch: calls.append(
            f"protect:{branch}"
        )
        or ProtectResult(True),
        remove_tree=lambda _path: calls.append("remove"),
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    assert calls == [
        "ensure",
        "project",
        "create",
        "git:clone:/tmp/gt-backup-1/mirror.git",
        "git:push:+refs/tags/*:refs/tags/*",
        "pick",
        "default",
        "git:show-ref:refs/heads/main",
        "protect:main",
        "git:show-ref:refs/heads/develop",
        "protect:develop",
        "remove",
    ]


def test_backup_one_repo_creates_private_project_and_mirrors_when_missing(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path | str]] = []

    def git(args, *, cwd, **_kwargs):
        calls.append((list(args), cwd))
        return CommandResult(0)

    h = make_harness(tmp_path, run_git=git)
    result = backup_one_repo(SOURCE, context=h.context)
    assert result == BackupResult(
        "ok", SOURCE, project_web_url(BACKUP_GROUP, BASE_NAME), None
    )
    assert h.created == [BASE_NAME]
    assert any(args[:2] == ["clone", "--mirror"] for args, _ in calls)
    push = next(args for args, _ in calls if args[0] == "push")
    assert push == [
        "push",
        "--prune",
        project_ssh_url(BACKUP_GROUP, BASE_NAME),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    ]
    assert len(h.removed) == 1


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ({"main"}, ["main"]),
        ({"main", "develop"}, ["main", "develop"]),
        ({"develop"}, ["develop"]),
    ],
)
def test_backup_one_repo_protects_each_present_primary_branch(
    tmp_path: Path, available: set[str], expected: list[str]
) -> None:
    protected: list[str] = []

    def git(args, **_kwargs):
        if args[0] == "show-ref":
            return CommandResult(0 if args[-1].split("/")[-1] in available else 1)
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        run_git=git,
        protect_branch=lambda _group, _name, branch: protected.append(branch)
        or ProtectResult(True),
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    assert protected == expected
    for branch in expected:
        assert f"Protected {branch}" in h.ui.statuses


def test_backup_one_repo_sets_preferred_default_branch_after_push(
    tmp_path: Path,
) -> None:
    defaults: list[tuple[str, str, str]] = []
    h = make_harness(
        tmp_path,
        pick_preferred_default_branch=lambda _repo, **_kwargs: "develop",
        set_default_branch=lambda group, name, branch: defaults.append(
            (group, name, branch)
        )
        or ActionResult(True),
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    assert defaults == [(BACKUP_GROUP, BASE_NAME, "develop")]
    assert "Default branch develop" in h.ui.statuses


def test_backup_one_repo_warns_when_default_branch_update_fails(
    tmp_path: Path,
) -> None:
    h = make_harness(
        tmp_path,
        set_default_branch=lambda *_args: ActionResult(False, "API denied"),
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    assert (
        "status",
        "Could not set default branch to main: API denied",
        "warning",
    ) in h.ui.events


def test_backup_one_repo_warns_when_branch_protection_fails(tmp_path: Path) -> None:
    def git(args, **_kwargs):
        if args[0] == "show-ref":
            return CommandResult(0 if args[-1].endswith("/main") else 1)
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        run_git=git,
        protect_branch=lambda *_args: ProtectResult(False, error="API denied"),
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    assert ("status", "Could not protect main: API denied", "warning") in h.ui.events


def test_backup_one_repo_reports_already_protected_branch(tmp_path: Path) -> None:
    def git(args, **_kwargs):
        if args[0] == "show-ref":
            return CommandResult(0 if args[-1].endswith("/main") else 1)
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        run_git=git,
        protect_branch=lambda *_args: ProtectResult(True, already_protected=True),
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    assert "main already protected" in h.ui.statuses


def test_backup_one_repo_prints_concise_progress_and_short_clone_path(
    tmp_path: Path,
) -> None:
    h = make_harness(
        tmp_path,
        make_temp_dir=lambda _prefix: Path("/tmp/gt-backup-test"),
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    messages = "\n".join(h.ui.statuses)
    assert f"Created {BACKUP_GROUP}/{BASE_NAME}" in messages
    assert "Cloning source to gt-backup-test/mirror.git" in messages
    assert f"Pushing all branches + tags → {BACKUP_GROUP}/{BASE_NAME}" in messages
    assert "Checking backup group" not in messages
    assert ("section", f"{SOURCE} → {BACKUP_GROUP}/{BASE_NAME}", None) in h.ui.events


def test_backup_one_repo_updates_live_existing_project(tmp_path: Path) -> None:
    def git(args, **_kwargs):
        if args[0] == "ls-remote":
            sha = "aaa" if args[1] == SOURCE else "bbb"
            return CommandResult(0, f"{sha}\trefs/heads/main\n")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=git,
    )
    result = backup_one_repo(SOURCE, context=h.context)
    assert result.status == "ok"
    assert h.created == []
    assert f"Updating existing backup {BACKUP_GROUP}/{BASE_NAME}" in h.ui.statuses


def test_backup_one_repo_recreates_inactive_project(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, inactive=True),
    )
    result = backup_one_repo(SOURCE, context=h.context)
    assert result.status == "ok"
    assert h.created == [BASE_NAME]
    assert "inactive" in "\n".join(h.ui.statuses)


def test_backup_one_repo_force_mirrors_without_fingerprint_probes(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def git(args, **_kwargs):
        calls.append(list(args))
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=git,
    )
    result = backup_one_repo(SOURCE, force=True, context=h.context)
    assert result.status == "ok"
    assert all(args[0] != "ls-remote" for args in calls)
    assert any(args[0] == "clone" for args in calls)


def test_backup_one_repo_skips_when_live_fingerprints_match(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def git(args, **_kwargs):
        calls.append(list(args))
        if args[0] == "ls-remote":
            return CommandResult(0, "abc\trefs/heads/main\n")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=git,
    )
    result = backup_one_repo(SOURCE, context=h.context)
    assert result.status == "skip"
    assert all(args[0] != "clone" for args in calls)
    assert ("status", "Unchanged; skipping mirror", "muted") in h.ui.events


def test_backup_one_repo_does_not_skip_when_fingerprints_differ(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def git(args, **_kwargs):
        calls.append(list(args))
        if args[0] == "ls-remote":
            sha = "aaa" if args[1] == SOURCE else "bbb"
            return CommandResult(0, f"{sha}\trefs/heads/main\n")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=git,
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    assert any(args[0] == "clone" for args in calls)


def test_backup_one_repo_never_probes_fingerprint_when_project_missing(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    h = make_harness(
        tmp_path,
        run_git=lambda args, **_kwargs: calls.append(list(args)) or CommandResult(0),
    )
    assert backup_one_repo(SOURCE, context=h.context).status == "ok"
    assert all(args[0] != "ls-remote" for args in calls)


@pytest.mark.parametrize(
    ("which", "stdout", "stderr", "expected"),
    [
        (SOURCE, "", "remote error", "remote error"),
        (SOURCE, "source error", "", "source error"),
        (project_ssh_url(BACKUP_GROUP, BASE_NAME), "", "", "destination"),
    ],
)
def test_backup_one_repo_fails_on_live_fingerprint_probe_error(
    tmp_path: Path, which: str, stdout: str, stderr: str, expected: str
) -> None:
    destination = project_ssh_url(BACKUP_GROUP, BASE_NAME)

    def git(args, **_kwargs):
        if args[0] == "ls-remote" and args[1] == which:
            return CommandResult(128, stdout, stderr)
        if args[0] == "ls-remote":
            return CommandResult(0, "abc\trefs/heads/main\n")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=git,
    )
    result = backup_one_repo(SOURCE, context=h.context)
    assert result.status == "fail"
    assert expected in (result.error or "")
    assert destination


@pytest.mark.parametrize(
    ("operation", "failure", "expected"),
    [
        ("clone", CommandResult(1, stderr="clone failed"), "clone failed"),
        ("push", CommandResult(1, stdout="push failed"), "push failed"),
    ],
)
def test_backup_one_repo_operation_failure_cleans_temp_directory(
    tmp_path: Path, operation: str, failure: CommandResult, expected: str
) -> None:
    def git(args, **_kwargs):
        return failure if args[0] == operation else CommandResult(0)

    h = make_harness(tmp_path, run_git=git)
    result = backup_one_repo(SOURCE, context=h.context)
    assert result.status == "fail"
    assert result.error == expected
    assert len(h.removed) == 1


def test_backup_one_repo_interrupt_during_clone_cleans_then_reraises(
    tmp_path: Path,
) -> None:
    def git(args, **_kwargs):
        if args[0] == "clone":
            raise KeyboardInterrupt
        return CommandResult(0)

    h = make_harness(tmp_path, run_git=git)
    with pytest.raises(KeyboardInterrupt):
        backup_one_repo(SOURCE, context=h.context)
    assert len(h.removed) == 1


def test_run_backup_batch_skip_updates_only_last_checked_at(tmp_path: Path) -> None:
    seed_repos(
        resolve_gt_paths(
            {"CLOUD_UTILS_CONFIG_DIR": str(tmp_path), "HOME": "/Users/me"}
        ),
        [
            {
                "url": SOURCE,
                "lastBackupAt": "2020-01-01T00:00:00.000Z",
                "lastCheckedAt": None,
            }
        ],
    )

    def git(args, **_kwargs):
        if args[0] == "ls-remote":
            return CommandResult(0, "abc\trefs/heads/main\n")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=git,
    )
    assert run_backup_batch([SOURCE], context=h.context) == 0
    repo = read_disk(h.paths)["repos"][0]
    assert repo["lastBackupAt"] == "2020-01-01T00:00:00.000Z"
    assert repo["lastCheckedAt"] == "2026-08-08T12:00:00.000Z"
    assert ("item", f"skip  {SOURCE}", "muted") in h.ui.events


def test_run_backup_batch_skip_metadata_failure_becomes_repository_failure(
    tmp_path: Path,
) -> None:
    seed_repos(resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}), [SOURCE])

    def git(args, **_kwargs):
        if args[0] == "ls-remote":
            return CommandResult(0, "abc\trefs/heads/main\n")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=git,
        record_last_checked_at=lambda *_args, **_kwargs: type(
            "Result", (), {"ok": False, "error": "disk full"}
        )(),
    )
    assert run_backup_batch([SOURCE], context=h.context) == 1
    output = "\n".join(h.ui.items)
    assert f"fail  {SOURCE}" in output
    assert "Backup skipped but failed to save lastCheckedAt: disk full" in output


def test_run_backup_batch_continues_after_failure_and_keeps_summary_order(
    tmp_path: Path,
) -> None:
    seed_repos(resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}), [SOURCE_B])
    h = make_harness(
        tmp_path,
        project_exists=lambda _group, name: ExistsResult(
            False, error="project lookup failed"
        )
        if name == BASE_NAME
        else ExistsResult(True),
    )
    assert run_backup_batch([SOURCE, SOURCE_B], context=h.context) == 1
    summary = "\n".join(h.ui.items)
    assert summary.index(f"fail  {SOURCE}") < summary.index(f"ok  {SOURCE_B}")
    assert "— project lookup failed" in summary
    assert f"→ {project_web_url(BACKUP_GROUP, BASE_NAME_B)}" in summary
    assert h.created == [BASE_NAME_B]
    assert len(h.ui.ends) == 1


def test_run_backup_batch_renders_failed_rows_red_with_real_ui(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    ui = GitToolsUi(stdout, stderr)
    h = make_harness(
        tmp_path,
        ui=ui,
        ensure_backup_group=lambda _group: EnsureGroupResult(False, error="access denied"),
    )
    assert run_backup_batch([SOURCE], context=h.context) == 1
    assert "\033[31m■\033[39m fail  git@github.com:org/app.git" in stdout.getvalue()
    assert "\033[31m— access denied\033[39m" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_run_backup_batch_separates_repo_and_summary_sections(
    tmp_path: Path,
) -> None:
    stdout = io.StringIO()
    ui = GitToolsUi(stdout, io.StringIO())
    h = make_harness(
        tmp_path,
        ui=ui,
        ensure_backup_group=lambda _group: EnsureGroupResult(False, error="access denied"),
    )
    assert run_backup_batch([SOURCE, SOURCE_B], context=h.context) == 1
    plain = stdout.getvalue()
    for code in ("\033[36m", "\033[39m", "\033[31m", "\033[90m"):
        plain = plain.replace(code, "")
    assert (
        f"│\n◆  {SOURCE} → {BACKUP_GROUP}/{BASE_NAME}"
        f"\n│\n◆  {SOURCE_B} → {BACKUP_GROUP}/{BASE_NAME_B}"
        "\n│\n◆  Backup summary"
    ) in plain


def test_run_backup_batch_returns_zero_and_records_both_timestamps_on_success(
    tmp_path: Path,
) -> None:
    seed_repos(resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}), [SOURCE])
    h = make_harness(tmp_path)
    assert run_backup_batch([SOURCE], context=h.context) == 0
    repo = read_disk(h.paths)["repos"][0]
    assert repo["lastBackupAt"] == "2026-08-08T12:00:00.000Z"
    assert repo["lastCheckedAt"] == "2026-08-08T12:00:00.000Z"
    assert f"ok  {SOURCE}" in h.ui.items
    assert f"→ {project_web_url(BACKUP_GROUP, BASE_NAME)}" in h.ui.items


def test_run_backup_batch_records_timestamp_only_after_each_success(
    tmp_path: Path,
) -> None:
    seed_repos(resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}), [SOURCE, SOURCE_B])
    h = make_harness(
        tmp_path,
        project_exists=lambda _group, name: ExistsResult(
            False, error="project lookup failed"
        )
        if name == BASE_NAME
        else ExistsResult(True),
    )
    assert run_backup_batch([SOURCE, SOURCE_B], context=h.context) == 1
    first, second = read_disk(h.paths)["repos"]
    assert first["lastBackupAt"] is None
    assert second["lastBackupAt"] == "2026-08-08T12:00:00.000Z"


def test_run_backup_batch_success_metadata_failure_counts_as_fail(
    tmp_path: Path,
) -> None:
    seed_repos(resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}), [SOURCE])
    h = make_harness(
        tmp_path,
        record_last_backup_at=lambda *_args, **_kwargs: type(
            "Result", (), {"ok": False, "error": "disk full"}
        )(),
    )
    assert run_backup_batch([SOURCE], context=h.context) == 1
    assert "Backup succeeded but failed to save lastBackupAt: disk full" in "\n".join(
        h.ui.items
    )


def test_run_backup_batch_dry_run_skip_never_writes_timestamps(tmp_path: Path) -> None:
    seed_repos(
        resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}),
        [
            {
                "url": SOURCE,
                "lastBackupAt": "2020-01-01T00:00:00.000Z",
                "lastCheckedAt": None,
            }
        ],
    )

    def git(args, **_kwargs):
        if args[0] == "ls-remote":
            return CommandResult(0, "abc\trefs/heads/main\n")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=git,
    )
    assert run_backup_batch([SOURCE], context=h.context, dry_run=True) == 0
    repo = read_disk(h.paths)["repos"][0]
    assert repo["lastBackupAt"] == "2020-01-01T00:00:00.000Z"
    assert repo["lastCheckedAt"] is None
    assert "Dry run (no changes)" in h.ui.statuses
    assert "→ would skip (unchanged)" in h.ui.items


def test_backup_one_repo_dry_run_equal_fingerprints_skips_without_mutation(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def git(args, **_kwargs):
        calls.append(args[0])
        if args[0] == "ls-remote":
            return CommandResult(0, "abc\trefs/heads/main\n")
        return CommandResult(0)

    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        create_private_project=lambda *_args: pytest.fail("must not create"),
        run_git=git,
    )
    result = backup_one_repo(SOURCE, dry_run=True, context=h.context)
    assert result.status == "skip"
    assert "clone" not in calls
    assert h.removed == []
    assert "Would skip (unchanged)" in h.ui.statuses


@pytest.mark.parametrize("inactive", [False, True])
def test_backup_one_repo_dry_run_missing_or_inactive_plans_without_mutation(
    tmp_path: Path, inactive: bool
) -> None:
    calls: list[str] = []
    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, inactive=inactive),
        create_private_project=lambda *_args: pytest.fail("must not create"),
        run_git=lambda args, **_kwargs: calls.append(args[0]) or CommandResult(0),
    )
    result = backup_one_repo(SOURCE, dry_run=True, context=h.context)
    assert result.status == "ok"
    assert result.destination_url == project_web_url(BACKUP_GROUP, BASE_NAME)
    assert all(call not in {"clone", "push"} for call in calls)
    expected = "Would recreate inactive backup" if inactive else "Would create"
    assert expected in "\n".join(h.ui.statuses)
    assert "Would mirror" in h.ui.statuses


def test_backup_one_repo_dry_run_checks_group_without_ensuring_it(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    h = make_harness(
        tmp_path,
        group_exists=lambda _group: calls.append("probe")
        or ExistsResult(True, exists=True),
        ensure_backup_group=lambda _group: pytest.fail("must not ensure"),
    )
    assert backup_one_repo(SOURCE, dry_run=True, context=h.context).status == "ok"
    assert calls == ["probe"]


def test_backup_one_repo_dry_run_probe_failure_is_reported(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        group_exists=lambda _group: ExistsResult(False, error="group denied"),
    )
    result = backup_one_repo(SOURCE, dry_run=True, context=h.context)
    assert result == BackupResult("fail", SOURCE, None, "group denied")


def test_run_backup_command_interactive_passes_remembered_initial_selection(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(
        paths,
        [
            {"url": SOURCE, "selectedLast": True},
            {"url": SOURCE_B, "selectedLast": False},
        ],
    )
    captured: dict[str, object] = {}

    def selector(items, *, initial, render, **_kwargs):
        captured["items"] = tuple(items)
        captured["initial"] = tuple(initial)
        render(SelectorState(tuple(items), 0, frozenset({0})))
        return SelectorResult(
            "submit", SelectorState(tuple(items), 0, frozenset({0})), (SOURCE,)
        )

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command([], context=h.context) == 0
    assert captured["initial"] == (SOURCE,)
    items = captured["items"]
    assert items[0].label == SOURCE
    assert items[0].value == SOURCE


def test_run_backup_command_interactive_passes_repo_timestamps_to_selector(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(
        paths,
        [
            {
                "url": SOURCE,
                "lastBackupAt": "2026-08-01T00:00:00.000Z",
                "lastCheckedAt": "2026-08-08T11:00:00.000Z",
            }
        ],
    )
    captured: list[object] = []

    def selector(items, **_kwargs):
        captured.extend(items)
        state = SelectorState(tuple(items), 0, frozenset({0}))
        return SelectorResult("submit", state, (SOURCE,))

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command([], context=h.context) == 0
    assert captured[0].last_backup_at == "2026-08-01T00:00:00.000Z"
    assert captured[0].last_checked_at == "2026-08-08T11:00:00.000Z"


def test_run_backup_command_submit_persists_selection_before_batch(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE, SOURCE_B])
    order: list[str] = []

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 1, frozenset({1}))
        return SelectorResult("submit", state, (SOURCE_B,))

    def create(_group, name):
        order.append(f"backup:{name}")
        document = read_disk(paths)
        assert document["repos"][1]["selectedLast"] is True
        return ActionResult(True)

    h = make_harness(
        tmp_path,
        run_selector=selector,
        create_private_project=create,
    )
    assert run_backup_command([], context=h.context) == 0
    repos = read_disk(paths)["repos"]
    assert [repo["selectedLast"] for repo in repos] == [False, True]
    assert order == [f"backup:{BASE_NAME_B}"]


def test_run_backup_command_selection_write_failure_skips_batch(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE])

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 0, frozenset({0}))
        return SelectorResult("submit", state, (SOURCE,))

    h = make_harness(
        tmp_path,
        run_selector=selector,
        set_selected_last=lambda *_args: type(
            "Result", (), {"ok": False, "error": "disk full"}
        )(),
    )
    assert run_backup_command([], context=h.context) == 1
    assert h.ui.errors == ["Failed to save selection: disk full"]
    assert h.created == []
    assert "Backup summary" not in "\n".join(h.ui.statuses)


@pytest.mark.parametrize(
    ("args", "heading"),
    [([], "Select repos to backup"), (["stale"], "Select stale repos to backup")],
)
def test_run_backup_command_cancel_redraws_selector(
    tmp_path: Path, args: list[str], heading: str
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE, SOURCE_B])

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 0, frozenset())
        return SelectorResult("cancel", state, ())

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command(args, context=h.context) == 1
    assert len(h.ui.cancelled_calls) == 1
    called_heading, state, list_path = h.ui.cancelled_calls[0]
    assert called_heading == heading
    assert len(state.items) == 2
    assert str(list_path).endswith("backups.json")


def test_run_backup_command_cancel_does_not_change_selected_last(tmp_path: Path) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [{"url": SOURCE, "selectedLast": True}])

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 0, frozenset())
        return SelectorResult("cancel", state, ())

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command([], context=h.context) == 1
    assert read_disk(paths)["repos"][0]["selectedLast"] is True


def test_run_backup_command_empty_submit_does_not_change_selected_last(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [{"url": SOURCE, "selectedLast": True}])

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 0, frozenset())
        return SelectorResult("submit", state, ())

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command([], context=h.context) == 1
    assert h.ui.errors == ["No repos selected"]
    assert h.ui.titles == []
    assert read_disk(paths)["repos"][0]["selectedLast"] is True


def test_run_backup_command_all_leaves_selected_last_unchanged(tmp_path: Path) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [{"url": SOURCE, "selectedLast": True}])
    h = make_harness(tmp_path)
    assert run_backup_command(["--all"], context=h.context) == 0
    assert read_disk(paths)["repos"][0]["selectedLast"] is True


def test_run_backup_command_all_migrates_v1_list_on_load(tmp_path: Path) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE], version=1)
    h = make_harness(tmp_path)
    assert run_backup_command(["--all"], context=h.context) == 0
    document = read_disk(paths)
    assert document["version"] == 4
    assert document["repos"][0] == {
        "url": SOURCE,
        "lastBackupAt": "2026-08-08T12:00:00.000Z",
        "lastCheckedAt": "2026-08-08T12:00:00.000Z",
        "selectedLast": False,
    }


def test_run_backup_command_add_then_all_backs_up_listed_repo(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["add", SOURCE], context=h.context) == 0
    assert "Added git@github.com:org/app.git at index 1" in h.ui.statuses
    assert h.ui.items[-1].endswith("gt/backups.json")
    h.ui.events.clear()
    assert run_backup_command(["--all"], context=h.context) == 0
    assert_backup_frame_before_repo(h.ui)
    assert f"ok  {SOURCE}" in h.ui.items


def test_run_backup_command_add_and_remove_use_home_relative_list_path(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    env = {"HOME": str(home)}
    paths = resolve_gt_paths(env)
    h = make_harness(tmp_path, env=env, paths=paths)
    assert run_backup_command(["add", SOURCE], context=h.context) == 0
    assert h.ui.items[-1] == (
        "~/Library/Mobile Documents/com~apple~CloudDocs/Backups/"
        "cloud-utils/gt/backups.json"
    )
    assert run_backup_command(["remove", "1"], context=h.context) == 0
    assert h.ui.items[-1].startswith("~/Library/")


def test_run_backup_command_remove_by_one_based_index(tmp_path: Path) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE, SOURCE_B])
    h = make_harness(tmp_path)
    assert run_backup_command(["remove", "1"], context=h.context) == 0
    assert h.ui.titles == ["REPO BACKUP"]
    assert "Remove repository" in h.ui.statuses
    assert f"Removed {SOURCE}" in h.ui.statuses
    assert [repo["url"] for repo in read_disk(paths)["repos"]] == [SOURCE_B]


def test_run_backup_command_interactive_backs_up_only_selected_repo(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE, SOURCE_B])

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 1, frozenset({1}))
        return SelectorResult("submit", state, (SOURCE_B,))

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command([], context=h.context) == 0
    assert f"ok  {SOURCE_B}" in h.ui.items
    assert f"ok  {SOURCE}" not in h.ui.items
    assert_backup_frame_before_repo(h.ui)


def test_run_backup_command_cancel_exits_without_starting_batch(tmp_path: Path) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE])

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 0, frozenset())
        return SelectorResult("cancel", state, ())

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command([], context=h.context) == 1
    assert h.created == []
    assert h.ui.titles == []
    assert h.ui.items == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "Use `gt backup --all`"),
        (["stale"], "Use `gt backup stale --all`"),
    ],
)
def test_run_backup_command_interactive_requires_tty(
    tmp_path: Path, args: list[str], message: str
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE])
    h = make_harness(tmp_path, stdin=InputStub(False))
    assert run_backup_command(args, context=h.context) == 1
    assert "terminal" in h.ui.errors[0].lower()
    assert message in h.ui.errors[0]


def test_run_backup_command_add_without_urls_shows_usage(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["add"], context=h.context) == 1
    assert h.ui.errors == ["Usage: gt backup add <ssh-url> [<ssh-url> ...]"]


def test_run_backup_command_add_multiple_valid_urls(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["add", SOURCE, SOURCE_B], context=h.context) == 0
    assert h.ui.titles == ["REPO BACKUP"]
    assert "Add repositories" in h.ui.statuses
    assert f"Added {SOURCE} at index 1" in h.ui.statuses
    assert f"Added {SOURCE_B} at index 2" in h.ui.statuses
    assert len(read_disk(h.paths)["repos"]) == 2
    assert len(h.ui.ends) == 1


def test_run_backup_command_add_partial_success_persists_valid_url(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["add", SOURCE, SOURCE], context=h.context) == 1
    assert f"Added {SOURCE} at index 1" in h.ui.statuses
    assert any("Duplicate" in error for error in h.ui.errors)
    assert [repo["url"] for repo in read_disk(h.paths)["repos"]] == [SOURCE]


def test_run_backup_command_add_all_invalid_leaves_file_missing(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path)
    assert (
        run_backup_command(
            ["add", "https://github.com/a.git", "not-a-url"],
            context=h.context,
        )
        == 1
    )
    assert len(h.ui.errors) == 2
    assert h.ui.statuses == []
    assert not h.paths.backups_file.exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["add", SOURCE, "--force"], "The --force flag is only valid"),
        (["add", SOURCE, "--dry-run"], "The --dry-run flag is only valid"),
        (["remove", "1", "--force"], "The --force flag is only valid"),
        (["remove", "1", "--dry-run"], "The --dry-run flag is only valid"),
    ],
)
def test_run_backup_command_rejects_batch_only_flags_on_add_remove(
    tmp_path: Path, args: list[str], message: str
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE])
    h = make_harness(tmp_path)
    assert run_backup_command(args, context=h.context) == 1
    assert message in h.ui.errors[0]


@pytest.mark.parametrize(
    "args",
    [
        ["--all", "--force", "--dry-run"],
        ["--force", "--dry-run"],
        ["stale", "--all", "--force", "--dry-run"],
    ],
)
def test_run_backup_command_rejects_force_and_dry_run_together(
    tmp_path: Path, args: list[str]
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE])
    h = make_harness(tmp_path)
    assert run_backup_command(args, context=h.context) == 1
    assert h.ui.errors == ["Cannot combine --force and --dry-run"]


def test_run_backup_command_unknown_flag_errors_before_loading_list(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["--new"], context=h.context) == 1
    assert h.ui.errors == ["Unknown flag: --new"]


def test_run_backup_command_preserves_double_dash_as_an_unknown_flag(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["--"], context=h.context) == 1
    assert h.ui.errors == ["Unknown flag: --"]


def test_run_backup_command_add_preserves_double_dash_as_invalid_url(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["add", "--", SOURCE], context=h.context) == 1
    assert any(error.startswith("--: Invalid SSH URL") for error in h.ui.errors)
    assert [repo["url"] for repo in read_disk(h.paths)["repos"]] == [SOURCE]


def test_run_backup_command_rejects_retired_one_shot_url(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command([SOURCE], context=h.context) == 1
    assert h.ui.errors == [
        "gt backup one-shot URL is retired; use gt backup add / gt backup / gt backup --all"
    ]


@pytest.mark.parametrize("missing", [True, False])
def test_run_backup_command_missing_or_empty_list_includes_add_hint(
    tmp_path: Path, missing: bool
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    if not missing:
        seed_repos(paths, [])
    h = make_harness(tmp_path)
    assert run_backup_command(["--all"], context=h.context) == 1
    assert "gt backup add" in h.ui.errors[0]
    assert ("list found" in h.ui.errors[0].lower()) is missing


def test_run_backup_command_all_force_skips_fingerprints_and_updates_timestamps(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(
        paths,
        [{"url": SOURCE, "lastBackupAt": "2020-01-01T00:00:00.000Z"}],
    )
    calls: list[str] = []
    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=lambda args, **_kwargs: calls.append(args[0]) or CommandResult(0),
    )
    assert run_backup_command(["--all", "--force"], context=h.context) == 0
    assert "ls-remote" not in calls
    assert "clone" in calls
    repo = read_disk(paths)["repos"][0]
    assert repo["lastBackupAt"] == "2026-08-08T12:00:00.000Z"
    assert repo["lastCheckedAt"] == "2026-08-08T12:00:00.000Z"


def test_run_backup_command_all_dry_run_renders_plan_without_writes(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE])
    h = make_harness(tmp_path)
    before = paths.backups_file.read_bytes()
    assert run_backup_command(["--all", "--dry-run"], context=h.context) == 0
    assert_backup_frame_before_repo(h.ui, dry_run=True)
    assert f"ok  {SOURCE}" in h.ui.items
    assert "→ would mirror" in h.ui.items
    assert paths.backups_file.read_bytes() == before


def test_run_backup_command_v1_dry_run_does_not_persist_migration(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE], version=1)
    before = paths.backups_file.read_bytes()
    h = make_harness(tmp_path)
    assert run_backup_command(["--all", "--dry-run"], context=h.context) == 0
    assert paths.backups_file.read_bytes() == before


def test_run_backup_command_v1_cancel_does_not_persist_migration(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE], version=1)
    before = paths.backups_file.read_bytes()

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 0, frozenset())
        return SelectorResult("cancel", state, ())

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command([], context=h.context) == 1
    assert paths.backups_file.read_bytes() == before


def test_run_backup_command_reports_invalid_timestamp_before_external_calls(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    paths.gt_dir.mkdir(parents=True)
    paths.backups_file.write_text(
        json.dumps(
            {
                "version": 4,
                "repos": [
                    {
                        "url": SOURCE,
                        "lastBackupAt": "not-a-date",
                        "lastCheckedAt": None,
                        "selectedLast": False,
                    }
                ],
            }
        )
        + "\n"
    )
    h = make_harness(tmp_path)
    assert run_backup_command(["--all"], context=h.context) == 1
    assert h.ui.errors == [f"Invalid lastBackupAt for {SOURCE}: \"not-a-date\""]
    assert h.created == []


def test_run_backup_command_interactive_dry_run_does_not_persist_selection(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE, SOURCE_B])

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 1, frozenset({1}))
        return SelectorResult("submit", state, (SOURCE_B,))

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command(["--dry-run"], context=h.context) == 0
    assert [repo["selectedLast"] for repo in read_disk(paths)["repos"]] == [
        False,
        False,
    ]


def test_run_backup_command_stale_empty_set_prints_muted_absent_item(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(
        paths,
        [
            {"url": SOURCE, "lastCheckedAt": RECENT_CHECKED},
            {"url": SOURCE_B, "lastCheckedAt": RECENT_CHECKED},
        ],
    )
    stdout = io.StringIO()
    ui = GitToolsUi(stdout, io.StringIO())
    h = make_harness(tmp_path, ui=ui)
    assert run_backup_command(["stale"], context=h.context) == 0
    assert "\033[90m□\033[39m No stale repos" in stdout.getvalue()
    assert "REPO BACKUP" in stdout.getvalue()


def test_run_backup_command_stale_all_preserves_full_list_order(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(
        paths,
        [
            {"url": SOURCE, "lastCheckedAt": None},
            {"url": SOURCE_B, "lastCheckedAt": RECENT_CHECKED},
        ],
    )
    recorded: list[str] = []
    h = make_harness(
        tmp_path,
        record_last_backup_at=lambda _paths, url, **_kwargs: recorded.append(url)
        or type("Result", (), {"ok": True, "error": None})(),
    )
    assert run_backup_command(["stale", "--all"], context=h.context) == 0
    assert recorded == [SOURCE]
    assert f"ok  {SOURCE}" in h.ui.items
    assert f"ok  {SOURCE_B}" not in h.ui.items


def test_run_backup_command_stale_interactive_shows_only_stale_repos(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(
        paths,
        [
            {"url": SOURCE, "lastCheckedAt": None},
            {"url": SOURCE_B, "lastCheckedAt": RECENT_CHECKED},
        ],
    )
    captured: list[object] = []

    def selector(items, **_kwargs):
        captured.extend(items)
        state = SelectorState(tuple(items), 0, frozenset({0}))
        return SelectorResult("submit", state, (SOURCE,))

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command(["stale"], context=h.context) == 0
    assert [item.value for item in captured] == [SOURCE]


def test_run_backup_command_stale_submit_rewrites_selection_on_full_list(
    tmp_path: Path,
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(
        paths,
        [
            {"url": SOURCE, "lastCheckedAt": None, "selectedLast": False},
            {
                "url": SOURCE_B,
                "lastCheckedAt": RECENT_CHECKED,
                "selectedLast": True,
            },
        ],
    )

    def selector(items, **_kwargs):
        state = SelectorState(tuple(items), 0, frozenset({0}))
        return SelectorResult("submit", state, (SOURCE,))

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command(["stale"], context=h.context) == 0
    assert [repo["selectedLast"] for repo in read_disk(paths)["repos"]] == [
        True,
        False,
    ]


def test_run_backup_command_stale_days_changes_filtered_set(tmp_path: Path) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(
        paths,
        [
            {"url": SOURCE, "lastCheckedAt": "2026-08-07T11:00:00.000Z"},
            {"url": SOURCE_B, "lastCheckedAt": RECENT_CHECKED},
        ],
    )
    captured: list[str] = []

    def selector(items, **_kwargs):
        captured.extend(item.value for item in items)
        state = SelectorState(tuple(items), 0, frozenset())
        return SelectorResult("cancel", state, ())

    h = make_harness(tmp_path, run_selector=selector)
    assert run_backup_command(["stale", "--days", "1"], context=h.context) == 1
    assert captured == [SOURCE]


@pytest.mark.parametrize("value", ["abc", "0", "1.5", "-1"])
def test_run_backup_command_stale_invalid_days_errors(
    tmp_path: Path, value: str
) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["stale", "--days", value], context=h.context) == 1
    if value == "-1":
        assert h.ui.errors == [
            "Usage: gt backup stale [--days <n>] [--all] [-f|--force] [--dry-run]"
        ]
    else:
        assert h.ui.errors == [
            f"Invalid --days value: {value} (must be a positive integer)"
        ]


@pytest.mark.parametrize("value", ["1e3", "0x10", "+1", " 1 "])
def test_run_backup_command_stale_days_accepts_js_number_integer_forms(
    tmp_path: Path, value: str
) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [{"url": SOURCE, "lastCheckedAt": RECENT_CHECKED}])
    h = make_harness(tmp_path)
    assert run_backup_command(["stale", "--days", value], context=h.context) == 0
    assert "No stale repos" in h.ui.statuses


def test_run_backup_command_stale_days_rejects_huge_number_without_crashing(
    tmp_path: Path,
) -> None:
    value = "9" * 10_000
    h = make_harness(tmp_path)
    assert run_backup_command(["stale", "--days", value], context=h.context) == 1
    assert h.ui.errors == [
        f"Invalid --days value: {value} (must be a positive integer)"
    ]


@pytest.mark.parametrize("args", [["stale", "--days"], ["stale", "repo"]])
def test_run_backup_command_stale_malformed_usage(
    tmp_path: Path, args: list[str]
) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(args, context=h.context) == 1
    assert h.ui.errors == [
        "Usage: gt backup stale [--days <n>] [--all] [-f|--force] [--dry-run]"
    ]


def test_run_backup_command_stale_unknown_flag_errors(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    assert run_backup_command(["stale", "--nope"], context=h.context) == 1
    assert h.ui.errors == ["Unknown flag: --nope"]


def test_run_backup_command_stale_all_force_skips_fingerprints(tmp_path: Path) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE])
    calls: list[str] = []
    h = make_harness(
        tmp_path,
        project_exists=lambda *_args: ExistsResult(True, exists=True),
        run_git=lambda args, **_kwargs: calls.append(args[0]) or CommandResult(0),
    )
    assert (
        run_backup_command(["stale", "--all", "--force"], context=h.context)
        == 0
    )
    assert "ls-remote" not in calls
    assert "clone" in calls


def test_run_backup_command_stale_all_dry_run_has_no_writes(tmp_path: Path) -> None:
    paths = resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)})
    seed_repos(paths, [SOURCE])
    before = paths.backups_file.read_bytes()
    h = make_harness(tmp_path)
    assert (
        run_backup_command(["stale", "--all", "--dry-run"], context=h.context)
        == 0
    )
    assert_backup_frame_before_repo(h.ui, dry_run=True)
    assert paths.backups_file.read_bytes() == before
