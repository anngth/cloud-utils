from __future__ import annotations

import copy
import json
import locale
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_duplicate_source_error_redacts_unsafe_source() -> None:
    unsafe = "https://user:secret@git.example.com/acme/skills.git"
    raw = {
        "version": 1,
        "sources": [
            {"source": unsafe, "skills": []},
            {"source": unsafe, "skills": []},
        ],
    }

    with pytest.raises(CatalogError) as caught:
        validate_catalog(raw)

    assert "Duplicate source" in str(caught.value)
    assert "https://git.example.com/acme/skills.git" in str(caught.value)
    assert "user" not in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "unsafe",
    [
        "https:user:secret@example.com/acme/repo.git",
        "https:/user:secret@example.com/acme/repo.git",
        "https:////user:secret@example.com/acme/repo.git",
    ],
)
def test_duplicate_source_slash_variants_never_expose_credentials(
    unsafe: str,
) -> None:
    raw = {
        "version": 1,
        "sources": [
            {"source": unsafe, "skills": []},
            {"source": unsafe, "skills": []},
        ],
    }

    with pytest.raises(CatalogError) as caught:
        validate_catalog(raw)

    message = str(caught.value)
    assert "Duplicate source: https://example.com/acme/repo.git" == message
    assert "user" not in message
    assert "secret" not in message


@pytest.mark.parametrize(
    "unsafe",
    [
        r"https:user\name:secret@example.com/a.git",
        r"https:user\name:password@example.com/a.git?token=query-secret",
    ],
)
def test_duplicate_raw_provider_candidate_never_exposes_credentials(
    unsafe: str,
) -> None:
    raw = {
        "version": 1,
        "sources": [
            {"source": unsafe, "skills": []},
            {"source": unsafe, "skills": []},
        ],
    }

    with pytest.raises(CatalogError) as caught:
        validate_catalog(raw)

    message = str(caught.value).lower()
    assert message == "duplicate source: [unsafe source redacted]"
    assert not any(
        credential in message
        for credential in ("user", "name", "password", "secret", "token")
    )


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


def test_catalog_and_source_extras_are_deeply_frozen_and_serialize_normally(
    tmp_path: Path,
) -> None:
    raw = {
        "meta": {"z": [1, {"nested": [2]}], "a": "last"},
        "version": 1,
        "sources": [
            {
                "source": "a/repo",
                "skills": [],
                "extra": ["first", {"inner": [3]}],
            }
        ],
    }
    catalog = validate_catalog(raw)

    with pytest.raises(AttributeError):
        catalog.meta["z"].append(4)
    with pytest.raises(TypeError):
        catalog.meta["z"][1]["nested"] += (4,)
    with pytest.raises(AttributeError):
        catalog.sources[0].extra.append("second")
    with pytest.raises(TypeError):
        catalog.meta["new"] = "blocked"

    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    write_catalog(paths, catalog, pid=76)
    assert json.loads(paths.sources_file.read_text()) == raw
    assert paths.sources_file.read_text().index('"z"') < (
        paths.sources_file.read_text().index('"a"')
    )


def test_write_catalog_emits_well_formed_json_without_escaping_unicode(
    tmp_path: Path,
) -> None:
    raw = {
        "meta": {
            "high": "\ud800",
            "nested": [
                {
                    "key\ud800": "value",
                    "low": "\udc00",
                    "pair": "\ud83d\ude00",
                }
            ],
            "emoji": "😀",
            "line": "left\u2028right",
        },
        "version": 1,
        "sources": [
            {
                "extra": {"second": 2, "first": 1},
                "source": "owner/old",
                "skills": [],
            }
        ],
    }
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)

    write_catalog(paths, validate_catalog(raw), pid=81)

    assert paths.sources_file.read_bytes() == (
        b'{\n  "meta": {\n    "high": "\\ud800",\n'
        b'    "nested": [\n      {\n        "key\\ud800": "value",\n'
        b'        "low": "\\udc00",\n'
        b'        "pair": "\xf0\x9f\x98\x80"\n      }\n    ],\n'
        b'    "emoji": "\xf0\x9f\x98\x80",\n'
        b'    "line": "left\xe2\x80\xa8right"\n  },\n'
        b'  "version": 1,\n  "sources": [\n    {\n'
        b'      "extra": {\n        "second": 2,\n'
        b'        "first": 1\n      },\n'
        b'      "source": "owner/old",\n      "skills": []\n'
        b'    }\n  ]\n}\n'
    )


