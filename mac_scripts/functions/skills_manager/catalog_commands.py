from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from shared.selector import SelectorItem

from .config import (
    cross_source_skill_conflicts,
    remove_source_at,
    resolve_source_token,
    upsert_source,
)
from .source import canonicalize_source, redact_source

if TYPE_CHECKING:
    from .cli import CommandContext


class SkmUsageError(ValueError):
    """A stable user-facing command grammar error."""


@dataclass(frozen=True, slots=True)
class SourceSkillItem(SelectorItem):
    kind: str = "skill"
    hint: str = ""


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    kind: Literal["submit", "cancel"]
    selected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceRequest:
    action: str
    target: str
    skills: tuple[str, ...]
    all_skills: bool
    no_skills: bool
    yes: bool


_USAGE = {
    "add": "skm source add <source> [-k skill...] [-a|--all] "
    "[-n|--no-skills] [-y]",
    "edit": "skm source edit <source|index> [-k skill...] [-a|--all] "
    "[-n|--no-skills] [-y]",
    "remove": "skm source remove <source|index>",
}
_FLAGS = {
    "-a": "all", "--all": "all",
    "-n": "none", "--no-skills": "none",
    "-y": "yes", "--yes": "yes",
}
_MODE_ERROR = "--skill, --all, and --no-skills are mutually exclusive"


def parse_source_command(args: Sequence[str]) -> SourceRequest | None:
    action = args[0] if args else ""
    if action not in _USAGE:
        return None
    positionals: list[str] = []
    skills: list[str] = []
    enabled: set[str] = set()
    index = 1
    while index < len(args):
        value = args[index]
        if value in {"-k", "--skill"}:
            if index + 1 == len(args) or args[index + 1].startswith("-"):
                raise SkmUsageError(f"Missing value for {value}")
            skills.append(args[index + 1])
            index += 2
            continue
        if value in _FLAGS:
            enabled.add(_FLAGS[value])
        elif value.startswith("-"):
            raise SkmUsageError(f"Unknown option: {value}")
        else:
            positionals.append(value)
        index += 1
    if bool(skills) + ("all" in enabled) + ("none" in enabled) > 1:
        raise SkmUsageError(_MODE_ERROR)
    if len(positionals) != 1:
        raise SkmUsageError(f"Usage: {_USAGE[action]}")
    if action == "remove":
        if skills:
            raise SkmUsageError("Unexpected --skill")
        for mode, option in (
            ("all", "--all"), ("none", "--no-skills"), ("yes", "--yes")
        ):
            if mode in enabled:
                raise SkmUsageError(f"Unexpected {option}")
    return SourceRequest(
        action, positionals[0], tuple(skills), "all" in enabled,
        "none" in enabled, "yes" in enabled,
    )


def _is_tty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def select_source_skills(
    source: str, *, requested: Sequence[str], all_skills: bool,
    no_skills: bool, initial: Sequence[str], context: CommandContext,
) -> SelectionOutcome:
    if bool(requested) + all_skills + no_skills > 1:
        raise SkmUsageError(_MODE_ERROR)
    if no_skills:
        return SelectionOutcome("submit", ())
    available = context.services.discover_available_skills(
        source, cwd=context.cwd
    )
    if all_skills:
        return SelectionOutcome("submit", tuple(item.name for item in available))
    if requested:
        selected = tuple(dict.fromkeys(requested))
        names = frozenset(item.name for item in available)
        missing = tuple(name for name in selected if name not in names)
        if missing:
            raise ValueError(f"Skills not found in source: {', '.join(missing)}")
        return SelectionOutcome("submit", selected)
    title = f"Select skills from {redact_source(source)}"
    if not _is_tty(context.stdin) or not _is_tty(context.stdout):
        raise ValueError(f"{title} requires an interactive terminal")
    result = context.select_items(
        tuple(
            SourceSkillItem(item.name, item.name, "skill", item.description)
            for item in available
        ),
        initial=initial, multiple=True, title=title,
    )
    return SelectionOutcome(result.kind, result.selected)


def _report(
    context: CommandContext, operation: Callable[..., int], *args: object,
) -> int:
    try:
        return operation(*args, context=context)
    except Exception as error:
        context.ui.error(str(error))
        return 1


def _change(
    action: Literal["added", "edited"], token: str, skills: Sequence[str],
    all_skills: bool, no_skills: bool, *, context: CommandContext,
) -> int:
    if action == "added":
        source = canonicalize_source(token, cwd=context.cwd)
        for index, entry in enumerate(context.catalog.sources, start=1):
            if entry.source == source:
                raise ValueError(
                    f"Source {redact_source(source)} already exists "
                    f"(index {index}). Use: skm source edit {index}"
                )
        initial: Sequence[str] = ()
    else:
        _index, entry = resolve_source_token(
            context.catalog, token, cwd=context.cwd
        )
        source, initial = entry.source, entry.skills
    selection = select_source_skills(
        source, requested=skills, all_skills=all_skills,
        no_skills=no_skills, initial=initial, context=context,
    )
    if selection.kind != "submit":
        return 0
    selected = tuple(dict.fromkeys(selection.selected))
    conflicts = cross_source_skill_conflicts(
        context.catalog, source, selected, cwd=context.cwd
    )
    if conflicts:
        details = "; ".join(
            f"{skill} ({redact_source(owner)})" for skill, owner in conflicts
        )
        raise ValueError(f"Skill already in another source: {details}")
    catalog = upsert_source(context.catalog, source, selected, cwd=context.cwd)
    context.services.write_catalog(context.paths, catalog)
    context.ui.source_changed(action, None, source, selected)
    return 0


def run_source_add(
    source: str, *, skills: Sequence[str], all_skills: bool,
    no_skills: bool, yes: bool, context: CommandContext,
) -> int:
    del yes
    return _report(
        context, _change, "added", source, skills, all_skills, no_skills
    )


def run_source_edit(
    token: str, *, skills: Sequence[str], all_skills: bool,
    no_skills: bool, yes: bool, context: CommandContext,
) -> int:
    del yes
    return _report(
        context, _change, "edited", token, skills, all_skills, no_skills
    )


def _remove(token: str, *, context: CommandContext) -> int:
    index, entry = resolve_source_token(context.catalog, token, cwd=context.cwd)
    root = context.services.resolve_project_root(cwd=context.cwd)
    installed = context.services.load_installed_state(
        project_root=root, env=context.env
    )
    names = tuple(
        item.name for item in installed.values() if item.source == entry.source
    )
    if names:
        raise ValueError(
            f"Cannot remove source {redact_source(entry.source)}: "
            f"installed skills: {', '.join(names)}"
        )
    catalog = remove_source_at(context.catalog, index)
    context.services.write_catalog(context.paths, catalog)
    context.ui.source_changed("removed", None, entry.source, ())
    return 0


def run_source_remove(token: str, *, context: CommandContext) -> int:
    return _report(context, _remove, token)


def run_source_command(args: Sequence[str], context: CommandContext) -> int:
    request = parse_source_command(args)
    if request is None:
        context.ui.error(f"Unknown source command: {args[0] if args else ''}")
        return 1
    if request.action == "remove":
        return run_source_remove(request.target, context=context)
    return _report(
        context, _change, "added" if request.action == "add" else "edited",
        request.target, request.skills, request.all_skills, request.no_skills,
    )
