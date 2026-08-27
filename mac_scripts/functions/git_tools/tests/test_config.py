import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from git_tools.config import (
    EMPTY_BACKUPS,
    BackupRepoV2,
    BackupRepoV3,
    BackupRepoV4,
    BackupsDocumentV1,
    BackupsDocumentV2,
    BackupsDocumentV3,
    BackupsDocumentV4,
    default_config_dir,
    format_display_path,
    is_iso_utc_timestamp,
    load_backups_document,
    migrate_backups_document,
    read_backups_document,
    resolve_gt_paths,
    write_backups_document,
)


V4_BYTES = (
    b'{\n  "version": 4,\n  "repos": [\n'
    b'    {\n      "url": "git@github.com:org/app.git",\n'
    b'      "lastBackupAt": null,\n      "lastCheckedAt": null,\n'
    b'      "selectedLast": false\n    }\n  ]\n}\n'
)


def test_default_config_dir_matches_skm_icloud_backups_root() -> None:
    assert default_config_dir({"HOME": "/Users/me"}) == Path(
        "/Users/me/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
    )
    assert default_config_dir({}) == Path(
        "/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
    )


def test_resolve_gt_paths_uses_environment_override() -> None:
    paths = resolve_gt_paths(
        {"CLOUD_UTILS_CONFIG_DIR": "/tmp/cu", "HOME": "/Users/me"}
    )
    assert paths.config_dir == Path("/tmp/cu")
    assert paths.gt_dir == Path("/tmp/cu/gt")
    assert paths.backups_file == Path("/tmp/cu/gt/backups.json")


def test_resolve_gt_paths_ignores_empty_environment_override() -> None:
    paths = resolve_gt_paths(
        {"CLOUD_UTILS_CONFIG_DIR": "", "HOME": "/Users/me"}
    )
    assert paths.config_dir == default_config_dir({"HOME": "/Users/me"})


@pytest.mark.parametrize(
    ("path", "home", "temp_dir", "expected"),
    [
        (Path("/Users/me"), Path("/Users/me"), Path("/tmp"), "~"),
        (
            Path(
                "/Users/me/Library/Mobile Documents/com~apple~CloudDocs/"
                "Backups/cloud-utils/gt/backups.json"
            ),
            Path("/Users/me"),
            Path("/tmp"),
            "~/Library/Mobile Documents/com~apple~CloudDocs/Backups/"
            "cloud-utils/gt/backups.json",
        ),
        (
            Path("/var/folders/xx/T/gt-backup-abc/mirror.git"),
            Path("/Users/me"),
            Path("/var/folders/xx/T"),
            "gt-backup-abc/mirror.git",
        ),
        (
            Path("/elsewhere/file.json"),
            Path("/Users/me"),
            Path("/tmp"),
            "/elsewhere/file.json",
        ),
        (
            Path("/Users/mean/file.json"),
            Path("/Users/me"),
            Path("/tmp"),
            "/Users/mean/file.json",
        ),
    ],
)
def test_format_display_path_uses_only_path_boundaries(
    path: Path, home: Path, temp_dir: Path, expected: str
) -> None:
    assert format_display_path(path, home=home, temp_dir=temp_dir) == expected


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-08T09:30:00Z",
        "2026-08-08T09:30:00.000Z",
        "2026-02-31T24:00:00Z",
        "0000-01-01T00:00:00Z",
    ],
)
def test_is_iso_utc_timestamp_accepts_node_valid_exact_z_forms(value: str) -> None:
    assert is_iso_utc_timestamp(value) is True


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        "",
        "yesterday",
        "2026-08-08T09:30:00+07:00",
        "2026-08-08T09:30:00.0Z",
        "2026-08-08T09:30:00.0000Z",
        "2026-13-08T09:30:00Z",
        "2026-08-00T09:30:00Z",
        "2026-08-08T24:00:01Z",
        "2026-08-08T23:60:00Z",
        "2026-08-08T23:59:60Z",
    ],
)
def test_is_iso_utc_timestamp_rejects_non_node_timestamp_forms(value: object) -> None:
    assert is_iso_utc_timestamp(value) is False


