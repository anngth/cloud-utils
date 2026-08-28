from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.process import CommandResult
from skills_manager.source import canonicalize_source
from skills_manager.state import (
    InstalledSkill,
    InstalledStateError,
    load_installed_state,
    read_project_provenance,
    resolve_project_root,
)
from skills_manager.upstream import InstalledRecord


def write_lock(
    path: Path,
    skills: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "skills": skills}) + "\n",
        encoding="utf-8",
    )


def actual(
    project: Path, name: str = "review", *, agents: tuple[str, ...] = ("Codex",)
) -> InstalledRecord:
    return InstalledRecord(
        name,
        str(project / ".agents" / "skills" / name),
        "project",
        agents,
    )


def test_combines_actual_presence_with_project_local_provenance(
    tmp_path: Path,
) -> None:
    write_lock(
        tmp_path / "skills-lock.json",
        {
            "review": {
                "source": "https://github.com/acme/skills.git",
                "sourceType": "github",
            }
        },
    )

    state = load_installed_state(
        project_root=tmp_path,
        env={},
        list_installed=lambda **_: (actual(tmp_path),),
    )

    assert state == {
        "review": InstalledSkill(
            "review",
            str(tmp_path / ".agents" / "skills" / "review"),
            ("Codex",),
            "acme/skills",
            "tracked",
        )
    }


def test_project_local_provenance_wins_over_global(tmp_path: Path) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    write_lock(home / ".agents/.skill-lock.json", {"demo": {"source": "other/source"}})
    write_lock(project / "skills-lock.json", {"demo": {"source": "owner/source"}})
    state = load_installed_state(
        project_root=project,
        env={"HOME": str(home)},
        list_installed=lambda **_: (
            InstalledRecord("demo", "/x", "project", ("codex",)),
        ),
    )
    assert state["demo"].source == canonicalize_source("owner/source")
    assert state["demo"].provenance == "tracked"


