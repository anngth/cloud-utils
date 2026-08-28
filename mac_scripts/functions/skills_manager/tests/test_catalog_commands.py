from __future__ import annotations

import io
from pathlib import Path

import pytest

from shared.selector import SelectorResult, create_selector_state
from skills_manager.catalog_commands import (
    run_source_add,
    run_source_edit,
    run_source_remove,
)
from skills_manager.cli import CommandContext
from skills_manager.config import Catalog, CatalogSource
from skills_manager.state import InstalledSkill, InstalledStateError
from skills_manager.upstream import AvailableSkill, UpstreamError


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def available(*names: str) -> tuple[AvailableSkill, ...]:
    return tuple(AvailableSkill(name, name.upper()) for name in names)


def submitted(*names: str) -> SelectorResult:
    return SelectorResult("submit", create_selector_state(()), names)


def cancelled() -> SelectorResult:
    return SelectorResult("cancel", create_selector_state(()), ())


def test_source_add_no_skills_never_checks_npx_or_discovers(
    context: CommandContext,
) -> None:
    writes: list[Catalog] = []
    context.catalog = Catalog(version=1, sources=())
    context.services.has_command = lambda *_args, **_kwargs: pytest.fail(
        "npx checked"
    )
    context.services.discover_available_skills = (
        lambda *_args, **_kwargs: pytest.fail("discovery called")
    )
    context.services.write_catalog = lambda _paths, value: writes.append(value)

    assert run_source_add(
        "anthropics/skills",
        skills=(),
        all_skills=False,
        no_skills=True,
        yes=False,
        context=context,
    ) == 0
    assert len(writes) == 1
    assert writes[0].sources[0].skills == ()
    assert context.catalog.sources == ()


def test_source_add_duplicate_fails_before_discovery_or_write(
    context: CommandContext,
) -> None:
    context.services.discover_available_skills = (
        lambda *_args, **_kwargs: pytest.fail("discovery called")
    )
    context.services.write_catalog = lambda *_args: pytest.fail("catalog wrote")

    assert run_source_add(
        "owner/catalog",
        skills=(),
        all_skills=False,
        no_skills=False,
        yes=False,
        context=context,
    ) == 1
    assert "already exists (index 1)" in context.stderr.getvalue()
    assert "skm source edit 1" in context.stderr.getvalue()


