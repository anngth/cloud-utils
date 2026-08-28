from __future__ import annotations

import io
from dataclasses import dataclass

import pytest

from shared.selector import SelectorItem, SelectorState
from skills_manager.config import Catalog, CatalogSource
from skills_manager.planner import (
    DesiredConflict,
    InstallPlan,
    Requirement,
    StatusResult,
    UninstallPlan,
    requirement_key,
)
from skills_manager.state import InstalledSkill
from skills_manager.ui import (
    SELECTOR_DESCRIPTION_COLOR,
    SkmUi,
    group_requirements_by_catalog_source,
    selector_name_color,
    shell_arg,
)
from skills_manager.upstream import AvailableSkill

CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
BRIGHT_GREEN = "\x1b[92m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
GRAY = "\x1b[90m"
WHITE = "\x1b[97m"
BLACK = "\x1b[30m"
BG_GREEN = "\x1b[42m"
FG_RESET = "\x1b[39m"
BG_RESET = "\x1b[49m"
RESET = "\x1b[0m"
DIM = "\x1b[2m"
PIPE = f"{CYAN}│{FG_RESET}"
CORNER = f"{CYAN}└{FG_RESET}"
HEADER = (
    "\n"
    f"   {BG_GREEN}{BLACK} SKILLS MANAGER {FG_RESET}{BG_RESET}\n"
    f"{PIPE}\n"
)


class FlushRecordingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


@dataclass(frozen=True, slots=True)
class RichSelectorItem(SelectorItem):
    kind: str = "choice"
    source_index: int = 0
    child_values: tuple[str, ...] = ()
    hint: str = ""


@dataclass(frozen=True, slots=True)
class MutationRecord:
    action: str
    source: str | None
    skills: tuple[str, ...]
    status: int


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    ok: bool
    succeeded: tuple[MutationRecord, ...]
    failed: tuple[MutationRecord, ...]


def _requirement(source: str, skill: str) -> Requirement:
    return Requirement(requirement_key(source, skill), source, skill)


def _actual(name: str, source: str | None) -> InstalledSkill:
    return InstalledSkill(
        name=name,
        path=f"/repo/.agents/skills/{name}",
        agents=("Codex",),
        source=source,
        provenance="tracked" if source else "untracked",
    )


def _ui() -> tuple[FlushRecordingStream, FlushRecordingStream, SkmUi]:
    stdout = FlushRecordingStream()
    stderr = FlushRecordingStream()
    return stdout, stderr, SkmUi(stdout, stderr)


def test_usage_keeps_green_header_and_exact_command_surface() -> None:
    stdout, stderr, ui = _ui()

    ui.usage()

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Usage: skm [command]\n{PIPE}\n"
        + f"{PIPE}  {GREEN}skm{FG_RESET}  "
        + f"{GRAY}Open interactive catalog selector{FG_RESET}\n"
        + f"{PIPE}  {GREEN}skm (help | -h | --help){FG_RESET}  "
        + f"{GRAY}Show this help{FG_RESET}\n"
        + f"{PIPE}\n{CYAN}◆{FG_RESET}  Lifecycle\n"
        + f"{PIPE}  {GREEN}skm add <source|index...> [(-a | --all)]"
        + f"{FG_RESET}\n"
        + f"{PIPE}      {GREEN}[(-y | --yes)] [(-d | --dry-run)]"
        + f"{FG_RESET}  {GRAY}Install catalog skills for selected sources"
        + f"{FG_RESET}\n"
        + f"{PIPE}  {GREEN}skm remove <source|index...> [(-a | --all)]"
        + f"{FG_RESET}\n"
        + f"{PIPE}      {GREEN}[(-y | --yes)] [(-d | --dry-run)]"
        + f"{FG_RESET}  {GRAY}Uninstall catalog skills for selected sources"
        + f"{FG_RESET}\n"
        + f"{PIPE}  {GREEN}skm status{FG_RESET}  "
        + f"{GRAY}Compare catalog and installed skills{FG_RESET}\n"
        + f"{PIPE}\n{CYAN}◆{FG_RESET}  Catalog\n"
        + f"{PIPE}  {GREEN}skm source add <source>{FG_RESET}\n"
        + f"{PIPE}      {GREEN}[[(-k | --skill) <skill>]... | "
        + f"(-a | --all) | (-n | --no-skills)] [(-y | --yes)]{FG_RESET}  "
        + f"{GRAY}Add a catalog source{FG_RESET}\n"
        + f"{PIPE}  {GREEN}skm source edit <source|index>{FG_RESET}\n"
        + f"{PIPE}      {GREEN}[[(-k | --skill) <skill>]... | "
        + f"(-a | --all) | (-n | --no-skills)] [(-y | --yes)]{FG_RESET}  "
        + f"{GRAY}Update skills for a catalog source{FG_RESET}\n"
        + f"{PIPE}  {GREEN}skm source remove <source|index>{FG_RESET}  "
        + f"{GRAY}Remove a catalog source{FG_RESET}\n"
        + f"{PIPE}\n{CYAN}◆{FG_RESET}  Notes\n"
        + f"{PIPE}  {GRAY}Source indexes are 1-based, matching the interactive "
        + f"selector and gt backup.{FG_RESET}\n"
        + f"{PIPE}  {GRAY}source add, source edit, and source remove change the "
        + f"catalog only; use add/remove to change disk.{FG_RESET}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""
    assert stdout.flush_count > 0