def test_frozen_extras_support_pydantic_json_dump_and_deep_copy() -> None:
    raw = {
        "meta": {"z": [1, {"nested": [2]}], "a": "last"},
        "version": 1,
        "sources": [
            {
                "source": "a/repo",
                "skills": ["review"],
                "extra": ["first", {"inner": [3]}],
            }
        ],
    }
    catalog = validate_catalog(raw)
    expected = {
        "version": 1,
        "sources": [
            {
                "source": "a/repo",
                "skills": ["review"],
                "extra": ["first", {"inner": [3]}],
            }
        ],
        "meta": {"z": [1, {"nested": [2]}], "a": "last"},
    }

    assert catalog.model_dump(mode="json") == expected
    assert json.loads(catalog.model_dump_json()) == expected
    copied = catalog.model_copy(deep=True)
    assert copied.model_dump(mode="json") == expected
    with pytest.raises(AttributeError):
        copied.meta["z"].append(4)
    with pytest.raises(AttributeError):
        copied.sources[0].extra.append("second")


def test_model_extra_and_extra_attributes_cannot_be_mutated_or_replaced() -> None:
    catalog = validate_catalog(
        {
            "meta": {"nested": [1]},
            "version": 1,
            "sources": [
                {"source": "a/repo", "skills": [], "extra": [2]}
            ],
        }
    )

    with pytest.raises(TypeError):
        catalog.model_extra["injected"] = []
    with pytest.raises(TypeError):
        catalog.sources[0].model_extra.update({"injected": []})
    with pytest.raises(ValidationError):
        catalog.model_extra = {}
    with pytest.raises(ValidationError):
        catalog.meta = {"mutable": []}
    with pytest.raises(ValidationError):
        catalog.injected = []
    with pytest.raises(ValidationError):
        catalog.sources[0].extra = []


def test_model_extra_cannot_be_mutated_through_dict_builtins_or_copy_aliases(
) -> None:
    catalog = validate_catalog(
        {
            "meta": {"nested": [{"value": [1]}]},
            "version": 1,
            "sources": [
                {"source": "a/repo", "skills": [], "extra": {"items": [2]}}
            ],
        }
    )

    for extras in (catalog.model_extra, catalog.sources[0].model_extra):
        assert isinstance(extras, Mapping)
        assert not isinstance(extras, dict)
        with pytest.raises(TypeError):
            dict.__setitem__(extras, "injected", [])
        with pytest.raises(TypeError):
            dict.update(extras, {"injected": []})
        with pytest.raises(TypeError):
            dict.__init__(extras, {"injected": []})

    copied = catalog.model_copy(deep=True)
    assert copied.model_extra is not catalog.model_extra
    assert copied.sources[0].model_extra is not catalog.sources[0].model_extra
    assert copied.meta is not catalog.meta
    assert copied.meta["nested"][0] is not catalog.meta["nested"][0]
    assert copied.meta["nested"][0]["value"] == (1,)
    assert copied.sources[0].extra["items"] == (2,)
    with pytest.raises(TypeError):
        copied.meta["nested"][0]["value"] += (3,)
    with pytest.raises(TypeError):
        copied.sources[0].extra["items"] += (3,)

    expected = {
        "version": 1,
        "sources": [
            {"source": "a/repo", "skills": [], "extra": {"items": [2]}}
        ],
        "meta": {"nested": [{"value": [1]}]},
    }
    assert catalog.model_dump(mode="json") == expected
    assert json.loads(catalog.model_dump_json()) == expected
    assert copied.model_dump(mode="json") == expected


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
        "a/repo",
        "z/repo",
    ]
    assert paths.profiles_file.read_bytes() == profiles_bytes
    assert paths.legacy_file.read_bytes() == legacy_bytes


def test_profiles_bootstrap_sorts_profiles_and_sources_before_migration(
    tmp_path: Path,
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    profiles = {
        "version": 1,
        "profiles": [
            {
                "name": "z-profile",
                "sources": [
                    {"source": "z/repo", "skills": []},
                    {"source": "b/repo", "skills": []},
                ],
            },
            {
                "name": "a-profile",
                "sources": [{"source": "a/repo", "skills": []}],
            },
        ],
    }
    contents = (json.dumps(profiles, indent=3) + "\n").encode()
    paths.profiles_file.write_bytes(contents)

    initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=17)

    assert [entry.source for entry in read_config(paths).sources] == [
        "a/repo",
        "b/repo",
        "z/repo",
    ]
    assert paths.profiles_file.read_bytes() == contents


