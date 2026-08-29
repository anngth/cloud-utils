from __future__ import annotations

from dataclasses import replace

from shared.selector import create_selector_state
from skills_manager.config import Catalog, CatalogSource
from skills_manager.planner import requirement_key
from skills_manager.selector import (
    CatalogSelectorItem,
    build_catalog_selector_items,
    catalog_slice_from_keys,
    filter_skill_requirement_keys,
    inverse_catalog_slice,
    reduce_catalog_selector,
)
from skills_manager.state import InstalledSkill


def catalog() -> Catalog:
    return Catalog(
        version=1,
        sources=(
            CatalogSource(source="a/repo", skills=("one", "two")),
            CatalogSource(source="b/repo", skills=("three",)),
        ),
    )


def installed(name: str, source: str | None, provenance: str = "tracked"):
    return InstalledSkill(name, f"/skills/{name}", ("Codex",), source, provenance)


def test_build_items_keeps_hierarchy_and_only_correct_installs_initially_selected(
) -> None:
    items, initial = build_catalog_selector_items(
        catalog(),
        installed_state={
            "one": installed("one", "a/repo"),
            "two": installed("two", "wrong/repo"),
            "three": installed("three", None, "untracked"),
        },
    )

    assert tuple((item.kind, item.label, item.source_index) for item in items) == (
        ("source", "a/repo", 1),
        ("skill", "one", 1),
        ("skill", "two", 1),
        ("source", "b/repo", 2),
        ("skill", "three", 2),
    )
    assert items[0].child_values == (
        requirement_key("a/repo", "one"),
        requirement_key("a/repo", "two"),
    )
    assert initial == (requirement_key("a/repo", "one"),)


def _items() -> tuple[CatalogSelectorItem, ...]:
    return build_catalog_selector_items(catalog(), installed_state={})[0]


def test_source_toggle_selects_then_clears_all_children() -> None:
    state = create_selector_state(_items())

    selected = reduce_catalog_selector(state, "toggle").state
    cleared = reduce_catalog_selector(selected, "toggle").state

    assert selected.selected == frozenset({0, 1, 2})
    assert cleared.selected == frozenset()


def test_source_toggle_completes_a_partial_group() -> None:
    state = replace(create_selector_state(_items()), selected=frozenset({1}))

    result = reduce_catalog_selector(state, "toggle")

    assert result.state.selected == frozenset({0, 1, 2})


def test_child_toggles_synchronize_parent_selection() -> None:
    partial = replace(
        create_selector_state(_items()), cursor=2, selected=frozenset({1})
    )
    complete = reduce_catalog_selector(partial, "toggle").state
    incomplete = reduce_catalog_selector(complete, "toggle").state

    assert complete.selected == frozenset({0, 1, 2})
    assert incomplete.selected == frozenset({1})


def test_select_all_and_clear_synchronize_every_source_row() -> None:
    selected = reduce_catalog_selector(
        create_selector_state(_items()), "select_all"
    ).state
    cleared = reduce_catalog_selector(selected, "clear").state

    assert selected.selected == frozenset(range(5))
    assert cleared.selected == frozenset()


def test_empty_source_rows_keep_generic_toggle_and_select_all_behavior() -> None:
    items = (CatalogSelectorItem("empty", "empty", "source", 1),)
    state = create_selector_state(items)

    assert reduce_catalog_selector(state, "toggle").state.selected == frozenset({0})
    assert reduce_catalog_selector(state, "select_all").state.selected == frozenset({0})
    mixed = (
        *items,
        CatalogSelectorItem("full", "full", "source", 2, ("key",)),
        CatalogSelectorItem("skill", "key", "skill", 2),
    )
    assert reduce_catalog_selector(
        create_selector_state(mixed), "select_all"
    ).state.selected == frozenset({1, 2})


def test_navigation_submit_and_cancel_keep_generic_contract() -> None:
    state = reduce_catalog_selector(create_selector_state(_items()), "down").state
    state = reduce_catalog_selector(state, "toggle").state

    submitted = reduce_catalog_selector(state, "submit")
    cancelled = reduce_catalog_selector(state, "cancel")

    assert state.cursor == 1
    assert submitted.kind == "submit"
    assert submitted.selected == (requirement_key("a/repo", "one"),)
    assert cancelled.kind == "cancel"
    assert cancelled.selected == ()


def test_filter_skill_keys_drops_source_rows_and_unknown_values() -> None:
    items = _items()

    assert filter_skill_requirement_keys(
        ("a/repo", requirement_key("a/repo", "one"), "unknown"), items
    ) == (requirement_key("a/repo", "one"),)


def test_catalog_slices_preserve_source_and_skill_order() -> None:
    keys = (
        requirement_key("a/repo", "two"),
        requirement_key("b/repo", "three"),
    )

    desired = catalog_slice_from_keys(catalog(), keys)
    inverse = inverse_catalog_slice(catalog(), keys)

    assert tuple((item.source, item.skill) for item in desired.requirements) == (
        ("b/repo", "three"),
        ("a/repo", "two"),
    )
    assert tuple((item.source, item.skill) for item in inverse.requirements) == (
        ("a/repo", "one"),
    )
