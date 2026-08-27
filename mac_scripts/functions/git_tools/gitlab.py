from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import quote

from shared.process import CommandResult, run_process

from .git import run_git

BACKUP_GROUP = "anngth-dev/backups"
GITLAB_HOST = "gitlab.com"
_DELETION_PATH_RE = re.compile(r"-(?:deletion_scheduled|deleted)-\d+$")

@dataclass(frozen=True, slots=True)
class ReadyResult:
    ok: bool
    error: str | None = None

@dataclass(frozen=True, slots=True)
class ExistsResult:
    ok: bool
    exists: bool = False
    inactive: bool = False
    project: Mapping[str, object] | None = None
    error: str | None = None

@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    error: str | None = None
    stdout: str = ""
    stderr: str = ""

@dataclass(frozen=True, slots=True)
class EnsureGroupResult:
    ok: bool
    created: bool = False
    error: str | None = None

@dataclass(frozen=True, slots=True)
class NameResult:
    ok: bool
    name: str | None = None
    error: str | None = None

@dataclass(frozen=True, slots=True)
class ProtectResult:
    ok: bool
    already_protected: bool = False
    error: str | None = None

RunGlab = Callable[[list[str]], CommandResult]

def run_glab(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    runner=run_process,
) -> CommandResult:
    try:
        return runner(["glab", *args], cwd=cwd, env=env, capture=True)
    except OSError as error:
        return CommandResult(1, stderr=str(error))

def _result_error(result: CommandResult, fallback: str) -> str:
    return result.stderr.strip() or result.stdout.strip() or fallback

def _parse_strict_json(value: str) -> Any:
    def reject_constant(_constant: str) -> None:
        raise ValueError("nonstandard JSON constant")

    return json.loads(value, parse_constant=reject_constant)

def assert_glab_ready(
    *,
    has_command: Callable[[str], object] = shutil.which,
    run_glab_fn: RunGlab | None = None,
    env: Mapping[str, str] | None = None,
) -> ReadyResult:
    if not has_command("glab"):
        return ReadyResult(False, "glab is not installed or not available on PATH")

    glab = run_glab_fn or (lambda args: run_glab(args, env=env))
    result = glab(["auth", "status"])
    if result.returncode != 0:
        detail = _result_error(result, "not logged in")
        return ReadyResult(False, f"glab authentication is required: {detail}")
    return ReadyResult(True)

def _inactive_project(project: object, requested_name: str) -> bool:
    if not isinstance(project, Mapping):
        return False
    if project.get("marked_for_deletion_on") or project.get("marked_for_deletion_at"):
        return True
    path = project.get("path")
    return (
        isinstance(path, str)
        and path != requested_name
        and _DELETION_PATH_RE.search(path) is not None
    )

def project_exists(
    group: str,
    name: str,
    *,
    run_glab_fn: RunGlab = run_glab,
) -> ExistsResult:
    path = f"projects/{quote(f'{group}/{name}', safe='')}"
    result = run_glab_fn(["api", path])
    if result.returncode == 0:
        try:
            parsed = _parse_strict_json(result.stdout or "{}")
        except (ValueError, TypeError):
            return ExistsResult(False, error="could not parse GitLab project response")
        project = parsed if isinstance(parsed, Mapping) else None
        if _inactive_project(parsed, name):
            return ExistsResult(True, inactive=True, project=project)
        return ExistsResult(True, exists=True, project=project)

    detail = f"{result.stdout}\n{result.stderr}"
    if re.search(r"404|not found", detail, re.IGNORECASE):
        return ExistsResult(True)
    return ExistsResult(False, error=_result_error(result, "glab API request failed"))

def group_exists(
    group: str,
    *,
    run_glab_fn: RunGlab = run_glab,
) -> ExistsResult:
    result = run_glab_fn(["api", f"groups/{quote(group, safe='')}"])
    if result.returncode == 0:
        return ExistsResult(True, exists=True)
    detail = f"{result.stdout}\n{result.stderr}"
    if re.search(r"404|not found", detail, re.IGNORECASE):
        return ExistsResult(True)
    return ExistsResult(False, error=_result_error(result, "glab API request failed"))

def create_private_group(
    group: str,
    *,
    run_glab_fn: RunGlab = run_glab,
) -> ActionResult:
    if "/" not in group:
        result = run_glab_fn(
            [
                "api", "--method", "POST", "groups",
                "-f", f"name={group}",
                "-f", f"path={group}",
                "-f", "visibility=private",
            ]
        )
        if result.returncode != 0:
            return ActionResult(
                False,
                error=_result_error(result, "failed to create GitLab group"),
            )
        return ActionResult(True, stdout=result.stdout, stderr=result.stderr)

    parent_path, leaf = group.rsplit("/", 1)
    parent_result = run_glab_fn(
        ["api", f"groups/{quote(parent_path, safe='')}"]
    )
    if parent_result.returncode != 0:
        return ActionResult(
            False,
            error=_result_error(parent_result, f"parent group {parent_path} not found"),
        )
    try:
        parent = _parse_strict_json(parent_result.stdout)
    except (ValueError, TypeError):
        return ActionResult(
            False,
            error=f"could not parse parent group id for {parent_path}",
        )
    if parent is None:
        return ActionResult(
            False,
            error=f"could not parse parent group id for {parent_path}",
        )
    parent_id = parent.get("id") if isinstance(parent, Mapping) else None
    if parent_id is None:
        return ActionResult(False, error=f"parent group {parent_path} has no id")

    result = run_glab_fn(
        [
            "api", "--method", "POST", "groups",
            "-f", f"name={leaf}",
            "-f", f"path={leaf}",
            "-f", f"parent_id={parent_id}",
            "-f", "visibility=private",
        ]
    )
    if result.returncode != 0:
        return ActionResult(
            False,
            error=_result_error(result, "failed to create GitLab subgroup"),
        )
    return ActionResult(True, stdout=result.stdout, stderr=result.stderr)

