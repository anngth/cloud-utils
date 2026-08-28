from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from skills_manager.config import (
    Catalog,
    CatalogError,
    CatalogSource,
    ConfigError,
    ConfigPaths,
    cross_source_skill_conflicts,
    initialize_config,
    migrate_profiles_to_catalog,
    read_config,
    remove_source_at,
    resolve_source_token,
    upsert_source,
    validate_catalog,
    write_catalog,
)


@pytest.mark.parametrize("version", [None, 0, 2, "1", True, 1.0])
def test_validate_catalog_requires_exact_integer_version_one(version: object) -> None:
    with pytest.raises(CatalogError, match="version 1"):
        validate_catalog({"version": version, "sources": []})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            {
                "version": 1,
                "sources": [
                    {"source": "a/one", "skills": []},
                    {"source": "a/one", "skills": []},
                ],
            },
            "Duplicate source: a/one",
        ),
        (
            {
                "version": 1,
                "sources": [
                    {"source": "a/one", "skills": ["review", "review"]}
                ],
            },
            "Duplicate skill: review",
        ),
        (
            {
                "version": 1,
                "sources": [
                    {"source": "a/one", "skills": ["review"]},
                    {"source": "b/two", "skills": ["review"]},
                ],
            },
            "review is selected from a different source: a/one",
        ),
    ],
)
def test_validate_catalog_rejects_duplicate_identity_and_ownership(
    raw: object, message: str
) -> None:
    with pytest.raises(CatalogError, match=message):
        validate_catalog(raw)


def test_catalog_ownership_error_redacts_unsafe_owner_source() -> None:
    raw = {
        "version": 1,
        "sources": [
            {
                "source": "https://user:secret@git.example.com/acme/skills.git",
                "skills": ["review"],
            },
            {"source": "b/two", "skills": ["review"]},
        ],
    }

    with pytest.raises(CatalogError) as caught:
        validate_catalog(raw)

    assert "review" in str(caught.value)
    assert "https://git.example.com/acme/skills.git" in str(caught.value)
    assert "user" not in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("skill", [None, 3, "", "   "])
def test_validate_catalog_rejects_invalid_skill_names(skill: object) -> None:
    raw = {"version": 1, "sources": [{"source": "a/one", "skills": [skill]}]}
    with pytest.raises(CatalogError, match="Invalid skill"):
        validate_catalog(raw)


def test_validate_catalog_returns_immutable_copy_without_reordering_input() -> None:
    raw = {
        "label": "catalog-extra",
        "version": 1,
        "sources": [
            {
                "note": {"nested": [1, 2]},
                "source": "z/repo",
                "skills": ["second", "first"],
            },
            {"source": "a/repo", "skills": []},
        ],
    }
    before = copy.deepcopy(raw)

    catalog = validate_catalog(raw)

    assert raw == before
    assert isinstance(catalog.sources, tuple)
    assert isinstance(catalog.sources[0].skills, tuple)
    assert [entry.source for entry in catalog.sources] == ["z/repo", "a/repo"]
    assert catalog.sources[0].skills == ("second", "first")
    with pytest.raises(Exception):
        catalog.sources = ()


def test_resolve_source_token_uses_one_based_index_or_canonical_identity() -> None:
    catalog = validate_catalog(
        {
            "version": 1,
            "sources": [
                {"source": "a/one", "skills": []},
                {"source": "b/two", "skills": ["x"]},
            ],
        }
    )

    assert resolve_source_token(catalog, "2") == (1, catalog.sources[1])
    assert resolve_source_token(catalog, "https://github.com/b/two.git") == (
        1,
        catalog.sources[1],
    )
    with pytest.raises(CatalogError, match="out of range: 0"):
        resolve_source_token(catalog, "0")
    with pytest.raises(CatalogError, match="out of range: 3"):
        resolve_source_token(catalog, "3")


def test_cross_source_conflicts_preserve_requested_order() -> None:
    catalog = validate_catalog(
        {
            "version": 1,
            "sources": [
                {"source": "a/one", "skills": ["alpha", "keep"]},
                {"source": "c/three", "skills": ["beta"]},
            ],
        }
    )
    assert cross_source_skill_conflicts(
        catalog, "b/two", ("alpha", "fresh", "beta")
    ) == (("alpha", "a/one"), ("beta", "c/three"))
    assert cross_source_skill_conflicts(
        catalog, "https://github.com/a/one", ("alpha", "new")
    ) == ()


