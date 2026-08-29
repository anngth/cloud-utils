from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from .catalog_commands import SkmUsageError, SourceSkillItem
from .config import Catalog, resolve_source_token
from .planner import (
    catalog_requirements,
    classify_status,
    create_install_plan,
    create_uninstall_plan,
)
from .source import redact_source

if TYPE_CHECKING:
    from .cli import CommandContext
LifecycleRequest = tuple[tuple[str, ...], bool, bool, bool]
def parse_lifecycle_command(action: str, args: Sequence[str]) -> LifecycleRequest:
    tokens: list[str] = []
    enabled: set[str] = set()
    flags = {
        "-y": "yes", "--yes": "yes",
        "-d": "dry_run", "--dry-run": "dry_run",
    }
    if action != "status":
        flags.update({"-a": "all", "--all": "all"})
    for value in args:
        if value in flags:
            enabled.add(flags[value])
        elif value.startswith("-"):
            raise SkmUsageError(f"Unknown option: {value}")
        elif action == "status":
            raise SkmUsageError(f"Unexpected argument: {value}")
        elif value not in tokens:
            tokens.append(value)
    if "all" in enabled and tokens:
        raise SkmUsageError("Cannot combine --all with explicit source targets")
    return (
        tuple(tokens), "all" in enabled, "yes" in enabled, "dry_run" in enabled)

def _report(context, operation: Callable[..., int], *args, **kwargs) -> int:
    try:
        return operation(*args, context=context, **kwargs)
    except Exception as error:
        message = str(error).encode("utf-16", "surrogatepass")
        context.ui.error(message.decode("utf-16", "replace"))
        return 1
def _state(context, root):
    return context.services.load_installed_state(
        project_root=root, env=context.env
    )
def run_status(context: CommandContext) -> int:
    def status(*, context: CommandContext) -> int:
        root = context.services.resolve_project_root(cwd=context.cwd)
        result = classify_status(
            catalog_requirements(context.catalog), _state(context, root)
        )
        context.ui.status(
            project_root=root, profile_names=(), catalog=context.catalog,
            status=result)
        drift = (
            result.missing or result.mismatches or result.untracked
            or result.desired_conflicts
        )
        return 1 if drift else 0

    return _report(context, status)
def _targets(action, tokens, all_sources, context):
    if not all_sources and not tokens:
        raise SkmUsageError(
            f"Usage: skm {action} <source|index...> [--all] "
            "[(-y | --yes)] [(-d | --dry-run)]"
        )
    if all_sources:
        return tuple(enumerate(context.catalog.sources))
    return tuple(
        resolve_source_token(context.catalog, token, cwd=context.cwd)
        for token in tokens)
def _slice(entries):
    return catalog_requirements(Catalog(version=1, sources=tuple(entries)))
def _conflict_message(conflicts) -> str:
    details = "; ".join(
        f"{item.skill} ("
        f"{' vs '.join(redact_source(value) for value in item.sources)})"
        for item in conflicts
    )
    return f"Conflicting desired skill sources: {details}"
def _finish(context, plan, root, targets, *, yes, dry_run, uninstall) -> int:
    labels = tuple(redact_source(entry.source) for _index, entry in targets)
    kind = "uninstall" if uninstall else "install"
    getattr(context.ui, f"{kind}_plan")(
        project_root=root, profile_names=labels, plan=plan, dry_run=dry_run)
    if dry_run:
        return 0
    if not yes and not context.confirm(f"Apply this {kind} plan?"):
        return 0
    execute = getattr(context.services, f"execute_{kind}_plan")
    result = execute(plan, yes=yes, project_root=root)
    options = {"operation": "uninstall"} if uninstall else {}
    context.ui.execution_summary(result, **options)
    return 0 if result.ok else 1
def _lifecycle(
    action, tokens, *, all_sources, yes, dry_run, context) -> int:
    root = context.services.resolve_project_root(cwd=context.cwd)
    targets = _targets(action, tokens, all_sources, context)
    selected_entries = tuple(entry for _index, entry in targets)
    selected = _slice(selected_entries)
    uninstall = action == "remove"
    if uninstall:
        selected_sources = frozenset(entry.source for entry in selected_entries)
        remaining = _slice(
            entry for entry in context.catalog.sources
            if entry.source not in selected_sources
        )
        conflicts = catalog_requirements(context.catalog).desired_conflicts
        if conflicts:
            raise ValueError(_conflict_message(conflicts))
        plan = create_uninstall_plan(
            selected=selected, remaining=remaining,
            installed_state=_state(context, root), linked_selected=())
        by_key = {item.key: item for item in remaining.requirements}
        plan = replace(
            plan,
            retain=tuple(by_key.get(item.key, item) for item in plan.retain))
    else:
        if not selected.requirements:
            raise ValueError("Selected sources contain no skills to install")
        if selected.desired_conflicts:
            raise ValueError(_conflict_message(selected.desired_conflicts))
        status = classify_status(selected, _state(context, root))
        plan = create_install_plan(status)
        if not yes and plan.install:
            items = tuple(
                SourceSkillItem(
                    item.skill, item.key, "skill", redact_source(item.source)
                )
                for item in plan.install
            )
            selection = context.select_items(items)
            if selection.kind != "submit":
                return 0
            plan = create_install_plan(
                status, selected_keys=frozenset(selection.selected))
        if plan.conflicts:
            names = dict.fromkeys(item.skill for item in plan.conflicts)
            raise ValueError(
                f"Blocked by installed skill conflicts: {', '.join(names)}"
            )
    return _finish(
        context, plan, root, targets, yes=yes, dry_run=dry_run,
        uninstall=uninstall)

def run_add(
    tokens: Sequence[str], *, all_sources: bool, yes: bool, dry_run: bool,
    context: CommandContext) -> int:
    options = dict(all_sources=all_sources, yes=yes, dry_run=dry_run)
    return _report(context, _lifecycle, "add", tokens, **options)
def run_remove(
    tokens: Sequence[str], *, all_sources: bool, yes: bool, dry_run: bool,
    context: CommandContext) -> int:
    options = dict(all_sources=all_sources, yes=yes, dry_run=dry_run)
    return _report(context, _lifecycle, "remove", tokens, **options)