def test_profiles_bootstrap_sorts_profile_names_by_literal_nfc_code_points(
    tmp_path: Path,
) -> None:
    profiles = {
        "version": 1,
        "profiles": [
            {"name": "z", "sources": [{"source": "https://profiles.test/z", "skills": []}]},
            {"name": "ä", "sources": [{"source": "https://profiles.test/a-umlaut-composed", "skills": []}]},
            {"name": "a", "sources": [{"source": "https://profiles.test/a", "skills": []}]},
            {"name": "A", "sources": [{"source": "https://profiles.test/A", "skills": []}]},
            {"name": "á", "sources": [{"source": "https://profiles.test/a-acute-composed", "skills": []}]},
            {"name": "aa", "sources": [{"source": "https://profiles.test/aa", "skills": []}]},
            {"name": "a2", "sources": [{"source": "https://profiles.test/a2", "skills": []}]},
            {"name": "a10", "sources": [{"source": "https://profiles.test/a10", "skills": []}]},
            {"name": ",", "sources": [{"source": "https://profiles.test/comma", "skills": []}]},
            {"name": ".", "sources": [{"source": "https://profiles.test/period", "skills": []}]},
            {"name": "a\N{COMBINING ACUTE ACCENT}", "sources": [{"source": "https://profiles.test/a-acute-decomposed", "skills": []}]},
            {"name": "a\N{COMBINING DIAERESIS}", "sources": [{"source": "https://profiles.test/a-umlaut-decomposed", "skills": []}]},
            {"name": "Å", "sources": [{"source": "https://profiles.test/aring", "skills": []}]},
            {"name": "×", "sources": [{"source": "https://profiles.test/multiply", "skills": []}]},
            {"name": "÷", "sources": [{"source": "https://profiles.test/divide", "skills": []}]},
        ],
    }
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    contents = (json.dumps(profiles, ensure_ascii=False, indent=2) + "\n").encode()
    paths.profiles_file.write_bytes(contents)

    initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=18)

    assert [entry.source for entry in read_config(paths).sources] == [
        "https://profiles.test/comma",
        "https://profiles.test/period",
        "https://profiles.test/A",
        "https://profiles.test/a",
        "https://profiles.test/a10",
        "https://profiles.test/a2",
        "https://profiles.test/aa",
        "https://profiles.test/z",
        "https://profiles.test/aring",
        "https://profiles.test/multiply",
        "https://profiles.test/a-acute-composed",
        "https://profiles.test/a-acute-decomposed",
        "https://profiles.test/a-umlaut-composed",
        "https://profiles.test/a-umlaut-decomposed",
        "https://profiles.test/divide",
    ]
    assert paths.profiles_file.read_bytes() == contents


def test_profiles_bootstrap_sorts_source_ids_by_literal_nfc_code_points(
    tmp_path: Path,
) -> None:
    prefix = "https://sources.test/"
    profiles = {
        "version": 1,
        "profiles": [
            {
                "name": "default",
                "sources": [
                    {"source": prefix + value, "skills": []}
                    for value in (
                        "z", "ä", "a", "A", "á", "aa", "a2", "a10", "Z", "é",
                        "e", "É", "ø", "o", "ß", "ss", "10", "2", "_x", "-x",
                        ",", ".", ".x", "@x", "0x", "a b", "a_b", "a-b", "a.b",
                        "a@b", "a\N{COMBINING ACUTE ACCENT}", "a\N{COMBINING DIAERESIS}",
                        "e\N{COMBINING ACUTE ACCENT}", "E\N{COMBINING ACUTE ACCENT}", "Å", "×", "÷",
                    )
                ],
            }
        ],
    }
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    paths.profiles_file.write_text(json.dumps(profiles, ensure_ascii=False))

    initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=19)

    catalog = read_config(paths)
    assert [entry.source for entry in catalog.sources] == [
        "https://sources.test/,",
        "https://sources.test/-x",
        "https://sources.test/.",
        "https://sources.test/.x",
        "https://sources.test/0x",
        "https://sources.test/10",
        "https://sources.test/2",
        "https://sources.test/@x",
        "https://sources.test/A",
        "https://sources.test/Z",
        "https://sources.test/_x",
        "https://sources.test/a",
        "https://sources.test/a b",
        "https://sources.test/a-b",
        "https://sources.test/a.b",
        "https://sources.test/a10",
        "https://sources.test/a2",
        "https://sources.test/a@b",
        "https://sources.test/a_b",
        "https://sources.test/aa",
        "https://sources.test/e",
        "https://sources.test/o",
        "https://sources.test/ss",
        "https://sources.test/z",
        "https://sources.test/Å",
        "https://sources.test/É",
        "https://sources.test/E\N{COMBINING ACUTE ACCENT}",
        "https://sources.test/×",
        "https://sources.test/ß",
        "https://sources.test/á",
        "https://sources.test/a\N{COMBINING ACUTE ACCENT}",
        "https://sources.test/ä",
        "https://sources.test/a\N{COMBINING DIAERESIS}",
        "https://sources.test/é",
        "https://sources.test/e\N{COMBINING ACUTE ACCENT}",
        "https://sources.test/÷",
        "https://sources.test/ø",
    ]
    assert resolve_source_token(catalog, "2")[1].source == "https://sources.test/-x"


