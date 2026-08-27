from pathlib import Path

import pytest

from shared.process import CommandResult

from git_tools.gitlab import (
    BACKUP_GROUP,
    GITLAB_HOST,
    assert_glab_ready,
    create_private_group,
    create_private_project,
    ensure_backup_group,
    group_exists,
    next_available_name,
    next_suffixed_name,
    pick_preferred_default_branch,
    project_exists,
    project_ssh_url,
    project_web_url,
    protect_branch,
    run_glab,
    set_default_branch,
)


GROUP = "anngth-dev/backups"


class GlabStub:
    def __init__(self, *responses: CommandResult) -> None:
        self.responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> CommandResult:
        self.calls.append(args)
        return self.responses.pop(0)


def test_run_glab_prepends_executable_and_forwards_process_options() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> CommandResult:
        calls.append((argv, kwargs))
        return CommandResult(3, "out", "err")

    env = {"PATH": "/fake/bin"}
    result = run_glab(["auth", "status"], cwd=Path("/repo"), env=env, runner=runner)

    assert result == CommandResult(3, "out", "err")
    assert calls == [
        (
            ["glab", "auth", "status"],
            {"cwd": Path("/repo"), "env": env, "capture": True},
        )
    ]


def test_run_glab_normalizes_process_launch_oserror() -> None:
    error = FileNotFoundError(2, "No such file or directory", "glab")

    def runner(_argv: list[str], **_kwargs: object) -> CommandResult:
        raise error

    result = run_glab(["auth", "status"], runner=runner)

    assert result == CommandResult(1, stderr=str(error))


def test_assert_glab_ready_fails_when_glab_is_missing() -> None:
    result = assert_glab_ready(has_command=lambda _name: False)

    assert result.ok is False
    assert result.error == "glab is not installed or not available on PATH"


def test_assert_glab_ready_fails_when_not_authenticated() -> None:
    glab = GlabStub(CommandResult(1, stderr="not logged in\n"))

    result = assert_glab_ready(has_command=lambda _name: True, run_glab_fn=glab)

    assert result.ok is False
    assert result.error == "glab authentication is required: not logged in"
    assert glab.calls == [["auth", "status"]]


def test_assert_glab_ready_uses_fallback_when_auth_output_is_empty() -> None:
    result = assert_glab_ready(
        has_command=lambda _name: True,
        run_glab_fn=GlabStub(CommandResult(1)),
    )

    assert result.ok is False
    assert result.error == "glab authentication is required: not logged in"


def test_assert_glab_ready_succeeds_when_authenticated() -> None:
    result = assert_glab_ready(
        has_command=lambda _name: True,
        run_glab_fn=GlabStub(CommandResult(0, stdout="Logged in")),
    )

    assert result.ok is True
    assert result.error is None


def test_project_exists_returns_live_project_and_encoded_nested_path() -> None:
    glab = GlabStub(
        CommandResult(
            0,
            stdout='{"path":"my app","marked_for_deletion_on":null}',
        )
    )

    result = project_exists("team name", "my app", run_glab_fn=glab)

    assert result.ok is True
    assert result.exists is True
    assert result.inactive is False
    assert result.project == {"path": "my app", "marked_for_deletion_on": None}
    assert glab.calls == [["api", "projects/team%20name%2Fmy%20app"]]


def test_project_exists_maps_not_found_to_missing() -> None:
    result = project_exists(
        GROUP,
        "missing",
        run_glab_fn=GlabStub(CommandResult(1, stderr="404 Not Found")),
    )

    assert result.ok is True
    assert result.exists is False
    assert result.inactive is False


@pytest.mark.parametrize("field", ["marked_for_deletion_on", "marked_for_deletion_at"])
def test_project_exists_treats_marked_for_deletion_project_as_inactive(field: str) -> None:
    glab = GlabStub(CommandResult(0, stdout=f'{{"path":"my-app","{field}":"2026-08-07"}}'))

    result = project_exists(GROUP, "my-app", run_glab_fn=glab)

    assert result.ok is True
    assert result.exists is False
    assert result.inactive is True


