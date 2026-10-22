"""Frozen records for parsed dbt secrets rows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RawSecrets:
    """Raw string rows grouped per secrets array; empty when absent."""

    vps: tuple[str, ...] = ()
    postgres_sources: tuple[str, ...] = ()
    postgres_targets: tuple[str, ...] = ()
    mongo_sources: tuple[str, ...] = ()
    mongo_targets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VpsTunnel:
    name: str
    user: str
    host: str
    service: str
    local_port: int
    target_host: str
    target_port: int


@dataclass(frozen=True, slots=True)
class PostgresSource:
    name: str
    url: str
    database: str


@dataclass(frozen=True, slots=True)
class PostgresTarget:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class MongoSource:
    name: str
    uri: str
    databases: str


@dataclass(frozen=True, slots=True)
class MongoTarget:
    name: str
    uri: str
