from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import get_type_hints

import pytest

import skills_manager.planner as planner
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
from skills_manager.state import InstalledSkill, InstalledState


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


def test_planner_runtime_type_hints_and_state_names_remain_available() -> None:
    assert get_type_hints(planner.StatusResult)["extras"] == tuple[
        InstalledSkill, ...
    ]
    assert get_type_hints(planner.InstallPlan)["extras"] == tuple[
        InstalledSkill, ...
    ]
    assert get_type_hints(planner.classify_status)["installed_state"] == (
        InstalledState
    )
    assert get_type_hints(planner.create_uninstall_plan)["installed_state"] == (
        Mapping[str, InstalledSkill]
    )
    assert planner.InstalledSkill is InstalledSkill
    assert planner.InstalledState is InstalledState


@pytest.mark.parametrize(
    "order",
    [
        ("source", "config", "upstream", "state", "planner"),
        ("planner", "state", "upstream", "config", "source"),
        ("state", "planner", "upstream", "source", "config"),
        ("upstream", "planner", "state", "config", "source"),
        ("config", "source", "planner", "upstream", "state"),
    ],
)
def test_skm_domain_modules_import_in_any_dependency_order(
    order: tuple[str, ...],
) -> None:
    imports = "\n".join(
        f"importlib.import_module('skills_manager.{name}')" for name in order
    )
    script = f"""
import importlib
import typing
{imports}
planner = importlib.import_module('skills_manager.planner')
state = importlib.import_module('skills_manager.state')
for target in (
    planner.StatusResult,
    planner.InstallPlan,
    planner.classify_status,
    planner.create_uninstall_plan,
):
    typing.get_type_hints(target)
assert planner.InstalledSkill is state.InstalledSkill
assert planner.InstalledState is state.InstalledState
"""

    subprocess.run((sys.executable, "-c", script), check=True)


@pytest.mark.parametrize(
    ("source", "skill", "expected"),
    [
        ('owner/"repo"', "skill\\name", r'["owner/\"repo\"","skill\\name"]'),
        ("owner/é", "skill/é", '["owner/é","skill/é"]'),
        ("owner/😀", "skill/😀", '["owner/😀","skill/😀"]'),
        ("owner/\u2028", "skill/\u2029", '["owner/\u2028","skill/\u2029"]'),
        (
            'owner/"\\\n',
            "skill/\x00\x01\b\f\n\r\t",
            r'["owner/\"\\\n","skill/\u0000\u0001\b\f\n\r\t"]',
        ),
        ("owner/\ud800", "skill/\udc00", r'["owner/\ud800","skill/\udc00"]'),
        ("owner/\ud83d\ude00", "skill/paired", '["owner/😀","skill/paired"]'),
    ],
)
def test_requirement_key_matches_literal_node_json_identity(
    source: str,
    skill: str,
    expected: str,
) -> None:
    assert requirement_key(source, skill) == expected


def test_requirement_key_matches_literal_json_stringify_matrix() -> None:
    pairs = (
        ("owner/é", "skill/é"),
        ("owner/😀", "skill/😀"),
        ("owner/\u2028", "skill/\u2029"),
        ('owner/"\\\n', "skill/\x00\x01\b\f\n\r\t"),
        ("owner/\ud800", "skill/\udc00"),
        ("owner/\ud83d\ude00", "skill/paired"),
    )
    expected = (
        '["owner/é","skill/é"]',
        '["owner/😀","skill/😀"]',
        '["owner/\u2028","skill/\u2029"]',
        r'["owner/\"\\\n","skill/\u0000\u0001\b\f\n\r\t"]',
        r'["owner/\ud800","skill/\udc00"]',
        '["owner/😀","skill/paired"]',
    )

    assert tuple(requirement_key(*pair) for pair in pairs) == expected


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


def test_js_equivalent_source_spellings_deduplicate_without_false_conflict() -> None:
    paired_source = "\ud83d\ude00/repo"

    merged = catalog_requirements(
        _catalog(
            (paired_source, ("review",)),
            ("😀/repo", ("review",)),
        )
    )

    assert merged.requirements == (
        Requirement('["😀/repo","review"]', paired_source, "review"),
    )
    assert merged.desired_conflicts == ()


