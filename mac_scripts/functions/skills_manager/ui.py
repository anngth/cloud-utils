from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from shared.selector import SelectorState
from shared.streams import TextWriter
from shared.ui import (
    BRIGHT_GREEN,
    CYAN,
    GRAY,
    GREEN,
    RED,
    YELLOW,
    FrameUi,
    _fg,
)

from .config import Catalog
from .planner import (
    InstallPlan,
    Requirement,
    StatusResult,
    UninstallPlan,
    requirement_key,
)
from .source import redact_source
from .upstream import AvailableSkill

WHITE = "\033[97m"
DIM = "\033[2m"
RESET = "\033[0m"
SELECTOR_DESCRIPTION_COLOR = f"{DIM}{GRAY}"
_SAFE_SHELL_DISPLAY = re.compile(r"[A-Za-z0-9_./:@%+=,-]+")
_Row = TypeVar("_Row")


@dataclass(frozen=True, slots=True)
class RequirementGroup:
    source_index: int
    source: str
    label: str
    skills: tuple[str, ...]


class MutationRecordLike(Protocol):
    action: str
    source: str | None
    skills: Sequence[str]
    status: int


class ExecutionResultLike(Protocol):
    ok: bool
    succeeded: Sequence[MutationRecordLike]
    failed: Sequence[MutationRecordLike]


def selector_name_color(*, is_cursor: bool, is_selected: bool) -> str:
    if is_cursor:
        return WHITE
    if is_selected:
        return GREEN
    return GRAY


def group_requirements_by_catalog_source(
    requirements: Iterable[Requirement], catalog: Catalog
) -> tuple[RequirementGroup, ...]:
    values = tuple(requirements)
    groups: list[RequirementGroup] = []
    for source_index, entry in enumerate(catalog.sources, start=1):
        skills = tuple(
            item.skill for item in values if item.source == entry.source
        )
        if skills:
            groups.append(
                RequirementGroup(
                    source_index,
                    entry.source,
                    redact_source(entry.source),
                    skills,
                )
            )
    return tuple(groups)


