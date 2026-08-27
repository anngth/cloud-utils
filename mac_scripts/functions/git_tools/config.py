"""Versioned backup configuration models and atomic persistence."""

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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    InstanceOf,
    TypeAdapter,
    ValidationError,
)
from pydantic import field_validator, model_validator


_ISO_UTC_TIMESTAMP_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{3}))?Z$"
)
_TIMESTAMP_FIELDS_BY_VERSION = {
    2: ("lastBackupAt",),
    3: ("lastBackupAt", "lastCheckedAt"),
    4: ("lastBackupAt", "lastCheckedAt"),
}


def is_iso_utc_timestamp(value: object) -> bool:
    """Match the exact UTC ``Z`` timestamp forms accepted by the JS oracle."""

    if not isinstance(value, str):
        return False
    match = _ISO_UTC_TIMESTAMP_RE.fullmatch(value)
    if match is None:
        return False

    _, month_text, day_text, hour_text, minute_text, second_text, millis = (
        match.groups()
    )
    month = int(month_text)
    day = int(day_text)
    hour = int(hour_text)
    minute = int(minute_text)
    second = int(second_text)

    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return False
    if not 0 <= minute <= 59 or not 0 <= second <= 59:
        return False
    if hour == 24:
        return minute == 0 and second == 0 and (millis is None or millis == "000")
    return 0 <= hour <= 23


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, populate_by_name=True)


class _VersionedDocument(_StrictModel):
    @model_validator(mode="before")
    @classmethod
    def _version_must_be_a_json_integer(cls, value: object) -> object:
        if isinstance(value, Mapping) and type(value.get("version")) is not int:
            raise ValueError("version must be an integer")
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
        if value is not None and not is_iso_utc_timestamp(value):
            raise ValueError("invalid UTC timestamp")
        return value


class BackupRepoV4(BackupRepoV3):
    selected_last: bool = Field(alias="selectedLast")


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
    document: (
        BackupsDocumentV1
        | BackupsDocumentV2
        | BackupsDocumentV3
        | BackupsDocumentV4
        | None
    ) = None
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
    return Path(
        f"{home}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
    )


def resolve_gt_paths(env: Mapping[str, str] = os.environ) -> GtPaths:
    configured = env.get("CLOUD_UTILS_CONFIG_DIR")
    config_dir = Path(configured) if configured else default_config_dir(env)
    gt_dir = config_dir / "gt"
    return GtPaths(
        config_dir=config_dir,
        gt_dir=gt_dir,
        backups_file=gt_dir / "backups.json",
    )


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

    if home_path and (
        file_path == home_path
        or file_path.startswith(f"{home_path.rstrip(os.sep)}{os.sep}")
    ):
        rest = file_path[len(home_path) :].lstrip(os.sep)
        return f"~/{rest}" if rest else "~"

    if temp_path and (
        file_path == temp_path
        or file_path.startswith(f"{temp_path.rstrip(os.sep)}{os.sep}")
    ):
        relative = os.path.relpath(file_path, temp_path)
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
        entries = (
            f"{_js_quote_string(str(key))}:{_js_json_stringify(item)}"
            for key, item in value.items()
        )
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
            return (
                f"Invalid {field} for {reference}: "
                f"{_format_invalid_timestamp_value(value)}"
            )
    return None


def _parse_document(
    document: object,
) -> BackupsDocumentV1 | BackupsDocumentV2 | BackupsDocumentV3 | BackupsDocumentV4:
    return _DOCUMENT_ADAPTER.validate_python(document, by_alias=True, by_name=False)


def _as_raw_document(document: object) -> object:
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
        return {
            _normalize_surrogate_pairs(key): _normalize_json_strings(item)
            for key, item in value.items()
        }
    return value


def read_backups_document(path: str | os.PathLike[str]) -> ReadBackupsResult:
    backups_file = Path(path)
    try:
        raw = backups_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ReadBackupsResult(
            ok=False,
            missing=True,
            error=f"Backups file not found: {backups_file}",
        )
    except (OSError, UnicodeError):
        return ReadBackupsResult(
            ok=False,
            error=f"Could not read backups file: {backups_file}",
        )

    try:
        document = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError):
        return ReadBackupsResult(
            ok=False,
            error=f"Invalid JSON in backups file: {backups_file}",
        )

    try:
        parsed = _parse_document(document)
    except ValidationError:
        timestamp_error = _find_repo_timestamp_error(document)
        return ReadBackupsResult(
            ok=False,
            error=timestamp_error or f"Invalid backups document: {backups_file}",
        )
    return ReadBackupsResult(ok=True, document=parsed)


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

    if isinstance(parsed, BackupsDocumentV1):
        repos = [
            BackupRepoV4(
                url=url,
                last_backup_at=None,
                last_checked_at=None,
                selected_last=False,
            )
            for url in parsed.repos
        ]
    elif isinstance(parsed, BackupsDocumentV2):
        repos = [
            BackupRepoV4(
                url=repo.url,
                last_backup_at=repo.last_backup_at,
                last_checked_at=None,
                selected_last=False,
            )
            for repo in parsed.repos
        ]
    else:
        repos = [
            BackupRepoV4(
                url=repo.url,
                last_backup_at=repo.last_backup_at,
                last_checked_at=repo.last_checked_at,
                selected_last=False,
            )
            for repo in parsed.repos
        ]

    return MigrateBackupsResult(
        ok=True,
        document=BackupsDocumentV4(version=4, repos=repos),
        migrated=True,
    )


def write_backups_document(
    path: str | os.PathLike[str], document: object
) -> WriteBackupsResult:
    raw_document = _as_raw_document(document)
    try:
        parsed = BackupsDocumentV4.model_validate(
            raw_document, by_alias=True, by_name=False
        )
    except ValidationError:
        return WriteBackupsResult(ok=False, error="Invalid backups document")

    backups_file = Path(path)
    temp_file = Path(f"{backups_file}.tmp")

    try:
        encoded = (
            json.dumps(
                _normalize_json_strings(parsed.model_dump(by_alias=True)),
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8", errors="backslashreplace")
        backups_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_bytes(encoded)
        os.replace(temp_file, backups_file)
    except Exception as error:
        try:
            temp_file.unlink(missing_ok=True)
        except Exception:
            pass
        return WriteBackupsResult(
            ok=False,
            error=str(error) or f"Could not write backups file: {backups_file}",
        )
    return WriteBackupsResult(ok=True)


def load_backups_document(path: str | os.PathLike[str]) -> LoadBackupsResult:
    read_result = read_backups_document(path)
    if not read_result.ok:
        return LoadBackupsResult(
            ok=False,
            error=read_result.error,
            missing=read_result.missing,
        )

    migrate_result = migrate_backups_document(read_result.document)
    if not migrate_result.ok:
        return LoadBackupsResult(ok=False, error=migrate_result.error)

    if migrate_result.migrated:
        write_result = write_backups_document(path, migrate_result.document)
        if not write_result.ok:
            return LoadBackupsResult(ok=False, error=write_result.error)

    return LoadBackupsResult(
        ok=True,
        document=migrate_result.document,
        migrated=migrate_result.migrated,
    )