@pytest.mark.parametrize(
    "path",
    ["my-app-deletion_scheduled-99", "my-app-deleted-42"],
)
def test_project_exists_treats_deletion_path_suffix_as_inactive(path: str) -> None:
    result = project_exists(
        GROUP,
        "my-app",
        run_glab_fn=GlabStub(CommandResult(0, stdout=f'{{"path":"{path}"}}')),
    )

    assert result.ok is True
    assert result.exists is False
    assert result.inactive is True


def test_project_exists_uses_marked_field_even_when_path_matches_request() -> None:
    name = "my-app-deletion_scheduled-99"
    result = project_exists(
        GROUP,
        name,
        run_glab_fn=GlabStub(
            CommandResult(
                0,
                stdout=(
                    '{"path":"my-app-deletion_scheduled-99",'
                    '"marked_for_deletion_on":"2026-08-07"}'
                ),
            )
        ),
    )

    assert result.ok is True
    assert result.inactive is True


def test_project_exists_returns_unrelated_api_error() -> None:
    result = project_exists(
        GROUP,
        "broken",
        run_glab_fn=GlabStub(CommandResult(1, stderr="connection refused\n")),
    )

    assert result.ok is False
    assert result.error == "connection refused"


def test_project_exists_rejects_invalid_json() -> None:
    result = project_exists(
        GROUP,
        "broken-json",
        run_glab_fn=GlabStub(CommandResult(0, stdout="not-json")),
    )

    assert result.ok is False
    assert result.error == "could not parse GitLab project response"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_project_exists_rejects_nonstandard_json_constants(constant: str) -> None:
    result = project_exists(
        GROUP,
        "nonstandard-json",
        run_glab_fn=GlabStub(CommandResult(0, stdout=constant)),
    )

    assert result.ok is False
    assert result.error == "could not parse GitLab project response"


def test_group_exists_uses_encoded_nested_group_path() -> None:
    glab = GlabStub(CommandResult(0, stdout="{}"))

    result = group_exists(GROUP, run_glab_fn=glab)

    assert result.ok is True
    assert result.exists is True
    assert glab.calls == [["api", "groups/anngth-dev%2Fbackups"]]


def test_group_exists_maps_not_found_and_preserves_unrelated_errors() -> None:
    missing = group_exists(
        GROUP,
        run_glab_fn=GlabStub(CommandResult(1, stdout="not found")),
    )
    failed = group_exists(
        GROUP,
        run_glab_fn=GlabStub(CommandResult(1, stderr="403 Forbidden")),
    )

    assert missing.ok is True
    assert missing.exists is False
    assert failed.ok is False
    assert failed.error == "403 Forbidden"


def test_create_private_group_creates_top_level_group_with_private_visibility() -> None:
    glab = GlabStub(CommandResult(0, stdout="created", stderr="warning"))

    result = create_private_group("backups", run_glab_fn=glab)

    assert result.ok is True
    assert result.stdout == "created"
    assert result.stderr == "warning"
    assert glab.calls == [[
        "api", "--method", "POST", "groups",
        "-f", "name=backups", "-f", "path=backups",
        "-f", "visibility=private",
    ]]


def test_create_private_group_creates_nested_subgroup_under_parent_id() -> None:
    glab = GlabStub(
        CommandResult(0, stdout='{"id":136327837}'),
        CommandResult(0, stdout="{}"),
    )

    result = create_private_group(GROUP, run_glab_fn=glab)

    assert result.ok is True
    assert glab.calls == [
        ["api", "groups/anngth-dev"],
        [
            "api", "--method", "POST", "groups",
            "-f", "name=backups", "-f", "path=backups",
            "-f", "parent_id=136327837", "-f", "visibility=private",
        ],
    ]