def test_source_add_rejects_mutually_exclusive_direct_modes_without_write(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(version=1, sources=())
    context.services.discover_available_skills = (
        lambda *_args, **_kwargs: pytest.fail("discovery called")
    )
    context.services.write_catalog = lambda *_args: pytest.fail("catalog wrote")

    assert run_source_add(
        "owner/new",
        skills=(),
        all_skills=True,
        no_skills=True,
        yes=False,
        context=context,
    ) == 1
    assert "mutually exclusive" in context.stderr.getvalue()


def test_source_add_missing_requested_skill_does_not_write(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(version=1, sources=())
    context.services.discover_available_skills = lambda *_args, **_kwargs: (
        available("present")
    )
    context.services.write_catalog = lambda *_args: pytest.fail("catalog wrote")

    assert run_source_add(
        "owner/new",
        skills=("missing",),
        all_skills=False,
        no_skills=False,
        yes=True,
        context=context,
    ) == 1
    assert "Skills not found in source: missing" in context.stderr.getvalue()


def test_source_add_cancelled_selection_does_not_write(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(version=1, sources=())
    context.services.discover_available_skills = lambda *_args, **_kwargs: (
        available("one")
    )
    context.stdin = TtyStringIO()
    context.stdout = TtyStringIO()
    context.select_items = lambda *_args, **_kwargs: cancelled()
    context.services.write_catalog = lambda *_args: pytest.fail("catalog wrote")

    assert run_source_add(
        "owner/new",
        skills=(),
        all_skills=False,
        no_skills=False,
        yes=False,
        context=context,
    ) == 0


def test_source_add_conflict_redacts_owner_and_does_not_write(
    context: CommandContext,
) -> None:
    secret = "https://user:secret@example.com/private"
    context.catalog = Catalog(
        version=1,
        sources=(CatalogSource(source=secret, skills=("shared",)),),
    )
    context.services.discover_available_skills = lambda *_args, **_kwargs: (
        available("shared")
    )
    context.services.write_catalog = lambda *_args: pytest.fail("catalog wrote")

    assert run_source_add(
        "owner/new",
        skills=(),
        all_skills=True,
        no_skills=False,
        yes=False,
        context=context,
    ) == 1
    error = context.stderr.getvalue()
    assert "Skill already in another source: shared" in error
    assert "https://example.com/private" in error
    assert "secret" not in error


def test_source_add_failed_discovery_does_not_write(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(version=1, sources=())
    context.services.discover_available_skills = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(UpstreamError("discovery failed"))
    )
    context.services.write_catalog = lambda *_args: pytest.fail("catalog wrote")

    assert run_source_add(
        "owner/new",
        skills=(),
        all_skills=True,
        no_skills=False,
        yes=False,
        context=context,
    ) == 1
    assert context.stderr.getvalue().count("discovery failed") == 1


def test_source_edit_uses_initial_selection_and_writes_once(
    context: CommandContext,
) -> None:
    calls: list[object] = []
    context.services.discover_available_skills = lambda *_args, **_kwargs: (
        available("demo", "new")
    )
    context.stdin = TtyStringIO()
    context.stdout = TtyStringIO()
    context.select_items = lambda items, **kwargs: (
        calls.append((items, kwargs)) or submitted("new")
    )
    context.services.write_catalog = lambda paths, value: calls.append(
        ("write", paths, value)
    )

    assert run_source_edit(
        "1",
        skills=(),
        all_skills=False,
        no_skills=False,
        yes=False,
        context=context,
    ) == 0
    assert calls[0][1]["initial"] == ("demo",)
    writes = [call for call in calls if call[0] == "write"]
    assert len(writes) == 1
    assert writes[0][2].sources[0].skills == ("new",)


def test_source_remove_installed_source_uses_state_path_without_preflight(
    context: CommandContext,
) -> None:
    context.services.has_command = lambda *_args, **_kwargs: pytest.fail(
        "npx checked"
    )
    context.services.resolve_project_root = lambda **_kwargs: Path("/project")
    context.services.load_installed_state = lambda **_kwargs: {
        "demo": InstalledSkill(
            "demo", "/project/demo", ("Codex",), "owner/catalog", "tracked"
        )
    }
    context.services.write_catalog = lambda *_args: pytest.fail("catalog wrote")

    assert run_source_remove("owner/catalog", context=context) == 1
    assert "installed skills: demo" in context.stderr.getvalue()


def test_source_remove_failed_state_discovery_does_not_write(
    context: CommandContext,
) -> None:
    context.services.resolve_project_root = lambda **_kwargs: Path("/project")
    context.services.load_installed_state = lambda **_kwargs: (
        (_ for _ in ()).throw(InstalledStateError("state failed"))
    )
    context.services.write_catalog = lambda *_args: pytest.fail("catalog wrote")

    assert run_source_remove("1", context=context) == 1
    assert context.stderr.getvalue().count("state failed") == 1


def test_source_remove_builds_new_catalog_and_writes_once(
    context: CommandContext,
) -> None:
    writes: list[Catalog] = []
    original = context.catalog
    context.services.resolve_project_root = lambda **_kwargs: Path("/project")
    context.services.load_installed_state = lambda **_kwargs: {}
    context.services.write_catalog = lambda _paths, value: writes.append(value)

    assert run_source_remove("1", context=context) == 0
    assert writes == [Catalog(version=1, sources=())]
    assert context.catalog is original
    assert original.sources[0].skills == ("demo",)
