from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from skills_manager.config import Catalog, CatalogSource
from skills_manager.planner import (
    DesiredConflict,
    InstallPlan,
    MergedRequirements,
    Requirement,
    StatusResult,
    UninstallPlan,
    catalog_requirements,
    classify_status,
    create_install_plan,
    create_uninstall_plan,
    requirement_key,
)
from skills_manager.state import InstalledSkill


def _catalog(*sources: tuple[str, tuple[str, ...]]) -> Catalog:
    return Catalog(
        version=1,
        sources=tuple(
            CatalogSource(source=source, skills=skills)
            for source, skills in sources
        ),
    )


def _actual(
    name: str,
    source: str | None,
    *,
    provenance: str | None = None,
) -> InstalledSkill:
    return InstalledSkill(
        name=name,
        path=f"/repo/.agents/skills/{name}",
        agents=("Codex",),
        source=source,
        provenance=(
            "tracked" if source is not None else "untracked"
        )
        if provenance is None
        else provenance,
    )


def test_requirement_key_matches_compact_json_array_identity() -> None:
    assert requirement_key('owner/"repo"', "skill\\name") == (
        '["owner/\\\"repo\\\"","skill\\\\name"]'
    )


def test_plan_values_are_frozen_and_ordered_collections_are_tuples() -> None:
    requirement = Requirement('["a/repo","review"]', "a/repo", "review")
    conflict = DesiredConflict("review", ("a/repo", "b/repo"))
    merged = MergedRequirements((requirement,), (conflict,))
    status = StatusResult(
        (requirement,), (), (), (), (_actual("extra", "x/repo"),), (conflict,)
    )
    install = InstallPlan(
        (requirement,), (), (), status.extras, (conflict,)
    )
    uninstall = UninstallPlan(
        (requirement,), (), (), (), ("profile",), (conflict,)
    )

    assert conflict.profiles == ()
    assert all(
        isinstance(value, tuple)
        for value in (
            merged.requirements,
            merged.desired_conflicts,
            status.installed,
            status.missing,
            status.mismatches,
            status.untracked,
            status.extras,
            install.install,
            install.skip,
            install.conflicts,
            uninstall.remove,
            uninstall.retain,
            uninstall.absent,
            uninstall.conflicts,
            uninstall.unlink_profiles,
        )
    )
    with pytest.raises(FrozenInstanceError):
        requirement.skill = "changed"  # type: ignore[misc]


def test_catalog_requirements_unions_deduplicates_and_reports_conflicts() -> None:
    merged = catalog_requirements(
        _catalog(
            ("b/repo", ("testing", "review")),
            ("a/repo", ("review", "review", "code-review")),
        )
    )

    assert merged.requirements == (
        Requirement('["a/repo","code-review"]', "a/repo", "code-review"),
        Requirement('["b/repo","review"]', "b/repo", "review"),
        Requirement('["a/repo","review"]', "a/repo", "review"),
        Requirement('["b/repo","testing"]', "b/repo", "testing"),
    )
    assert merged.desired_conflicts == (
        DesiredConflict("review", ("a/repo", "b/repo")),
    )


def test_catalog_requirements_uses_stable_nfc_code_point_skill_order() -> None:
    merged = catalog_requirements(
        _catalog(
            (
                "owner/repo",
                (
                    "zeta",
                    "e\u0301clair",
                    "apple",
                    "Ångstrom",
                    "!bang",
                    "éclair",
                    "A\u030angstrom",
                    "_under",
                ),
            ),
        )
    )

    assert tuple(item.skill for item in merged.requirements) == (
        "!bang",
        "_under",
        "apple",
        "zeta",
        "Ångstrom",
        "A\u030angstrom",
        "e\u0301clair",
        "éclair",
    )


