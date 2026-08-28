from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from shared.process import CommandResult, run_process


class UpstreamError(RuntimeError):
    """Raised when the external skills CLI breaks its adapter contract."""


class DiscoveryParseError(UpstreamError):
    """Raised when available-skill output cannot be parsed confidently."""


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


Runner = Callable[..., CommandResult]
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_NAME = re.compile(r"^│\s{4}(\S.*)$")
_DESCRIPTION = re.compile(r"^│\s{6}(\S.*)$")
_BLANK = re.compile(r"^│\s*$")


class _DefaultProcessValueError(Exception):
    def __init__(self, cause: ValueError) -> None:
        super().__init__(str(cause))
        self.cause = cause


def _default_process_runner(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    capture: bool,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    try:
        return run_process(argv, cwd=cwd, capture=capture, env=env)
    except ValueError as error:
        raise _DefaultProcessValueError(error) from error


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def has_command(name: str, *, env: Mapping[str, str]) -> bool:
    for directory in env.get("PATH", "").split(os.pathsep):
        if directory and os.access(Path(directory) / name, os.X_OK):
            return True
    return False


def parse_available_skills(output: str) -> tuple[AvailableSkill, ...]:
    lines = _ANSI.sub("", output).splitlines()
    start = next(
        (index for index, line in enumerate(lines) if "Available Skills" in line),
        -1,
    )
    end = next(
        (
            index
            for index, line in enumerate(lines)
            if index > start and "Use --skill" in line
        ),
        -1,
    )
    if start < 0 or end < 0:
        raise DiscoveryParseError("Could not parse available skills")

    result: list[AvailableSkill] = []
    index = start + 1
    while index < end:
        name_match = _NAME.match(lines[index])
        if name_match is None:
            index += 1
            continue
        name = name_match.group(1).strip()
        description_index = index + 1
        while description_index < end and _BLANK.match(lines[description_index]):
            description_index += 1
        description_match = (
            _DESCRIPTION.match(lines[description_index])
            if description_index < end
            else None
        )
        if description_match is None:
            raise DiscoveryParseError(f"Missing description for skill: {name}")
        result.append(
            AvailableSkill(name, description_match.group(1).strip())
        )
        index = description_index + 1

    if not result:
        raise DiscoveryParseError("No skill records found")
    if len({skill.name for skill in result}) != len(result):
        raise DiscoveryParseError("Duplicate skill names in discovery output")
    return tuple(result)


def discover_available_skills(
    source: str,
    *,
    cwd: str | Path,
    runner: Runner = _default_process_runner,
) -> tuple[AvailableSkill, ...]:
    try:
        result = runner(
            ("npx", "skills", "add", source, "--list"),
            cwd=cwd,
            capture=True,
        )
    except _DefaultProcessValueError as error:
        raise UpstreamError("Could not discover available skills") from error.cause
    except OSError as error:
        raise UpstreamError("Could not discover available skills") from error
    if result.returncode != 0:
        raise UpstreamError("Could not discover available skills")
    return parse_available_skills(result.stdout)


def _installed_record(value: object) -> InstalledRecord | None:
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    path = value.get("path")
    scope = value.get("scope")
    agents = value.get("agents")
    if not (
        isinstance(name, str)
        and isinstance(path, str)
        and isinstance(scope, str)
        and isinstance(agents, list)
        and all(isinstance(agent, str) for agent in agents)
    ):
        return None
    return InstalledRecord(name, path, scope, tuple(agents))


def list_installed_skills(
    *,
    cwd: str | Path,
    runner: Runner = _default_process_runner,
) -> tuple[InstalledRecord, ...]:
    try:
        result = runner(
            ("npx", "skills", "list", "--json"),
            cwd=cwd,
            capture=True,
        )
    except _DefaultProcessValueError as error:
        raise UpstreamError("Could not list installed skills") from error.cause
    except OSError as error:
        raise UpstreamError("Could not list installed skills") from error
    if result.returncode != 0:
        raise UpstreamError("Could not list installed skills")
    try:
        values = json.loads(
            result.stdout,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, UnicodeError) as error:
        raise UpstreamError("Could not parse installed skills") from error
    if not isinstance(values, list):
        raise UpstreamError("Invalid installed skills output")
    records = tuple(_installed_record(value) for value in values)
    if any(record is None for record in records):
        raise UpstreamError("Invalid installed skills output")
    return tuple(record for record in records if record is not None)


def run_skills_mutation(
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    runner: Runner = _default_process_runner,
) -> int:
    try:
        result = runner(
            ("npx", *args),
            cwd=cwd,
            env=env,
            capture=False,
        )
    except (OSError, _DefaultProcessValueError):
        return 1
    return result.returncode if result.returncode >= 0 else 1