def test_js_equivalent_skill_spellings_share_one_first_seen_conflict() -> None:
    paired_skill = "\ud83d\ude00"

    merged = catalog_requirements(
        _catalog(
            ("a/repo", ("😀",)),
            ("b/repo", (paired_skill,)),
        )
    )

    assert merged.requirements == (
        Requirement('["a/repo","😀"]', "a/repo", "😀"),
        Requirement('["b/repo","😀"]', "b/repo", paired_skill),
    )
    assert merged.desired_conflicts == (
        DesiredConflict("😀", ("a/repo", "b/repo")),
    )


def test_catalog_utf16_identity_matches_literal_oracle() -> None:
    paired = "\ud83d\ude00"
    lone = "\ud800"
    same_source = catalog_requirements(
        _catalog(
            (f"{paired}/repo", ("review",)),
            ("😀/repo", ("review",)),
        )
    )
    same_skill = catalog_requirements(
        _catalog(
            ("a/repo", ("😀",)),
            ("b/repo", (paired,)),
        )
    )
    lone_source = catalog_requirements(
        _catalog(
            ("😀/repo", ("review",)),
            (f"{lone}/repo", ("review",)),
        )
    )
    python_summary = {
        "sameSource": {
            "keys": [item.key for item in same_source.requirements],
            "conflicts": len(same_source.desired_conflicts),
        },
        "sameSkill": {
            "keys": [item.key for item in same_skill.requirements],
            "conflicts": [
                {
                    "skill": requirement_key("", item.skill),
                    "sources": list(item.sources),
                }
                for item in same_skill.desired_conflicts
            ],
        },
        "loneSource": {
            "keys": [item.key for item in lone_source.requirements],
            "conflicts": len(lone_source.desired_conflicts),
        },
    }
    assert python_summary == {
        "sameSource": {
            "keys": ['["😀/repo","review"]'],
            "conflicts": 0,
        },
        "sameSkill": {
            "keys": ['["a/repo","😀"]', '["b/repo","😀"]'],
            "conflicts": [
                {
                    "skill": '["","😀"]',
                    "sources": ["a/repo", "b/repo"],
                }
            ],
        },
        "loneSource": {
            "keys": ['["😀/repo","review"]', r'["\ud800/repo","review"]'],
            "conflicts": 1,
        },
    }


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


def test_conflict_sources_use_literal_javascript_array_sort_order() -> None:
    merged = catalog_requirements(
        _catalog(
            ("\ue000/repo", ("review",)),
            ("😀/repo", ("review",)),
            ("a/repo", ("review",)),
            ("\ud7ff/repo", ("review",)),
            ("\ud800/repo", ("review",)),
            ("\udc00/repo", ("review",)),
        )
    )

    assert merged.desired_conflicts == (
        DesiredConflict(
            "review",
            (
                "a/repo",
                "\ud7ff/repo",
                "\ud800/repo",
                "😀/repo",
                "\udc00/repo",
                "\ue000/repo",
            ),
        ),
    )


def test_conflict_source_order_matches_literal_array_sort_oracle() -> None:
    sources = (
        "\ue000/repo",
        "😀/repo",
        "a/repo",
        "\ud7ff/repo",
        "\ud800/repo",
        "\udc00/repo",
    )
    merged = catalog_requirements(
        _catalog(*tuple((source, ("review",)) for source in sources))
    )

    assert merged.desired_conflicts[0].sources == (
        "a/repo",
        "\ud7ff/repo",
        "\ud800/repo",
        "😀/repo",
        "\udc00/repo",
        "\ue000/repo",
    )


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


def test_equivalent_conflict_spellings_suppress_mutation_but_keep_safe_work() -> None:
    paired = "\ud83d\ude00"
    ambiguous_pair = Requirement(
        requirement_key("a/repo", paired), "a/repo", paired
    )
    ambiguous_scalar = Requirement(
        requirement_key("b/repo", "😀"), "b/repo", "😀"
    )
    safe = Requirement(requirement_key("c/repo", "safe"), "c/repo", "safe")
    merged = MergedRequirements(
        (ambiguous_pair, ambiguous_scalar, safe),
        (DesiredConflict(paired, ("a/repo", "b/repo")),),
    )

    status = classify_status(
        merged,
        {"😀": _actual("😀", "a/repo")},
    )
    install = create_install_plan(status)
    uninstall = create_uninstall_plan(
        selected=merged,
        remaining=MergedRequirements((), ()),
        installed_state={
            "😀": _actual("😀", "a/repo"),
            "safe": _actual("safe", "c/repo"),
        },
        linked_selected=(),
    )

    assert status.installed == ()
    assert status.missing == (safe,)
    assert status.mismatches == ()
    assert status.untracked == ()
    assert status.extras == ()
    assert install.install == (safe,)
    assert install.skip == ()
    assert uninstall.remove == (safe,)
    assert uninstall.retain == ()
    assert uninstall.absent == ()