def ensure_backup_group(
    group: str,
    *,
    run_glab_fn: RunGlab = run_glab,
    group_exists_fn: Callable[[str], ExistsResult] | None = None,
    create_private_group_fn: Callable[[str], ActionResult] | None = None,
) -> EnsureGroupResult:
    existing = (
        group_exists_fn(group)
        if group_exists_fn is not None
        else group_exists(group, run_glab_fn=run_glab_fn)
    )
    if not existing.ok:
        return EnsureGroupResult(
            False,
            error=existing.error or "could not check GitLab group",
        )
    if existing.exists:
        return EnsureGroupResult(True)

    created = (
        create_private_group_fn(group)
        if create_private_group_fn is not None
        else create_private_group(group, run_glab_fn=run_glab_fn)
    )
    if not created.ok:
        return EnsureGroupResult(
            False,
            error=created.error or "failed to create GitLab group",
        )
    return EnsureGroupResult(True, created=True)

def create_private_project(
    group: str,
    name: str,
    *,
    run_glab_fn: RunGlab = run_glab,
) -> ActionResult:
    result = run_glab_fn(
        [
            "repo", "create", name,
            "--group", group,
            "--private", "--skipGitInit",
        ]
    )
    if result.returncode != 0:
        return ActionResult(
            False,
            error=_result_error(result, "failed to create GitLab project"),
        )
    return ActionResult(True, stdout=result.stdout, stderr=result.stderr)

def _find_name(
    group: str,
    base_name: str,
    project_exists_fn: Callable[[str, str], ExistsResult],
    start: int,
) -> NameResult:
    suffix = start
    while True:
        name = base_name if suffix == 0 else f"{base_name}-{suffix}"
        result = project_exists_fn(group, name)
        if not result.ok:
            return NameResult(
                False,
                error=result.error or "could not check GitLab project",
            )
        if not result.exists:
            return NameResult(True, name=name)
        suffix += 1

def next_available_name(
    group: str,
    base_name: str,
    *,
    project_exists_fn: Callable[[str, str], ExistsResult] = project_exists,
) -> NameResult:
    return _find_name(group, base_name, project_exists_fn, 0)

def next_suffixed_name(
    group: str,
    base_name: str,
    *,
    project_exists_fn: Callable[[str, str], ExistsResult] = project_exists,
) -> NameResult:
    return _find_name(group, base_name, project_exists_fn, 2)

def project_ssh_url(group: str, name: str) -> str:
    return f"git@{GITLAB_HOST}:{group}/{name}.git"

def project_web_url(group: str, name: str) -> str:
    return f"https://{GITLAB_HOST}/{group}/{name}"

def pick_preferred_default_branch(
    repo_dir: str | Path,
    *,
    run_git_fn=run_git,
) -> str | None:
    for branch in ("main", "develop"):
        result = run_git_fn(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo_dir,
        )
        if result.returncode == 0:
            return branch
    return None

def set_default_branch(
    group: str,
    name: str,
    branch: str,
    *,
    run_glab_fn: RunGlab = run_glab,
) -> ActionResult:
    path = f"projects/{quote(f'{group}/{name}', safe='')}"
    result = run_glab_fn(
        ["api", "--method", "PUT", path, "-f", f"default_branch={branch}"]
    )
    if result.returncode != 0:
        return ActionResult(
            False,
            error=_result_error(result, "failed to set default branch"),
        )
    return ActionResult(True)

def protect_branch(
    group: str,
    name: str,
    branch: str,
    *,
    run_glab_fn: RunGlab = run_glab,
) -> ProtectResult:
    project = quote(f"{group}/{name}", safe="")
    path = f"projects/{project}/protected_branches"
    result = run_glab_fn(
        [
            "api", "--method", "POST", path,
            "-f", f"name={branch}",
            "-f", "push_access_level=40",
            "-f", "merge_access_level=40",
            "-f", "allow_force_push=true",
        ]
    )
    if result.returncode == 0:
        return ProtectResult(True)
    detail = f"{result.stdout}\n{result.stderr}"
    if re.search(r"already exists|already protected", detail, re.IGNORECASE):
        return ProtectResult(True, already_protected=True)
    return ProtectResult(
        False,
        error=_result_error(result, "failed to protect branch"),
    )