def test_create_private_group_maps_parent_lookup_and_json_errors() -> None:
    lookup = create_private_group(
        GROUP,
        run_glab_fn=GlabStub(CommandResult(1, stderr="parent denied")),
    )
    invalid = create_private_group(
        GROUP,
        run_glab_fn=GlabStub(CommandResult(0, stdout="not-json")),
    )
    missing_id = create_private_group(
        GROUP,
        run_glab_fn=GlabStub(CommandResult(0, stdout="{}")),
    )

    assert lookup.ok is False
    assert lookup.error == "parent denied"
    assert invalid.ok is False
    assert invalid.error == "could not parse parent group id for anngth-dev"
    assert missing_id.ok is False
    assert missing_id.error == "parent group anngth-dev has no id"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_create_private_group_rejects_nonstandard_parent_json_constants(
    constant: str,
) -> None:
    result = create_private_group(
        "parent/leaf",
        run_glab_fn=GlabStub(CommandResult(0, stdout=constant)),
    )

    assert result.ok is False
    assert result.error == "could not parse parent group id for parent"


def test_ensure_backup_group_queries_before_creating_nested_group() -> None:
    glab = GlabStub(
        CommandResult(1, stderr="404 Group Not Found"),
        CommandResult(0, stdout='{"id":136327837}'),
        CommandResult(0, stdout="{}"),
    )

    result = ensure_backup_group(GROUP, run_glab_fn=glab)

    assert result.ok is True
    assert result.created is True
    assert glab.calls == [
        ["api", "groups/anngth-dev%2Fbackups"],
        ["api", "groups/anngth-dev"],
        [
            "api", "--method", "POST", "groups",
            "-f", "name=backups", "-f", "path=backups",
            "-f", "parent_id=136327837", "-f", "visibility=private",
        ],
    ]


def test_ensure_backup_group_skips_creation_when_group_exists() -> None:
    glab = GlabStub(CommandResult(0, stdout="{}"))

    result = ensure_backup_group(GROUP, run_glab_fn=glab)

    assert result.ok is True
    assert result.created is False
    assert len(glab.calls) == 1


def test_ensure_backup_group_propagates_check_and_create_errors() -> None:
    check = ensure_backup_group(
        GROUP,
        run_glab_fn=GlabStub(CommandResult(1, stderr="connection refused")),
    )
    create = ensure_backup_group(
        GROUP,
        run_glab_fn=GlabStub(
            CommandResult(1, stderr="404 Not Found"),
            CommandResult(1, stderr="parent denied"),
        ),
    )

    assert check.ok is False
    assert check.error == "connection refused"
    assert create.ok is False
    assert create.error == "parent denied"


def test_create_private_project_uses_private_empty_project_subcommand() -> None:
    glab = GlabStub(CommandResult(0, stdout="created", stderr=""))

    result = create_private_project(GROUP, "my-app", run_glab_fn=glab)

    assert result.ok is True
    assert result.stdout == "created"
    assert result.stderr == ""
    assert glab.calls == [[
        "repo", "create", "my-app", "--group", GROUP,
        "--private", "--skipGitInit",
    ]]


def test_create_private_project_maps_failure_to_stable_error() -> None:
    result = create_private_project(
        GROUP,
        "my-app",
        run_glab_fn=GlabStub(CommandResult(1)),
    )

    assert result.ok is False
    assert result.error == "failed to create GitLab project"


def test_next_available_name_returns_free_base_name() -> None:
    calls: list[tuple[str, str]] = []

    def exists(group: str, name: str):
        calls.append((group, name))
        return type("Exists", (), {"ok": True, "exists": False, "error": None})()

    result = next_available_name(GROUP, "my-app", project_exists_fn=exists)

    assert result.ok is True
    assert result.name == "my-app"
    assert calls == [(GROUP, "my-app")]


def test_next_suffixed_name_starts_at_two_and_walks_taken_names() -> None:
    taken = {"my-app-2", "my-app-3"}

    def exists(_group: str, name: str):
        return type("Exists", (), {"ok": True, "exists": name in taken, "error": None})()

    result = next_suffixed_name(GROUP, "my-app", project_exists_fn=exists)

    assert result.ok is True
    assert result.name == "my-app-4"


def test_next_suffixed_name_never_returns_free_base_name() -> None:
    def exists(_group: str, _name: str):
        return type("Exists", (), {"ok": True, "exists": False, "error": None})()

    result = next_suffixed_name(GROUP, "my-app", project_exists_fn=exists)

    assert result.ok is True
    assert result.name == "my-app-2"