def test_project_agents_lock_overlays_ambiguous_global_provenance(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_home = tmp_path / "state"
    home = tmp_path / "home"
    write_lock(
        project / ".agents/.skill-lock.json",
        {"demo": {"source": "owner/local", "sourceType": "github"}},
    )
    write_lock(
        state_home / "skills/.skill-lock.json",
        {"demo": {"source": "owner/first", "sourceType": "github"}},
    )
    write_lock(
        home / ".agents/.skill-lock.json",
        {"demo": {"source": "owner/second", "sourceType": "github"}},
    )

    state = load_installed_state(
        project_root=project,
        env={"XDG_STATE_HOME": str(state_home), "HOME": str(home)},
        list_installed=lambda **_: (actual(project, "demo"),),
    )

    assert state["demo"].source == "owner/local"
    assert state["demo"].provenance == "tracked"


@pytest.mark.parametrize("tier", ["local", "global"])
def test_equal_priority_source_disagreement_is_untracked(
    tmp_path: Path, tier: str
) -> None:
    project = tmp_path / "project"
    state_home = tmp_path / "state"
    home = tmp_path / "home"
    if tier == "local":
        paths = (
            project / "skills-lock.json",
            project / ".agents/.skill-lock.json",
        )
        env = {"XDG_STATE_HOME": str(state_home), "HOME": str(home)}
        write_lock(
            state_home / "skills/.skill-lock.json",
            {"demo": {"source": "owner/global", "sourceType": "github"}},
        )
    else:
        paths = (
            state_home / "skills/.skill-lock.json",
            home / ".agents/.skill-lock.json",
        )
        env = {"XDG_STATE_HOME": str(state_home), "HOME": str(home)}
    write_lock(
        paths[0], {"demo": {"source": "owner/first", "sourceType": "github"}}
    )
    write_lock(
        paths[1], {"demo": {"source": "owner/second", "sourceType": "github"}}
    )

    state = load_installed_state(
        project_root=project,
        env=env,
        list_installed=lambda **_: (actual(project, "demo"),),
    )

    assert state["demo"].source is None
    assert state["demo"].provenance == "untracked"


def test_equal_priority_source_metadata_disagreement_is_untracked(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_home = tmp_path / "state"
    home = tmp_path / "home"
    write_lock(
        state_home / "skills/.skill-lock.json",
        {"demo": {"source": "owner/source", "sourceType": "github"}},
    )
    write_lock(
        home / ".agents/.skill-lock.json",
        {"demo": {"source": "owner/source", "sourceType": "local"}},
    )

    state = load_installed_state(
        project_root=project,
        env={"XDG_STATE_HOME": str(state_home), "HOME": str(home)},
        list_installed=lambda **_: (actual(project, "demo"),),
    )

    assert state["demo"].source is None
    assert state["demo"].provenance == "untracked"


def test_matching_equal_priority_records_remain_tracked(tmp_path: Path) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    record = {"demo": {"source": "https://github.com/owner/source.git"}}
    write_lock(project / "skills-lock.json", record)
    write_lock(project / ".agents/.skill-lock.json", record)

    state = load_installed_state(
        project_root=project,
        env={"HOME": str(home)},
        list_installed=lambda **_: (actual(project, "demo"),),
    )

    assert state["demo"].source == "owner/source"
    assert state["demo"].provenance == "tracked"


@pytest.mark.parametrize(
    "invalid_text",
    [
        "{broken",
        "[]",
        json.dumps({"skills": []}),
        json.dumps({"skills": {"review": None}}),
        json.dumps({"skills": {"review": {"source": "   "}}}),
        json.dumps({"skills": {"review": {"source": 7}}}),
        '{"skills":{"review":{"source":"owner/source","extra":NaN}}}',
    ],
)
def test_invalid_lock_documents_and_entries_are_ignored(
    tmp_path: Path, invalid_text: str
) -> None:
    lock = tmp_path / "skills-lock.json"
    lock.write_text(invalid_text, encoding="utf-8")

    state = load_installed_state(
        project_root=tmp_path,
        env={},
        list_installed=lambda **_: (actual(tmp_path),),
    )

    assert state["review"].source is None
    assert state["review"].provenance == "untracked"


def test_reads_only_the_four_supported_lock_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state_home = tmp_path / "state"
    home = tmp_path / "home"
    write_lock(
        project / "skills-lock.json", {"project": {"source": "owner/project"}}
    )
    write_lock(
        project / ".agents/.skill-lock.json",
        {"agents": {"source": "owner/agents"}},
    )
    write_lock(
        state_home / "skills/.skill-lock.json",
        {"state": {"source": "owner/state"}},
    )
    write_lock(
        home / ".agents/.skill-lock.json", {"home": {"source": "owner/home"}}
    )
    write_lock(
        project / ".skill-lock.json", {"unsupported": {"source": "bad/root"}}
    )
    write_lock(
        home / "skills-lock.json", {"unsupported-home": {"source": "bad/home"}}
    )

    provenance = read_project_provenance(
        project_root=project,
        env={"XDG_STATE_HOME": str(state_home), "HOME": str(home)},
    )

    assert set(provenance) == {"project", "agents", "state", "home"}
    assert provenance["project"].origin == project / "skills-lock.json"
    assert provenance["agents"].origin == project / ".agents/.skill-lock.json"
    assert provenance["state"].origin == state_home / "skills/.skill-lock.json"
    assert provenance["home"].origin == home / ".agents/.skill-lock.json"


def test_stale_lock_entry_does_not_create_actual_presence(tmp_path: Path) -> None:
    write_lock(
        tmp_path / "skills-lock.json", {"review": {"source": "owner/source"}}
    )
    state = load_installed_state(
        project_root=tmp_path,
        env={},
        list_installed=lambda **_: (),
    )
    assert state == {}


def test_installed_list_receives_project_cwd_and_agents_are_copied(
    tmp_path: Path,
) -> None:
    calls: list[Path] = []

    def list_installed(*, cwd):
        calls.append(Path(cwd))
        return (actual(tmp_path, agents=("Codex", "Claude")),)

    state = load_installed_state(
        project_root=tmp_path,
        env={},
        list_installed=list_installed,
    )

    assert calls == [tmp_path]
    assert state["review"].agents == ("Codex", "Claude")


def test_installed_list_failure_is_wrapped_with_stable_domain_error(
    tmp_path: Path,
) -> None:
    cause = RuntimeError("npx failed")

    def list_installed(**_):
        raise cause

    with pytest.raises(
        InstalledStateError,
        match=f"^Could not list installed skills in {tmp_path}$",
    ) as caught:
        load_installed_state(
            project_root=tmp_path,
            env={},
            list_installed=list_installed,
        )
    assert caught.value.__cause__ is cause


def test_resolve_project_root_uses_captured_git_without_shell_and_canonicalizes(
    tmp_path: Path,
) -> None:
    actual_root = tmp_path / "actual"
    actual_root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual_root, target_is_directory=True)
    cwd = actual_root / "nested"
    cwd.mkdir()
    calls: list[tuple[tuple[str, ...], Path, bool]] = []

    def runner(argv, *, cwd, capture, **_):
        calls.append((tuple(argv), Path(cwd), capture))
        return CommandResult(0, f"{alias}\n", "")

    assert resolve_project_root(cwd=cwd, runner=runner) == actual_root
    assert calls == [
        (("git", "rev-parse", "--show-toplevel"), cwd, True)
    ]


@pytest.mark.parametrize("mode", ["nonzero", "empty", "error"])
def test_resolve_project_root_falls_back_to_canonical_cwd(
    tmp_path: Path, mode: str
) -> None:
    actual_cwd = tmp_path / "actual"
    actual_cwd.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual_cwd, target_is_directory=True)

    def runner(*_args, **_kwargs):
        if mode == "error":
            raise RuntimeError("git unavailable")
        if mode == "empty":
            return CommandResult(0, "\n", "")
        return CommandResult(128, "", "not a repository")

    assert resolve_project_root(cwd=alias, runner=runner) == actual_cwd
