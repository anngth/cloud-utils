"""Build TOML secrets files for the suites from their compact `a|b|c` rows.

Fields map positionally; a row with fewer fields leaves the rest out, so
the loader's own validation decides whether the file is usable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

_FIELDS: dict[str, tuple[str, ...]] = {
    "tunnels": ("name", "user", "host", "service", "local_port", "target_host", "target_port"),
    "postgres.sources": ("name", "url", "database"),
    "postgres.targets": ("name", "url"),
    "mongo.sources": ("name", "uri", "databases"),
    "mongo.targets": ("name", "uri"),
}
_PORTS = frozenset({"local_port", "target_port"})


def _value(key: str, text: str) -> str:
    if key in _PORTS:
        return text if text.isdigit() else json.dumps(text)
    if key == "databases":
        return json.dumps([name.strip() for name in text.split(",")])
    return json.dumps(text)


def toml_secrets(
    *,
    tunnels: Iterable[str] = (),
    postgres_sources: Iterable[str] = (),
    postgres_targets: Iterable[str] = (),
    mongo_sources: Iterable[str] = (),
    mongo_targets: Iterable[str] = (),
) -> str:
    sections = {
        "tunnels": tunnels,
        "postgres.sources": postgres_sources,
        "postgres.targets": postgres_targets,
        "mongo.sources": mongo_sources,
        "mongo.targets": mongo_targets,
    }
    lines: list[str] = []
    for section, rows in sections.items():
        for row in rows:
            lines.append(f"[[{section}]]")
            for key, text in zip(_FIELDS[section], row.split("|"), strict=False):
                lines.append(f"{key} = {_value(key, text)}")
            lines.append("")
    return "\n".join(lines)


def write_secrets(path: Path, **sections: Iterable[str]) -> Path:
    path.write_text(toml_secrets(**sections), encoding="utf-8")
    return path