def test_errors_warnings_and_info_use_exact_streams_and_flush() -> None:
    stdout, stderr, ui = _ui()

    ui.error("broken")
    ui.warn("careful")
    ui.info("ok")

    assert stdout.getvalue() == "ok\n"
    assert stderr.getvalue() == (
        f"{RED}❌ broken{FG_RESET}\n"
        f"{YELLOW}⚠️  careful{FG_RESET}\n"
    )
    assert stdout.flush_count == 1
    assert stderr.flush_count == 2


@pytest.mark.parametrize(
    ("skills", "expected_count"),
    [((), "0 selected skills"), (("one",), "1 selected skill")],
)
def test_source_changed_renders_exact_empty_and_singular_counts(
    skills: tuple[str, ...], expected_count: str
) -> None:
    stdout, stderr, ui = _ui()

    ui.source_changed("shown", None, "owner/catalog", skills)

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Source shown: owner/catalog\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  {expected_count}\n{PIPE}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_source_changed_renders_available_skills_with_exact_spacing() -> None:
    stdout, stderr, ui = _ui()

    ui.source_changed(
        "added",
        "default",
        "obra/superpowers",
        ("brainstorming", "testing"),
        available=(
            AvailableSkill("brainstorming", "Explore requirements"),
            AvailableSkill("testing", ""),
        ),
    )

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Source added: obra/superpowers\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  Profile: default\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  2 selected skills\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Available skills\n"
        + f"{PIPE}  {GREEN}■{FG_RESET} {BRIGHT_GREEN}brainstorming"
        + f"{FG_RESET} {GRAY}— Explore requirements{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {GREEN}■{FG_RESET} {BRIGHT_GREEN}testing{FG_RESET}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""


