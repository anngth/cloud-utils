from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, InstanceOf, PrivateAttr, TypeAdapter, ValidationError
from pydantic import field_validator, model_validator

from .last_backup import parse_js_timestamp

_ISO_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$")
_TIMESTAMP_FIELDS_BY_VERSION = {
    2: ("lastBackupAt",), 3: ("lastBackupAt", "lastCheckedAt"),
    4: ("lastBackupAt", "lastCheckedAt"),
}

def is_iso_utc_timestamp(value: object) -> bool:
    return (
        isinstance(value, str) and bool(_ISO_UTC_TIMESTAMP_RE.fullmatch(value))
        and parse_js_timestamp(value) is not None
    )

class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, populate_by_name=True)

class _VersionedDocument(_StrictModel):
    @model_validator(mode="before")
    @classmethod
    def _version_must_be_a_json_integer(cls, value: object) -> object:
        if isinstance(value, Mapping):
            version = value.get("version")
            if type(version) is int:
                return value
            if type(version) is not float or not math.isfinite(version) or not version.is_integer():
                raise ValueError("version must be an integer")
            value = dict(value)
            value["version"] = int(version)
        return value

class BackupRepoV2(_StrictModel):
    url: InstanceOf[str]
    last_backup_at: InstanceOf[str] | None = Field(alias="lastBackupAt")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if not value:
            raise ValueError("url must not be empty")
        return value

    @field_validator("last_backup_at")
    @classmethod
    def _validate_last_backup_at(cls, value: str | None) -> str | None:
        if value is not None and not is_iso_utc_timestamp(value):
            raise ValueError("invalid UTC timestamp")
        return value

class BackupRepoV3(BackupRepoV2):
    last_checked_at: InstanceOf[str] | None = Field(alias="lastCheckedAt")

    @field_validator("last_checked_at")
    @classmethod
    def _validate_last_checked_at(cls, value: str | None) -> str | None:
        return cls._validate_last_backup_at(value)

class BackupRepoV4(BackupRepoV3):
    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)
    selected_last: bool = Field(alias="selectedLast")
    _key_order: tuple[str, ...] = PrivateAttr(default=())

class BackupsDocumentV1(_VersionedDocument):
    version: Literal[1]
    repos: list[InstanceOf[str]]

    @field_validator("repos")
    @classmethod
    def _validate_repo_urls(cls, value: list[str]) -> list[str]:
        if any(not url for url in value):
            raise ValueError("repo URLs must not be empty")
        return value

class BackupsDocumentV2(_VersionedDocument):
    version: Literal[2]
    repos: list[BackupRepoV2]

class BackupsDocumentV3(_VersionedDocument):
    version: Literal[3]
    repos: list[BackupRepoV3]

class BackupsDocumentV4(_VersionedDocument):
    version: Literal[4]
    repos: list[BackupRepoV4]

BackupsDocument: TypeAlias = Annotated[
    BackupsDocumentV1 | BackupsDocumentV2 | BackupsDocumentV3 | BackupsDocumentV4,
    Field(discriminator="version"),
]
_DOCUMENT_ADAPTER = TypeAdapter(BackupsDocument)

EMPTY_BACKUPS = BackupsDocumentV4(version=4, repos=[])

@dataclass(frozen=True, slots=True)
class GtPaths:
    config_dir: Path
    gt_dir: Path
    backups_file: Path

@dataclass(frozen=True, slots=True)
class ReadBackupsResult:
    ok: bool
    document: BackupsDocumentV1 | BackupsDocumentV2 | BackupsDocumentV3 | BackupsDocumentV4 | None = None
    error: str | None = None
    missing: bool = False

@dataclass(frozen=True, slots=True)
class MigrateBackupsResult:
    ok: bool
    document: BackupsDocumentV4 | None = None
    migrated: bool = False
    error: str | None = None

@dataclass(frozen=True, slots=True)
class WriteBackupsResult:
    ok: bool
    error: str | None = None

@dataclass(frozen=True, slots=True)
class LoadBackupsResult:
    ok: bool
    document: BackupsDocumentV4 | None = None
    migrated: bool = False
    error: str | None = None
    missing: bool = False

def default_config_dir(env: Mapping[str, str] = os.environ) -> Path:
    home = env.get("HOME") or ""
    return Path(f"{home}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils")

def resolve_gt_paths(env: Mapping[str, str] = os.environ) -> GtPaths:
    configured = env.get("CLOUD_UTILS_CONFIG_DIR")
    config_dir = Path(configured) if configured else default_config_dir(env)
    gt_dir = config_dir / "gt"
    return GtPaths(config_dir=config_dir, gt_dir=gt_dir, backups_file=gt_dir / "backups.json")