def test_upsert_and_remove_return_new_catalogs_and_preserve_order_and_extras() -> None:
    raw = {
        "catalogNote": "keep",
        "version": 1,
        "sources": [
            {
                "entryNote": "keep-too",
                "source": "z/repo",
                "skills": ["old"],
            }
        ],
    }
    original = validate_catalog(raw)

    replaced = upsert_source(original, "z/repo", ("new", "later"))
    appended = upsert_source(replaced, "a/repo", ("fresh",))
    removed = remove_source_at(appended, 0)

    assert original.sources[0].skills == ("old",)
    assert replaced.sources[0].skills == ("new", "later")
    assert getattr(replaced, "catalogNote") == "keep"
    assert getattr(replaced.sources[0], "entryNote") == "keep-too"
    assert [entry.source for entry in appended.sources] == ["z/repo", "a/repo"]
    assert [entry.source for entry in removed.sources] == ["a/repo"]
    with pytest.raises(CatalogError, match="out of range"):
        remove_source_at(appended, -1)


def test_upsert_rejects_duplicate_and_cross_source_skills_without_mutation() -> None:
    catalog = Catalog(
        version=1,
        sources=(CatalogSource(source="a/one", skills=("alpha",)),),
    )
    with pytest.raises(CatalogError, match="Duplicate skill: beta"):
        upsert_source(catalog, "b/two", ("beta", "beta"))
    with pytest.raises(CatalogError, match="alpha.*different source"):
        upsert_source(catalog, "b/two", ("alpha",))
    assert catalog.sources == (CatalogSource(source="a/one", skills=("alpha",)),)


def test_migrate_profiles_unions_skills_and_preserves_first_seen_order() -> None:
    profiles = {
        "version": 1,
        "profiles": [
            {
                "name": "frontend",
                "sources": [
                    {
                        "source": "https://github.com/vercel-labs/agent-skills.git",
                        "skills": ["code-review"],
                    },
                    {"source": "anthropics/skills", "skills": []},
                ],
            },
            {
                "name": "review",
                "sources": [
                    {
                        "source": "vercel-labs/agent-skills",
                        "skills": ["code-review", "frontend-design"],
                    }
                ],
            },
        ],
    }
    before = copy.deepcopy(profiles)

    migrated = migrate_profiles_to_catalog(profiles)

    assert profiles == before
    assert [entry.source for entry in migrated.sources] == [
        "vercel-labs/agent-skills",
        "anthropics/skills",
    ]
    assert migrated.sources[0].skills == ("code-review", "frontend-design")


def test_migrate_profiles_rejects_cross_profile_skill_owner_conflict() -> None:
    profiles = {
        "version": 1,
        "profiles": [
            {
                "name": "a",
                "sources": [{"source": "one/repo", "skills": ["dup"]}],
            },
            {
                "name": "b",
                "sources": [{"source": "two/repo", "skills": ["dup"]}],
            },
        ],
    }
    with pytest.raises(CatalogError, match="dup.*different source"):
        migrate_profiles_to_catalog(profiles)


def test_config_paths_match_current_layout(tmp_path: Path) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    assert paths == ConfigPaths(
        config_dir=tmp_path,
        skm_dir=tmp_path / "skm",
        sources_file=tmp_path / "skm" / "sources.json",
        profiles_file=tmp_path / "skm" / "profiles.json",
        projects_file=tmp_path / "skm" / "projects.json",
        legacy_file=tmp_path / "skm" / "list.json",
    )


def test_empty_bootstrap_writes_current_catalog_once(tmp_path: Path) -> None:
    paths = initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=41)
    assert paths.sources_file.read_bytes() == (
        b'{\n  "version": 1,\n  "sources": []\n}\n'
    )
    paths.sources_file.write_bytes(b'{"sentinel":true}\n')
    initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=42)
    assert paths.sources_file.read_bytes() == b'{"sentinel":true}\n'


def test_default_config_dir_uses_home_without_fallback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths = initialize_config(env={"HOME": str(home)}, pid=11)
    assert paths.config_dir == (
        home / "Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
    )


def test_profiles_bootstrap_has_precedence_and_preserves_legacy_bytes(
    tmp_path: Path,
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    profiles_bytes = json.dumps(
        {
            "version": 1,
            "profiles": [
                {
                    "name": "default",
                    "sources": [
                        {"source": "z/repo", "skills": ["review"]},
                        {"source": "a/repo", "skills": []},
                    ],
                }
            ],
        },
        indent=4,
    ).encode()
    legacy_bytes = b'[{"source":"ignored/repo"}]\n'
    paths.profiles_file.write_bytes(profiles_bytes)
    paths.legacy_file.write_bytes(legacy_bytes)

    initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=12)

    assert [entry.source for entry in read_config(paths).sources] == [
        "z/repo",
        "a/repo",
    ]
    assert paths.profiles_file.read_bytes() == profiles_bytes
    assert paths.legacy_file.read_bytes() == legacy_bytes