def test_versioned_models_use_aliases_ignore_extras_and_preserve_field_order() -> None:
    v2 = BackupRepoV2.model_validate(
        {
            "url": "git@github.com:org/app.git",
            "lastBackupAt": None,
            "ignored": "value",
        }
    )
    v3 = BackupRepoV3.model_validate(
        {
            "url": "git@github.com:org/app.git",
            "lastBackupAt": None,
            "lastCheckedAt": "2026-08-08T09:30:00Z",
        }
    )
    v4 = BackupRepoV4(
        url="git@github.com:org/app.git",
        last_backup_at=None,
        last_checked_at=None,
        selected_last=False,
    )

    assert v2.model_dump(by_alias=True) == {
        "url": "git@github.com:org/app.git",
        "lastBackupAt": None,
    }
    assert v3.last_checked_at == "2026-08-08T09:30:00Z"
    assert list(v4.model_dump(by_alias=True)) == [
        "url",
        "lastBackupAt",
        "lastCheckedAt",
        "selectedLast",
    ]


@pytest.mark.parametrize(
    ("model", "value"),
    [
        (BackupRepoV2, {"url": 123, "lastBackupAt": None}),
        (
            BackupRepoV4,
            {
                "url": "git@github.com:org/app.git",
                "lastBackupAt": None,
                "lastCheckedAt": None,
                "selectedLast": 1,
            },
        ),
    ],
)
def test_versioned_models_do_not_coerce_repo_fields(model: type, value: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(value)


def test_empty_backups_is_a_v4_document() -> None:
    assert EMPTY_BACKUPS == BackupsDocumentV4(version=4, repos=[])


@pytest.mark.parametrize(
    ("raw", "document_type", "repo_type"),
    [
        ({"version": 1, "repos": ["git@github.com:org/app.git"]}, BackupsDocumentV1, str),
        (
            {
                "version": 2,
                "repos": [
                    {"url": "git@github.com:org/app.git", "lastBackupAt": None}
                ],
            },
            BackupsDocumentV2,
            BackupRepoV2,
        ),
        (
            {
                "version": 3,
                "repos": [
                    {
                        "url": "git@github.com:org/app.git",
                        "lastBackupAt": None,
                        "lastCheckedAt": "2026-08-08T09:30:00.000Z",
                    }
                ],
            },
            BackupsDocumentV3,
            BackupRepoV3,
        ),
        (
            {
                "version": 4,
                "repos": [
                    {
                        "url": "git@github.com:org/app.git",
                        "lastBackupAt": None,
                        "lastCheckedAt": None,
                        "selectedLast": True,
                    }
                ],
            },
            BackupsDocumentV4,
            BackupRepoV4,
        ),
    ],
)
def test_read_backups_document_accepts_each_version_without_coercion(
    tmp_path: Path, raw: dict, document_type: type, repo_type: type
) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_text(json.dumps(raw), encoding="utf-8")

    result = read_backups_document(backups_file)

    assert result.ok is True
    assert isinstance(result.document, document_type)
    assert isinstance(result.document.repos[0], repo_type)
    assert result.error is None
    assert result.missing is False


def test_read_backups_document_reports_missing_file(tmp_path: Path) -> None:
    backups_file = tmp_path / "missing.json"

    result = read_backups_document(backups_file)

    assert result.ok is False
    assert result.missing is True
    assert result.document is None
    assert result.error == f"Backups file not found: {backups_file}"


def test_read_backups_document_reports_other_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_text("{}", encoding="utf-8")

    def fail_read_text(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    result = read_backups_document(backups_file)

    assert result.ok is False
    assert result.missing is False
    assert result.error == f"Could not read backups file: {backups_file}"


def test_read_backups_document_rejects_invalid_json(tmp_path: Path) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_text("{", encoding="utf-8")

    result = read_backups_document(backups_file)

    assert result.ok is False
    assert result.error == f"Invalid JSON in backups file: {backups_file}"


@pytest.mark.parametrize(
    "raw",
    [
        {"version": 1, "repos": "nope"},
        {"version": True, "repos": []},
        {"version": 4.0, "repos": []},
        {
            "version": 4,
            "repos": [
                {
                    "url": "git@github.com:org/app.git",
                    "lastBackupAt": None,
                    "lastCheckedAt": None,
                    "selectedLast": "yes",
                }
            ],
        },
    ],
)
def test_read_backups_document_maps_shape_errors_to_stable_domain_message(
    tmp_path: Path, raw: dict
) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_text(json.dumps(raw), encoding="utf-8")

    result = read_backups_document(backups_file)

    assert result.ok is False
    assert result.error == f"Invalid backups document: {backups_file}"
    assert "pydantic" not in result.error.lower()
    assert "errors.pydantic.dev" not in result.error


@pytest.mark.parametrize(
    "raw",
    [
        {
            "version": 2,
            "repos": [{"url": "repo", "last_backup_at": None}],
        },
        {
            "version": 3,
            "repos": [
                {
                    "url": "repo",
                    "last_backup_at": None,
                    "last_checked_at": None,
                }
            ],
        },
        {
            "version": 4,
            "repos": [
                {
                    "url": "repo",
                    "last_backup_at": None,
                    "last_checked_at": None,
                    "selected_last": False,
                }
            ],
        },
    ],
)
def test_raw_documents_require_canonical_camel_case_aliases(
    tmp_path: Path, raw: dict
) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_text(json.dumps(raw), encoding="utf-8")

    read_result = read_backups_document(backups_file)
    migrate_result = migrate_backups_document(raw)

    assert read_result.ok is False
    assert read_result.error == f"Invalid backups document: {backups_file}"
    assert migrate_result.ok is False
    assert migrate_result.error == "Invalid backups document"


def test_write_raw_document_requires_canonical_camel_case_aliases(
    tmp_path: Path,
) -> None:
    backups_file = tmp_path / "backups.json"
    raw = {
        "version": 4,
        "repos": [
            {
                "url": "repo",
                "last_backup_at": None,
                "last_checked_at": None,
                "selected_last": False,
            }
        ],
    }

    result = write_backups_document(backups_file, raw)

    assert result.ok is False
    assert result.error == "Invalid backups document"
    assert not backups_file.exists()


def test_raw_documents_keep_accepting_unrelated_extra_keys(tmp_path: Path) -> None:
    backups_file = tmp_path / "backups.json"
    raw = {
        "version": 4,
        "documentExtra": "ignored",
        "repos": [
            {
                "url": "repo",
                "lastBackupAt": None,
                "lastCheckedAt": None,
                "selectedLast": False,
                "last_backup_at": "ignored",
                "repoExtra": "ignored",
            }
        ],
    }
    backups_file.write_text(json.dumps(raw), encoding="utf-8")

    result = read_backups_document(backups_file)

    assert result.ok is True
    assert result.document.repos[0].last_backup_at is None


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            {
                "version": 2,
                "repos": [
                    {
                        "url": "git@github.com:org/app.git",
                        "lastBackupAt": 123,
                    }
                ],
            },
            "Invalid lastBackupAt for git@github.com:org/app.git: 123",
        ),
        (
            {
                "version": 4,
                "repos": [
                    {
                        "url": "git@github.com:org/app.git",
                        "lastBackupAt": None,
                        "lastCheckedAt": "not-iso",
                        "selectedLast": False,
                    }
                ],
            },
            'Invalid lastCheckedAt for git@github.com:org/app.git: "not-iso"',
        ),
        (
            {
                "version": 3,
                "repos": [
                    {
                        "url": 42,
                        "lastBackupAt": {"bad": True},
                        "lastCheckedAt": None,
                    }
                ],
            },
            'Invalid lastBackupAt for repo at index 0: {"bad":true}',
        ),
    ],
)
def test_read_backups_document_reports_exact_timestamp_errors(
    tmp_path: Path, raw: dict, message: str
) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_text(json.dumps(raw), encoding="utf-8")

    result = read_backups_document(backups_file)

    assert result.ok is False
    assert result.error == message


