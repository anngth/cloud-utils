"""Loader for the dbt secrets file (TOML).

The file has three optional sections::

    [[tunnels]]           name, user, host, service, local_port, target_host, target_port
    [[postgres.sources]]  name, url, database (defaults to the URL path)
    [[postgres.targets]]  name, url
    [[mongo.sources]]     name, uri, databases (a list)
    [[mongo.targets]]     name, uri

Anything else, a missing field, a wrong type, or a bad port rejects the
whole file: dbt never guesses what a half-valid entry meant.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote

from .models import MongoSource, MongoTarget, PostgresSource, PostgresTarget, Secrets, VpsTunnel

_CREDENTIALS = re.compile(r"(://)[^/\s|]*@")
_MIN_PORT = 1
_MAX_PORT = 65535
_POSTGRES_SCHEMES = ("postgresql://", "postgres://")
_MONGO_SCHEMES = ("mongodb://", "mongodb+srv://")


class SecretsError(Exception):
    """The secrets file is not valid TOML or does not match the dbt layout."""


def redact(text: str) -> str:
    """Mask `scheme://user:password@` so a value can be quoted in a message."""
    return _CREDENTIALS.sub(r"\1***@", text)


def parse_secrets_text(text: str) -> Secrets:
    """Parse a secrets file into typed entries, or raise SecretsError."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SecretsError(f"invalid TOML: {exc}") from None

    _only_keys(document, "", ("tunnels", "postgres", "mongo"))
    postgres = _section(document, "postgres")
    mongo = _section(document, "mongo")
    _only_keys(postgres, "postgres", ("sources", "targets"))
    _only_keys(mongo, "mongo", ("sources", "targets"))
    return Secrets(
        tunnels=_entries(document, "tunnels", _tunnel, unique="local_port"),
        postgres_sources=_entries(postgres, "sources", _postgres_source, "postgres."),
        postgres_targets=_entries(postgres, "targets", _postgres_target, "postgres."),
        mongo_sources=_entries(mongo, "sources", _mongo_source, "mongo."),
        mongo_targets=_entries(mongo, "targets", _mongo_target, "mongo."),
    )


# --- sections -------------------------------------------------------------------


def _only_keys(table: Mapping[str, Any], where: str, allowed: tuple[str, ...]) -> None:
    for key in table:
        if key not in allowed:
            place = f"{where}.{key}" if where else key
            raise SecretsError(f"unknown key {place!r}; expected one of: {', '.join(allowed)}")


def _section(document: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = document.get(key, {})
    if not isinstance(value, Mapping):
        raise SecretsError(f"{key!r} must be a table ([{key}]), not {_kind(value)}")
    return value


def _entries[T](
    table: Mapping[str, Any],
    key: str,
    build: Callable[[_Entry], T],
    prefix: str = "",
    unique: str = "name",
) -> tuple[T, ...]:
    """Build every `[[prefix.key]]` entry; the `unique` field may not repeat in the list."""
    value = table.get(key, [])
    label = f"{prefix}{key}"
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise SecretsError(f"{label!r} must be an array of tables ([[{label}]])")
    items: list[T] = []
    seen: dict[object, int] = {}
    for index, item in enumerate(value, start=1):
        entry = _Entry(f"{label}[{index}]", item)
        items.append(build(entry))
        marker = item[unique]
        if marker in seen:
            raise SecretsError(
                f"{entry.where}: {unique} {marker!r} is already used by {label}[{seen[marker]}]"
            )
        seen[marker] = index
    return tuple(items)


# --- entries --------------------------------------------------------------------


class _Entry:
    """One table of the file, with typed field access that names its location."""

    def __init__(self, where: str, table: Mapping[str, Any]) -> None:
        self.table = table
        name = table.get("name")
        self.where = f"{where} ({name})" if isinstance(name, str) and name else where

    def only(self, *keys: str) -> None:
        for key in self.table:
            if key not in keys:
                raise SecretsError(
                    f"{self.where}: unknown field {key!r}; expected: {', '.join(keys)}"
                )

    def string(self, key: str) -> str:
        value = self.table.get(key)
        if value is None:
            raise SecretsError(f"{self.where}: missing field {key!r}")
        if not isinstance(value, str) or not value:
            raise SecretsError(f"{self.where}: {key!r} must be a non-empty string")
        return value

    def optional_string(self, key: str) -> str | None:
        value = self.table.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise SecretsError(f"{self.where}: {key!r} must be a non-empty string")
        return value

    def strings(self, key: str) -> tuple[str, ...]:
        value = self.table.get(key)
        if value is None:
            raise SecretsError(f"{self.where}: missing field {key!r}")
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise SecretsError(f"{self.where}: {key!r} must be a non-empty list of strings")
        return tuple(value)

    def port(self, key: str) -> int:
        value = self.table.get(key)
        if value is None:
            raise SecretsError(f"{self.where}: missing field {key!r}")
        if isinstance(value, bool) or not isinstance(value, int):
            raise SecretsError(f"{self.where}: {key!r} must be an integer port, not {_kind(value)}")
        if not _MIN_PORT <= value <= _MAX_PORT:
            raise SecretsError(f"{self.where}: {key!r} must be between {_MIN_PORT} and {_MAX_PORT}")
        return value

    def url(self, key: str, schemes: tuple[str, ...]) -> str:
        value = self.string(key)
        if not value.startswith(schemes):
            raise SecretsError(
                f"{self.where}: {key!r} must start with {' or '.join(schemes)}"
                f" (got {redact(value)})"
            )
        return value


def _kind(value: object) -> str:
    return {str: "a string", bool: "a boolean", list: "an array"}.get(type(value), "a table")


def _tunnel(entry: _Entry) -> VpsTunnel:
    entry.only("name", "user", "host", "service", "local_port", "target_host", "target_port")
    return VpsTunnel(
        name=entry.string("name"),
        user=entry.string("user"),
        host=entry.string("host"),
        service=entry.string("service"),
        local_port=entry.port("local_port"),
        target_host=entry.string("target_host"),
        target_port=entry.port("target_port"),
    )


def _postgres_source(entry: _Entry) -> PostgresSource:
    entry.only("name", "url", "database")
    url = entry.url("url", _POSTGRES_SCHEMES)
    database = entry.optional_string("database") or _url_database(url)
    if not database:
        raise SecretsError(
            f"{entry.where}: set 'database'; the url names no database in its path"
        )
    return PostgresSource(name=entry.string("name"), url=url, database=database)


def _postgres_target(entry: _Entry) -> PostgresTarget:
    entry.only("name", "url")
    return PostgresTarget(name=entry.string("name"), url=entry.url("url", _POSTGRES_SCHEMES))


def _mongo_source(entry: _Entry) -> MongoSource:
    entry.only("name", "uri", "databases")
    return MongoSource(
        name=entry.string("name"),
        uri=entry.url("uri", _MONGO_SCHEMES),
        databases=entry.strings("databases"),
    )


def _mongo_target(entry: _Entry) -> MongoTarget:
    entry.only("name", "uri")
    return MongoTarget(name=entry.string("name"), uri=entry.url("uri", _MONGO_SCHEMES))


def _url_database(url: str) -> str:
    """Database path of a Postgres URL, split the way `parse_postgres_url` does.

    The host starts after the last `@` (passwords may hold `@`, `#` or `?`),
    and the database is the first path segment before any `?query`.
    """
    for scheme in _POSTGRES_SCHEMES:
        url = url.removeprefix(scheme)
    host_part = url.rpartition("@")[2]
    host_part = host_part.partition("?")[0]
    return unquote(host_part.partition("/")[2].split("/", 1)[0])