@pytest.mark.parametrize(
    ("source", "display"),
    [
        (
            "https://user:secret@git.example.com/acme/skills?token=x#frag",
            "https://git.example.com/acme/skills",
        ),
        (
            "git@github.com:owner/repo@ACCESS_TOKEN=secret",
            "[unsafe source redacted]",
        ),
        ("git@gitlab.com:owner/repo.git", "git@gitlab.com:owner/repo.git"),
    ],
)
def test_source_changed_redacts_source_text_exactly(
    source: str, display: str
) -> None:
    stdout, stderr, ui = _ui()

    ui.source_changed("shown", None, source, ())

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Source shown: {display}\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  0 selected skills\n{PIPE}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_status_renders_tree_and_every_overflow_classification_exactly() -> None:
    stdout, stderr, ui = _ui()
    catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(
                source="a/repo",
                skills=("ready", "missing", "mismatch", "unknown"),
            ),
            CatalogSource(source="empty/repo", skills=()),
            CatalogSource(source="b/repo", skills=("missing-b",)),
        ),
    )
    result = StatusResult(
        installed=(_requirement("a/repo", "ready"),),
        missing=(
            _requirement("a/repo", "missing"),
            _requirement("b/repo", "missing-b"),
        ),
        mismatches=(_requirement("a/repo", "mismatch"),),
        untracked=(_requirement("a/repo", "unknown"),),
        extras=(_actual("other", "x/repo"), _actual("orphan", None)),
        desired_conflicts=(
            DesiredConflict(
                "ambiguous",
                ("a/repo", "b/repo"),
                ("frontend", "quality"),
            ),
        ),
    )

    ui.status(
        project_root="/repo/app",
        profile_names=("frontend", "quality"),
        catalog=catalog,
        status=result,
    )

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Status: /repo/app\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  Profiles: frontend, quality\n{PIPE}\n"
        + f"{PIPE}  1  {GRAY}a/repo{FG_RESET}\n"
        + f"{PIPE}      {GREEN}■{FG_RESET} {BRIGHT_GREEN}ready{FG_RESET}\n"
        + f"{PIPE}      {GRAY}□{FG_RESET} {BRIGHT_GREEN}missing{FG_RESET}\n"
        + f"{PIPE}      {RED}▲{FG_RESET} {BRIGHT_GREEN}mismatch{FG_RESET}\n"
        + f"{PIPE}      {RED}▲{FG_RESET} {BRIGHT_GREEN}unknown{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  3  {GRAY}b/repo{FG_RESET}\n"
        + f"{PIPE}      {GRAY}□{FG_RESET} {BRIGHT_GREEN}missing-b{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Source mismatch\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}mismatch{FG_RESET} "
        + f"{RED}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Untracked\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}unknown{FG_RESET} "
        + f"{RED}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Extra\n"
        + f"{PIPE}  {YELLOW}■{FG_RESET} {BRIGHT_GREEN}other{FG_RESET} "
        + f"{YELLOW}— x/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {YELLOW}■{FG_RESET} {BRIGHT_GREEN}orphan{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Desired-source conflict\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}ambiguous{FG_RESET} "
        + f"{RED}— a/repo vs b/repo — required by frontend, quality"
        + f"{FG_RESET}\n"
        + f"{PIPE}\n{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_status_omits_every_empty_section_exactly() -> None:
    stdout, stderr, ui = _ui()
    catalog = Catalog(
        version=1,
        sources=(CatalogSource(source="a/repo", skills=("ready",)),),
    )
    result = StatusResult(
        installed=(_requirement("a/repo", "ready"),),
        missing=(),
        mismatches=(),
        untracked=(),
        extras=(),
        desired_conflicts=(),
    )

    ui.status(
        project_root="/repo/app",
        profile_names=(),
        catalog=catalog,
        status=result,
    )

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Status: /repo/app\n{PIPE}\n"
        + f"{PIPE}  1  {GRAY}a/repo{FG_RESET}\n"
        + f"{PIPE}      {GREEN}■{FG_RESET} {BRIGHT_GREEN}ready{FG_RESET}\n"
        + f"{PIPE}\n{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_install_plan_renders_dry_run_and_all_sections_exactly() -> None:
    stdout, stderr, ui = _ui()
    plan = InstallPlan(
        install=(
            _requirement("a/repo", "missing"),
            _requirement("b/repo", "also-missing"),
        ),
        skip=(_requirement("a/repo", "ready"),),
        conflicts=(_requirement("a/repo", "blocked"),),
        extras=(_actual("other", "x/repo"),),
        desired_conflicts=(
            DesiredConflict("ambiguous", ("a/repo", "b/repo")),
        ),
    )

    ui.install_plan(
        project_root="/repo/app",
        profile_names=("frontend",),
        plan=plan,
        dry_run=True,
    )

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  DRY RUN — Install plan: /repo/app\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  Profiles: frontend\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Install\n"
        + f"{PIPE}  {GREEN}■{FG_RESET} {BRIGHT_GREEN}missing{FG_RESET} "
        + f"{GREEN}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {GREEN}■{FG_RESET} {BRIGHT_GREEN}also-missing{FG_RESET} "
        + f"{GREEN}— b/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Already installed\n"
        + f"{PIPE}  {GRAY}■{FG_RESET} {BRIGHT_GREEN}ready{FG_RESET} "
        + f"{GRAY}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Conflict\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}blocked{FG_RESET} "
        + f"{RED}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Extra\n"
        + f"{PIPE}  {GRAY}■{FG_RESET} {BRIGHT_GREEN}other{FG_RESET} "
        + f"{GRAY}— x/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Desired-source conflict\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}ambiguous{FG_RESET} "
        + f"{RED}— a/repo vs b/repo{FG_RESET}\n"
        + f"{PIPE}\n{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_install_plan_omits_empty_sections_exactly() -> None:
    stdout, stderr, ui = _ui()
    plan = InstallPlan(
        install=(_requirement("a/repo", "missing"),),
        skip=(),
        conflicts=(),
        extras=(),
        desired_conflicts=(),
    )

    ui.install_plan(
        project_root="/repo/app",
        profile_names=(),
        plan=plan,
    )

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Install plan: /repo/app\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Install\n"
        + f"{PIPE}  {GREEN}■{FG_RESET} {BRIGHT_GREEN}missing{FG_RESET} "
        + f"{GREEN}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_uninstall_plan_renders_every_branch_exactly() -> None:
    stdout, stderr, ui = _ui()
    plan = UninstallPlan(
        remove=(
            _requirement("a/repo", "remove-me"),
            _requirement("b/repo", "also-remove"),
        ),
        retain=(_requirement("a/repo", "shared"),),
        absent=(_requirement("a/repo", "gone"),),
        conflicts=(_requirement("a/repo", "blocked"),),
        unlink_profiles=("frontend", "quality"),
        desired_conflicts=(),
    )

    ui.uninstall_plan(
        project_root="/repo/app",
        profile_names=("frontend",),
        plan=plan,
        dry_run=True,
        keep_link=False,
    )

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  DRY RUN — Uninstall plan: /repo/app\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  Profiles: frontend\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Remove\n"
        + f"{PIPE}  {YELLOW}■{FG_RESET} {BRIGHT_GREEN}remove-me{FG_RESET} "
        + f"{YELLOW}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {YELLOW}■{FG_RESET} {BRIGHT_GREEN}also-remove{FG_RESET} "
        + f"{YELLOW}— b/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Keep\n"
        + f"{PIPE}  {GRAY}■{FG_RESET} {BRIGHT_GREEN}shared{FG_RESET} "
        + f"{GRAY}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Already absent\n"
        + f"{PIPE}  {GRAY}■{FG_RESET} {BRIGHT_GREEN}gone{FG_RESET} "
        + f"{GRAY}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Conflict\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}blocked{FG_RESET} "
        + f"{RED}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Unlink\n"
        + f"{PIPE}  {YELLOW}■{FG_RESET} frontend\n"
        + f"{PIPE}  {YELLOW}■{FG_RESET} quality\n"
        + f"{PIPE}\n{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_uninstall_plan_keep_link_omits_unlink_exactly() -> None:
    stdout, stderr, ui = _ui()
    plan = UninstallPlan(
        remove=(_requirement("a/repo", "remove-me"),),
        retain=(),
        absent=(),
        conflicts=(),
        unlink_profiles=("frontend",),
        desired_conflicts=(),
    )

    ui.uninstall_plan(
        project_root="/repo/app",
        profile_names=(),
        plan=plan,
        keep_link=True,
    )

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Uninstall plan: /repo/app\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Remove\n"
        + f"{PIPE}  {YELLOW}■{FG_RESET} {BRIGHT_GREEN}remove-me{FG_RESET} "
        + f"{YELLOW}— a/repo{FG_RESET}\n"
        + f"{PIPE}\n{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_group_requirements_uses_catalog_indexes_and_does_not_mutate() -> None:
    catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="a/one", skills=("a",)),
            CatalogSource(source="b/two", skills=("b",)),
            CatalogSource(source="c/three", skills=("c",)),
        ),
    )
    requirements = (
        _requirement("c/three", "c"),
        _requirement("a/one", "a"),
        _requirement("outside/catalog", "ignored"),
    )
    before = tuple(requirements)

    groups = group_requirements_by_catalog_source(requirements, catalog)

    assert [
        (group.source_index, group.source, group.label, group.skills)
        for group in groups
    ] == [
        (1, "a/one", "a/one", ("a",)),
        (3, "c/three", "c/three", ("c",)),
    ]
    assert requirements == before


