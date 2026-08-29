from __future__ import annotations

import copy
import json
import math
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    ValidationError,
    model_serializer,
)

from .source import canonicalize_source, js_string_key, nfc_codepoint_key, redact_source


class CatalogError(ValueError):
    """Raised when a catalog violates the version-1 domain contract."""


class ConfigError(ValueError):
    """Raised when a configuration file cannot be read or migrated safely."""

    def __init__(self, message: str, *, file_path: Path | None = None) -> None:
        super().__init__(message)
        self.file_path = file_path


ConfigFileError = ConfigError


class _FrozenMapping(Mapping[Any, Any]):
    __slots__ = ("_items",)

    def __init__(self, items: Sequence[tuple[Any, Any]]) -> None:
        if hasattr(self, "_items"):
            raise TypeError("Frozen JSON mapping cannot be reinitialized")
        object.__setattr__(self, "_items", tuple(items))

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("Frozen JSON mapping cannot be mutated")

    def __getitem__(self, key: Any) -> Any:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def update(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("Frozen JSON mapping cannot be mutated")

    def __deepcopy__(self, memo: dict[int, object]) -> _FrozenMapping:
        return _FrozenMapping(
            tuple(
                (copy.deepcopy(key, memo), copy.deepcopy(value, memo))
                for key, value in self._items
            )
        )


def _deep_freeze_extra(value: object) -> object:
    if isinstance(value, Mapping):
        return _FrozenMapping(
            tuple(
                (key, _deep_freeze_extra(item)) for key, item in value.items()
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_extra(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze_extra(item) for item in value)
    return copy.deepcopy(value)


def _pydantic_extra_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _pydantic_extra_value(item) for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_pydantic_extra_value(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_pydantic_extra_value(item) for item in value)
    return copy.deepcopy(value)


class _OrderedFrozenModel(BaseModel):
    _key_order: tuple[str, ...] = PrivateAttr(default=())

    def __init__(self, **data: Any) -> None:
        key_order = tuple(data)
        super().__init__(**data)
        object.__setattr__(self, "_key_order", key_order)
        if self.__pydantic_extra__ is not None:
            object.__setattr__(
                self,
                "__pydantic_extra__",
                _FrozenMapping(
                    tuple(
                        (key, _deep_freeze_extra(value))
                        for key, value in self.__pydantic_extra__.items()
                    )
                ),
            )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        serialized = {
            field_name: getattr(self, field_name)
            for field_name in type(self).model_fields
        }
        if self.model_extra is not None:
            serialized.update(
                (key, _pydantic_extra_value(value))
                for key, value in self.model_extra.items()
            )
        return serialized


class CatalogSource(_OrderedFrozenModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    source: str
    skills: tuple[str, ...]


class Catalog(_OrderedFrozenModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    version: Literal[1]
    sources: tuple[CatalogSource, ...]


EMPTY_CATALOG = Catalog(version=1, sources=())


@dataclass(frozen=True, slots=True)
class ConfigPaths:
    config_dir: Path
    skm_dir: Path
    sources_file: Path
    profiles_file: Path
    projects_file: Path
    legacy_file: Path

    @classmethod
    def for_config_dir(cls, config_dir: Path | str) -> ConfigPaths:
        root = Path(config_dir)
        skm_dir = root / "skm"
        return cls(
            config_dir=root,
            skm_dir=skm_dir,
            sources_file=skm_dir / "sources.json",
            profiles_file=skm_dir / "profiles.json",
            projects_file=skm_dir / "projects.json",
            legacy_file=skm_dir / "list.json",
        )


def _validate_skill_name(skill: object) -> str:
    if not isinstance(skill, str) or not skill.strip():
        raise CatalogError("Invalid skill name")
    return skill


def _sequence(value: object) -> bool:
    return isinstance(value, (list, tuple))


def _reject_non_finite_numbers(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CatalogError("Catalog numbers must be finite")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_non_finite_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite_numbers(item)


def _ordered_model_value(value: object) -> object:
    if isinstance(value, Catalog):
        known: dict[str, object] = {
            "version": value.version,
            "sources": [_ordered_model_value(entry) for entry in value.sources],
        }
        return _ordered_fields(value, known)
    if isinstance(value, CatalogSource):
        known = {"source": value.source, "skills": list(value.skills)}
        return _ordered_fields(value, known)
    if isinstance(value, Mapping):
        return {key: _ordered_model_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_ordered_model_value(item) for item in value]
    if isinstance(value, list):
        return [_ordered_model_value(item) for item in value]
    return copy.deepcopy(value)


def _ordered_fields(
    model: _OrderedFrozenModel, known: Mapping[str, object]
) -> dict[str, object]:
    extras = model.model_extra or {}
    result: dict[str, object] = {}
    for key in model._key_order:
        if key in known:
            result[key] = _ordered_model_value(known[key])
        elif key in extras:
            result[key] = _ordered_model_value(extras[key])
    for key, item in known.items():
        if key not in result:
            result[key] = _ordered_model_value(item)
    for key, item in extras.items():
        if key not in result:
            result[key] = _ordered_model_value(item)
    return result


def _as_catalog_mapping(value: Catalog | Mapping[str, object]) -> dict[str, object]:
    if isinstance(value, Catalog):
        result = _ordered_model_value(value)
        assert isinstance(result, dict)
        return result
    if not isinstance(value, Mapping):
        raise CatalogError("sources.json must have version 1")
    return copy.deepcopy(dict(value))


def validate_catalog(value: Catalog | Mapping[str, object]) -> Catalog:
    raw = _as_catalog_mapping(value)
    _reject_non_finite_numbers(raw)
    if type(raw.get("version")) is not int or raw.get("version") != 1:
        raise CatalogError("sources.json must have version 1")
    sources = raw.get("sources")
    if not _sequence(sources):
        raise CatalogError("sources.json must contain a sources array")

    source_ids: set[str] = set()
    skill_owners: dict[str, str] = {}
    for entry in sources:
        if not isinstance(entry, Mapping):
            raise CatalogError("Invalid catalog source entry")
        source = entry.get("source")
        skills = entry.get("skills")
        if not isinstance(source, str) or not _sequence(skills):
            raise CatalogError("Invalid catalog source entry")
        if source in source_ids:
            raise CatalogError(f"Duplicate source: {redact_source(source)}")
        source_ids.add(source)

        seen: set[str] = set()
        for raw_skill in skills:
            skill = _validate_skill_name(raw_skill)
            if skill in seen:
                raise CatalogError(f"Duplicate skill: {skill}")
            seen.add(skill)
            owner = skill_owners.get(skill)
            if owner is not None and owner != source:
                raise CatalogError(
                    f"{skill} is selected from a different source: "
                    f"{redact_source(owner)}"
                )
            skill_owners[skill] = source

    try:
        catalog = Catalog.model_validate(raw)
    except ValidationError as error:
        raise CatalogError("Invalid catalog source entry") from error

    object.__setattr__(catalog, "_key_order", tuple(raw))
    for model, entry in zip(catalog.sources, sources, strict=True):
        object.__setattr__(model, "_key_order", tuple(entry))
    return catalog


def _catalog_skill_owner(catalog: Catalog, skill_name: str) -> str | None:
    for entry in catalog.sources:
        if skill_name in entry.skills:
            return entry.source
    return None


def resolve_source_token(
    catalog: Catalog,
    token: str,
    *,
    cwd: Path | None = None,
) -> tuple[int, CatalogSource]:
    if re.fullmatch(r"[0-9]+", token):
        index = int(token) - 1
        if index < 0 or index >= len(catalog.sources):
            raise CatalogError(f"Source index out of range: {token}")
        return index, catalog.sources[index]

    canonical = canonicalize_source(token, cwd=cwd)
    for index, entry in enumerate(catalog.sources):
        if entry.source == canonical:
            return index, entry
    raise CatalogError(f"Source not found: {canonical}")


def cross_source_skill_conflicts(
    catalog: Catalog,
    source: str,
    skills: Sequence[str],
    *,
    cwd: Path | None = None,
) -> tuple[tuple[str, str], ...]:
    canonical = canonicalize_source(source, cwd=cwd)
    conflicts: list[tuple[str, str]] = []
    for skill in skills:
        owner = _catalog_skill_owner(catalog, skill)
        if owner is not None and owner != canonical:
            conflicts.append((skill, owner))
    return tuple(conflicts)


def upsert_source(
    catalog: Catalog,
    source: str,
    skills: Sequence[str],
    *,
    cwd: Path | None = None,
) -> Catalog:
    canonical = canonicalize_source(source, cwd=cwd)
    if isinstance(skills, (str, bytes)) or not _sequence(skills):
        raise CatalogError("skills must be an array")

    skill_list: list[str] = []
    seen: set[str] = set()
    for raw_skill in skills:
        skill = _validate_skill_name(raw_skill)
        if skill in seen:
            raise CatalogError(f"Duplicate skill: {skill}")
        seen.add(skill)
        skill_list.append(skill)

    validated = validate_catalog(catalog)
    for skill in skill_list:
        owner = _catalog_skill_owner(validated, skill)
        if owner is not None and owner != canonical:
            raise CatalogError(
                f"{skill} is selected from a different source: "
                f"{redact_source(owner)}"
            )

    raw = _as_catalog_mapping(validated)
    raw_sources = raw["sources"]
    assert isinstance(raw_sources, list)
    for entry in raw_sources:
        assert isinstance(entry, dict)
        if entry["source"] == canonical:
            entry["skills"] = skill_list
            break
    else:
        raw_sources.append({"source": canonical, "skills": skill_list})
    return validate_catalog(raw)


def remove_source_at(catalog: Catalog, index: int) -> Catalog:
    if type(index) is not int or index < 0 or index >= len(catalog.sources):
        raise CatalogError(f"Source index out of range: {index}")
    raw = _as_catalog_mapping(validate_catalog(catalog))
    raw_sources = raw["sources"]
    assert isinstance(raw_sources, list)
    del raw_sources[index]
    return validate_catalog(raw)


def migrate_profiles_to_catalog(profiles: Mapping[str, object]) -> Catalog:
    profile_items = profiles.get("profiles", ())
    if not _sequence(profile_items):
        raise CatalogError("Invalid profiles file")

    sources: list[dict[str, object]] = []
    source_indices: dict[str, int] = {}
    skill_owners: dict[str, str] = {}
    for profile in profile_items:
        if not isinstance(profile, Mapping):
            raise CatalogError("Invalid profiles file")
        entries = profile.get("sources", ())
        if not _sequence(entries):
            raise CatalogError("Invalid profiles file")
        for entry in entries:
            if not isinstance(entry, Mapping) or not isinstance(
                entry.get("source"), str
            ):
                raise CatalogError("Invalid profiles file")
            canonical = canonicalize_source(entry["source"])
            if canonical not in source_indices:
                source_indices[canonical] = len(sources)
                sources.append({"source": canonical, "skills": []})
            target_skills = sources[source_indices[canonical]]["skills"]
            assert isinstance(target_skills, list)
            skills = entry.get("skills", ())
            if not _sequence(skills):
                raise CatalogError("Invalid profiles file")
            for raw_skill in skills:
                skill = _validate_skill_name(raw_skill)
                owner = skill_owners.get(skill)
                if owner is not None and owner != canonical:
                    raise CatalogError(
                        f"{skill} is selected from a different source: "
                        f"{redact_source(owner)}"
                    )
                skill_owners[skill] = canonical
                if skill not in target_skills:
                    target_skills.append(skill)
    return validate_catalog({"version": 1, "sources": sources})


class _ProfileError(ValueError):
    pass


def _validate_profiles(value: object) -> dict[str, object]:
    if (
        not isinstance(value, Mapping)
        or type(value.get("version")) is not int
        or value.get("version") != 1
    ):
        raise _ProfileError("profiles.json must have version 1")
    profiles = value.get("profiles")
    if not _sequence(profiles) or len(profiles) == 0:
        raise _ProfileError("profiles.json must contain at least one profile")

    names: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, Mapping):
            raise _ProfileError("Profile name must not be empty")
        name = profile.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _ProfileError("Profile name must not be empty")
        if name in names:
            raise _ProfileError(f"Duplicate profile: {name}")
        names.add(name)
        entries = profile.get("sources")
        if not _sequence(entries):
            raise _ProfileError(f"Invalid sources: {name}")
        source_ids: set[str] = set()
        skill_owners: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise _ProfileError(f"Invalid source in profile: {name}")
            source = entry.get("source")
            skills = entry.get("skills")
            if not isinstance(source, str) or not _sequence(skills):
                raise _ProfileError(f"Invalid source in profile: {name}")
            if source in source_ids:
                raise _ProfileError(f"Duplicate source: {source}")
            source_ids.add(source)
            seen: set[str] = set()
            for raw_skill in skills:
                if not isinstance(raw_skill, str) or not raw_skill.strip():
                    raise _ProfileError(f"Invalid skill in source: {source}")
                if raw_skill in seen:
                    raise _ProfileError(f"Duplicate skill: {raw_skill}")
                seen.add(raw_skill)
                owner = skill_owners.get(raw_skill)
                if owner is not None and owner != source:
                    raise _ProfileError(
                        f"{raw_skill} is selected from a different source: {owner}"
                    )
                skill_owners[raw_skill] = source
    validated = copy.deepcopy(dict(value))
    validated_profiles = validated["profiles"]
    assert isinstance(validated_profiles, list)
    validated_profiles.sort(
        key=lambda profile: unicodedata.normalize("NFC", profile["name"])
    )
    for profile in validated_profiles:
        profile["sources"].sort(
            key=lambda entry: unicodedata.normalize("NFC", entry["source"])
        )
    return validated


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _read_json(file_path: Path) -> object:
    try:
        return json.loads(
            file_path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ConfigError(
            f"Could not read configuration file: {file_path}",
            file_path=file_path,
        ) from error


def _read_legacy_sources(file_path: Path) -> tuple[str, ...]:
    data = _read_json(file_path)
    if isinstance(data, list):
        entries = data
    elif isinstance(data, Mapping) and isinstance(data.get("presets"), list):
        entries = data["presets"]
    else:
        entries = None
    if entries is None or any(
        not isinstance(entry, Mapping) or not isinstance(entry.get("source"), str)
        for entry in entries
    ):
        raise ConfigError(
            f"Invalid legacy source list: {file_path}", file_path=file_path
        )
    try:
        sources: dict[bytes, str] = {}
        for entry in entries:
            source = canonicalize_source(entry["source"])
            sources.setdefault(js_string_key(source), source)
        return tuple(sorted(sources.values(), key=nfc_codepoint_key))
    except Exception as error:
        raise ConfigError(
            f"Invalid legacy source list: {file_path}", file_path=file_path
        ) from error


def _well_formed_json(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        points = tuple(map(ord, match.group()))
        if len(points) == 1:
            return f"\\u{points[0]:04x}"
        return chr(0x10000 + (points[0] - 0xD800) * 0x400 + points[1] - 0xDC00)
    pattern = r"[\ud800-\udbff][\udc00-\udfff]|[\ud800-\udfff]"
    return re.sub(pattern, replace, text)


def _json_bytes(catalog: Catalog) -> bytes:
    try:
        serialized = json.dumps(
            _ordered_model_value(catalog),
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise CatalogError("Catalog contains a non-JSON value") from error
    return (_well_formed_json(serialized) + "\n").encode("utf-8")


def _write_catalog_atomic(file_path: Path, catalog: Catalog, *, pid: int) -> None:
    temp_path = file_path.with_name(f"{file_path.name}.{pid}.tmp")
    try:
        temp_path.write_bytes(_json_bytes(catalog))
        temp_path.replace(file_path)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _bootstrap_catalog(paths: ConfigPaths, *, pid: int) -> None:
    if paths.sources_file.exists():
        return

    if paths.profiles_file.exists():
        try:
            profiles = _validate_profiles(_read_json(paths.profiles_file))
        except ConfigError:
            raise
        except _ProfileError as error:
            raise ConfigError(
                f"Invalid profiles file: {paths.profiles_file}",
                file_path=paths.profiles_file,
            ) from error
        try:
            catalog = migrate_profiles_to_catalog(profiles)
        except CatalogError as error:
            raise ConfigError(str(error), file_path=paths.sources_file) from error
        except Exception as error:
            raise ConfigError(
                f"Invalid profiles file: {paths.profiles_file}",
                file_path=paths.profiles_file,
            ) from error
    elif paths.legacy_file.exists():
        sources = _read_legacy_sources(paths.legacy_file)
        catalog = validate_catalog(
            {
                "version": 1,
                "sources": [
                    {"source": source, "skills": []} for source in sources
                ],
            }
        )
    else:
        catalog = EMPTY_CATALOG
    _write_catalog_atomic(paths.sources_file, catalog, pid=pid)


def initialize_config(
    *,
    env: Mapping[str, str] | None = None,
    pid: int | None = None,
) -> ConfigPaths:
    environment = os.environ if env is None else env
    configured = environment.get("CLOUD_UTILS_CONFIG_DIR")
    if configured:
        config_dir = Path(configured)
    else:
        home = environment.get("HOME", "")
        config_dir = Path(
            f"{home}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
        )
    paths = ConfigPaths.for_config_dir(config_dir)
    paths.skm_dir.mkdir(parents=True, exist_ok=True)
    _bootstrap_catalog(paths, pid=os.getpid() if pid is None else pid)
    return paths


def read_config(paths: ConfigPaths) -> Catalog:
    try:
        return validate_catalog(_read_json(paths.sources_file))
    except ConfigError:
        raise
    except CatalogError as error:
        raise ConfigError(
            f"Invalid sources file: {paths.sources_file}",
            file_path=paths.sources_file,
        ) from error


def write_catalog(
    paths: ConfigPaths,
    catalog: Catalog | Mapping[str, object],
    *,
    pid: int | None = None,
) -> None:
    validated = validate_catalog(catalog)
    _write_catalog_atomic(
        paths.sources_file,
        validated,
        pid=os.getpid() if pid is None else pid,
    )