@pytest.mark.parametrize(
    ("requirement_skill", "state_key", "required_source", "actual_source"),
    [
        ("\ud83d\ude00", "😀", "\ud83d\ude00/repo", "😀/repo"),
        ("😀", "\ud83d\ude00", "😀/repo", "\ud83d\ude00/repo"),
    ],
)
def test_equivalent_installed_name_and_source_spellings_are_installed(
    requirement_skill: str,
    state_key: str,
    required_source: str,
    actual_source: str,
) -> None:
    requirement = Requirement(
        requirement_key(required_source, requirement_skill),
        required_source,
        requirement_skill,
    )

    status = classify_status(
        MergedRequirements((requirement,), ()),
        {state_key: _actual(state_key, actual_source)},
    )

    assert status.installed == (requirement,)
    assert status.missing == ()
    assert status.mismatches == ()
    assert status.untracked == ()
    assert status.extras == ()


@pytest.mark.parametrize(
    ("actual_source", "provenance"),
    [(None, None), ("😀/repo", "untracked")],
)
def test_equivalent_name_lookup_keeps_untracked_rules(
    actual_source: str | None,
    provenance: str | None,
) -> None:
    paired = "\ud83d\ude00"
    requirement = Requirement(
        requirement_key(f"{paired}/repo", paired),
        f"{paired}/repo",
        paired,
    )

    status = classify_status(
        MergedRequirements((requirement,), ()),
        {
            "😀": _actual(
                "😀",
                actual_source,
                provenance=provenance,
            )
        },
    )

    assert status.installed == ()
    assert status.missing == ()
    assert status.mismatches == ()
    assert status.untracked == (requirement,)
    assert status.extras == ()


def test_equivalent_installed_collisions_keep_last_then_sort_extras() -> None:
    paired = "\ud83d\ude00"
    b_first = InstalledSkill(
        f"b{paired}", "/b-first", (), "b/repo", "tracked"
    )
    a_first = InstalledSkill(
        f"a{paired}", "/a-first", (), "a/repo", "tracked"
    )
    b_last = InstalledSkill("b😀", "/b-last", (), "b/repo", "tracked")
    a_last = InstalledSkill("a😀", "/a-last", (), "a/repo", "tracked")
    state = {
        f"b{paired}": b_first,
        f"a{paired}": a_first,
        "b😀": b_last,
        "a😀": a_last,
    }

    status = classify_status(MergedRequirements((), ()), state)

    assert status.extras == (a_last, b_last)


def test_equivalent_installed_key_collision_uses_last_record_for_lookup() -> None:
    paired = "\ud83d\ude00"
    requirement = Requirement(
        requirement_key(f"{paired}/repo", paired),
        f"{paired}/repo",
        paired,
    )
    first = _actual(paired, "wrong/repo")
    last = _actual("😀", "😀/repo")

    status = classify_status(
        MergedRequirements((requirement,), ()),
        {paired: first, "😀": last},
    )

    assert status.installed == (requirement,)
    assert status.missing == ()
    assert status.mismatches == ()
    assert status.extras == ()


