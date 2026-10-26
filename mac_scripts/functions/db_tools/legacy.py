"""One-way conversion of the pre-TOML secrets file (zsh arrays of `a|b|c` rows).

dbt reads this format only to migrate it to `secrets.toml`; it never runs
from it. The file is never handed to zsh: only a fixed, double-quoted subset
of zsh syntax is read, and anything outside that subset is rejected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

_TYPESET = re.compile(r"typeset -a (\w+)=\($")
_ESCAPES = ('"', "\\", "$")
_MIN_PORT = 1
_MAX_PORT = 65535
_DEL = "\x7f"
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

# zsh array name -> TOML section, in the order the converted file lists them.
_SECTIONS: Mapping[str, str] = {
    "VPS_CONFIGS": "tunnels",
    "POSTGRES_SOURCES": "postgres.sources",
    "POSTGRES_TARGETS": "postgres.targets",
    "MONGO_SOURCES": "mongo.sources",
    "MONGO_TARGETS": "mongo.targets",
}

_HEADER = """\
# dbt secrets (converted from the zsh-array file).
#
# [[tunnels]]           name, user, host, service, local_port, target_host, target_port
# [[postgres.sources]]  name, url, database (optional: defaults to the URL path)
# [[postgres.targets]]  name, url
# [[mongo.sources]]     name, uri, databases (a list)
# [[mongo.targets]]     name, uri
"""


class LegacySecretsError(Exception):
    """The old-format file failed validation.

    Attributes:
        line: 1-based number of the offending source line.
    """

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass(frozen=True, slots=True)
class Conversion:
    """The TOML text plus the rows that could not be converted (kept as comments)."""

    toml: str
    skipped: tuple[str, ...] = ()


def convert_legacy_secrets(text: str) -> Conversion:
    """Turn the zsh-array file into TOML; invalid rows become comments in place."""
    arrays = read_legacy_arrays(text)
    chunks = [_HEADER]
    skipped: list[str] = []
    for array, section in _SECTIONS.items():
        rows = arrays.get(array, [])
        if not rows:
            continue
        convert = _CONVERTERS[section]
        if chunks[-1] != "":
            chunks.append("")
        for row in rows:
            fields = convert(row)
            if fields is None:
                skipped.append(row)
                chunks.append(f"# not converted (invalid {section} row): {_comment(row)}")
                continue
            chunks.append(f"[[{section}]]")
            chunks.extend(f"{key} = {_toml_value(value)}" for key, value in fields.items())
            chunks.append("")
    return Conversion("\n".join(chunks).rstrip("\n") + "\n", tuple(skipped))


def read_legacy_arrays(text: str) -> dict[str, list[str]]:
    """Parse the zsh arrays into raw rows, keyed by array name."""
    arrays: dict[str, list[str]] = {}
    open_name: str | None = None
    open_line = 0

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if open_name is None:
            match = _TYPESET.fullmatch(line)
            if match is None:
                raise LegacySecretsError(
                    lineno, "expected `typeset -a NAME=(`, a comment, or blank line"
                )
            name = match[1]
            if name not in _SECTIONS:
                raise LegacySecretsError(lineno, f"unknown secrets array name: {name}")
            if name in arrays:
                raise LegacySecretsError(lineno, f"duplicate array: {name}")
            arrays[name] = []
            open_name = name
            open_line = lineno
            continue

        if line == ")":
            open_name = None
            continue

        arrays[open_name].append(_read_element(line, lineno))

    if open_name is not None:
        raise LegacySecretsError(open_line, f"array {open_name} is never closed with `)`")
    return arrays


def _read_element(line: str, lineno: int) -> str:
    if not line.startswith('"'):
        raise LegacySecretsError(lineno, "array elements must be double-quoted strings")
    chars: list[str] = []
    i = 1
    while i < len(line):
        char = line[i]
        if char == "\\":
            if i + 1 >= len(line) or line[i + 1] not in _ESCAPES:
                raise LegacySecretsError(lineno, "only \\\\, \\\" and \\$ are valid escapes")
            chars.append(line[i + 1])
            i += 2
            continue
        if char == '"':
            if line[i + 1 :].strip():
                raise LegacySecretsError(lineno, "unexpected text after closing quote")
            return "".join(chars)
        if char == "`":
            raise LegacySecretsError(lineno, "backticks are not allowed")
        if char == "$":
            # zsh would expand this; dbt never does, so say so instead of
            # silently keeping the literal text.
            raise LegacySecretsError(
                lineno, "unescaped $ is not expanded; write \\$ for a literal $"
            )
        chars.append(char)
        i += 1
    raise LegacySecretsError(lineno, "unterminated double-quoted string")


# --- rows -------------------------------------------------------------------------

type _Fields = dict[str, str | int | list[str]]


def _tunnel_row(row: str) -> _Fields | None:
    """`name|user|host|service|local_port|target_host|target_port`."""
    fields = row.split("|")
    if len(fields) != 7:
        return None
    name, user, host, service, local_text, target_host, target_text = fields
    local_port = _port(local_text)
    target_port = _port(target_text)
    if local_port is None or target_port is None:
        return None
    if not name or not user or not host or not service or not target_host:
        return None
    return {
        "name": name,
        "user": user,
        "host": host,
        "service": service,
        "local_port": local_port,
        "target_host": target_host,
        "target_port": target_port,
    }


def _postgres_source_row(row: str) -> _Fields | None:
    """`name|url|database`; `database` is dropped when the URL path already says it."""
    fields = row.split("|")
    if len(fields) != 3 or not all(fields):
        return None
    name, url, database = fields
    result: _Fields = {"name": name, "url": url}
    if _postgres_url_database(url) != database:
        result["database"] = database
    return result


def _postgres_target_row(row: str) -> _Fields | None:
    """`name|url`."""
    fields = row.split("|")
    if len(fields) != 2 or not all(fields):
        return None
    return {"name": fields[0], "url": fields[1]}


def _mongo_source_row(row: str) -> _Fields | None:
    """`name|uri|db1,db2`."""
    fields = row.split("|")
    if len(fields) != 3 or not all(fields):
        return None
    databases = [name.strip() for name in fields[2].split(",")]
    if not all(databases):
        return None
    return {"name": fields[0], "uri": fields[1], "databases": databases}


def _mongo_target_row(row: str) -> _Fields | None:
    """`name|uri`."""
    fields = row.split("|")
    if len(fields) != 2 or not all(fields):
        return None
    return {"name": fields[0], "uri": fields[1]}


_CONVERTERS: Mapping[str, Callable[[str], _Fields | None]] = {
    "tunnels": _tunnel_row,
    "postgres.sources": _postgres_source_row,
    "postgres.targets": _postgres_target_row,
    "mongo.sources": _mongo_source_row,
    "mongo.targets": _mongo_target_row,
}


def _port(text: str) -> int | None:
    if not text.isdigit():
        return None
    port = int(text)
    return port if _MIN_PORT <= port <= _MAX_PORT else None


def _postgres_url_database(url: str) -> str:
    for scheme in ("postgresql://", "postgres://"):
        url = url.removeprefix(scheme)
    host_part = url.rpartition("@")[2].partition("?")[0]
    return host_part.partition("/")[2].split("/", 1)[0]


def _comment(text: str) -> str:
    """Comment text: TOML forbids control characters there as well."""
    return _CONTROL.sub(lambda match: f"\\u{ord(match[0]):04X}", text)


def _toml_value(value: str | int | list[str]) -> str:
    """A TOML literal: integers as is, strings as basic strings, lists inline."""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    # JSON string escapes are a subset of TOML basic-string escapes, except
    # that JSON leaves DEL unescaped and TOML requires it escaped.
    return json.dumps(value, ensure_ascii=False).replace(_DEL, "\\u007F")