def test_catalog_requirements_sorts_conflicts_with_the_same_collation_rule() -> None:
    merged = catalog_requirements(
        _catalog(
            ("b/repo", ("éclair", "!bang", "e\u0301clair")),
            ("a/repo", ("e\u0301clair", "éclair", "!bang")),
        )
    )

    assert tuple(item.skill for item in merged.desired_conflicts) == (
        "!bang",
        "éclair",
        "e\u0301clair",
    )
    assert merged.desired_conflicts[1].skill == "éclair"
    assert merged.desired_conflicts[2].skill == "e\u0301clair"


def test_classify_status_keeps_requirement_and_extra_order() -> None:
    merged = MergedRequirements(
        requirements=(
            Requirement(requirement_key("owner/b", "b"), "owner/b", "b"),
            Requirement(requirement_key("owner/a", "a"), "owner/a", "a"),
        ),
        desired_conflicts=(),
    )
    state = {
        "a": _actual("a", "owner/a"),
        "z": _actual("z", None),
    }

    result = classify_status(merged, state)

    assert tuple(item.skill for item in result.installed) == ("a",)
    assert tuple(item.skill for item in result.missing) == ("b",)
    assert tuple(item.name for item in result.extras) == ("z",)


def test_classify_status_covers_mismatch_and_both_untracked_signals() -> None:
    requirements = tuple(
        Requirement(requirement_key("a/repo", skill), "a/repo", skill)
        for skill in ("ok", "missing", "wrong", "no-source", "flagged")
    )
    state = {
        "ok": _actual("ok", "a/repo"),
        "wrong": _actual("wrong", "other/repo"),
        "no-source": _actual("no-source", None),
        "flagged": _actual("flagged", "a/repo", provenance="untracked"),
        "z-extra": _actual("z-extra", "x/repo"),
        "!extra": _actual("!extra", "x/repo"),
    }

    result = classify_status(MergedRequirements(requirements, ()), state)

    assert tuple(item.skill for item in result.installed) == ("ok",)
    assert tuple(item.skill for item in result.missing) == ("missing",)
    assert tuple(item.skill for item in result.mismatches) == ("wrong",)
    assert tuple(item.skill for item in result.untracked) == (
        "no-source",
        "flagged",
    )
    assert tuple(item.name for item in result.extras) == ("!extra", "z-extra")


def test_classify_status_uses_stable_nfc_code_point_extra_order() -> None:
    state = {
        "zeta": _actual("zeta", "x/repo"),
        "e\u0301clair": _actual("e\u0301clair", "x/repo"),
        "apple": _actual("apple", "x/repo"),
        "Ångstrom": _actual("Ångstrom", "x/repo"),
        "!bang": _actual("!bang", "x/repo"),
        "éclair": _actual("éclair", "x/repo"),
        "A\u030angstrom": _actual("A\u030angstrom", "x/repo"),
        "_under": _actual("_under", "x/repo"),
    }

    result = classify_status(MergedRequirements((), ()), state)

    assert tuple(item.name for item in result.extras) == (
        "!bang",
        "_under",
        "apple",
        "zeta",
        "Ångstrom",
        "A\u030angstrom",
        "e\u0301clair",
        "éclair",
    )


def test_status_preserves_conflicts_and_suppresses_their_ambiguous_names() -> None:
    merged = catalog_requirements(
        _catalog(
            ("a/repo", ("review", "testing")),
            ("b/repo", ("review",)),
        )
    )

    result = classify_status(
        merged,
        {
            "review": _actual("review", "a/repo"),
            "testing": _actual("testing", "a/repo"),
        },
    )

    assert tuple(item.skill for item in result.installed) == ("testing",)
    assert result.missing == ()
    assert result.mismatches == ()
    assert result.untracked == ()
    assert result.extras == ()
    assert result.desired_conflicts is merged.desired_conflicts


