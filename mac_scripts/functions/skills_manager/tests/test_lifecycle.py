from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Literal

import pytest

from shared.selector import SelectorResult, create_selector_state
from skills_manager.cli import CommandContext, Services
from skills_manager.config import Catalog, CatalogSource
import skills_manager.lifecycle as lifecycle
from skills_manager.lifecycle import run_status
from skills_manager.state import InstalledSkill, InstalledStateError
from skills_manager.upstream import ExecutionResult, MutationRecord
import skills_manager.upstream as upstream


@pytest.mark.parametrize("all_flag", ["-a", "--all"])
def test_lifecycle_parser_accepts_exact_all_yes_and_dry_flags(
    all_flag: str,
) -> None:
    assert lifecycle.parse_lifecycle_command(
        "add", (all_flag, "-y", "--dry-run")
    ) == ((), True, True, True)


def test_lifecycle_parser_rejects_all_with_explicit_target() -> None:
    with pytest.raises(ValueError, match="Cannot combine --all"):
        lifecycle.parse_lifecycle_command("remove", ("-a", "source"))


def test_services_defaults_are_concrete_lifecycle_executors() -> None:
    services = Services()

    assert services.execute_install_plan.__name__ == "execute_install_plan"
    assert services.execute_uninstall_plan.__name__ == "execute_uninstall_plan"


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


def configure_lifecycle(
    context: CommandContext,
    *,
    installed_state: dict[str, InstalledSkill] | None = None,
    result: ExecutionResult | None = None,
) -> list[object]:
    calls: list[object] = []
    root = Path("/project")
    installed_value = {} if installed_state is None else installed_state
    execution = result or ExecutionResult(True, (), ())
    context.services.resolve_project_root = lambda **kwargs: calls.append(
        ("resolve", kwargs)
    ) or root
    context.services.load_installed_state = lambda **kwargs: calls.append(
        ("load", kwargs)
    ) or installed_value
    context.services.execute_install_plan = lambda plan, **kwargs: calls.append(
        ("install", plan, kwargs)
    ) or execution
    context.services.execute_uninstall_plan = lambda plan, **kwargs: calls.append(
        ("uninstall", plan, kwargs)
    ) or execution
    return calls


def test_add_resolves_target_before_state_and_passes_canonical_root(
    context: CommandContext,
) -> None:
    calls = configure_lifecycle(context)

    assert lifecycle.run_add(
        ("1",), all_sources=False, yes=True, dry_run=False, context=context
    ) == 0

    assert [call[0] for call in calls] == ["resolve", "load", "install"]
    assert calls[1] == (
        "load", {"project_root": Path("/project"), "env": context.env}
    )
    plan = calls[2][1]
    assert tuple(item.skill for item in plan.install) == ("demo",)
    assert calls[2][2] == {
        "yes": True, "project_root": Path("/project")
    }


def test_add_invalid_target_fails_before_state_discovery(
    context: CommandContext,
) -> None:
    calls = configure_lifecycle(context)

    assert lifecycle.run_add(
        ("9",), all_sources=False, yes=True, dry_run=False, context=context
    ) == 1

    assert [call[0] for call in calls] == ["resolve"]
    assert "Source index out of range: 9" in context.stderr.getvalue()


def test_add_rejects_empty_selected_source_before_state(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(CatalogSource(source="owner/empty", skills=()),),
    )
    calls = configure_lifecycle(context)

    assert lifecycle.run_add(
        ("1",), all_sources=False, yes=True, dry_run=False, context=context
    ) == 1

    assert [call[0] for call in calls] == ["resolve"]
    assert "Selected sources contain no skills" in context.stderr.getvalue()


def test_add_desired_conflict_fails_before_state_and_preview(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/one", skills=("demo",)),
            CatalogSource(source="owner/two", skills=("demo",)),
        ),
    )
    calls = configure_lifecycle(context)

    assert lifecycle.run_add(
        (), all_sources=True, yes=True, dry_run=False, context=context
    ) == 1

    assert [call[0] for call in calls] == ["resolve"]
    assert "Conflicting desired skill sources" in context.stderr.getvalue()
    assert context.stdout.getvalue() == ""