def test_downstream_utf16_identity_matches_literal_oracle() -> None:
    paired = "\ud83d\ude00"
    lookup_requirement = Requirement(
        requirement_key(f"{paired}/repo", paired),
        f"{paired}/repo",
        paired,
    )
    lookup_status = classify_status(
        MergedRequirements((lookup_requirement,), ()),
        {
            paired: _actual(paired, "wrong/repo"),
            "😀": _actual("😀", "😀/repo"),
        },
    )

    collision_state = {
        f"b{paired}": InstalledSkill(
            f"b{paired}", "/b-first", (), "b/repo", "tracked"
        ),
        f"a{paired}": InstalledSkill(
            f"a{paired}", "/a-first", (), "a/repo", "tracked"
        ),
        "b😀": InstalledSkill("b😀", "/b-last", (), "b/repo", "tracked"),
        "a😀": InstalledSkill("a😀", "/a-last", (), "a/repo", "tracked"),
    }
    collision_status = classify_status(
        MergedRequirements((), ()), collision_state
    )

    pair_requirement = Requirement(
        requirement_key("a/repo", paired), "a/repo", paired
    )
    scalar_requirement = Requirement(
        requirement_key("b/repo", "😀"), "b/repo", "😀"
    )
    safe = Requirement(requirement_key("c/repo", "safe"), "c/repo", "safe")
    conflict_merged = MergedRequirements(
        (pair_requirement, scalar_requirement, safe),
        (DesiredConflict(paired, ("a/repo", "b/repo")),),
    )
    conflict_status = classify_status(conflict_merged, {})
    install = create_install_plan(conflict_status)
    uninstall = create_uninstall_plan(
        selected=conflict_merged,
        remaining=MergedRequirements((), ()),
        installed_state={
            "😀": _actual("😀", "a/repo"),
            "safe": _actual("safe", "c/repo"),
        },
        linked_selected=(),
    )

    python_summary = {
        "lookup": {
            "installed": [item.key for item in lookup_status.installed],
            "missing": [item.key for item in lookup_status.missing],
            "mismatches": [item.key for item in lookup_status.mismatches],
            "extras": [item.path for item in lookup_status.extras],
        },
        "collision": {
            "rawPaths": ["/b-last", "/a-last"],
            "extras": [
                [item.name, item.path] for item in collision_status.extras
            ],
        },
        "conflict": {
            "missing": [item.key for item in conflict_status.missing],
            "extras": [item.path for item in conflict_status.extras],
            "install": [item.key for item in install.install],
            "skip": [item.key for item in install.skip],
            "remove": [item.key for item in uninstall.remove],
            "absent": [item.key for item in uninstall.absent],
        },
    }
    assert python_summary == {
        "lookup": {
            "installed": ['["😀/repo","😀"]'],
            "missing": [],
            "mismatches": [],
            "extras": [],
        },
        "collision": {
            "rawPaths": ["/b-last", "/a-last"],
            "extras": [["a😀", "/a-last"], ["b😀", "/b-last"]],
        },
        "conflict": {
            "missing": ['["c/repo","safe"]'],
            "extras": [],
            "install": ['["c/repo","safe"]'],
            "skip": [],
            "remove": ['["c/repo","safe"]'],
            "absent": [],
        },
    }


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


def test_unicode_node_identity_key_selects_only_the_requested_install() -> None:
    status = classify_status(
        catalog_requirements(
            _catalog(("owner/😀", ("é", "😀")))
        ),
        {},
    )

    plan = create_install_plan(
        status,
        selected_keys=frozenset(('["owner/😀","😀"]',)),
    )

    assert tuple(item.skill for item in plan.install) == ("😀",)
    assert tuple(item.key for item in status.missing) == (
        '["owner/😀","é"]',
        '["owner/😀","😀"]',
    )


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


def test_uninstall_uses_equivalent_name_and_requirement_key_identities() -> None:
    paired = "\ud83d\ude00"
    selected = Requirement(
        requirement_key(f"{paired}/repo", paired),
        f"{paired}/repo",
        paired,
    )
    equivalent_remaining = Requirement(
        requirement_key("😀/repo", "😀"),
        "😀/repo",
        "😀",
    )

    remove = create_uninstall_plan(
        selected=MergedRequirements((selected,), ()),
        remaining=MergedRequirements((), ()),
        installed_state={"😀": _actual("😀", "😀/repo")},
        linked_selected=(),
    )
    retain = create_uninstall_plan(
        selected=MergedRequirements((selected,), ()),
        remaining=MergedRequirements((equivalent_remaining,), ()),
        installed_state={},
        linked_selected=(),
    )
    absent = create_uninstall_plan(
        selected=MergedRequirements((selected,), ()),
        remaining=MergedRequirements((), ()),
        installed_state={"\ud800": _actual("\ud800", "other/repo")},
        linked_selected=(),
    )

    assert remove.remove == (selected,)
    assert remove.retain == ()
    assert remove.absent == ()
    assert retain.remove == ()
    assert retain.retain == (selected,)
    assert retain.absent == ()
    assert absent.remove == ()
    assert absent.retain == ()
    assert absent.absent == (selected,)


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


def test_uninstall_conflict_identity_uses_js_units_without_merging_lone() -> None:
    first = DesiredConflict("\ud83d\ude00", ("a/repo", "b/repo"))
    equivalent = DesiredConflict("😀", ("a/repo", "b/repo"))
    unrelated_lone = DesiredConflict("\ud800", ("a/repo", "b/repo"))

    plan = create_uninstall_plan(
        selected=MergedRequirements((), (first,)),
        remaining=MergedRequirements((), (equivalent, unrelated_lone)),
        installed_state={},
        linked_selected=(),
    )

    assert plan.desired_conflicts == (first, unrelated_lone)