@pytest.mark.parametrize(
    "legacy",
    [
        [{"source": "b/repo"}, {"source": "a/repo"}],
        {
            "presets": [
                {"source": "https://github.com/acme/skills.git"},
                {"source": "acme/skills"},
            ]
        },
    ],
)
def test_legacy_list_forms_migrate_without_modifying_input_file(
    tmp_path: Path, legacy: object
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    legacy_bytes = (json.dumps(legacy, indent=3) + "\n").encode()
    paths.legacy_file.write_bytes(legacy_bytes)

    initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=13)

    expected = (
        ["a/repo", "b/repo"]
        if isinstance(legacy, list)
        else ["acme/skills"]
    )
    assert [entry.source for entry in read_config(paths).sources] == expected
    assert paths.legacy_file.read_bytes() == legacy_bytes


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("list.json", b"{broken"),
        ("list.json", b'[{"missing":"source"}]\n'),
        ("profiles.json", b'{"version":1,"profiles":[]}\n'),
    ],
)
def test_invalid_legacy_input_is_byte_preserving_and_creates_no_catalog(
    tmp_path: Path, filename: str, contents: bytes
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    legacy_path = paths.skm_dir / filename
    legacy_path.write_bytes(contents)

    with pytest.raises(ConfigError):
        initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=14)

    assert legacy_path.read_bytes() == contents
    assert not paths.sources_file.exists()


def test_profile_skill_owner_conflict_creates_no_catalog(tmp_path: Path) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    profiles = {
        "version": 1,
        "profiles": [
            {
                "name": "a",
                "sources": [{"source": "one/repo", "skills": ["dup"]}],
            },
            {
                "name": "b",
                "sources": [{"source": "two/repo", "skills": ["dup"]}],
            },
        ],
    }
    paths.profiles_file.write_text(json.dumps(profiles))

    with pytest.raises(ConfigError, match="dup"):
        initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=15)

    assert not paths.sources_file.exists()


def test_read_invalid_catalog_is_byte_preserving_and_hides_library_errors(
    tmp_path: Path,
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    contents = b'{"version":2,"sources":[]}\n'
    paths.sources_file.write_bytes(contents)

    with pytest.raises(ConfigError, match="Invalid sources file") as caught:
        read_config(paths)

    assert "validation error" not in str(caught.value).lower()
    assert paths.sources_file.read_bytes() == contents


def test_write_preserves_all_json_key_and_value_order_and_unicode(
    tmp_path: Path,
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    catalog = validate_catalog(
        {
            "title": "Café",
            "version": 1,
            "sources": [
                {
                    "note": "đầu",
                    "source": "z/repo",
                    "skills": ["second", "first"],
                    "tail": {"b": 2, "a": 1},
                }
            ],
            "ending": True,
        }
    )

    write_catalog(paths, catalog, pid=72)

    assert paths.sources_file.read_bytes() == (
        '{\n'
        '  "title": "Café",\n'
        '  "version": 1,\n'
        '  "sources": [\n'
        '    {\n'
        '      "note": "đầu",\n'
        '      "source": "z/repo",\n'
        '      "skills": [\n'
        '        "second",\n'
        '        "first"\n'
        '      ],\n'
        '      "tail": {\n'
        '        "b": 2,\n'
        '        "a": 1\n'
        '      }\n'
        '    }\n'
        '  ],\n'
        '  "ending": true\n'
        '}\n'
    ).encode()


def test_failed_atomic_write_removes_only_its_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    unrelated = paths.sources_file.with_name("sources.json.99.tmp")
    unrelated.write_text("keep")
    monkeypatch.setattr(
        Path,
        "replace",
        lambda *_: (_ for _ in ()).throw(OSError("boom")),
    )
    with pytest.raises(OSError, match="boom"):
        write_catalog(paths, Catalog(version=1, sources=()), pid=73)
    assert not paths.sources_file.with_name("sources.json.73.tmp").exists()
    assert unrelated.read_text() == "keep"


def test_write_validates_before_touching_existing_catalog(tmp_path: Path) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    original = b'{"version":1,"sources":[]}\n'
    paths.sources_file.write_bytes(original)
    invalid = {
        "version": 1,
        "sources": [
            {"source": "a/one", "skills": ["dup"]},
            {"source": "b/two", "skills": ["dup"]},
        ],
    }

    with pytest.raises(CatalogError, match="dup"):
        write_catalog(paths, invalid, pid=74)

    assert paths.sources_file.read_bytes() == original
    assert not paths.sources_file.with_name("sources.json.74.tmp").exists()
