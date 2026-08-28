from __future__ import annotations

import json
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from .config import Catalog
from .state import InstalledSkill, InstalledState


@dataclass(frozen=True, slots=True)
class Requirement:
    key: str
    source: str
    skill: str


@dataclass(frozen=True, slots=True)
class DesiredConflict:
    skill: str
    sources: tuple[str, ...]
    profiles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MergedRequirements:
    requirements: tuple[Requirement, ...]
    desired_conflicts: tuple[DesiredConflict, ...]


@dataclass(frozen=True, slots=True)
class StatusResult:
    installed: tuple[Requirement, ...]
    missing: tuple[Requirement, ...]
    mismatches: tuple[Requirement, ...]
    untracked: tuple[Requirement, ...]
    extras: tuple[InstalledSkill, ...]
    desired_conflicts: tuple[DesiredConflict, ...]


@dataclass(frozen=True, slots=True)
class InstallPlan:
    install: tuple[Requirement, ...]
    skip: tuple[Requirement, ...]
    conflicts: tuple[Requirement, ...]
    extras: tuple[InstalledSkill, ...]
    desired_conflicts: tuple[DesiredConflict, ...]


@dataclass(frozen=True, slots=True)
class UninstallPlan:
    remove: tuple[Requirement, ...]
    retain: tuple[Requirement, ...]
    absent: tuple[Requirement, ...]
    conflicts: tuple[Requirement, ...]
    unlink_profiles: tuple[str, ...]
    desired_conflicts: tuple[DesiredConflict, ...]


def _collation_key(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def requirement_key(source: str, skill: str) -> str:
    return json.dumps([source, skill], separators=(",", ":"))


def catalog_requirements(document: Catalog) -> MergedRequirements:
    by_key: dict[str, Requirement] = {}
    by_skill: dict[str, dict[str, None]] = {}

    for entry in document.sources:
        for skill in entry.skills:
            key = requirement_key(entry.source, skill)
            if key not in by_key:
                by_key[key] = Requirement(key, entry.source, skill)
            sources = by_skill.setdefault(skill, {})
            sources.setdefault(entry.source, None)

    desired_conflicts = tuple(
        sorted(
            (
                DesiredConflict(skill, tuple(sorted(sources)))
                for skill, sources in by_skill.items()
                if len(sources) > 1
            ),
            key=lambda item: _collation_key(item.skill),
        )
    )
    requirements = tuple(
        sorted(by_key.values(), key=lambda item: _collation_key(item.skill))
    )
    return MergedRequirements(requirements, desired_conflicts)


def _is_untracked(actual: InstalledSkill) -> bool:
    return actual.source is None or actual.provenance == "untracked"


def classify_status(
    merge_result: MergedRequirements,
    installed_state: InstalledState,
) -> StatusResult:
    installed: list[Requirement] = []
    missing: list[Requirement] = []
    mismatches: list[Requirement] = []
    untracked: list[Requirement] = []
    ambiguous = {item.skill for item in merge_result.desired_conflicts}
    desired_names = {item.skill for item in merge_result.requirements}

    for requirement in merge_result.requirements:
        if requirement.skill in ambiguous:
            continue
        actual = installed_state.get(requirement.skill)
        if actual is None:
            missing.append(requirement)
        elif _is_untracked(actual):
            untracked.append(requirement)
        elif actual.source != requirement.source:
            mismatches.append(requirement)
        else:
            installed.append(requirement)

    extras = tuple(
        sorted(
            (
                actual
                for actual in installed_state.values()
                if actual.name not in desired_names
            ),
            key=lambda item: _collation_key(item.name),
        )
    )
    return StatusResult(
        tuple(installed),
        tuple(missing),
        tuple(mismatches),
        tuple(untracked),
        extras,
        merge_result.desired_conflicts,
    )


def create_install_plan(
    status_result: StatusResult,
    *,
    selected_keys: Collection[str] | None = None,
) -> InstallPlan:
    install = tuple(
        item
        for item in status_result.missing
        if selected_keys is None or item.key in selected_keys
    )
    return InstallPlan(
        install,
        status_result.installed,
        status_result.mismatches + status_result.untracked,
        status_result.extras,
        status_result.desired_conflicts,
    )


def _combine_desired_conflicts(
    selected: MergedRequirements,
    remaining: MergedRequirements,
) -> tuple[DesiredConflict, ...]:
    combined: list[DesiredConflict] = []
    seen: set[DesiredConflict] = set()
    for conflict in selected.desired_conflicts + remaining.desired_conflicts:
        if conflict not in seen:
            seen.add(conflict)
            combined.append(conflict)
    return tuple(combined)


def create_uninstall_plan(
    *,
    selected: MergedRequirements,
    remaining: MergedRequirements,
    installed_state: Mapping[str, InstalledSkill],
    linked_selected: Sequence[str],
) -> UninstallPlan:
    remove: list[Requirement] = []
    retain: list[Requirement] = []
    absent: list[Requirement] = []
    desired_conflicts = _combine_desired_conflicts(selected, remaining)
    ambiguous = {item.skill for item in desired_conflicts}
    remaining_keys = {item.key for item in remaining.requirements}

    for requirement in selected.requirements:
        if requirement.skill in ambiguous:
            continue
        if requirement.key in remaining_keys:
            retain.append(requirement)
        elif requirement.skill in installed_state:
            remove.append(requirement)
        else:
            absent.append(requirement)

    return UninstallPlan(
        tuple(remove),
        tuple(retain),
        tuple(absent),
        (),
        tuple(linked_selected),
        desired_conflicts,
    )