def test_add_picker_filters_only_install_requirements(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/catalog", skills=("one", "two")),
        ),
    )
    calls = configure_lifecycle(context)
    selected = '["owner/catalog","two"]'

    def select(items, **kwargs):
        calls.append(("select", tuple(items), kwargs))
        return SelectorResult(
            "submit", create_selector_state(items), (selected,)
        )

    context.select_items = select

    assert lifecycle.run_add(
        ("1",), all_sources=False, yes=False, dry_run=False, context=context
    ) == 0

    plan = next(call[1] for call in calls if call[0] == "install")
    assert tuple(item.skill for item in plan.install) == ("two",)
    selection = next(call for call in calls if call[0] == "select")
    assert tuple(item.label for item in selection[1]) == ("one", "two")


def test_add_picker_cancellation_is_successful_no_op(
    context: CommandContext,
) -> None:
    calls = configure_lifecycle(context)
    context.select_items = lambda items, **_: SelectorResult(
        "cancel", create_selector_state(items), ()
    )
    context.confirm = lambda *_: pytest.fail("confirmation called")

    assert lifecycle.run_add(
        ("1",), all_sources=False, yes=False, dry_run=False, context=context
    ) == 0

    assert not any(call[0] == "install" for call in calls)
    assert context.stdout.getvalue() == ""


@pytest.mark.parametrize(
    "actual",
    [
        installed("demo", source="other/catalog"),
        installed("demo", source=None, provenance="untracked"),
    ],
)
def test_add_conflicts_fail_before_preview_even_when_picker_drops_them(
    context: CommandContext, actual: InstalledSkill
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/catalog", skills=("demo", "safe")),
        ),
    )
    calls = configure_lifecycle(context, installed_state={"demo": actual})
    context.select_items = lambda items, **_: SelectorResult(
        "submit", create_selector_state(items),
        ('["owner/catalog","safe"]',),
    )

    assert lifecycle.run_add(
        ("1",), all_sources=False, yes=False, dry_run=False, context=context
    ) == 1

    assert "Blocked by installed skill conflicts: demo" in (
        context.stderr.getvalue()
    )
    assert context.stdout.getvalue() == ""
    assert not any(call[0] == "install" for call in calls)


def test_add_dry_run_renders_without_confirmation_or_mutation(
    context: CommandContext,
) -> None:
    configure_lifecycle(context)
    context.confirm = lambda *_: pytest.fail("confirmation called")
    context.services.execute_install_plan = lambda *_args, **_kwargs: (
        pytest.fail("mutation called")
    )

    assert lifecycle.run_add(
        ("1",), all_sources=False, yes=False, dry_run=True, context=context
    ) == 0

    assert "DRY RUN" in context.stdout.getvalue()


def test_add_confirmation_decline_returns_zero_without_mutation(
    context: CommandContext,
) -> None:
    calls = configure_lifecycle(context)
    context.confirm = lambda _message: False

    assert lifecycle.run_add(
        ("1",), all_sources=False, yes=False, dry_run=False, context=context
    ) == 0

    assert "Install plan" in context.stdout.getvalue()
    assert not any(call[0] == "install" for call in calls)


def test_add_failed_execution_renders_summary_and_returns_one(
    context: CommandContext,
) -> None:
    record = MutationRecord("install", "owner/catalog", ("demo",), 7)
    configure_lifecycle(
        context, result=ExecutionResult(False, (), (record,))
    )

    assert lifecycle.run_add(
        ("1",), all_sources=False, yes=True, dry_run=False, context=context
    ) == 1

    output = context.stdout.getvalue()
    assert "Install incomplete" in output
    assert "status 7" in output
    assert "npx skills add owner/catalog --skill demo" in output


