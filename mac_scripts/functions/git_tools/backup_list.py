"""Mutations for the versioned ``gt`` backup repository list."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from .config import (
    BackupsDocumentV4,
    BackupRepoV4,
    GtPaths,
    migrate_backups_document,
    read_backups_document,
    write_backups_document,
)
from .ssh_url import ParsedSshUrl, canonicalize_ssh_git_url


ADD_HINT = "Use `gt backup add <ssh-url>` to add a repo first."
_INDEX_RE = re.compile(r"^[0-9]+$")


@dataclass(frozen=True, slots=True)
class AddedRepo:
    url: str
    index: int


@dataclass(frozen=True, slots=True)
class FailedRepo:
    url: str
    error: str


@dataclass(frozen=True, slots=True)
class AddBackupRepoResult:
    ok: bool
    index: int | None = None
    document: BackupsDocumentV4 | None = None
    created_file: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AddBackupReposResult:
    ok: bool
    added: tuple[AddedRepo, ...] = ()
    failures: tuple[FailedRepo, ...] = ()
    document: BackupsDocumentV4 | None = None
    created_file: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RemoveBackupRepoResult:
    ok: bool
    removed: str | None = None
    document: BackupsDocumentV4 | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BackupListUpdateResult:
    ok: bool
    document: BackupsDocumentV4 | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _LoadedDocument:
    document: BackupsDocumentV4 | None = None
    missing: bool = False
    error: str | None = None


def _canonicalize(value: str) -> tuple[ParsedSshUrl | None, str | None]:
    try:
        return canonicalize_ssh_git_url(value), None
    except ValueError as error:
        return None, str(error)


def _canonical_key(value: str) -> str | None:
    parsed, _ = _canonicalize(value)
    return parsed.canonical if parsed is not None else None


def _load_for_mutation(paths: GtPaths) -> _LoadedDocument:
    """Read and upgrade a document without writing an intermediate migration."""

    read = read_backups_document(paths.backups_file)
    if not read.ok:
        return _LoadedDocument(missing=read.missing, error=read.error)

    migrated = migrate_backups_document(read.document)
    if not migrated.ok:
        return _LoadedDocument(error=migrated.error)

    return _LoadedDocument(document=migrated.document.model_copy(deep=True))


def _empty_document() -> BackupsDocumentV4:
    return BackupsDocumentV4(version=4, repos=[])


def _repo(url: str) -> BackupRepoV4:
    return BackupRepoV4(
        url=url,
        last_backup_at=None,
        last_checked_at=None,
        selected_last=False,
    )


def _write(paths: GtPaths, document: BackupsDocumentV4) -> str | None:
    written = write_backups_document(paths.backups_file, document)
    return None if written.ok else written.error


def _missing_list_error() -> str:
    return f"No backups list found. {ADD_HINT}"


def _find_repo(document: BackupsDocumentV4, canonical: str) -> int:
    return next(
        (
            index
            for index, repo in enumerate(document.repos)
            if _canonical_key(repo.url) == canonical
        ),
        -1,
    )


def _format_timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_index(token: str, maximum: int) -> int | None:
    """Parse a decimal index without converting an arbitrarily long string."""

    significant = token.lstrip("0") or "0"
    maximum_text = str(maximum)
    if len(significant) > len(maximum_text) or (
        len(significant) == len(maximum_text) and significant > maximum_text
    ):
        return None
    return int(significant)


def add_backup_repo(paths: GtPaths, ssh_url: str) -> AddBackupRepoResult:
    parsed, error = _canonicalize(ssh_url)
    if error is not None:
        return AddBackupRepoResult(ok=False, error=error)

    loaded = _load_for_mutation(paths)
    if loaded.document is None:
        if not loaded.missing:
            return AddBackupRepoResult(ok=False, error=loaded.error)
        document = _empty_document()
        created_file = True
    else:
        document = loaded.document
        created_file = False

    if _find_repo(document, parsed.canonical) != -1:
        return AddBackupRepoResult(
            ok=False,
            error=f"Duplicate repo (already listed): {parsed.ssh_url}",
        )

    document.repos.append(_repo(parsed.ssh_url))
    error = _write(paths, document)
    if error is not None:
        return AddBackupRepoResult(ok=False, error=error)
    return AddBackupRepoResult(
        ok=True,
        index=len(document.repos),
        document=document,
        created_file=created_file,
    )


def add_backup_repos(paths: GtPaths, urls: list[str]) -> AddBackupReposResult:
    loaded = _load_for_mutation(paths)
    if loaded.document is None:
        if not loaded.missing:
            return AddBackupReposResult(ok=False, error=loaded.error)
        document = _empty_document()
        created_file = True
    else:
        document = loaded.document
        created_file = False

    added: list[AddedRepo] = []
    failures: list[FailedRepo] = []
    for raw_url in urls:
        parsed, error = _canonicalize(raw_url)
        if error is not None:
            failures.append(FailedRepo(url=raw_url, error=error))
            continue
        if _find_repo(document, parsed.canonical) != -1:
            failures.append(
                FailedRepo(
                    url=raw_url,
                    error=f"Duplicate repo (already listed): {parsed.ssh_url}",
                )
            )
            continue
        document.repos.append(_repo(parsed.ssh_url))
        added.append(AddedRepo(url=parsed.ssh_url, index=len(document.repos)))

    if not added:
        return AddBackupReposResult(
            ok=not failures,
            added=tuple(added),
            failures=tuple(failures),
        )

    error = _write(paths, document)
    if error is not None:
        return AddBackupReposResult(
            ok=False,
            added=tuple(added),
            failures=tuple(failures),
            error=error,
        )
    return AddBackupReposResult(
        ok=not failures and len(added) == len(urls),
        added=tuple(added),
        failures=tuple(failures),
        document=document,
        created_file=created_file,
    )


def remove_backup_repo(paths: GtPaths, token: str) -> RemoveBackupRepoResult:
    loaded = _load_for_mutation(paths)
    if loaded.document is None:
        if loaded.missing:
            return RemoveBackupRepoResult(ok=False, error=_missing_list_error())
        return RemoveBackupRepoResult(ok=False, error=loaded.error)
    document = loaded.document
    if not document.repos:
        return RemoveBackupRepoResult(
            ok=False,
            error=f"Backups list is empty. {ADD_HINT}",
    )

    if _INDEX_RE.fullmatch(token):
        index = _parse_index(token, len(document.repos))
        if index is None or index < 1:
            return RemoveBackupRepoResult(
                ok=False,
                error=(
                    f"Index out of range: {token} "
                    f"(valid 1–{len(document.repos)})"
                ),
            )
        remove_index = index - 1
    else:
        parsed, error = _canonicalize(token)
        if error is not None:
            return RemoveBackupRepoResult(ok=False, error=error)
        remove_index = _find_repo(document, parsed.canonical)
        if remove_index == -1:
            return RemoveBackupRepoResult(
                ok=False,
                error=f"Repo not found in backups list: {token}",
            )

    removed = document.repos.pop(remove_index).url
    error = _write(paths, document)
    if error is not None:
        return RemoveBackupRepoResult(ok=False, error=error)
    return RemoveBackupRepoResult(ok=True, removed=removed, document=document)


def _update_timestamp(
    paths: GtPaths,
    ssh_url: str,
    *,
    now: datetime | None,
    include_backup: bool,
) -> BackupListUpdateResult:
    parsed, error = _canonicalize(ssh_url)
    if error is not None:
        return BackupListUpdateResult(ok=False, error=error)
    loaded = _load_for_mutation(paths)
    if loaded.document is None:
        if loaded.missing:
            return BackupListUpdateResult(ok=False, error=_missing_list_error())
        return BackupListUpdateResult(ok=False, error=loaded.error)
    document = loaded.document
    index = _find_repo(document, parsed.canonical)
    if index == -1:
        return BackupListUpdateResult(
            ok=False, error=f"Repo not found in backups list: {ssh_url}"
        )

    timestamp = _format_timestamp(now or datetime.now(timezone.utc))
    repo = document.repos[index]
    document.repos[index] = repo.model_copy(
        update={
            "last_backup_at": timestamp if include_backup else repo.last_backup_at,
            "last_checked_at": timestamp,
        }
    )
    error = _write(paths, document)
    if error is not None:
        return BackupListUpdateResult(ok=False, error=error)
    return BackupListUpdateResult(ok=True, document=document)


def record_last_backup_at(
    paths: GtPaths, ssh_url: str, *, now: datetime | None = None
) -> BackupListUpdateResult:
    return _update_timestamp(paths, ssh_url, now=now, include_backup=True)


def record_last_checked_at(
    paths: GtPaths, ssh_url: str, *, now: datetime | None = None
) -> BackupListUpdateResult:
    return _update_timestamp(paths, ssh_url, now=now, include_backup=False)


def set_selected_last(
    paths: GtPaths, selected_urls: list[str]
) -> BackupListUpdateResult:
    loaded = _load_for_mutation(paths)
    if loaded.document is None:
        if loaded.missing:
            return BackupListUpdateResult(ok=False, error=_missing_list_error())
        return BackupListUpdateResult(ok=False, error=loaded.error)
    document = loaded.document
    selected = {
        canonical
        for url in selected_urls
        if (canonical := _canonical_key(url)) is not None
    }
    document.repos = [
        repo.model_copy(update={"selected_last": _canonical_key(repo.url) in selected})
        for repo in document.repos
    ]
    error = _write(paths, document)
    if error is not None:
        return BackupListUpdateResult(ok=False, error=error)
    return BackupListUpdateResult(ok=True, document=document)