@pytest.mark.parametrize("version", [1, 2, 3])
def test_migrate_backups_document_upgrades_versions_in_repo_order(version: int) -> None:
    urls = ["git@github.com:org/one.git", "git@github.com:org/two.git"]
    if version == 1:
        raw = {"version": 1, "repos": urls}
    elif version == 2:
        raw = {
            "version": 2,
            "repos": [
                {"url": urls[0], "lastBackupAt": "2026-01-01T00:00:00.000Z"},
                {"url": urls[1], "lastBackupAt": None},
            ],
        }
    else:
        raw = {
            "version": 3,
            "repos": [
                {
                    "url": urls[0],
                    "lastBackupAt": "2026-01-01T00:00:00.000Z",
                    "lastCheckedAt": "2026-01-02T00:00:00Z",
                },
                {
                    "url": urls[1],
                    "lastBackupAt": None,
                    "lastCheckedAt": None,
                },
            ],
        }

    result = migrate_backups_document(raw)

    assert result.ok is True
    assert result.migrated is True
    assert isinstance(result.document, BackupsDocumentV4)
    assert [repo.url for repo in result.document.repos] == urls
    assert result.document.repos[0].last_backup_at == (
        None if version == 1 else "2026-01-01T00:00:00.000Z"
    )
    assert result.document.repos[0].last_checked_at == (
        "2026-01-02T00:00:00Z" if version == 3 else None
    )
    assert [repo.selected_last for repo in result.document.repos] == [False, False]


