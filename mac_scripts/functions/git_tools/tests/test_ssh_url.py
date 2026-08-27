import pytest

from git_tools.ssh_url import (
    ParsedSshUrl,
    build_project_name,
    canonicalize_ssh_git_url,
    parse_ssh_git_url,
    slugify_segment,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "git@github.com:org/my-app.git",
            ParsedSshUrl(
                host="github.com",
                owner="org",
                repo="my-app",
                ssh_url="git@github.com:org/my-app.git",
                canonical="git@github.com:org/my-app",
            ),
        ),
        (
            "git@gitlab.com:acme/api",
            ParsedSshUrl(
                host="gitlab.com",
                owner="acme",
                repo="api",
                ssh_url="git@gitlab.com:acme/api.git",
                canonical="git@gitlab.com:acme/api",
            ),
        ),
        (
            "git@gitlab.vinova.sg:vinova/project-sit/sit-be.git",
            ParsedSshUrl(
                host="gitlab.vinova.sg",
                owner="vinova/project-sit",
                repo="sit-be",
                ssh_url="git@gitlab.vinova.sg:vinova/project-sit/sit-be.git",
                canonical="git@gitlab.vinova.sg:vinova/project-sit/sit-be",
            ),
        ),
    ],
)
def test_parse_ssh_git_url_preserves_host_and_nested_owner(
    value: str, expected: ParsedSshUrl
) -> None:
    assert parse_ssh_git_url(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "https://github.com/org/app.git", "ssh://git@github.com/org/app"])
def test_parse_ssh_git_url_rejects_invalid_and_https_values(value: str) -> None:
    with pytest.raises(ValueError, match="^(Invalid SSH URL|HTTPS URLs are not supported)$"):
        parse_ssh_git_url(value)


def test_parse_ssh_git_url_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="^Invalid SSH URL$"):
        parse_ssh_git_url(None)  # type: ignore[arg-type]


def test_slugify_segment_and_project_name_replace_odd_characters() -> None:
    assert slugify_segment("Org_Name") == "org-name"
    assert build_project_name("Org_Name", "My App") == "org-name-my-app"
    assert build_project_name("vinova/project-sit", "sit-be") == "vinova-project-sit-sit-be"


def test_canonicalize_ssh_git_url_strips_git_and_lowercases_host_only() -> None:
    expected = ParsedSshUrl(
        host="GitHub.com",
        owner="Org",
        repo="My-App",
        ssh_url="git@github.com:Org/My-App.git",
        canonical="git@github.com:Org/My-App",
    )
    assert canonicalize_ssh_git_url("git@GitHub.com:Org/My-App.git") == expected


def test_canonicalize_ssh_git_url_preserves_error_message() -> None:
    with pytest.raises(ValueError, match="^HTTPS URLs are not supported$"):
        canonicalize_ssh_git_url("https://github.com/org/app.git")
