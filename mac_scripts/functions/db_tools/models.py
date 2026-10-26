"""Frozen records for the entries of the dbt secrets file."""

from __future__ import annotations

from dataclasses import dataclass


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
    databases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MongoTarget:
    name: str
    uri: str


@dataclass(frozen=True, slots=True)
class Secrets:
    """Every entry of the secrets file in file order; a missing section is empty."""

    tunnels: tuple[VpsTunnel, ...] = ()
    postgres_sources: tuple[PostgresSource, ...] = ()
    postgres_targets: tuple[PostgresTarget, ...] = ()
    mongo_sources: tuple[MongoSource, ...] = ()
    mongo_targets: tuple[MongoTarget, ...] = ()
