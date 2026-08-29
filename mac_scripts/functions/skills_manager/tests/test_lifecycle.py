from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from shared.selector import SelectorResult, create_selector_state
from skills_manager.cli import CommandContext, Services
from skills_manager.config import Catalog, CatalogSource
import skills_manager.lifecycle as lifecycle
from skills_manager.lifecycle import run_status
from skills_manager.planner import requirement_key
from skills_manager.selector import reduce_catalog_selector
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


def test_add_js_equivalent_sources_use_one_literal_batch_and_retry(
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

    assert calls == [
        (
            "skills",
            "add",
            scalar,
            "--skill",
            "first",
            "--skill",
            "second",
            "--yes",
        )
    ]
    assert events == [
        MutationRecord(
            action="install",
            source=scalar,
            skills=("first", "second"),
            status=7,
        )
    ]
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


def configure_interactive(
    context: CommandContext,
    *,
    selected: tuple[str, ...] = (),
    installed_state: dict[str, InstalledSkill] | None = None,
    confirmed: bool = True,
    available: bool = True,
    install_result: ExecutionResult | None = None,
    uninstall_result: ExecutionResult | None = None,
) -> list[object]:
    calls: list[object] = []
    root = Path("/project")
    state = {} if installed_state is None else installed_state
    context.services.resolve_project_root = lambda **options: calls.append(
        ("resolve", options)
    ) or root
    context.services.load_installed_state = lambda **options: calls.append(
        ("load", options)
    ) or state

    def select(items, **options):
        calls.append(("select", items, options))
        return SelectorResult(
            "submit", create_selector_state(items, initial=options["initial"]), selected
        )

    context.select_items = select
    context.confirm_apply = lambda **preview: calls.append(
        ("confirm", preview)
    ) or confirmed
    context.services.has_command = lambda name, **options: calls.append(
        ("npx", name, options)
    ) or available
    context.services.execute_install_plan = lambda plan, **options: calls.append(
        ("install", plan, options)
    ) or (install_result or ExecutionResult(True, (), ()))
    context.services.execute_uninstall_plan = lambda plan, **options: calls.append(
        ("uninstall", plan, options)
    ) or (uninstall_result or ExecutionResult(True, (), ()))
    return calls


def test_interactive_empty_catalog_errors_without_external_calls(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(version=1, sources=())
    context.services.resolve_project_root = lambda **_: pytest.fail("root resolved")

    assert lifecycle.run_interactive(context) == 1
    assert context.stdout.getvalue() == ""
    assert "Catalog is empty" in context.stderr.getvalue()
    assert "skm source add" in context.stderr.getvalue()


def test_interactive_selector_receives_only_correct_installs_as_initial(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(CatalogSource(source="owner/catalog", skills=("one", "two")),),
    )
    calls = configure_interactive(
        context,
        installed_state={
            "one": installed("one"),
            "two": installed("two", source="wrong/catalog"),
        },
    )

    def cancel(items, **options):
        calls.append(("select", items, options))
        return SelectorResult("cancel", create_selector_state(items), ())

    context.select_items = cancel

    assert lifecycle.run_interactive(context) == 1
    selection = next(call for call in calls if call[0] == "select")
    assert selection[2]["initial"] == (
        requirement_key("owner/catalog", "one"),
    )
    assert "Selection cancelled" in context.stdout.getvalue()
    assert not any(
        call[0] in {"confirm", "npx", "install", "uninstall"} for call in calls
    )


def test_interactive_matching_selection_is_noop_without_confirmation(
    context: CommandContext,
) -> None:
    selected = (requirement_key("owner/catalog", "demo"),)
    calls = configure_interactive(
        context, selected=selected, installed_state={"demo": installed("demo")}
    )

    assert lifecycle.run_interactive(context) == 0
    assert not any(
        call[0] in {"confirm", "npx", "install", "uninstall"} for call in calls
    )


def test_interactive_desired_conflict_fails_before_preview(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/one", skills=("demo",)),
            CatalogSource(source="owner/two", skills=("demo",)),
        ),
    )
    calls = configure_interactive(
        context, selected=(requirement_key("owner/one", "demo"),)
    )

    assert lifecycle.run_interactive(context) == 1
    assert "Conflicting desired skill sources" in context.stderr.getvalue()
    assert not any(call[0] == "confirm" for call in calls)


def test_interactive_selected_installed_conflict_fails_before_preview(
    context: CommandContext,
) -> None:
    calls = configure_interactive(
        context,
        selected=(requirement_key("owner/catalog", "demo"),),
        installed_state={"demo": installed("demo", source="wrong/catalog")},
    )

    assert lifecycle.run_interactive(context) == 1
    assert "Blocked by installed skill conflicts: demo" in context.stderr.getvalue()
    assert not any(call[0] == "confirm" for call in calls)


def test_interactive_preview_decline_has_no_preflight_or_mutation(
    context: CommandContext,
) -> None:
    calls = configure_interactive(
        context,
        selected=(requirement_key("owner/catalog", "demo"),),
        confirmed=False,
    )
    context.services.write_catalog = lambda *_: pytest.fail("config written")

    assert lifecycle.run_interactive(context) == 1
    assert [call[0] for call in calls] == ["resolve", "load", "select", "confirm"]


def test_interactive_preview_excludes_a_skill_key_colliding_with_source_row(
    context: CommandContext,
) -> None:
    colliding_source = requirement_key("b", "x")
    context.catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source=colliding_source, skills=("y",)),
            CatalogSource(source="b", skills=("x",)),
        ),
    )
    calls = configure_interactive(context, confirmed=False)

    def select(items, **options):
        state = create_selector_state(items, initial=options["initial"])
        selected = reduce_catalog_selector(state, "toggle").state
        return reduce_catalog_selector(selected, "submit")

    context.select_items = select

    assert lifecycle.run_interactive(context) == 1
    preview = next(call[1] for call in calls if call[0] == "confirm")
    assert tuple(item.skill for item in preview["install"]) == ("y",)
    assert preview["remove"] == ()