def test_add_js_equivalent_sources_match_live_node_batch_and_retry(
    context: CommandContext,
) -> None:
    scalar = "owner/😀"
    paired = "owner/\ud83d\ude00"
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source=scalar, skills=("first",)),
            CatalogSource(source=paired, skills=("second",)),
        ),
    )
    calls: list[tuple[str, ...]] = []
    events: list[MutationRecord] = []
    configure_lifecycle(context)
    context.services.execute_install_plan = lambda plan, **options: (
        upstream.execute_install_plan(
            plan,
            **options,
            run_mutation=lambda args, **_: calls.append(tuple(args)) or 7,
            on_event=events.append,
        )
    )

    assert lifecycle.run_add(
        (), all_sources=True, yes=True, dry_run=False, context=context
    ) == 1

    python_summary = {
        "calls": [list(call) for call in calls],
        "events": [
            {
                "action": event.action,
                "source": event.source,
                "skills": list(event.skills),
                "status": event.status,
            }
            for event in events
        ],
    }
    script = r'''
const { executeInstallPlan } = await import(
  "./mac_scripts/functions/skills-manager/operations.mjs"
);
const scalar = "owner/😀";
const paired = "owner/\ud83d\ude00";
const calls = [];
const events = [];
await executeInstallPlan({
  install: [
    { source: scalar, skill: "first" },
    { source: paired, skill: "second" },
  ],
  conflicts: [], desiredConflicts: [],
}, {
  yes: true,
  runMutation: async (args) => { calls.push(args); return 7; },
  onEvent: (event) => events.push(event),
});
process.stdout.write(JSON.stringify({ calls, events }));
'''
    node = subprocess.run(
        ("node", "--input-type=module", "-e", script),
        check=True,
        capture_output=True,
        text=True,
    )

    assert python_summary == json.loads(node.stdout)
    retry = (
        "npx skills add '[unsafe source redacted]' --skill first "
        "--skill second"
    )
    assert "Install incomplete" in context.stdout.getvalue()
    assert "status 7" in context.stdout.getvalue()
    assert context.stdout.getvalue().count(retry) == 1


def test_remove_uses_selected_and_remaining_catalog_slices(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/one", skills=("one",)),
            CatalogSource(source="owner/two", skills=("two",)),
        ),
    )
    calls = configure_lifecycle(
        context,
        installed_state={
            "one": installed("one", source="owner/one"),
            "two": installed("two", source="owner/two"),
        },
    )

    assert lifecycle.run_remove(
        ("1",), all_sources=False, yes=True, dry_run=False, context=context
    ) == 0

    plan = next(call[1] for call in calls if call[0] == "uninstall")
    assert tuple(item.skill for item in plan.remove) == ("one",)
    assert plan.retain == ()
    assert "two" not in tuple(item.skill for item in plan.remove)


def test_remove_mismatch_and_untracked_names_remain_eligible(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/catalog", skills=("one", "two")),
        ),
    )
    calls = configure_lifecycle(
        context,
        installed_state={
            "one": installed("one", source="other/catalog"),
            "two": installed("two", source=None, provenance="untracked"),
        },
    )

    assert lifecycle.run_remove(
        ("1",), all_sources=False, yes=True, dry_run=False, context=context
    ) == 0

    plan = next(call[1] for call in calls if call[0] == "uninstall")
    assert tuple(item.skill for item in plan.remove) == ("one", "two")
    assert plan.conflicts == ()


def test_remove_dry_run_skips_confirmation_and_mutation(
    context: CommandContext,
) -> None:
    calls = configure_lifecycle(
        context, installed_state={"demo": installed("demo")}
    )
    context.confirm = lambda *_: pytest.fail("confirmation called")

    assert lifecycle.run_remove(
        ("1",), all_sources=False, yes=False, dry_run=True, context=context
    ) == 0

    assert "DRY RUN" in context.stdout.getvalue()
    assert not any(call[0] == "uninstall" for call in calls)


def test_remove_failed_execution_uses_uninstall_summary_and_exit_one(
    context: CommandContext,
) -> None:
    record = MutationRecord("uninstall", None, ("demo",), 4)
    configure_lifecycle(
        context,
        installed_state={"demo": installed("demo")},
        result=ExecutionResult(False, (), (record,)),
    )

    assert lifecycle.run_remove(
        ("1",), all_sources=False, yes=True, dry_run=False, context=context
    ) == 1

    output = context.stdout.getvalue()
    assert "Uninstall incomplete" in output
    assert "npx skills remove demo" in output
