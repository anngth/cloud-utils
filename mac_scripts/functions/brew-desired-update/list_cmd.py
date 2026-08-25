from grid import format_grid, init_layout
from ui import FG_RESET, GREEN, YELLOW

BLUE = "\033[34m"
BRIGHT_GREEN = "\033[92m"
GRAY = "\033[90m"

STATUS_SECTIONS = (
    {
        "key": "formulas",
        "field": "installed",
        "title": "Formulae · in list, installed",
        "color": GREEN,
    },
    {
        "key": "formulas",
        "field": "missing",
        "title": "Formulae · in list, not installed",
        "color": YELLOW,
    },
    {
        "key": "formulas",
        "field": "extra",
        "title": "Formulae · installed, not in list",
        "color": BLUE,
    },
    {
        "key": "taps",
        "field": "installed",
        "title": "Taps · in list, tapped",
        "color": GREEN,
    },
    {
        "key": "taps",
        "field": "missing",
        "title": "Taps · in list, not tapped",
        "color": YELLOW,
    },
    {
        "key": "taps",
        "field": "extra",
        "title": "Taps · tapped, not in list",
        "color": BLUE,
    },
    {
        "key": "casks",
        "field": "installed",
        "title": "Casks · in list, installed",
        "color": GREEN,
    },
    {
        "key": "casks",
        "field": "missing",
        "title": "Casks · in list, not installed",
        "color": YELLOW,
    },
    {
        "key": "casks",
        "field": "extra",
        "title": "Casks · installed, not in list",
        "color": BLUE,
    },
)


def _fg(color: str, text: str) -> str:
    return f"{color}{text}{FG_RESET}"


def _partition_category(desired: list[str], installed: list[str]) -> dict:
    has = set(installed)
    want = set(desired)
    installed_in_list = []
    missing = []
    for item in desired:
        if item in has:
            installed_in_list.append(item)
        else:
            missing.append(item)

    extra = []
    seen = set()
    for item in installed:
        if item in seen:
            continue
        seen.add(item)
        if item not in want:
            extra.append(item)

    return {"installed": installed_in_list, "missing": missing, "extra": extra}


def partition_lists(desired: dict, installed: dict) -> dict:
    formulas = _partition_category(
        desired.get("formulas") or [], installed.get("formulas") or []
    )
    casks = _partition_category(
        desired.get("casks") or [], installed.get("casks") or []
    )
    desired_taps = desired.get("taps") or []
    taps = (
        _partition_category(desired_taps, installed.get("taps") or [])
        if desired_taps
        else {"installed": [], "missing": [], "extra": []}
    )
    return {"formulas": formulas, "taps": taps, "casks": casks}


def run_list(ui, document: dict, brew_state: dict, columns: int) -> int:
    partitions = partition_lists(document, brew_state)
    term_width = columns if columns >= 20 else 120
    usable_width = term_width - 2
    all_items = [
        item
        for section in STATUS_SECTIONS
        for item in partitions[section["key"]][section["field"]]
    ]
    layout = init_layout(usable_width, all_items) if all_items else None

    counts = {
        "formulas": len(document.get("formulas") or []),
        "taps": len(document.get("taps") or []),
        "casks": len(document.get("casks") or []),
    }
    ui.active(
        f"Desired vs installed · {counts['formulas']} formulae · "
        f"{counts['taps']} taps · {counts['casks']} casks"
    )

    for section in STATUS_SECTIONS:
        items = partitions[section["key"]][section["field"]]
        if not items:
            continue
        ui.command(
            f"{_fg(section['color'], '▸')} {_fg(BRIGHT_GREEN, section['title'])} "
            f"{_fg(GRAY, f'({len(items)})')}"
        )
        if layout is not None:
            for line in format_grid(items, layout):
                ui.command(line)
        else:
            for item in items:
                ui.command(f"  {item}")
    return 0