def test_interactive_preflights_then_installs_before_uninstalling(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(CatalogSource(source="owner/catalog", skills=("one", "two")),),
    )
    calls = configure_interactive(
        context,
        selected=(requirement_key("owner/catalog", "two"),),
        installed_state={"one": installed("one")},
    )

    assert lifecycle.run_interactive(context) == 0
    assert [call[0] for call in calls] == [
        "resolve", "load", "select", "confirm", "npx", "install", "uninstall"
    ]
    assert tuple(item.skill for item in calls[5][1].install) == ("two",)
    assert tuple(item.skill for item in calls[6][1].remove) == ("one",)
    assert calls[5][2] == {"project_root": Path("/project")}
    assert calls[6][2] == {"project_root": Path("/project")}
    assert "Changes complete" in context.stdout.getvalue()


def test_interactive_missing_npx_after_confirmation_prevents_mutation(
    context: CommandContext,
) -> None:
    calls = configure_interactive(
        context,
        selected=(requirement_key("owner/catalog", "demo"),),
        available=False,
    )

    assert lifecycle.run_interactive(context) == 1
    assert [call[0] for call in calls][-2:] == ["confirm", "npx"]
    assert "npx is required" in context.stderr.getvalue()


def test_interactive_combines_partial_results_and_returns_one(
    context: CommandContext,
) -> None:
    context.catalog = Catalog(
        version=1,
        sources=(CatalogSource(source="owner/catalog", skills=("one", "two")),),
    )
    failed = MutationRecord("install", "owner/catalog", ("two",), 7)
    removed = MutationRecord("uninstall", None, ("one",), 0)
    configure_interactive(
        context,
        selected=(requirement_key("owner/catalog", "two"),),
        installed_state={"one": installed("one")},
        install_result=ExecutionResult(False, (), (failed,)),
        uninstall_result=ExecutionResult(True, (removed,), ()),
    )

    assert lifecycle.run_interactive(context) == 1
    output = context.stdout.getvalue()
    assert "Changes incomplete" in output
    assert "1 succeeded; 1 failed" in output
