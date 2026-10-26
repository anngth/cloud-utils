"""Parser for the dbt secrets zsh-array file.

The file is never handed to zsh; we only read a fixed, double-quoted
subset of zsh syntax. Anything outside that subset is rejected instead
of interpreted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .models import RawSecrets

_TYPESET = re.compile(r"typeset -a (\w+)=\($")
_ESCAPES = ('"', "\\", "$")

_ARRAY_FIELDS: Mapping[str, str] = {
    "VPS_CONFIGS": "vps",
    "POSTGRES_SOURCES": "postgres_sources",
    "POSTGRES_TARGETS": "postgres_targets",
    "MONGO_SOURCES": "mongo_sources",
    "MONGO_TARGETS": "mongo_targets",
}


class SecretsError(Exception):
    """A secrets file failed validation.

    Attributes:
        line: 1-based number of the offending source line.
    """

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


def parse_secrets_text(text: str) -> RawSecrets:
    """Parse a secrets file into raw rows, or raise SecretsError."""
    arrays: dict[str, list[str]] = {}
    seen_names: set[str] = set()
    open_field: str | None = None
    open_line = 0

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if open_field is None:
            match = _TYPESET.fullmatch(line)
            if match is None:
                raise SecretsError(lineno, "expected `typeset -a NAME=(`, a comment, or blank line")
            name = match[1]
            field = _ARRAY_FIELDS.get(name)
            if field is None:
                raise SecretsError(lineno, f"unknown secrets array name: {name}")
            if name in seen_names:
                raise SecretsError(lineno, f"duplicate array: {name}")
            seen_names.add(name)
            arrays[field] = []
            open_field = field
            open_line = lineno
            continue

        if line == ")":
            open_field = None
            continue

        arrays[open_field].append(_read_element(line, lineno))

    if open_field is not None:
        raise SecretsError(open_line, f"array {open_field} is never closed with `)`")

    return RawSecrets(
        vps=tuple(arrays.get("vps", ())),
        postgres_sources=tuple(arrays.get("postgres_sources", ())),
        postgres_targets=tuple(arrays.get("postgres_targets", ())),
        mongo_sources=tuple(arrays.get("mongo_sources", ())),
        mongo_targets=tuple(arrays.get("mongo_targets", ())),
    )


def _read_element(line: str, lineno: int) -> str:
    if not line.startswith('"'):
        raise SecretsError(lineno, "array elements must be double-quoted strings")
    chars: list[str] = []
    i = 1
    while i < len(line):
        char = line[i]
        if char == "\\":
            if i + 1 >= len(line) or line[i + 1] not in _ESCAPES:
                raise SecretsError(lineno, "only \\\\, \\\" and \\$ are valid escapes")
            chars.append(line[i + 1])
            i += 2
            continue
        if char == '"':
            if line[i + 1 :].strip():
                raise SecretsError(lineno, "unexpected text after closing quote")
            return "".join(chars)
        if char == "`":
            raise SecretsError(lineno, "backticks are not allowed")
        if char == "$":
            # zsh would expand this; dbt never does, so say so instead of
            # silently keeping the literal text.
            raise SecretsError(lineno, "unescaped $ is not expanded; write \\$ for a literal $")
        chars.append(char)
        i += 1
    raise SecretsError(lineno, "unterminated double-quoted string")
