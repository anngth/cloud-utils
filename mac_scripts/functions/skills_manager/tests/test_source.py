from __future__ import annotations

from pathlib import Path

import pytest

from skills_manager.source import SourceError, canonicalize_source, redact_source


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("anthropics/skills", "anthropics/skills"),
        ("https://github.com/anthropics/skills", "anthropics/skills"),
        ("https://github.com/anthropics/skills.git", "anthropics/skills"),
        ("git@github.com:anthropics/skills.git", "anthropics/skills"),
        ("ssh://git@github.com/anthropics/skills.git", "anthropics/skills"),
        ("git@gitlab.com:acme/skills.git", "git@gitlab.com:acme/skills"),
        (
            "git@bitbucket.org:workspace/repo_name.git",
            "git@bitbucket.org:workspace/repo_name",
        ),
    ],
)
def test_canonicalize_source_matches_safe_provider_identity(
    value: str, expected: str
) -> None:
    assert canonicalize_source(value) == expected


def test_safe_github_shorthand_component_characters_are_preserved() -> None:
    assert canonicalize_source("owner-name/repo_name.v2.git") == (
        "owner-name/repo_name.v2"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "owner/repo?TOKEN=query-secret",
        "owner/repo?Access_Token=query-secret",
        "owner/repo#fragment-secret",
        "git@github.com:owner/repo?ToKeN=query-secret",
        "ssh://git@github.com/owner/repo#fragment-secret",
        "git@github.com:owner/repo@ACCESS_TOKEN=query-secret",
        "ssh://git@github.com/owner/repo.git?KeY=query-secret",
        "git@gitlab.com:owner/repo.git?ToKeN=query-secret",
        "git@bitbucket.org:workspace/repo#fragment-secret",
        "git@gitlab.com:owner/repo@ACCESS_TOKEN=query-secret",
    ],
)
def test_unsafe_source_suffixes_are_rejected_and_redacted(raw: str) -> None:
    with pytest.raises(SourceError):
        canonicalize_source(raw)
    assert not any(
        marker in redact_source(raw).lower()
        for marker in ("token", "secret", "fragment", "query")
    )


@pytest.mark.parametrize(
    "raw",
    [
        "owner/repo@secret",
        "owner/repo=secret",
        "owner:token/repo",
        "opaque:ACCESS_TOKEN=query-secret",
        "opaque\nsource",
        "opaque\x00source",
        "owner/repo\n",
    ],
)
def test_unsafe_delimiters_opaque_credentials_and_controls_fail_closed(
    raw: str,
) -> None:
    with pytest.raises(SourceError):
        canonicalize_source(raw)
    assert "secret" not in redact_source(raw).lower()


def test_github_ref_and_subpath_are_preserved() -> None:
    source = "https://github.com/acme/skills/tree/v2/skills/review"
    assert canonicalize_source(source) == source


def test_local_paths_use_injected_cwd_and_realpath() -> None:
    assert canonicalize_source(
        "../skills",
        cwd=Path("/repo/app"),
        realpath=lambda value: f"/real{value}",
    ) == "/real/repo/skills"


def test_generic_url_credentials_query_and_fragment_are_removed() -> None:
    raw = "https://user:secret@git.example.com/acme/skills.git?token=query-secret"
    canonical = canonicalize_source(raw)
    assert "user" not in canonical
    assert "secret" not in canonical
    assert canonical == "https://git.example.com/acme/skills"
    assert redact_source(raw) == "https://git.example.com/acme/skills.git"


def test_backslash_https_url_credentials_are_normalized_and_removed() -> None:
    raw = r"https:\\user:secret@example.com/acme/repo.git"

    assert canonicalize_source(raw) == "https://example.com/acme/repo"
    assert redact_source(raw) == "https://example.com/acme/repo.git"


def test_generic_url_query_and_fragment_are_stripped_case_insensitively() -> None:
    raw = (
        "https://git.example.com/acme/skills.git"
        "?ToKeN=query-secret&mode=private#fragment-secret"
    )
    assert canonicalize_source(raw) == "https://git.example.com/acme/skills"
    assert redact_source(raw) == "https://git.example.com/acme/skills.git"


def test_unsafe_opaque_source_is_rejected_and_safely_redacted() -> None:
    raw = "opaque source?ACCESS_TOKEN=query-secret#fragment-secret"
    with pytest.raises(SourceError):
        canonicalize_source(raw)
    assert redact_source(raw) == "opaque source"


def test_opaque_credential_assignment_is_fully_redacted() -> None:
    raw = "opaque:ACCESS_TOKEN=query-secret"
    with pytest.raises(SourceError):
        canonicalize_source(raw)
    assert redact_source(raw) == "[unsafe source redacted]"


def test_empty_source_is_rejected() -> None:
    with pytest.raises(SourceError, match="empty"):
        canonicalize_source("   ")