def test_migrate_backups_document_returns_v4_without_migration() -> None:
    document = BackupsDocumentV4(
        version=4,
        repos=[
            BackupRepoV4(
                url="git@github.com:org/app.git",
                last_backup_at=None,
                last_checked_at=None,
                selected_last=True,
            )
        ],
    )

    result = migrate_backups_document(document)

    assert result.ok is True
    assert result.document is document
    assert result.migrated is False


def test_migrate_backups_document_uses_pathless_stable_error() -> None:
    result = migrate_backups_document({"version": 4, "repos": "bad"})

    assert result.ok is False
    assert result.document is None
    assert result.migrated is False
    assert result.error == "Invalid backups document"


def test_migrate_backups_document_reports_exact_timestamp_error() -> None:
    result = migrate_backups_document(
        {
            "version": 2,
            "repos": [
                {
                    "url": "git@github.com:org/app.git",
                    "lastBackupAt": "not-iso",
                }
            ],
        }
    )

    assert result.ok is False
    assert result.error == (
        'Invalid lastBackupAt for git@github.com:org/app.git: "not-iso"'
    )


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        (1.0, "1"),
        (-0.0, "0"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (-12.5, "-12.5"),
        (9_007_199_254_740_993, "9007199254740992"),
        ("bad", '"bad"'),
        ("\ud800", '"\\ud800"'),
        ("\ud83d\ude00", '"😀"'),
        (True, "true"),
        (
            [1.0, 1e-7, None, True, "é"],
            '[1,1e-7,null,true,"é"]',
        ),
        (
            {
                "fixed": 1e20,
                "exponent": 1e-7,
                "nil": None,
                "flag": False,
                "text": "é",
            },
            '{"fixed":100000000000000000000,"exponent":1e-7,'
            '"nil":null,"flag":false,"text":"é"}',
        ),
        (float("inf"), "null"),
    ],
)
def test_migrate_backups_document_formats_timestamp_values_like_json_stringify(
    value: object, rendered: str
) -> None:
    result = migrate_backups_document(
        {
            "version": 2,
            "repos": [{"url": "repo", "lastBackupAt": value}],
        }
    )

    assert result.ok is False
    assert result.error == f"Invalid lastBackupAt for repo: {rendered}"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_read_backups_document_rejects_nonstandard_json_numbers(
    tmp_path: Path, constant: str
) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_text(
        '{"version":2,"repos":[{"url":"repo","lastBackupAt":'
        f"{constant}" + "}]}",
        encoding="utf-8",
    )

    result = read_backups_document(backups_file)

    assert result.ok is False
    assert result.error == f"Invalid JSON in backups file: {backups_file}"


def test_write_backups_document_creates_parent_and_serializes_exact_bytes(
    tmp_path: Path,
) -> None:
    backups_file = tmp_path / "nested" / "gt" / "backups.json"
    document = BackupsDocumentV4(
        version=4,
        repos=[
            BackupRepoV4(
                url="git@github.com:org/app.git",
                last_backup_at=None,
                last_checked_at=None,
                selected_last=False,
            )
        ],
    )

    result = write_backups_document(backups_file, document)

    assert result.ok is True
    assert result.error is None
    assert backups_file.read_bytes() == V4_BYTES
    assert not Path(f"{backups_file}.tmp").exists()


@pytest.mark.parametrize(
    ("url", "encoded_url"),
    [
        ("\ud800", b"\\ud800"),
        ("\udc00", b"\\udc00"),
        ("\ud83d\ude00", b"\xf0\x9f\x98\x80"),
        ("café", b"caf\xc3\xa9"),
    ],
)
def test_write_backups_document_serializes_utf16_surrogates_like_javascript(
    tmp_path: Path, url: str, encoded_url: bytes
) -> None:
    backups_file = tmp_path / "backups.json"
    document = {
        "version": 4,
        "repos": [
            {
                "url": url,
                "lastBackupAt": None,
                "lastCheckedAt": None,
                "selectedLast": False,
            }
        ],
    }

    result = write_backups_document(backups_file, document)

    assert result.ok is True
    assert backups_file.read_bytes() == (
        b'{\n  "version": 4,\n  "repos": [\n'
        b'    {\n      "url": "'
        + encoded_url
        + b'",\n      "lastBackupAt": null,\n      "lastCheckedAt": null,\n'
        b'      "selectedLast": false\n    }\n  ]\n}\n'
    )


