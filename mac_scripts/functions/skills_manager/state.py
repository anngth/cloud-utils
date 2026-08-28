from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from shared.process import CommandResult, run_process

from .source import SourceError, canonicalize_source
from .upstream import InstalledRecord, list_installed_skills


class InstalledStateError(RuntimeError):
    """Raised when actual installed state cannot be determined."""


@dataclass(frozen=True, slots=True)
class InstalledSkill:
    name: str
    path: str
    agents: tuple[str, ...]
    source: str | None
    provenance: Literal["tracked", "untracked"]


InstalledState: TypeAlias = dict[str, InstalledSkill]


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    source: str
    source_type: str | None
    origin: Path


Runner = Callable[..., CommandResult]
InstalledLister = Callable[..., Sequence[InstalledRecord]]


def resolve_project_root(
    *,
    cwd: str | Path,
    runner: Runner = run_process,
) -> Path:
    canonical_cwd = Path(cwd).resolve(strict=False)
    try:
        result = runner(
            ("git", "rev-parse", "--show-toplevel"),
            cwd=cwd,
            capture=True,
        )
        root = result.stdout.strip()
        if result.returncode == 0 and root:
            return Path(root).resolve(strict=False)
    except Exception:
        pass
    return canonical_cwd


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _read_lock(file_path: Path, *, project_root: Path) -> dict[str, ProvenanceRecord]:
    try:
        document = json.loads(
            file_path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError):
        return {}
    if not isinstance(document, dict) or not isinstance(document.get("skills"), dict):
        return {}

    provenance: dict[str, ProvenanceRecord] = {}
    for name, entry in document["skills"].items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if not isinstance(source, str):
            continue
        try:
            canonical = canonicalize_source(source, cwd=project_root)
        except (SourceError, OSError):
            continue
        source_type = entry.get("sourceType")
        provenance[name] = ProvenanceRecord(
            canonical,
            source_type if isinstance(source_type, str) else None,
            file_path,
        )
    return provenance


def _reconcile_tier(
    paths: Sequence[Path], *, project_root: Path
) -> tuple[dict[str, ProvenanceRecord], set[str]]:
    provenance: dict[str, ProvenanceRecord] = {}
    ambiguous: set[str] = set()
    for file_path in paths:
        for name, entry in _read_lock(
            file_path, project_root=project_root
        ).items():
            if name in ambiguous:
                continue
            existing = provenance.get(name)
            if existing is None:
                provenance[name] = entry
            elif (
                existing.source != entry.source
                or existing.source_type != entry.source_type
            ):
                provenance.pop(name)
                ambiguous.add(name)
    return provenance, ambiguous


def read_project_provenance(
    *,
    project_root: str | Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, ProvenanceRecord]:
    root = Path(project_root)
    actual_env = os.environ if env is None else env
    local, local_ambiguous = _reconcile_tier(
        (root / "skills-lock.json", root / ".agents" / ".skill-lock.json"),
        project_root=root,
    )
    global_paths: list[Path] = []
    if actual_env.get("XDG_STATE_HOME"):
        global_paths.append(
            Path(actual_env["XDG_STATE_HOME"]) / "skills" / ".skill-lock.json"
        )
    if actual_env.get("HOME"):
        global_paths.append(
            Path(actual_env["HOME"]) / ".agents" / ".skill-lock.json"
        )
    combined, _global_ambiguous = _reconcile_tier(
        global_paths, project_root=root
    )
    for name in local_ambiguous:
        combined.pop(name, None)
    combined.update(local)
    return combined


def load_installed_state(
    *,
    project_root: str | Path,
    env: Mapping[str, str] | None = None,
    list_installed: InstalledLister = list_installed_skills,
) -> InstalledState:
    root = Path(project_root)
    try:
        actual = list_installed(cwd=root)
    except Exception as cause:
        raise InstalledStateError(
            f"Could not list installed skills in {root}"
        ) from cause
    provenance = read_project_provenance(project_root=root, env=env)
    return {
        skill.name: InstalledSkill(
            skill.name,
            skill.path,
            tuple(skill.agents),
            provenance[skill.name].source if skill.name in provenance else None,
            "tracked" if skill.name in provenance else "untracked",
        )
        for skill in actual
    }