def format_display_path(
    path: str | os.PathLike[str],
    *,
    home: str | os.PathLike[str] | None = None,
    temp_dir: str | os.PathLike[str] | None = None,
) -> str:
    file_path = os.fspath(path)
    if not file_path:
        return file_path

    home_path = os.fspath(home) if home is not None else os.environ.get("HOME")
    temp_path = os.fspath(temp_dir) if temp_dir is not None else tempfile.gettempdir()

    for root, label in ((home_path, "~"), (temp_path, None)):
        if not root or not (
            file_path == root or file_path.startswith(f"{root.rstrip(os.sep)}{os.sep}")
        ):
            continue
        relative = os.path.relpath(file_path, root)
        if label is not None:
            return label if relative == "." else f"{label}/{relative}"
        if relative != "." and not relative.startswith(".."):
            return relative

    return file_path

def _js_number_to_string(value: int | float) -> str:
    try:
        number = float(value)
    except OverflowError:
        return "null"
    if not math.isfinite(number):
        return "null"
    if number == 0:
        return "0"

    decimal = Decimal(repr(number))
    if -6 <= decimal.adjusted() < 21:
        rendered = format(decimal, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered

    coefficient, exponent = format(decimal.normalize(), "e").split("e")
    exponent_number = int(exponent)
    exponent_sign = "+" if exponent_number >= 0 else ""
    return f"{coefficient}e{exponent_sign}{exponent_number}"

def _normalize_surrogate_pairs(value: str) -> str:
    normalized: list[str] = []
    index = 0
    while index < len(value):
        code = ord(value[index])
        if 0xD800 <= code <= 0xDBFF and index + 1 < len(value):
            low = ord(value[index + 1])
            if 0xDC00 <= low <= 0xDFFF:
                scalar = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                normalized.append(chr(scalar))
                index += 2
                continue
        normalized.append(value[index])
        index += 1
    return "".join(normalized)

def _js_quote_string(value: str) -> str:
    quoted = json.dumps(_normalize_surrogate_pairs(value), ensure_ascii=False)
    return quoted.encode("utf-8", errors="backslashreplace").decode("utf-8")

def _js_json_stringify(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _js_quote_string(value)
    if isinstance(value, int | float):
        return _js_number_to_string(value)
    if isinstance(value, list | tuple):
        return f"[{','.join(_js_json_stringify(item) for item in value)}]"
    if isinstance(value, Mapping):
        entries = (f"{_js_quote_string(str(key))}:{_js_json_stringify(item)}" for key, item in value.items())
        return f"{{{','.join(entries)}}}"
    return "undefined"

def _format_invalid_timestamp_value(value: object) -> str:
    return _js_json_stringify(value)

def _find_repo_timestamp_error(document: object) -> str | None:
    if not isinstance(document, Mapping):
        return None
    repos = document.get("repos")
    if not isinstance(repos, list):
        return None
    version = document.get("version")
    if type(version) is float and math.isfinite(version) and version.is_integer():
        version = int(version)
    if type(version) is not int:
        return None
    fields = _TIMESTAMP_FIELDS_BY_VERSION.get(version)
    if fields is None:
        return None

    for index, repo in enumerate(repos):
        for field in fields:
            if not isinstance(repo, Mapping) or field not in repo:
                continue
            value = repo[field]
            if value is None or is_iso_utc_timestamp(value):
                continue
            url = repo.get("url")
            reference = url if isinstance(url, str) and url else f"repo at index {index}"
            return f"Invalid {field} for {reference}: {_format_invalid_timestamp_value(value)}"
    return None

def _remember_repo_order(document: BackupsDocument, raw: object) -> BackupsDocument:
    if isinstance(document, BackupsDocumentV4) and isinstance(raw, Mapping):
        repos = raw.get("repos")
        if isinstance(repos, list):
            for repo, source in zip(document.repos, repos, strict=True):
                if isinstance(source, Mapping):
                    repo._key_order = tuple(source)
    return document

def _parse_document(document: object) -> BackupsDocument:
    parsed = _DOCUMENT_ADAPTER.validate_python(document, by_alias=True, by_name=False)
    return _remember_repo_order(parsed, document)

def _dump_repo(repo: BackupRepoV4) -> dict[str, object]:
    dumped = repo.model_dump(by_alias=True)
    keys = dict.fromkeys((*repo._key_order, *dumped))
    return {key: dumped[key] for key in keys if key in dumped}

def _dump_v4(document: BackupsDocumentV4) -> dict[str, object]:
    return {"version": document.version, "repos": [_dump_repo(repo) for repo in document.repos]}

def _as_raw_document(document: object) -> object:
    if isinstance(document, BackupsDocumentV4):
        return _dump_v4(document)
    if isinstance(document, BaseModel):
        return document.model_dump(by_alias=True)
    return document

def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")

def _normalize_json_strings(value: object) -> object:
    if isinstance(value, str):
        return _normalize_surrogate_pairs(value)
    if isinstance(value, list):
        return [_normalize_json_strings(item) for item in value]
    if isinstance(value, dict):
        return {_normalize_surrogate_pairs(key): _normalize_json_strings(item) for key, item in value.items()}
    return value

def _js_json_pretty(value: object, level: int = 0) -> str:
    if isinstance(value, Mapping):
        rendered = (
            f"{_js_quote_string(str(key))}: {_js_json_pretty(item, level + 1)}"
            for key, item in value.items()
        )
        opening, closing = "{", "}"
    if isinstance(value, list | tuple):
        rendered = (_js_json_pretty(item, level + 1) for item in value)
        opening, closing = "[", "]"
    if not isinstance(value, Mapping | list | tuple):
        return _js_json_stringify(value)
    body = ",\n".join(f"{'  ' * (level + 1)}{item}" for item in rendered)
    return f"{opening}\n{body}\n{'  ' * level}{closing}" if body else opening + closing

def read_backups_document(path: str | os.PathLike[str]) -> ReadBackupsResult:
    backups_file = Path(path)
    try:
        raw = backups_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ReadBackupsResult(ok=False, missing=True, error=f"Backups file not found: {backups_file}")
    except (OSError, UnicodeError):
        return ReadBackupsResult(ok=False, error=f"Could not read backups file: {backups_file}")

    try:
        document = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError):
        return ReadBackupsResult(ok=False, error=f"Invalid JSON in backups file: {backups_file}")

    try:
        parsed = _parse_document(document)
    except ValidationError:
        timestamp_error = _find_repo_timestamp_error(document)
        error = timestamp_error or f"Invalid backups document: {backups_file}"
        return ReadBackupsResult(ok=False, error=error)
    return ReadBackupsResult(ok=True, document=parsed)

def _upgrade_repo(repo: str | BackupRepoV2 | BackupRepoV3) -> BackupRepoV4:
    if isinstance(repo, str):
        return BackupRepoV4(url=repo, last_backup_at=None, last_checked_at=None, selected_last=False)
    return BackupRepoV4(
        url=repo.url,
        last_backup_at=repo.last_backup_at,
        last_checked_at=getattr(repo, "last_checked_at", None),
        selected_last=False,
    )

def migrate_backups_document(document: object) -> MigrateBackupsResult:
    if isinstance(document, BackupsDocumentV4):
        return MigrateBackupsResult(ok=True, document=document, migrated=False)

    raw_document = _as_raw_document(document)
    try:
        parsed = _parse_document(raw_document)
    except ValidationError:
        return MigrateBackupsResult(
            ok=False,
            error=_find_repo_timestamp_error(raw_document)
            or "Invalid backups document",
        )

    if isinstance(parsed, BackupsDocumentV4):
        return MigrateBackupsResult(ok=True, document=parsed, migrated=False)

    repos = [_upgrade_repo(repo) for repo in parsed.repos]
    return MigrateBackupsResult(
        ok=True, document=BackupsDocumentV4(version=4, repos=repos), migrated=True
    )

def write_backups_document(
    path: str | os.PathLike[str], document: object
) -> WriteBackupsResult:
    raw_document = _as_raw_document(document)
    try:
        parsed = BackupsDocumentV4.model_validate(
            raw_document, by_alias=True, by_name=False
        )
        _remember_repo_order(parsed, raw_document)
    except ValidationError:
        return WriteBackupsResult(ok=False, error="Invalid backups document")

    backups_file = Path(path)
    temp_file = Path(f"{backups_file}.tmp")

    try:
        encoded = f"{_js_json_pretty(_dump_v4(parsed))}\n".encode(
            "utf-8", errors="backslashreplace"
        )
        backups_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_bytes(encoded)
        os.replace(temp_file, backups_file)
    except Exception as error:
        try:
            temp_file.unlink(missing_ok=True)
        except Exception:
            pass
        message = str(error) or f"Could not write backups file: {backups_file}"
        return WriteBackupsResult(ok=False, error=message)
    return WriteBackupsResult(ok=True)

def load_backups_document(path: str | os.PathLike[str]) -> LoadBackupsResult:
    read_result = read_backups_document(path)
    if not read_result.ok:
        return LoadBackupsResult(ok=False, error=read_result.error, missing=read_result.missing)

    migrate_result = migrate_backups_document(read_result.document)
    if not migrate_result.ok:
        return LoadBackupsResult(ok=False, error=migrate_result.error)

    if migrate_result.migrated:
        write_result = write_backups_document(path, migrate_result.document)
        if not write_result.ok:
            return LoadBackupsResult(ok=False, error=write_result.error)

    return LoadBackupsResult(ok=True, document=migrate_result.document, migrated=migrate_result.migrated)