@pytest.mark.parametrize(
    ("encoded_url", "url"),
    [(b"\\ud800", "\ud800"), (b"\\udc00", "\udc00")],
)
def test_read_backups_document_accepts_json_lone_surrogates(
    tmp_path: Path, encoded_url: bytes, url: str
) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_bytes(
        b'{"version":4,"repos":[{"url":"'
        + encoded_url
        + b'","lastBackupAt":null,"lastCheckedAt":null,"selectedLast":false}]}'
    )

    result = read_backups_document(backups_file)

    assert result.ok is True
    assert result.document.repos[0].url == url


def test_write_backups_document_cleans_temp_after_serialization_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backups_file = tmp_path / "backups.json"
    temp_file = Path(f"{backups_file}.tmp")
    temp_file.write_bytes(b"stale")

    def fail_serialization(*args: object, **kwargs: object) -> str:
        raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogate")

    monkeypatch.setattr("git_tools.config.json.dumps", fail_serialization)

    result = write_backups_document(backups_file, EMPTY_BACKUPS)

    assert result.ok is False
    assert "can't encode character" in result.error
    assert not temp_file.exists()
    assert not backups_file.exists()


def test_write_backups_document_replaces_from_exact_tmp_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backups_file = tmp_path / "backups.json"
    temp_file = Path(f"{backups_file}.tmp")
    calls: list[tuple[Path, Path, bytes]] = []
    real_replace = os.replace

    def recording_replace(source: Path, destination: Path) -> None:
        calls.append((Path(source), Path(destination), Path(source).read_bytes()))
        real_replace(source, destination)

    monkeypatch.setattr("git_tools.config.os.replace", recording_replace)

    result = write_backups_document(backups_file, EMPTY_BACKUPS)

    assert result.ok is True
    assert calls == [(temp_file, backups_file, b'{\n  "version": 4,\n  "repos": []\n}\n')]


def test_write_backups_document_cleans_temp_after_failed_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backups_file = tmp_path / "gt" / "backups.json"
    temp_file = Path(f"{backups_file}.tmp")

    def fail_replace(source: Path, destination: Path) -> None:
        assert Path(source) == temp_file
        assert Path(destination) == backups_file
        assert temp_file.exists()
        raise OSError("rename failed")

    monkeypatch.setattr("git_tools.config.os.replace", fail_replace)

    result = write_backups_document(backups_file, EMPTY_BACKUPS)

    assert result.ok is False
    assert result.error == "rename failed"
    assert not temp_file.exists()
    assert not backups_file.exists()


@pytest.mark.parametrize(
    "document",
    [
        {"version": 1, "repos": ["git@github.com:org/app.git"]},
        {
            "version": 4,
            "repos": [
                {
                    "url": "git@github.com:org/app.git",
                    "lastBackupAt": None,
                    "lastCheckedAt": None,
                    "selectedLast": "false",
                }
            ],
        },
    ],
)
def test_write_backups_document_rejects_non_v4_before_filesystem_changes(
    tmp_path: Path, document: dict
) -> None:
    backups_file = tmp_path / "gt" / "backups.json"

    result = write_backups_document(backups_file, document)

    assert result.ok is False
    assert result.error == "Invalid backups document"
    assert not backups_file.parent.exists()


def test_load_backups_document_migrates_and_rewrites_exact_bytes(tmp_path: Path) -> None:
    backups_file = tmp_path / "backups.json"
    backups_file.write_text(
        json.dumps({"version": 1, "repos": ["git@github.com:org/app.git"]}),
        encoding="utf-8",
    )

    result = load_backups_document(backups_file)

    assert result.ok is True
    assert result.migrated is True
    assert isinstance(result.document, BackupsDocumentV4)
    assert backups_file.read_bytes() == V4_BYTES


def test_load_backups_document_does_not_rewrite_v4(tmp_path: Path) -> None:
    backups_file = tmp_path / "backups.json"
    compact = (
        b'{"version":4,"repos":[{"url":"git@github.com:org/app.git",'
        b'"lastBackupAt":null,"lastCheckedAt":null,"selectedLast":false}]}\n'
    )
    backups_file.write_bytes(compact)

    result = load_backups_document(backups_file)

    assert result.ok is True
    assert result.migrated is False
    assert backups_file.read_bytes() == compact


def test_load_backups_document_propagates_read_error(tmp_path: Path) -> None:
    backups_file = tmp_path / "missing.json"

    result = load_backups_document(backups_file)

    assert result.ok is False
    assert result.missing is True
    assert result.error == f"Backups file not found: {backups_file}"