def test_apply_preview_groups_by_catalog_index_and_renders_prompt_exactly() -> None:
    stdout, stderr, ui = _ui()
    catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="a/one", skills=("skill-a", "skill-b")),
            CatalogSource(source="b/two", skills=("skill-c",)),
        ),
    )
    state = SelectorState(
        items=(SelectorItem("Yes", "yes"), SelectorItem("No", "no")),
        cursor=0,
        selected=frozenset(),
    )

    ui.apply_preview(
        install=(_requirement("b/two", "skill-c"),),
        remove=(_requirement("a/one", "skill-b"),),
        catalog=catalog,
        heading="Apply these changes?",
        confirm_state=state,
    )

    assert stdout.getvalue() == (
        "\x1b[2J\x1b[H"
        + HEADER
        + f"{GREEN}◇{FG_RESET}  Apply these changes?\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Install\n{PIPE}\n"
        + f"{PIPE}  2  {GRAY}b/two{FG_RESET}\n"
        + f"{PIPE}      {GREEN}■{FG_RESET} {BRIGHT_GREEN}skill-c{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Remove\n{PIPE}\n"
        + f"{PIPE}  1  {GRAY}a/one{FG_RESET}\n"
        + f"{PIPE}      {GREEN}■{FG_RESET} {BRIGHT_GREEN}skill-b{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Select an item "
        + f"{WHITE}(enter to continue, q to quit){FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {BRIGHT_GREEN}■{RESET} {WHITE}Yes{FG_RESET}\n"
        + f"{PIPE}  {GRAY}□{RESET} {GRAY}No{FG_RESET}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_execution_summary_renders_install_failures_and_deduplicated_retry() -> None:
    stdout, stderr, ui = _ui()
    unsafe = (
        "https://user:secret@git.example.com/acme/skills"
        "?token=query-secret#fragment-secret"
    )
    failed = MutationRecord(
        "install", unsafe, ("code review", "quote's"), 7
    )
    result = ExecutionResult(
        ok=False,
        succeeded=(MutationRecord("install", "b/repo", ("three",), 0),),
        failed=(failed, failed),
    )

    ui.execution_summary(result, operation="install")

    retry = (
        "npx skills add https://git.example.com/acme/skills "
        "--skill 'code review' --skill 'quote'\\''s'"
    )
    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Install incomplete\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  1 succeeded; 2 failed\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Succeeded\n"
        + f"{PIPE}  {GREEN}■{FG_RESET} {BRIGHT_GREEN}three{FG_RESET} "
        + f"{GRAY}— install{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Failed\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}code review{FG_RESET} "
        + f"{RED}— install failed (status 7){FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}quote's{FG_RESET} "
        + f"{RED}— install failed (status 7){FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}code review{FG_RESET} "
        + f"{RED}— install failed (status 7){FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}quote's{FG_RESET} "
        + f"{RED}— install failed (status 7){FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Retry commands\n"
        + f"{PIPE}  {RED}■{FG_RESET} {retry}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""
    assert stdout.getvalue().count(retry) == 1
    assert "secret" not in stdout.getvalue().lower()


@pytest.mark.parametrize(
    ("operation", "label", "action", "retry"),
    [
        (
            "uninstall",
            "Uninstall",
            "uninstall",
            "npx skills remove one two",
        ),
        (
            "changes",
            "Changes",
            "install",
            "npx skills add a/repo --skill one --skill two",
        ),
    ],
)
def test_execution_summary_renders_uninstall_and_combined_branches_exactly(
    operation: str, label: str, action: str, retry: str
) -> None:
    stdout, stderr, ui = _ui()
    source = None if action == "uninstall" else "a/repo"
    result = ExecutionResult(
        ok=False,
        succeeded=(),
        failed=(MutationRecord(action, source, ("one", "two"), 4),),
    )

    ui.execution_summary(result, operation=operation)

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  {label} incomplete\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  0 succeeded; 1 failed\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Failed\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}one{FG_RESET} "
        + f"{RED}— {action} failed (status 4){FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {RED}■{FG_RESET} {BRIGHT_GREEN}two{FG_RESET} "
        + f"{RED}— {action} failed (status 4){FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Retry commands\n"
        + f"{PIPE}  {RED}■{FG_RESET} {retry}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_execution_summary_renders_combined_success_exactly() -> None:
    stdout, stderr, ui = _ui()
    result = ExecutionResult(
        ok=True,
        succeeded=(
            MutationRecord("install", "a/repo", ("one",), 0),
            MutationRecord("uninstall", None, ("two",), 0),
        ),
        failed=(),
    )

    ui.execution_summary(result, operation="changes")

    assert stdout.getvalue() == (
        HEADER
        + f"{GREEN}◇{FG_RESET}  Changes complete\n{PIPE}\n"
        + f"{GREEN}◇{FG_RESET}  2 succeeded; 0 failed\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Succeeded\n"
        + f"{PIPE}  {GREEN}■{FG_RESET} {BRIGHT_GREEN}one{FG_RESET} "
        + f"{GRAY}— install{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {GREEN}■{FG_RESET} {BRIGHT_GREEN}two{FG_RESET} "
        + f"{GRAY}— uninstall{FG_RESET}\n"
        + f"{PIPE}\n{CORNER}\n"
    )
    assert stderr.getvalue() == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("safe/repo:@%+=,-", "safe/repo:@%+=,-"),
        ("", "''"),
        ("code review", "'code review'"),
        ("quote's", "'quote'\\''s'"),
        ("$(touch /tmp/skm-ui-must-not-run)", "'$(touch /tmp/skm-ui-must-not-run)'"),
    ],
)
def test_shell_arg_is_only_a_single_quote_display_encoder(
    value: str, expected: str
) -> None:
    assert shell_arg(value) == expected


