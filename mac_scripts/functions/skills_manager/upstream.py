from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from shared.process import CommandResult, run_process

from .planner import js_string_key

if TYPE_CHECKING:
    from .planner import InstallPlan, UninstallPlan

class UpstreamError(RuntimeError): ...
class DiscoveryParseError(UpstreamError): ...

@dataclass(frozen=True, slots=True)
class AvailableSkill:
    name: str
    description: str
@dataclass(frozen=True, slots=True)
class InstalledRecord:
    name: str
    path: str
    scope: str
    agents: tuple[str, ...]
@dataclass(frozen=True, slots=True)
class MutationRecord:
    action: Literal["install", "uninstall"]
    source: str | None
    skills: tuple[str, ...]
    status: int
@dataclass(frozen=True, slots=True)
class ExecutionResult:
    ok: bool
    succeeded: tuple[MutationRecord, ...]
    failed: tuple[MutationRecord, ...]

Runner = Callable[..., CommandResult]
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_NAME = re.compile(r"^│\s{4}(\S.*)$")
_DESCRIPTION = re.compile(r"^│\s{6}(\S.*)$")
_BLANK = re.compile(r"^│\s*$")

class _DefaultProcessValueError(Exception): ...
def _default_process_runner(
    argv: Sequence[str], *, cwd: str | Path, capture: bool,
    env: Mapping[str, str] | None = None) -> CommandResult:
    try:
        return run_process(argv, cwd=cwd, capture=capture, env=env)
    except ValueError as error:
        raise _DefaultProcessValueError from error
def has_command(name: str, *, env: Mapping[str, str]) -> bool:
    return any(
        directory and os.access(Path(directory) / name, os.X_OK)
        for directory in env.get("PATH", "").split(os.pathsep))

def parse_available_skills(output: str) -> tuple[AvailableSkill, ...]:
    lines = _ANSI.sub("", output).splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if "Available Skills" in line)
        end = next(
            i for i, line in enumerate(lines)
            if i > start and "Use --skill" in line)
    except StopIteration as error:
        raise DiscoveryParseError("Could not parse available skills") from error
    skills: list[AvailableSkill] = []
    index = start + 1
    while index < end:
        match = _NAME.match(lines[index])
        if match is None:
            index += 1
            continue
        name = match.group(1).strip()
        index += 1
        while index < end and _BLANK.match(lines[index]):
            index += 1
        description = _DESCRIPTION.match(lines[index]) if index < end else None
        if description is None:
            raise DiscoveryParseError(f"Missing description for skill: {name}")
        skills.append(AvailableSkill(name, description.group(1).strip()))
        index += 1
    if not skills:
        raise DiscoveryParseError("No skill records found")
    if len({skill.name for skill in skills}) != len(skills):
        raise DiscoveryParseError("Duplicate skill names in discovery output")
    return tuple(skills)
def _checked(argv, *, cwd, runner: Runner, message: str) -> CommandResult:
    try:
        result = runner(argv, cwd=cwd, capture=True)
    except (OSError, _DefaultProcessValueError) as error:
        cause = error.__cause__ or error
        raise UpstreamError(message) from cause
    if result.returncode != 0:
        raise UpstreamError(message)
    return result
def discover_available_skills(
    source: str, *, cwd: str | Path, runner: Runner = _default_process_runner,
) -> tuple[AvailableSkill, ...]:
    result = _checked(
        ("npx", "skills", "add", source, "--list"), cwd=cwd, runner=runner,
        message="Could not discover available skills")
    return parse_available_skills(result.stdout)

def _installed_record(value: object) -> InstalledRecord | None:
    if not isinstance(value, dict):
        return None
    name, path = value.get("name"), value.get("path")
    scope, agents = value.get("scope"), value.get("agents")
    valid = (
        isinstance(name, str) and isinstance(path, str)
        and isinstance(scope, str) and isinstance(agents, list)
        and all(isinstance(agent, str) for agent in agents)
    )
    return InstalledRecord(name, path, scope, tuple(agents)) if valid else None
def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")
def list_installed_skills(
    *, cwd: str | Path, runner: Runner = _default_process_runner,
) -> tuple[InstalledRecord, ...]:
    result = _checked(
        ("npx", "skills", "list", "--json"), cwd=cwd, runner=runner,
        message="Could not list installed skills")
    try:
        values = json.loads(result.stdout, parse_constant=_reject_json_constant)
    except (ValueError, UnicodeError) as error:
        raise UpstreamError("Could not parse installed skills") from error
    if not isinstance(values, list):
        raise UpstreamError("Invalid installed skills output")
    records = tuple(_installed_record(value) for value in values)
    if any(record is None for record in records):
        raise UpstreamError("Invalid installed skills output")
    return tuple(record for record in records if record is not None)
def run_skills_mutation(
    args: Sequence[str], *, cwd: str | Path,
    env: Mapping[str, str] | None = None, runner: Runner = _default_process_runner,
) -> int:
    try:
        result = runner(("npx", *args), cwd=cwd, env=env, capture=False)
    except (OSError, _DefaultProcessValueError):
        return 1
    return result.returncode if result.returncode >= 0 else 1
def _result(records: Sequence[MutationRecord], conflicts: bool) -> ExecutionResult:
    failed = tuple(record for record in records if record.status != 0)
    succeeded = tuple(record for record in records if record.status == 0)
    return ExecutionResult(not failed and not conflicts, succeeded, failed)
def execute_install_plan(
    plan: InstallPlan, *, yes: bool = False, project_root: str | Path,
    run_mutation: Callable[..., int] = run_skills_mutation,
    on_event: Callable[[MutationRecord], object] | None = None) -> ExecutionResult:
    if plan.desired_conflicts:
        return ExecutionResult(False, (), ())
    groups: dict[bytes, tuple[str, list[str]]] = {}
    for item in plan.install:
        group = groups.setdefault(js_string_key(item.source), (item.source, []))
        group[1].append(item.skill)
    records = []
    for source, skills in groups.values():
        args = ["skills", "add", source]
        for skill in skills:
            args.extend(("--skill", skill))
        if yes:
            args.append("--yes")
        record = MutationRecord(
            "install", source, tuple(skills),
            run_mutation(args, cwd=project_root))
        records.append(record)
        if on_event is not None:
            on_event(record)
    return _result(records, bool(plan.conflicts))
def execute_uninstall_plan(
    plan: UninstallPlan, *, yes: bool = False, project_root: str | Path,
    run_mutation: Callable[..., int] = run_skills_mutation,
    on_event: Callable[[MutationRecord], object] | None = None) -> ExecutionResult:
    if plan.desired_conflicts:
        return ExecutionResult(False, (), ())
    skills = tuple(item.skill for item in plan.remove)
    if not skills:
        return ExecutionResult(not plan.conflicts, (), ())
    args = ["skills", "remove", *skills]
    if yes:
        args.append("--yes")
    record = MutationRecord(
        "uninstall", None, skills, run_mutation(args, cwd=project_root)
    )
    if on_event is not None:
        on_event(record)
    return _result((record,), bool(plan.conflicts))
