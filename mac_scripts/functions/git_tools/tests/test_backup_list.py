"""Managed backup-list mutation contracts."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from git_tools.config import GtPaths, resolve_gt_paths
from git_tools.backup_list import (
    add_backup_repo,
    add_backup_repos,
    record_last_backup_at,
    record_last_checked_at,
    remove_backup_repo,
    set_selected_last,
)


URL_A = "git@github.com:Org/Foo.git"
URL_B = "git@gitlab.com:acme/bar.git"
URL_C = "git@github.com:existing/repo.git"
ADD_HINT = "Use `gt backup add <ssh-url>` to add a repo first."


def paths_for(tmp_path: Path) -> GtPaths:
    return resolve_gt_paths({"CLOUD_UTILS_CONFIG_DIR": str(tmp_path), "HOME": "/Users/me"})


def repo(url: str, **overrides: object) -> dict[str, object]:
    return {
        "url": url,
        "lastBackupAt": None,
        "lastCheckedAt": None,
        "selectedLast": False,
        **overrides,
    }


def seed(paths: GtPaths, document: dict[str, object]) -> None:
    paths.gt_dir.mkdir(parents=True, exist_ok=True)
    paths.backups_file.write_text(json.dumps(document) + "\n", encoding="utf-8")


def read_repos(paths: GtPaths) -> list[dict[str, object]]:
    return json.loads(paths.backups_file.read_text(encoding="utf-8"))["repos"]


def test_add_migrates_v1_and_uses_v4_defaults(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    seed(paths, {"version": 1, "repos": [URL_C]})

    result = add_backup_repo(paths, URL_A)

    assert result.ok is True
    assert result.index == 2
    assert result.created_file is False
    assert result.document is not None
    assert [item.model_dump(by_alias=True) for item in result.document.repos] == [
        repo(URL_C),
        repo(URL_A),
    ]
    assert json.loads(paths.backups_file.read_text(encoding="utf-8"))["version"] == 4


def test_add_canonicalizes_and_rejects_duplicate(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    first = add_backup_repo(paths, "git@GitHub.com:Org/Foo.git")
    duplicate = add_backup_repo(paths, "git@github.com:Org/Foo")

    assert first.ok is True
    assert first.created_file is True
    assert first.index == 1
    assert first.document is not None
    assert first.document.repos[0].url == URL_A
    assert duplicate.ok is False
    assert duplicate.error == f"Duplicate repo (already listed): {URL_A}"


def test_add_rejects_invalid_url_without_creating_file(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)

    result = add_backup_repo(paths, "https://github.com/org/app.git")

    assert result.ok is False
    assert result.error == "HTTPS URLs are not supported"
    assert paths.backups_file.exists() is False


def test_multi_add_writes_once_and_preserves_input_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = paths_for(tmp_path)
    import git_tools.backup_list as backup_list

    original_write = backup_list.write_backups_document
    writes = []

    def write_spy(path: Path, document: object):
        writes.append(type("Call", (), {"path": path, "document": document})())
        return original_write(path, document)

    monkeypatch.setattr(backup_list, "write_backups_document", write_spy)

    result = add_backup_repos(paths, [URL_A, "git@gitlab.com:acme/bar"])

    assert result.ok is True
    assert [(entry.url, entry.index) for entry in result.added] == [(URL_A, 1), (URL_B, 2)]
    assert result.failures == ()
    assert result.document is not None
    assert [call.path for call in writes] == [paths.backups_file]
    assert [entry.url for entry in result.document.repos] == [URL_A, URL_B]


def test_multi_add_partially_succeeds_once_and_collects_failures_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    seed(paths, {"version": 1, "repos": [URL_C]})
    import git_tools.backup_list as backup_list

    original_write = backup_list.write_backups_document
    writes = []

    def write_spy(path: Path, document: object):
        writes.append(type("Call", (), {"path": path})())
        return original_write(path, document)

    monkeypatch.setattr(backup_list, "write_backups_document", write_spy)

    result = add_backup_repos(paths, [URL_A, URL_C, "not-a-url", URL_B])

    assert result.ok is False
    assert [(entry.url, entry.index) for entry in result.added] == [(URL_A, 2), (URL_B, 3)]
    assert [(entry.url, entry.error) for entry in result.failures] == [
        (URL_C, f"Duplicate repo (already listed): {URL_C}"),
        ("not-a-url", "Invalid SSH URL"),
    ]
    assert result.document is not None
    assert [call.path for call in writes] == [paths.backups_file]
    assert [entry.url for entry in result.document.repos] == [URL_C, URL_A, URL_B]


def test_multi_add_all_invalid_does_not_write_or_migrate(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    original = {"version": 1, "repos": [URL_C]}
    seed(paths, original)

    result = add_backup_repos(paths, ["https://github.com/a/one.git", "not-a-url"])

    assert result.ok is False
    assert result.added == ()
    assert [entry.error for entry in result.failures] == [
        "HTTPS URLs are not supported",
        "Invalid SSH URL",
    ]
    assert result.document is None
    assert json.loads(paths.backups_file.read_text(encoding="utf-8")) == original


def test_multi_add_rejects_canonical_duplicate_inside_batch(tmp_path: Path) -> None:
    result = add_backup_repos(
        paths_for(tmp_path), [URL_A, "git@GitHub.com:Org/Foo"]
    )

    assert result.ok is False
    assert [(entry.url, entry.index) for entry in result.added] == [(URL_A, 1)]
    assert [(entry.url, entry.error) for entry in result.failures] == [
        ("git@GitHub.com:Org/Foo", f"Duplicate repo (already listed): {URL_A}"),
    ]


def test_remove_by_one_based_index_preserves_remaining_values(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    seed(
        paths,
        {
            "version": 3,
            "repos": [
                {
                    "url": URL_A,
                    "lastBackupAt": "2026-01-01T00:00:00.000Z",
                    "lastCheckedAt": None,
                },
                {"url": URL_B, "lastBackupAt": None, "lastCheckedAt": None},
            ],
        },
    )

    result = remove_backup_repo(paths, "2")

    assert result.ok is True
    assert result.removed == URL_B
    assert result.document is not None
    assert [item.model_dump(by_alias=True) for item in result.document.repos] == [
        repo(URL_A, lastBackupAt="2026-01-01T00:00:00.000Z")
    ]


def test_remove_by_canonical_url(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    seed(paths, {"version": 1, "repos": [URL_A, URL_B]})

    result = remove_backup_repo(paths, "git@GitLab.com:acme/bar")

    assert result.ok is True
    assert result.removed == URL_B
    assert read_repos(paths) == [repo(URL_A)]


@pytest.mark.parametrize("token", ["0", "3"])
def test_remove_out_of_range_uses_en_dash_message(tmp_path: Path, token: str) -> None:
    paths = paths_for(tmp_path)
    seed(paths, {"version": 1, "repos": [URL_A]})

    result = remove_backup_repo(paths, token)

    assert result.ok is False
    assert result.error == f"Index out of range: {token} (valid 1–1)"


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (None, f"No backups list found. {ADD_HINT}"),
        ({"version": 1, "repos": []}, f"Backups list is empty. {ADD_HINT}"),
    ],
)
def test_remove_gives_missing_and_empty_hints(
    tmp_path: Path, document: dict[str, object] | None, expected: str
) -> None:
    paths = paths_for(tmp_path)
    if document is not None:
        seed(paths, document)

    result = remove_backup_repo(paths, "1")

    assert result.ok is False
    assert result.error == expected


def test_timestamp_updates_use_one_utc_millisecond_value(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    seed(paths, {"version": 1, "repos": [URL_A]})
    now = datetime(2026, 8, 8, 9, 30, 0, 987654, tzinfo=timezone.utc)

    result = record_last_backup_at(paths, URL_A, now=now)

    assert result.ok is True
    assert result.document is not None
    entry = result.document.repos[0]
    assert entry.last_backup_at == "2026-08-08T09:30:00.987Z"
    assert entry.last_checked_at == "2026-08-08T09:30:00.987Z"


def test_checked_timestamp_updates_only_checked_value(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    seed(
        paths,
        {
            "version": 3,
            "repos": [
                {
                    "url": URL_A,
                    "lastBackupAt": "2020-01-01T00:00:00.000Z",
                    "lastCheckedAt": None,
                }
            ],
        },
    )

    result = record_last_checked_at(
        paths, URL_A, now=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    )

    assert result.ok is True
    assert result.document is not None
    assert result.document.repos[0].last_backup_at == "2020-01-01T00:00:00.000Z"
    assert result.document.repos[0].last_checked_at == "2026-08-08T12:00:00.000Z"


def test_timestamp_updates_give_missing_and_not_found_errors(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)

    missing = record_last_checked_at(paths, URL_A)
    seed(paths, {"version": 1, "repos": [URL_B]})
    not_found = record_last_backup_at(paths, URL_A)

    assert missing.error == f"No backups list found. {ADD_HINT}"
    assert not_found.error == f"Repo not found in backups list: {URL_A}"


def test_set_selected_last_maps_canonical_selected_urls(tmp_path: Path) -> None:
    paths = paths_for(tmp_path)
    seed(
        paths,
        {
            "version": 4,
            "repos": [repo(URL_A), repo(URL_B, selectedLast=True)],
        },
    )

    result = set_selected_last(paths, ["git@GitHub.com:Org/Foo"])

    assert result.ok is True
    assert result.document is not None
    assert [entry.selected_last for entry in result.document.repos] == [True, False]
    assert [entry["selectedLast"] for entry in read_repos(paths)] == [True, False]