def test_confirm_renders_only_the_injected_prompt_and_flushes() -> None:
    stdout, stderr, ui = _ui()

    ui.confirm("Apply this plan?")

    assert stdout.getvalue() == f"{CYAN}◆{FG_RESET}  Apply this plan?\n"
    assert stderr.getvalue() == ""
    assert stdout.flush_count == 1


@pytest.mark.parametrize(
    ("is_cursor", "is_selected", "expected"),
    [
        (True, True, WHITE),
        (True, False, WHITE),
        (False, True, GREEN),
        (False, False, GRAY),
    ],
)
def test_selector_name_color_uses_cursor_selected_unselected_ladder(
    is_cursor: bool, is_selected: bool, expected: str
) -> None:
    assert selector_name_color(
        is_cursor=is_cursor, is_selected=is_selected
    ) == expected
    assert SELECTOR_DESCRIPTION_COLOR == f"{DIM}{GRAY}"


def test_generic_multi_selector_renders_exact_colors_spacing_and_hints() -> None:
    stdout, stderr, ui = _ui()
    state = SelectorState(
        items=(
            RichSelectorItem("alpha", "a", kind="skill", hint="first"),
            RichSelectorItem("beta", "b", kind="skill", hint="second"),
        ),
        cursor=0,
        selected=frozenset({1}),
    )

    ui.selector("Select skills from demo", state, mode="install")

    assert stdout.getvalue() == (
        "\x1b[2J\x1b[H"
        + HEADER
        + f"{GREEN}◇{FG_RESET}  Select skills from demo\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Select items "
        + f"{WHITE}(space toggle, a all, c clear, enter to continue, q to quit)"
        + f"{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {GRAY}□{RESET} {WHITE}alpha{FG_RESET} "
        + f"{DIM}{GRAY}first{RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {BRIGHT_GREEN}■{RESET} {GREEN}beta{FG_RESET} "
        + f"{DIM}{GRAY}second{RESET}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_generic_single_selector_and_cancelled_footer_are_exact() -> None:
    stdout, stderr, ui = _ui()
    state = SelectorState(
        items=(SelectorItem("Frontend", "frontend"), SelectorItem("Review", "review")),
        cursor=1,
        selected=frozenset({0}),
    )

    ui.cancelled_selector("Choose profile", state, mode="single")

    assert stdout.getvalue() == (
        "\x1b[2J\x1b[H"
        + HEADER
        + f"{GREEN}◇{FG_RESET}  Choose profile\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Select an item "
        + f"{WHITE}(enter to continue, q to quit){FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  {GRAY}□{RESET} {GRAY}Frontend{FG_RESET}\n"
        + f"{PIPE}  {BRIGHT_GREEN}■{RESET} {WHITE}Review{FG_RESET}\n"
        + f"{CORNER}  {RED}Selection cancelled{FG_RESET}\n"
    )
    assert stderr.getvalue() == ""


def test_catalog_selector_renders_hierarchy_partial_parent_and_color_ladder() -> None:
    stdout, stderr, ui = _ui()
    one = requirement_key("a/repo", "one")
    two = requirement_key("a/repo", "two")
    three = requirement_key("b/repo", "three")
    state = SelectorState(
        items=(
            RichSelectorItem(
                "a/repo",
                "a/repo",
                kind="source",
                source_index=1,
                child_values=(one, two),
            ),
            RichSelectorItem("one", one, kind="skill", source_index=1),
            RichSelectorItem(
                "two",
                two,
                kind="skill",
                source_index=1,
                hint="second skill",
            ),
            RichSelectorItem(
                "b/repo",
                "b/repo",
                kind="source",
                source_index=2,
                child_values=(three,),
            ),
            RichSelectorItem("three", three, kind="skill", source_index=2),
        ),
        cursor=1,
        selected=frozenset({1, 4}),
    )

    ui.catalog_selector("Choose skills", state)

    assert stdout.getvalue() == (
        "\x1b[2J\x1b[H"
        + HEADER
        + f"{GREEN}◇{FG_RESET}  Choose skills\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Select items "
        + f"{WHITE}(space toggle, a all, c clear, enter to continue, q to quit)"
        + f"{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  1  {GRAY}□{RESET}  {GRAY}a/repo{FG_RESET}\n"
        + f"{PIPE}      {BRIGHT_GREEN}■{RESET} {WHITE}one{FG_RESET}\n"
        + f"{PIPE}      {GRAY}□{RESET} {GRAY}two{FG_RESET} "
        + f"{DIM}{GRAY}second skill{RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  2  {BRIGHT_GREEN}■{RESET}  {GREEN}b/repo{FG_RESET}\n"
        + f"{PIPE}      {BRIGHT_GREEN}■{RESET} {GREEN}three{FG_RESET}\n"
        + f"{CORNER}\n"
    )
    assert stderr.getvalue() == ""


def test_cancelled_catalog_selector_renders_exact_footer() -> None:
    stdout, stderr, ui = _ui()
    child = requirement_key("a/repo", "one")
    state = SelectorState(
        items=(
            RichSelectorItem(
                "a/repo",
                "a/repo",
                kind="source",
                source_index=1,
                child_values=(child,),
            ),
            RichSelectorItem("one", child, kind="skill", source_index=1),
        ),
        cursor=0,
        selected=frozenset({1}),
    )

    ui.cancelled_catalog_selector("Choose skills", state)

    assert stdout.getvalue() == (
        "\x1b[2J\x1b[H"
        + HEADER
        + f"{GREEN}◇{FG_RESET}  Choose skills\n{PIPE}\n"
        + f"{CYAN}◆{FG_RESET}  Select items "
        + f"{WHITE}(space toggle, a all, c clear, enter to continue, q to quit)"
        + f"{FG_RESET}\n"
        + f"{PIPE}\n"
        + f"{PIPE}  1  {BRIGHT_GREEN}■{RESET}  {WHITE}a/repo{FG_RESET}\n"
        + f"{PIPE}      {BRIGHT_GREEN}■{RESET} {GREEN}one{FG_RESET}\n"
        + f"{CORNER}  {RED}Selection cancelled{FG_RESET}\n"
    )
    assert stderr.getvalue() == ""