def test_install_plan_selects_missing_and_blocks_mismatch_and_untracked() -> None:
    requirements = tuple(
        Requirement(requirement_key("a/repo", skill), "a/repo", skill)
        for skill in ("ok", "first", "second", "wrong", "unknown")
    )
    status = classify_status(
        MergedRequirements(requirements, ()),
        {
            "ok": _actual("ok", "a/repo"),
            "wrong": _actual("wrong", "other/repo"),
            "unknown": _actual("unknown", None),
            "extra": _actual("extra", "x/repo"),
        },
    )

    plan = create_install_plan(
        status,
        selected_keys=frozenset((requirement_key("a/repo", "second"),)),
    )

    assert tuple(item.skill for item in plan.install) == ("second",)
    assert tuple(item.skill for item in plan.skip) == ("ok",)
    assert tuple(item.skill for item in plan.conflicts) == ("wrong", "unknown")
    assert tuple(item.name for item in plan.extras) == ("extra",)
    assert status.missing == (requirements[1], requirements[2])


def test_desired_conflicts_do_not_block_independent_safe_install_work() -> None:
    status = classify_status(
        catalog_requirements(
            _catalog(
                ("a/repo", ("review",)),
                ("b/repo", ("review",)),
                ("c/repo", ("testing",)),
            )
        ),
        {},
    )

    plan = create_install_plan(status)

    assert tuple(item.skill for item in plan.install) == ("testing",)
    assert tuple(item.skill for item in plan.desired_conflicts) == ("review",)


def test_uninstall_retains_only_the_same_source_and_skill_key() -> None:
    selected = catalog_requirements(
        _catalog(("a/repo", ("code-review", "frontend-design", "review")))
    )
    remaining = catalog_requirements(
        _catalog(
            ("a/repo", ("code-review",)),
            ("b/repo", ("review",)),
        )
    )

    plan = create_uninstall_plan(
        selected=selected,
        remaining=remaining,
        installed_state={
            "code-review": _actual("code-review", "a/repo"),
            "frontend-design": _actual("frontend-design", "a/repo"),
            "review": _actual("review", "a/repo"),
        },
        linked_selected=("frontend",),
    )

    assert tuple(item.skill for item in plan.remove) == (
        "frontend-design",
        "review",
    )
    assert tuple(item.skill for item in plan.retain) == ("code-review",)
    assert plan.absent == ()
    assert plan.unlink_profiles == ("frontend",)


def test_uninstall_classifies_absent_and_removes_mismatch_and_untracked() -> None:
    selected = MergedRequirements(
        tuple(
            Requirement(requirement_key("a/repo", skill), "a/repo", skill)
            for skill in ("absent", "wrong", "unknown")
        ),
        (),
    )

    plan = create_uninstall_plan(
        selected=selected,
        remaining=MergedRequirements((), ()),
        installed_state={
            "wrong": _actual("wrong", "other/repo"),
            "unknown": _actual("unknown", None),
        },
        linked_selected=("quality",),
    )

    assert tuple(item.skill for item in plan.remove) == ("wrong", "unknown")
    assert plan.retain == ()
    assert tuple(item.skill for item in plan.absent) == ("absent",)
    assert plan.conflicts == ()


def test_desired_conflicts_do_not_block_independent_safe_uninstall_work() -> None:
    selected_catalog = catalog_requirements(
        _catalog(
            ("a/repo", ("review",)),
            ("b/repo", ("review",)),
            ("c/repo", ("safe",)),
        )
    )

    plan = create_uninstall_plan(
        selected=selected_catalog,
        remaining=MergedRequirements((), ()),
        installed_state={
            "review": _actual("review", "a/repo"),
            "safe": _actual("safe", "c/repo"),
        },
        linked_selected=("a", "b"),
    )

    assert tuple(item.skill for item in plan.remove) == ("safe",)
    assert tuple(item.skill for item in plan.desired_conflicts) == ("review",)


def test_uninstall_combines_conflicts_without_reordering_or_duplicates() -> None:
    first = DesiredConflict("zeta", ("a/repo", "b/repo"))
    duplicate = DesiredConflict("zeta", ("a/repo", "b/repo"))
    second = DesiredConflict("!bang", ("c/repo", "d/repo"), ("quality",))

    plan = create_uninstall_plan(
        selected=MergedRequirements((), (first,)),
        remaining=MergedRequirements((), (duplicate, second)),
        installed_state={},
        linked_selected=(),
    )

    assert plan.desired_conflicts == (first, second)