def shell_arg(value: object) -> str:
    text = str(value)
    if _SAFE_SHELL_DISPLAY.fullmatch(text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


class SkmUi:
    def __init__(self, stdout: TextWriter, stderr: TextWriter) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.frame = FrameUi(stdout, stderr)

    def _out(self, text: object = "") -> None:
        self.frame.line(text)

    def _clear(self) -> None:
        self.stdout.write("\033[2J\033[H")
        self.stdout.flush()

    def _title(self) -> None:
        self.frame.title("SKILLS MANAGER")

    def _continuation(self, syntax: object, description: object = "") -> None:
        suffix = f"  {_fg(GRAY, description)}" if description else ""
        self._out(f"{self.frame.pipe}      {_fg(GREEN, syntax)}{suffix}")

    def _skill_item(
        self,
        name: object,
        *,
        suffix: object = "",
        marker: str = "■",
        marker_color: str = GREEN,
        suffix_color: str = GRAY,
        indent: str = "  ",
    ) -> None:
        detail = f" {_fg(suffix_color, suffix)}" if suffix else ""
        self._out(
            f"{self.frame.pipe}{indent}{_fg(marker_color, marker)} "
            f"{_fg(BRIGHT_GREEN, name)}{detail}"
        )

    def _raw_item(self, text: object, color: str = GREEN) -> None:
        self._out(f"{self.frame.pipe}  {_fg(color, '■')} {text}")

    def _skill_list(
        self,
        values: Sequence[_Row],
        render: Callable[[_Row], None],
    ) -> None:
        for index, value in enumerate(values):
            if index:
                self._out(self.frame.pipe)
            render(value)

    def _requirement_suffix(self, value: Requirement) -> str:
        profiles = tuple(getattr(value, "profiles", ()))
        required = (
            f" — required by {', '.join(profiles)}" if profiles else ""
        )
        return f"— {redact_source(value.source)}{required}"

    def _requirement_section(
        self,
        label: str,
        values: Sequence[Requirement],
        color: str = GREEN,
    ) -> None:
        if not values:
            return
        self.frame.active(label)

        def render(value: Requirement) -> None:
            self._skill_item(
                value.skill,
                suffix=self._requirement_suffix(value),
                marker_color=color,
                suffix_color=color,
            )

        self._skill_list(values, render)
        self._out(self.frame.pipe)

    def usage(self) -> None:
        self._title()
        self.frame.step("Usage: skm [command]")
        self.frame.command("skm", "Open interactive catalog selector")
        self.frame.command("skm (help | -h | --help)", "Show this help")

        self.frame.section("Lifecycle")
        self.frame.command("skm add <source|index...> [(-a | --all)]")
        self._continuation(
            "[(-y | --yes)] [(-d | --dry-run)]",
            "Install catalog skills for selected sources",
        )
        self.frame.command("skm remove <source|index...> [(-a | --all)]")
        self._continuation(
            "[(-y | --yes)] [(-d | --dry-run)]",
            "Uninstall catalog skills for selected sources",
        )
        self.frame.command(
            "skm status", "Compare catalog and installed skills"
        )

        self.frame.section("Catalog")
        self.frame.command("skm source add <source>")
        self._continuation(
            "[[(-k | --skill) <skill>]... | (-a | --all) | "
            "(-n | --no-skills)] [(-y | --yes)]",
            "Add a catalog source",
        )
        self.frame.command("skm source edit <source|index>")
        self._continuation(
            "[[(-k | --skill) <skill>]... | (-a | --all) | "
            "(-n | --no-skills)] [(-y | --yes)]",
            "Update skills for a catalog source",
        )
        self.frame.command(
            "skm source remove <source|index>", "Remove a catalog source"
        )

        self.frame.section("Notes")
        self.frame.note(
            "Source indexes are 1-based, matching the interactive selector "
            "and gt backup."
        )
        self.frame.note(
            "source add, source edit, and source remove change the catalog "
            "only; use add/remove to change disk."
        )
        self.frame.end()

    def source_changed(
        self,
        action: object,
        profile: object | None = None,
        source: object | None = None,
        skills: Sequence[str] = (),
        *,
        available: Sequence[AvailableSkill] = (),
    ) -> None:
        self._title()
        self.frame.step(f"Source {action}: {redact_source(str(source))}")
        if profile:
            self.frame.step(f"Profile: {profile}")
        count = len(skills)
        noun = "selected skill" if count == 1 else "selected skills"
        self.frame.step(f"{count} {noun}")
        if available:
            self.frame.active("Available skills")

            def render(record: AvailableSkill) -> None:
                suffix = f"— {record.description}" if record.description else ""
                self._skill_item(record.name, suffix=suffix)

            self._skill_list(available, render)
        self.frame.end()

    def _render_status_tree(
        self, catalog: Catalog, result: StatusResult
    ) -> None:
        installed = {item.key for item in result.installed}
        missing = {item.key for item in result.missing}
        mismatches = {item.key for item in result.mismatches}
        untracked = {item.key for item in result.untracked}
        for source_index, entry in enumerate(catalog.sources, start=1):
            if not entry.skills:
                continue
            self._out(
                f"{self.frame.pipe}  {source_index}  "
                f"{_fg(GRAY, redact_source(entry.source))}"
            )
            for skill in entry.skills:
                key = requirement_key(entry.source, skill)
                marker, marker_color = "□", GRAY
                if key in installed:
                    marker, marker_color = "■", GREEN
                elif key in mismatches or key in untracked:
                    marker, marker_color = "▲", RED
                elif key in missing:
                    marker, marker_color = "□", GRAY
                self._skill_item(
                    skill,
                    marker=marker,
                    marker_color=marker_color,
                    indent="      ",
                )
            self._out(self.frame.pipe)

    def status(
        self,
        *,
        project_root: object,
        profile_names: Sequence[str],
        catalog: Catalog,
        status: StatusResult,
    ) -> None:
        self._title()
        self.frame.step(f"Status: {project_root}")
        if profile_names:
            self.frame.step(f"Profiles: {', '.join(profile_names)}")
        self._render_status_tree(catalog, status)
        self._requirement_section("Source mismatch", status.mismatches, RED)
        self._requirement_section("Untracked", status.untracked, RED)
        if status.extras:
            self.frame.active("Extra")

            def render_extra(extra: object) -> None:
                name = getattr(extra, "name")
                source = getattr(extra, "source")
                suffix = f"— {redact_source(source)}" if source else ""
                self._skill_item(
                    name,
                    suffix=suffix,
                    marker_color=YELLOW,
                    suffix_color=YELLOW,
                )

            self._skill_list(status.extras, render_extra)
            self._out(self.frame.pipe)
        if status.desired_conflicts:
            self.frame.active("Desired-source conflict")

            def render_conflict(conflict: object) -> None:
                sources = " vs ".join(
                    redact_source(source)
                    for source in getattr(conflict, "sources")
                )
                profiles = tuple(getattr(conflict, "profiles", ()))
                required = f" — required by {', '.join(profiles)}"
                self._skill_item(
                    getattr(conflict, "skill"),
                    suffix=f"— {sources}{required}",
                    marker_color=RED,
                    suffix_color=RED,
                )

            self._skill_list(status.desired_conflicts, render_conflict)
            self._out(self.frame.pipe)
        self.frame.end()

    def install_plan(
        self,
        *,
        project_root: object,
        profile_names: Sequence[str],
        plan: InstallPlan,
        dry_run: bool = False,
    ) -> None:
        self._title()
        prefix = "DRY RUN — " if dry_run else ""
        self.frame.step(f"{prefix}Install plan: {project_root}")
        if profile_names:
            self.frame.step(f"Profiles: {', '.join(profile_names)}")
        self._requirement_section("Install", plan.install)
        self._requirement_section("Already installed", plan.skip, GRAY)
        self._requirement_section("Conflict", plan.conflicts, RED)
        if plan.extras:
            self.frame.active("Extra")

            def render_extra(extra: object) -> None:
                source = getattr(extra, "source")
                suffix = f"— {redact_source(source)}" if source else ""
                self._skill_item(
                    getattr(extra, "name"),
                    suffix=suffix,
                    marker_color=GRAY,
                    suffix_color=GRAY,
                )

            self._skill_list(plan.extras, render_extra)
            self._out(self.frame.pipe)
        if plan.desired_conflicts:
            self.frame.active("Desired-source conflict")

            def render_conflict(conflict: object) -> None:
                sources = " vs ".join(
                    redact_source(source)
                    for source in getattr(conflict, "sources")
                )
                self._skill_item(
                    getattr(conflict, "skill"),
                    suffix=f"— {sources}",
                    marker_color=RED,
                    suffix_color=RED,
                )

            self._skill_list(plan.desired_conflicts, render_conflict)
            self._out(self.frame.pipe)
        self.frame.end()

    def uninstall_plan(
        self,
        *,
        project_root: object,
        profile_names: Sequence[str],
        plan: UninstallPlan,
        dry_run: bool = False,
        keep_link: bool = False,
    ) -> None:
        self._title()
        prefix = "DRY RUN — " if dry_run else ""
        self.frame.step(f"{prefix}Uninstall plan: {project_root}")
        if profile_names:
            self.frame.step(f"Profiles: {', '.join(profile_names)}")
        self._requirement_section("Remove", plan.remove, YELLOW)
        self._requirement_section("Keep", plan.retain, GRAY)
        self._requirement_section("Already absent", plan.absent, GRAY)
        self._requirement_section("Conflict", plan.conflicts, RED)
        if not keep_link and plan.unlink_profiles:
            self.frame.active("Unlink")
            for name in plan.unlink_profiles:
                self._raw_item(name, YELLOW)
            self._out(self.frame.pipe)
        self.frame.end()

    def _retry_command(self, record: MutationRecordLike) -> str:
        if record.action == "uninstall":
            return "npx skills remove " + " ".join(
                shell_arg(skill) for skill in record.skills
            )
        skills = " ".join(
            f"--skill {shell_arg(skill)}" for skill in record.skills
        )
        source = shell_arg(redact_source(str(record.source)))
        return f"npx skills add {source} {skills}"

    def execution_summary(
        self,
        result: ExecutionResultLike,
        *,
        operation: str = "install",
    ) -> None:
        self._title()
        label = (
            "Uninstall"
            if operation == "uninstall"
            else "Changes" if operation == "changes" else "Install"
        )
        outcome = "complete" if result.ok else "incomplete"
        self.frame.step(f"{label} {outcome}")
        self.frame.step(
            f"{len(result.succeeded)} succeeded; {len(result.failed)} failed"
        )
        if result.succeeded:
            self.frame.active("Succeeded")
            rows = tuple(
                (name, record)
                for record in result.succeeded
                for name in record.skills
            )

            def render_success(row: tuple[str, MutationRecordLike]) -> None:
                name, record = row
                self._skill_item(name, suffix=f"— {record.action}")

            self._skill_list(rows, render_success)
            self._out(self.frame.pipe)
        if result.failed:
            self.frame.active("Failed")
            rows = tuple(
                (name, record)
                for record in result.failed
                for name in record.skills
            )

            def render_failure(row: tuple[str, MutationRecordLike]) -> None:
                name, record = row
                self._skill_item(
                    name,
                    suffix=(
                        f"— {record.action} failed (status {record.status})"
                    ),
                    marker_color=RED,
                    suffix_color=RED,
                )

            self._skill_list(rows, render_failure)
            self._out(self.frame.pipe)
        retry_commands = tuple(
            dict.fromkeys(self._retry_command(record) for record in result.failed)
        )
        if retry_commands:
            self.frame.active("Retry commands")
            for command in retry_commands:
                self._raw_item(command, RED)
        self.frame.end()

    def apply_preview(
        self,
        *,
        install: Sequence[Requirement] = (),
        remove: Sequence[Requirement] = (),
        catalog: Catalog,
        heading: object,
        confirm_state: SelectorState,
    ) -> None:
        self._clear()
        self._title()
        self.frame.step(heading)
        for label, requirements in (("Install", install), ("Remove", remove)):
            if not requirements:
                continue
            self.frame.active(label)
            self._out(self.frame.pipe)
            groups = group_requirements_by_catalog_source(
                requirements, catalog
            )
            for group in groups:
                self._out(
                    f"{self.frame.pipe}  {group.source_index}  "
                    f"{_fg(GRAY, group.label)}"
                )
                for skill in group.skills:
                    self._skill_item(skill, indent="      ")
                self._out(self.frame.pipe)
        hint = _fg(WHITE, "(enter to continue, q to quit)")
        self.frame.active(f"Select an item {hint}")
        self._out(self.frame.pipe)
        for index, entry in enumerate(confirm_state.items):
            selected = index == confirm_state.cursor
            box = "■" if selected else "□"
            box_color = BRIGHT_GREEN if selected else GRAY
            label_color = WHITE if selected else GRAY
            self._out(
                f"{self.frame.pipe}  {box_color}{box}{RESET} "
                f"{_fg(label_color, entry.label)}"
            )
        self.frame.end()

    def confirm(self, message: object) -> None:
        self.frame.active(message)

    def selector(
        self,
        heading: object,
        state: SelectorState,
        *,
        mode: str | None = None,
    ) -> None:
        self._render_selector(heading, state, mode=mode, cancelled=False)

    def cancelled_selector(
        self,
        heading: object,
        state: SelectorState,
        *,
        mode: str | None = None,
    ) -> None:
        self._render_selector(heading, state, mode=mode, cancelled=True)

    def _render_selector(
        self,
        heading: object,
        state: SelectorState,
        *,
        mode: str | None,
        cancelled: bool,
    ) -> None:
        self._clear()
        self._title()
        self.frame.step(heading)
        multiple = mode == "install"
        hint_text = (
            "(space toggle, a all, c clear, enter to continue, q to quit)"
            if multiple
            else "(enter to continue, q to quit)"
        )
        noun = "items" if multiple else "an item"
        self.frame.active(f"Select {noun} {_fg(WHITE, hint_text)}")
        self._out(self.frame.pipe)
        previous_skill = False
        for index, entry in enumerate(state.items):
            skill_entry = getattr(entry, "kind", None) == "skill"
            if index and skill_entry and previous_skill:
                self._out(self.frame.pipe)
            selected = index in state.selected if multiple else index == state.cursor
            box = "■" if selected else "□"
            box_color = BRIGHT_GREEN if selected else GRAY
            is_cursor = index == state.cursor
            label_color = (
                selector_name_color(
                    is_cursor=is_cursor, is_selected=selected
                )
                if skill_entry
                else WHITE if is_cursor else GRAY
            )
            hint = getattr(entry, "hint", "")
            hint_suffix = (
                f" {SELECTOR_DESCRIPTION_COLOR}{hint}{RESET}" if hint else ""
            )
            self._out(
                f"{self.frame.pipe}  {box_color}{box}{RESET} "
                f"{_fg(label_color, entry.label)}{hint_suffix}"
            )
            previous_skill = skill_entry
        self._selector_end(cancelled)

    def catalog_selector(
        self, heading: object, state: SelectorState
    ) -> None:
        self._render_catalog_selector(heading, state, cancelled=False)

    def cancelled_catalog_selector(
        self, heading: object, state: SelectorState
    ) -> None:
        self._render_catalog_selector(heading, state, cancelled=True)

    def _render_catalog_selector(
        self,
        heading: object,
        state: SelectorState,
        *,
        cancelled: bool,
    ) -> None:
        self._clear()
        self._title()
        self.frame.step(heading)
        hint = _fg(
            WHITE,
            "(space toggle, a all, c clear, enter to continue, q to quit)",
        )
        self.frame.active(f"Select items {hint}")
        self._out(self.frame.pipe)
        for index, entry in enumerate(state.items):
            kind = getattr(entry, "kind", None)
            if index and kind == "source":
                self._out(self.frame.pipe)
            if kind == "source":
                selected = self._catalog_source_selected(state, entry)
            else:
                selected = index in state.selected
            box = "■" if selected else "□"
            box_color = BRIGHT_GREEN if selected else GRAY
            label_color = selector_name_color(
                is_cursor=index == state.cursor,
                is_selected=selected,
            )
            hint_text = getattr(entry, "hint", "")
            hint_suffix = (
                f" {SELECTOR_DESCRIPTION_COLOR}{hint_text}{RESET}"
                if hint_text
                else ""
            )
            if kind == "source":
                number = getattr(entry, "source_index", index + 1)
                self._out(
                    f"{self.frame.pipe}  {number}  {box_color}{box}{RESET}  "
                    f"{_fg(label_color, entry.label)}"
                )
            else:
                self._out(
                    f"{self.frame.pipe}      {box_color}{box}{RESET} "
                    f"{_fg(label_color, entry.label)}{hint_suffix}"
                )
        self._selector_end(cancelled)

    @staticmethod
    def _catalog_source_selected(state: SelectorState, source: object) -> bool:
        child_values = frozenset(getattr(source, "child_values", ()))
        if not child_values:
            return False
        child_indexes = tuple(
            index
            for index, item in enumerate(state.items)
            if getattr(item, "kind", None) == "skill"
            and item.value in child_values
        )
        return bool(child_indexes) and all(
            index in state.selected for index in child_indexes
        )

    def _selector_end(self, cancelled: bool) -> None:
        if cancelled:
            self._out(
                f"{_fg(CYAN, '└')}  {_fg(RED, 'Selection cancelled')}"
            )
        else:
            self.frame.end()

    def error(self, message: object) -> None:
        self.frame.error(message)

    def warn(self, message: object) -> None:
        self.stderr.write(f"{_fg(YELLOW, f'⚠️  {message}')}\n")
        self.stderr.flush()

    def info(self, message: object) -> None:
        self.frame.line(message)
