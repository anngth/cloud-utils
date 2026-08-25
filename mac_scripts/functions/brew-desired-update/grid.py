from dataclasses import dataclass

GAP = 2


@dataclass(frozen=True)
class Layout:
    cols: int
    cell: int


def init_layout(usable_width: int, items: list[str]) -> Layout:
    max_len = 1
    for item in items:
        if len(item) > max_len:
            max_len = len(item)

    if max_len > usable_width:
        return Layout(cols=1, cell=usable_width)

    cell = max_len
    cols = (usable_width + GAP) // (max_len + GAP)
    if cols < 1:
        cols = 1
    return Layout(cols=cols, cell=cell)


def _fit_cell(text: str, cell: int) -> str:
    if len(text) > cell:
        return text[:cell]
    return text.ljust(cell)


def format_grid(items: list[str], layout: Layout) -> list[str]:
    n = len(items)
    cols = layout.cols
    cell = layout.cell
    if not n or not cols:
        return []

    nrow = (n + cols - 1) // cols
    empty = _fit_cell("", cell)
    lines = []

    for row in range(nrow):
        last_col = -1
        for col in range(cols):
            index = col * nrow + row
            if index < n:
                last_col = col

        parts = []
        for col in range(last_col + 1):
            index = col * nrow + row
            parts.append(_fit_cell(items[index], cell) if index < n else empty)
        lines.append(f"  {'  '.join(parts)}")

    return lines