def test_next_suffixed_name_returns_mid_walk_api_error() -> None:
    def exists(_group: str, name: str):
        if name == "my-app-2":
            return type("Exists", (), {"ok": True, "exists": True, "error": None})()
        return type(
            "Exists", (), {"ok": False, "exists": False, "error": "connection refused"}
        )()

    result = next_suffixed_name(GROUP, "my-app", project_exists_fn=exists)

    assert result.ok is False
    assert result.error == "connection refused"


def test_url_helpers_use_nested_group_and_fixed_gitlab_host() -> None:
    assert BACKUP_GROUP == "anngth-dev/backups"
    assert GITLAB_HOST == "gitlab.com"
    assert project_ssh_url(BACKUP_GROUP, "my-app") == (
        "git@gitlab.com:anngth-dev/backups/my-app.git"
    )
    assert project_web_url(BACKUP_GROUP, "my-app") == (
        "https://gitlab.com/anngth-dev/backups/my-app"
    )


def test_set_default_branch_uses_encoded_project_and_put() -> None:
    glab = GlabStub(CommandResult(0, stdout="{}"))

    result = set_default_branch(GROUP, "my-app", "main", run_glab_fn=glab)

    assert result.ok is True
    assert glab.calls == [[
        "api", "--method", "PUT",
        "projects/anngth-dev%2Fbackups%2Fmy-app",
        "-f", "default_branch=main",
    ]]


def test_set_default_branch_returns_stable_failure() -> None:
    result = set_default_branch(
        GROUP,
        "my-app",
        "main",
        run_glab_fn=GlabStub(CommandResult(1)),
    )

    assert result.ok is False
    assert result.error == "failed to set default branch"


def test_protect_branch_uses_maintainer_access_and_allows_force_push() -> None:
    glab = GlabStub(CommandResult(0, stdout="{}"))

    result = protect_branch(GROUP, "my-app", "main", run_glab_fn=glab)

    assert result.ok is True
    assert result.already_protected is False
    assert glab.calls == [[
        "api", "--method", "POST",
        "projects/anngth-dev%2Fbackups%2Fmy-app/protected_branches",
        "-f", "name=main",
        "-f", "push_access_level=40",
        "-f", "merge_access_level=40",
        "-f", "allow_force_push=true",
    ]]


def test_protect_branch_treats_already_protected_as_success() -> None:
    result = protect_branch(
        GROUP,
        "my-app",
        "main",
        run_glab_fn=GlabStub(
            CommandResult(1, stdout='{\"message\":\"Protected branch already exists\"}')
        ),
    )

    assert result.ok is True
    assert result.already_protected is True


def test_protect_branch_returns_unrelated_api_failure() -> None:
    result = protect_branch(
        GROUP,
        "my-app",
        "main",
        run_glab_fn=GlabStub(CommandResult(1, stderr="403 Forbidden")),
    )

    assert result.ok is False
    assert result.error == "403 Forbidden"


def test_pick_preferred_default_branch_prefers_main_and_stops() -> None:
    calls: list[tuple[list[str], str | Path]] = []

    def git(args: list[str], *, cwd: str | Path):
        calls.append((args, cwd))
        return CommandResult(0)

    branch = pick_preferred_default_branch("/mirror.git", run_git_fn=git)

    assert branch == "main"
    assert calls == [
        (["show-ref", "--verify", "--quiet", "refs/heads/main"], "/mirror.git")
    ]


def test_pick_preferred_default_branch_falls_back_to_develop() -> None:
    def git(args: list[str], *, cwd: str | Path):
        return CommandResult(0 if args[-1] == "refs/heads/develop" else 1)

    assert pick_preferred_default_branch("/mirror.git", run_git_fn=git) == "develop"


def test_pick_preferred_default_branch_returns_none_when_neither_exists() -> None:
    def git(_args: list[str], *, cwd: str | Path):
        return CommandResult(1)

    assert pick_preferred_default_branch("/mirror.git", run_git_fn=git) is None
