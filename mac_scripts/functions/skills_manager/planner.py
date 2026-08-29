from __future__ import annotations

import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from .config import Catalog
from .source import js_string_key as _js_string_key
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
    identity_spelling = _js_string_key(value).decode(
        "utf-16-be", errors="surrogatepass"
    )
    return unicodedata.normalize("NFC", identity_spelling)


def _json_string(value: str) -> str:
    escapes = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
    }
    encoded = ['"']
    index = 0
    while index < len(value):
        character = value[index]
        code_point = ord(character)
        if character in escapes:
            encoded.append(escapes[character])
        elif code_point < 0x20:
            encoded.append(f"\\u{code_point:04x}")
        elif 0xD800 <= code_point <= 0xDBFF:
            if index + 1 < len(value):
                low = ord(value[index + 1])
                if 0xDC00 <= low <= 0xDFFF:
                    encoded.append(
                        chr(
                            0x10000
                            + ((code_point - 0xD800) << 10)
                            + low
                            - 0xDC00
                        )
                    )
                    index += 1
                else:
                    encoded.append(f"\\u{code_point:04x}")
            else:
                encoded.append(f"\\u{code_point:04x}")
        elif 0xDC00 <= code_point <= 0xDFFF:
            encoded.append(f"\\u{code_point:04x}")
        else:
            encoded.append(character)
        index += 1
    encoded.append('"')
    return "".join(encoded)


def requirement_key(source: str, skill: str) -> str:
    return f"[{_json_string(source)},{_json_string(skill)}]"


def catalog_requirements(document: Catalog) -> MergedRequirements:
    by_key: dict[str, Requirement] = {}
    skill_spellings: dict[bytes, str] = {}
    sources_by_skill: dict[bytes, dict[bytes, str]] = {}

    for entry in document.sources:
        for skill in entry.skills:
            key = requirement_key(entry.source, skill)
            if key not in by_key:
                by_key[key] = Requirement(key, entry.source, skill)
            skill_identity = _js_string_key(skill)
            skill_spellings.setdefault(skill_identity, skill)
            sources = sources_by_skill.setdefault(skill_identity, {})
            sources.setdefault(_js_string_key(entry.source), entry.source)

    desired_conflicts = tuple(
        sorted(
            (
                DesiredConflict(
                    skill_spellings[skill_identity],
                    tuple(sorted(sources.values(), key=_js_string_key)),
                )
                for skill_identity, sources in sources_by_skill.items()
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


def _installed_by_identity(
    installed_state: Mapping[str, InstalledSkill],
) -> dict[bytes, InstalledSkill]:
    installed: dict[bytes, InstalledSkill] = {}
    for name, actual in installed_state.items():
        installed[_js_string_key(name)] = actual
    return installed


def classify_status(
    merge_result: MergedRequirements,
    installed_state: InstalledState,
) -> StatusResult:
    installed: list[Requirement] = []
    missing: list[Requirement] = []
    mismatches: list[Requirement] = []
    untracked: list[Requirement] = []
    actual_by_name = _installed_by_identity(installed_state)
    ambiguous = {
        _js_string_key(item.skill) for item in merge_result.desired_conflicts
    }
    desired_names = {
        _js_string_key(item.skill) for item in merge_result.requirements
    }

    for requirement in merge_result.requirements:
        skill_identity = _js_string_key(requirement.skill)
        if skill_identity in ambiguous:
            continue
        actual = actual_by_name.get(skill_identity)
        if actual is None:
            missing.append(requirement)
        elif _is_untracked(actual):
            untracked.append(requirement)
        elif actual.source is None or (
            _js_string_key(actual.source) != _js_string_key(requirement.source)
        ):
            mismatches.append(requirement)
        else:
            installed.append(requirement)

    extras = tuple(
        sorted(
            (
                actual
                for actual in actual_by_name.values()
                if _js_string_key(actual.name) not in desired_names
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
    seen: set[
        tuple[bytes, tuple[bytes, ...], tuple[bytes, ...]]
    ] = set()
    for conflict in selected.desired_conflicts + remaining.desired_conflicts:
        identity = (
            _js_string_key(conflict.skill),
            tuple(_js_string_key(source) for source in conflict.sources),
            tuple(_js_string_key(profile) for profile in conflict.profiles),
        )
        if identity not in seen:
            seen.add(identity)
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
    actual_by_name = _installed_by_identity(installed_state)
    ambiguous = {_js_string_key(item.skill) for item in desired_conflicts}
    remaining_keys = {item.key for item in remaining.requirements}

    for requirement in selected.requirements:
        skill_identity = _js_string_key(requirement.skill)
        if skill_identity in ambiguous:
            continue
        if requirement.key in remaining_keys:
            retain.append(requirement)
        elif skill_identity in actual_by_name:
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