def test_profiles_bootstrap_keeps_equal_nfc_source_values_byte_for_byte(
    tmp_path: Path,
) -> None:
    first = "https://sources.test/a\N{COMBINING ACUTE ACCENT}"
    second = "https://sources.test/á"
    profiles = {
        "version": 1,
        "profiles": [
            {
                "name": "default",
                "sources": [
                    {"source": first, "skills": ["first"]},
                    {"source": second, "skills": ["second"]},
                ],
            }
        ],
    }
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    contents = (json.dumps(profiles, ensure_ascii=False) + "\n").encode()
    paths.profiles_file.write_bytes(contents)

    initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=20)

    assert [(entry.source, entry.skills) for entry in read_config(paths).sources] == [
        (first, ("first",)),
        (second, ("second",)),
    ]
    assert paths.profiles_file.read_bytes() == contents


def test_profiles_bootstrap_never_calls_locale_setup_for_valid_unicode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_locale_setup(*_args: object) -> str:
        raise OSError("locale setup must not run")

    monkeypatch.setattr(locale, "setlocale", fail_locale_setup)
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    paths.profiles_file.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    {
                        "name": "Å",
                        "sources": [
                            {"source": "https://sources.test/×", "skills": []},
                            {"source": "https://sources.test/÷", "skills": []},
                            {
                                "source": "https://sources.test/a\N{COMBINING ACUTE ACCENT}",
                                "skills": [],
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=21)

    assert [entry.source for entry in read_config(paths).sources] == [
        "https://sources.test/×",
        "https://sources.test/a\N{COMBINING ACUTE ACCENT}",
        "https://sources.test/÷",
    ]


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


def test_unsafe_profile_source_is_wrapped_as_profile_config_error(
    tmp_path: Path,
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    contents = (
        b'{"version":1,"profiles":[{"name":"default","sources":'
        b'[{"source":"owner/repo?token=secret","skills":[]}]}]}\n'
    )
    paths.profiles_file.write_bytes(contents)

    with pytest.raises(ConfigError, match="Invalid profiles file") as caught:
        initialize_config(env={"CLOUD_UTILS_CONFIG_DIR": str(tmp_path)}, pid=16)

    assert caught.value.file_path == paths.profiles_file
    assert "secret" not in str(caught.value)
    assert paths.profiles_file.read_bytes() == contents
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


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_read_rejects_non_standard_json_constants_without_modifying_bytes(
    tmp_path: Path, constant: str
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    contents = (
        f'{{"version":1,"sources":[],"meta":{constant}}}\n'.encode()
    )
    paths.sources_file.write_bytes(contents)

    with pytest.raises(ConfigError, match="Could not read configuration file"):
        read_config(paths)

    assert paths.sources_file.read_bytes() == contents


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_write_rejects_non_finite_extra_values_without_touching_files(
    tmp_path: Path, value: float
) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path)
    paths.skm_dir.mkdir(parents=True)
    catalog = Catalog(version=1, sources=(), meta={"nested": [value]})

    with pytest.raises(CatalogError, match="finite"):
        write_catalog(paths, catalog, pid=75)

    assert not paths.sources_file.exists()
    assert not paths.sources_file.with_name("sources.json.75.tmp").exists()


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
