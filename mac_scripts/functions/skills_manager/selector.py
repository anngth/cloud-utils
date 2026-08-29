from __future__ import annotations
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal
from shared.selector import SelectorItem, SelectorResult, SelectorState, reduce_selector
from .config import Catalog, CatalogSource
from .planner import MergedRequirements, catalog_requirements, requirement_key
from .source import js_string_key, redact_source
from .state import InstalledState
Keys = Sequence[str]; Selected = tuple[str, ...]
@dataclass(frozen=True, slots=True)
class CatalogSelectorItem(SelectorItem):
    kind: Literal["source", "skill"]; source_index: int
    child_values: tuple[str, ...] = (); hint: str = ""
def build_catalog_selector_items(
    catalog: Catalog, *, installed_state: InstalledState
) -> tuple[tuple[CatalogSelectorItem, ...], tuple[str, ...]]:
    items, initial = [], []
    actual_by_name = {
        js_string_key(name): item for name, item in installed_state.items()
    }
    for index, entry in enumerate(catalog.sources, start=1):
        children = tuple(requirement_key(entry.source, skill) for skill in entry.skills)
        items.append(CatalogSelectorItem(
            redact_source(entry.source), entry.source, "source", index, children
        ))
        for skill, value in zip(entry.skills, children, strict=True):
            actual = actual_by_name.get(js_string_key(skill))
            if (actual is not None and actual.source is not None
                    and actual.provenance != "untracked"
                    and js_string_key(actual.source) == js_string_key(entry.source)):
                initial.append(value)
            items.append(CatalogSelectorItem(skill, value, "skill", index))
    return tuple(items), tuple(initial)
def _children(state: SelectorState, source: object) -> tuple[int, ...]:
    values = frozenset(getattr(source, "child_values", ()))
    return tuple(index for index, item in enumerate(state.items)
                 if getattr(item, "kind", None) == "skill" and item.value in values)
def _sync(state, selected, *, clear_empty=False) -> frozenset[int]:
    for index, item in enumerate(state.items):
        if getattr(item, "kind", None) != "source": continue
        children = _children(state, item)
        if not children and clear_empty: selected.discard(index)
        if not children: continue
        if all(child in selected for child in children): selected.add(index)
        else: selected.discard(index)
    return frozenset(selected)
def filter_skill_requirement_keys(
    selected: Keys, items: Sequence[SelectorItem]
) -> Selected:
    skills = {
        item.value for item in items if getattr(item, "kind", None) == "skill"
    }
    return tuple(value for value in selected if value in skills)
def reduce_catalog_selector(state: SelectorState, key: str) -> SelectorResult:
    result = reduce_selector(state, key, multiple=True)
    selected = set(result.state.selected)
    current = state.items[state.cursor] if state.items else None
    if (key == "toggle" and getattr(current, "kind", None) == "source"
            and _children(state, current)):
        children, selected = _children(state, current), set(state.selected)
        if children and all(index in selected for index in children):
            selected.difference_update(children)
        else: selected.update(children)
    if key in {"toggle", "select_all", "clear"}:
        clear_empty = key == "select_all" and any(
            getattr(item, "kind", None) == "skill" for item in state.items)
        state = replace(
            result.state, selected=_sync(state, selected, clear_empty=clear_empty))
        result = replace(result, state=state)
    if result.kind == "submit":
        selected_values = filter_skill_requirement_keys(result.selected, state.items)
        result = replace(result, selected=selected_values)
    return result
def _slice(catalog: Catalog, desired: Keys, inverse: bool) -> MergedRequirements:
    keys, sources = frozenset(desired), []
    for entry in catalog.sources:
        skills = tuple(
            skill for skill in entry.skills
            if (requirement_key(entry.source, skill) in keys) != inverse
        )
        if skills: sources.append(CatalogSource(source=entry.source, skills=skills))
    return catalog_requirements(Catalog(version=1, sources=tuple(sources)))
def catalog_slice_from_keys(catalog: Catalog, desired_keys: Keys) -> MergedRequirements:
    return _slice(catalog, desired_keys, False)
def inverse_catalog_slice(catalog: Catalog, desired_keys: Keys) -> MergedRequirements:
    return _slice(catalog, desired_keys, True)
