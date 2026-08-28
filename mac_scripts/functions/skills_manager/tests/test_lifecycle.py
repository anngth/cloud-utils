from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from skills_manager.cli import CommandContext
from skills_manager.config import Catalog, CatalogSource
from skills_manager.lifecycle import run_status
from skills_manager.state import InstalledSkill, InstalledStateError


def installed(
    name: str,
    *,
    source: str | None = "owner/catalog",
    provenance: Literal["tracked", "untracked"] = "tracked",
) -> InstalledSkill:
    return InstalledSkill(
        name,
        f"/project/.agents/skills/{name}",
        ("Codex",),
        source,
        provenance,
    )


def test_status_renders_drift_and_returns_one(
    context: CommandContext,
) -> None:
    context.services.resolve_project_root = lambda **_: Path("/project")
    context.services.load_installed_state = lambda **_: {}
    context.services.execute_install_plan = lambda *_args, **_kwargs: (
        pytest.fail("install stub reached")
    )
    context.services.execute_uninstall_plan = lambda *_args, **_kwargs: (
        pytest.fail("uninstall stub reached")
    )

    assert run_status(context) == 1
    assert "Status: /project" in context.stdout.getvalue()
    assert "demo" in context.stdout.getvalue()
    assert "□" in context.stdout.getvalue()
    assert context.stderr.getvalue() == ""


def test_status_returns_zero_when_catalog_is_installed(
    context: CommandContext,
) -> None:
    context.services.resolve_project_root = lambda **_: Path("/project")
    context.services.load_installed_state = lambda **_: {
        "demo": installed("demo")
    }

    assert run_status(context) == 0
    assert "■" in context.stdout.getvalue()
    assert context.stderr.getvalue() == ""


def test_status_extras_are_informational_only(
    context: CommandContext,
) -> None:
    context.services.resolve_project_root = lambda **_: Path("/project")
    context.services.load_installed_state = lambda **_: {
        "demo": installed("demo"),
        "extra": installed("extra", source="other/catalog"),
    }

    assert run_status(context) == 0
    assert "Extra" in context.stdout.getvalue()
    assert "extra" in context.stdout.getvalue()


def test_status_reports_source_mismatch(context: CommandContext) -> None:
    context.services.resolve_project_root = lambda **_: Path("/project")
    context.services.load_installed_state = lambda **_: {
        "demo": installed("demo", source="other/catalog")
    }

    assert run_status(context) == 1
    assert "Source mismatch" in context.stdout.getvalue()


def test_status_reports_untracked_skill(context: CommandContext) -> None:
    context.services.resolve_project_root = lambda **_: Path("/project")
    context.services.load_installed_state = lambda **_: {
        "demo": installed("demo", source=None, provenance="untracked")
    }

    assert run_status(context) == 1
    assert "Untracked" in context.stdout.getvalue()


def test_status_reports_desired_source_conflict(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/one", skills=("demo",)),
            CatalogSource(source="owner/two", skills=("demo",)),
        ),
    )
    context.services.resolve_project_root = lambda **_: Path("/project")
    context.services.load_installed_state = lambda **_: {}

    assert run_status(context) == 1
    assert "Desired-source conflict" in context.stdout.getvalue()
    assert "owner/one" in context.stdout.getvalue()
    assert "owner/two" in context.stdout.getvalue()


def test_status_normalizes_upstream_list_failure_once(
    context: CommandContext,
) -> None:
    context.services.resolve_project_root = lambda **_: Path("/project")

    def fail_load(**_kwargs: object) -> object:
        raise InstalledStateError(
            "Could not list installed skills in /project"
        )

    context.services.load_installed_state = fail_load

    assert run_status(context) == 1
    assert context.stdout.getvalue() == ""
    assert context.stderr.getvalue().count(
        "Could not list installed skills in /project"
    ) == 1


def test_status_uses_project_root_fallback(
    context: CommandContext,
    tmp_path: Path,
) -> None:
    project = tmp_path / "not-a-repository" / "nested"
    project.mkdir(parents=True)
    context.cwd = project
    context.services.load_installed_state = lambda **_: {
        "demo": installed("demo")
    }

    assert run_status(context) == 0
    assert f"Status: {project}" in context.stdout.getvalue()


def test_status_calls_services_in_orchestration_order(
    context: CommandContext,
) -> None:
    calls: list[object] = []
    root = Path("/project")

    def resolve(**kwargs: object) -> Path:
        calls.append(("resolve", kwargs))
        return root

    def load(**kwargs: object) -> dict[str, InstalledSkill]:
        calls.append(("load", kwargs))
        return {"demo": installed("demo")}

    context.services.resolve_project_root = resolve
    context.services.load_installed_state = load

    assert run_status(context) == 0
    assert calls == [
        ("resolve", {"cwd": context.cwd}),
        (
            "load",
            {"project_root": root, "env": context.env},
        ),
    ]
