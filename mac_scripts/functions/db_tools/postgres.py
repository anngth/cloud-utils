"""Postgres URL parsing, client discovery, status, and backup cleanup.

Every psql call is an argv list. The password only ever travels in the
child's PGPASSWORD environment variable, and cleanup always connects to
the `postgres` database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from .models import PostgresSource, PostgresTarget
from .runtime import Runtime

_BACKUP_SUFFIX = "_backup_[0-9]{8}_[0-9]{6}$"
_LIBPQ_BIN = "/opt/homebrew/opt/libpq/bin"
_PG18_BIN = "/opt/homebrew/opt/postgresql@18/bin"
_CLIENTS = ("psql", "pg_dump", "pg_restore")


@dataclass(frozen=True, slots=True)
class PostgresUrl:
    user: str
    password: str
    host: str
    port: str
    database: str
    sslmode_require: bool


@dataclass(frozen=True, slots=True)
class PostgresTools:
    psql: str
    pg_dump: str
    pg_restore: str
    source: str


def parse_postgres_url(url: str) -> PostgresUrl:
    """Split `postgresql://user:pass@host:port/db` into its parts.

    The user ends at the first colon, the password keeps remaining
    colons, and the host starts after the last `@`. A missing database
    defaults to `postgres`. `sslmode=require` anywhere in the URL sets
    `sslmode_require`; any other SSL mode leaves it unset.
    """
    rest = url.removeprefix("postgresql://")
    auth_part, sep, host_part = rest.rpartition("@")
    if not sep:
        auth_part, host_part = "", rest
    user, _, password = auth_part.partition(":")
    path_part = ""
    if "/" in host_part:
        host_part, _, path_part = host_part.partition("/")
    elif "?" in host_part:
        host_part, _, path_part = host_part.partition("?")
    host, _, port_and_db = host_part.partition(":")
    port = port_and_db.split("/", 1)[0].split("?", 1)[0]
    database = path_part.split("/", 1)[0].split("?", 1)[0]
    if not database:
        database = "postgres"
    return PostgresUrl(
        user=user,
        password=password,
        host=host,
        port=port,
        database=database,
        sslmode_require="sslmode=require" in url,
    )


def quote_identifier(name: str) -> str:
    """Escape one SQL identifier; embedded `"` doubles."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Escape one SQL string literal; embedded `'` doubles."""
    return "'" + value.replace("'", "''") + "'"


def parse_postgres_source_row(row: str) -> PostgresSource | None:
    """Parse `name|url|database`; None when one of the three is missing."""
    name, sep, rest = row.partition("|")
    if not sep:
        return None
    url, sep, database = rest.partition("|")
    if not sep or not name or not url or not database:
        return None
    return PostgresSource(name=name, url=url, database=database)


def parse_postgres_target_row(row: str) -> PostgresTarget | None:
    """Parse `name|url`; None when one of the two is missing."""
    name, sep, url = row.partition("|")
    if not sep or not name or not url:
        return None
    return PostgresTarget(name=name, url=url)


def find_postgres_tools(runtime: Runtime) -> PostgresTools | None:
    """Prefer PATH, then Homebrew libpq, then Homebrew postgresql@18."""
    on_path = [runtime.which(name) for name in _CLIENTS]
    if all(path is not None for path in on_path):
        return PostgresTools(
            psql=on_path[0],
            pg_dump=on_path[1],
            pg_restore=on_path[2],
            source="PATH",
        )
    for prefix, source in (
        (_LIBPQ_BIN, "Homebrew libpq"),
        (_PG18_BIN, "Homebrew postgresql@18"),
    ):
        paths = [f"{prefix}/{name}" for name in _CLIENTS]
        if all(runtime.path_exists(path) for path in paths):
            return PostgresTools(
                psql=paths[0],
                pg_dump=paths[1],
                pg_restore=paths[2],
                source=source,
            )
    return None


def postgres_status(url: str, *, runtime: Runtime) -> bool:
    """Run `SELECT 1;` on the `postgres` database; no client counts as up."""
    tools = find_postgres_tools(runtime)
    if tools is None:
        return True
    parsed = parse_postgres_url(url)
    result = runtime.run_command(
        _psql_argv(tools, parsed, ["-t", "-c", "SELECT 1;"]),
        env=_child_env(parsed),
    )
    return result.returncode == 0


def clear_postgres_backups(
    targets: Sequence[PostgresTarget],
    *,
    source_database: str | None,
    dry_run: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Drop `_backup_YYYYMMDD_HHMMSS` databases on each target.

    A failed target does not stop the remaining ones; the command exits 1
    when any target failed.
    """
    tools = find_postgres_tools(runtime)
    if tools is None:
        stderr.write("❌ Error: PostgreSQL client tools not found\n")
        stderr.write("   Install client-only tools: brew install libpq\n")
        stderr.write("   Or install PostgreSQL 18: brew install postgresql@18\n")
        stderr.write("   Then ensure psql, pg_dump, and pg_restore are available on PATH\n")
        return 1
    failed = False
    for target in targets:
        stdout.write(f"Target: {target.name}\n")
        if not _clear_target(
            target, tools, source_database, dry_run, runtime, stdout, stderr
        ):
            failed = True
        stdout.write("\n")
    return 1 if failed else 0


def _backup_query(source_database: str | None) -> str:
    """One listing query, optionally scoped to `{source_database}_backup_`."""
    query = (
        "SELECT datname FROM pg_database WHERE datistemplate = false AND datname ~ "
        + quote_literal(_BACKUP_SUFFIX)
    )
    if source_database:
        prefix = f"{source_database}_backup_"
        prefix_literal = quote_literal(prefix)
        query += f" AND left(datname, length({prefix_literal})) = {prefix_literal}"
    return query + " ORDER BY datname;"


def _clear_target(
    target: PostgresTarget,
    tools: PostgresTools,
    source_database: str | None,
    dry_run: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> bool:
    url = parse_postgres_url(target.url)
    env = _child_env(url)
    listed = runtime.run_command(
        _psql_argv(tools, url, ["-At", "-c", _backup_query(source_database)]),
        env=env,
    )
    if listed.returncode != 0:
        stderr.write("❌ Failed to list PostgreSQL backup databases\n")
        message = _first_error_line(listed.stderr)
        if message:
            stderr.write(f"   Error: {message}\n")
        return False

    backups = [line for line in listed.stdout.splitlines() if line]
    if not backups:
        stdout.write("✅ No PostgreSQL backup databases found\n")
        return True

    stdout.write("Found backup databases:\n")
    for backup in backups:
        stdout.write(f"  - {backup}\n")
    stdout.write("\n")

    if dry_run:
        stdout.write("ℹ️  Dry run only; no databases were dropped\n")
        return True

    dropped = 0
    failed_drops = 0
    for backup in backups:
        literal = quote_literal(backup)
        runtime.run_command(
            _psql_argv(
                tools,
                url,
                [
                    "-t",
                    "-c",
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    f" WHERE datname = {literal} AND pid <> pg_backend_pid();",
                ],
            ),
            env=env,
        )  # best effort; only the DROP decides success
        drop_result = runtime.run_command(
            _psql_argv(
                tools,
                url,
                ["-c", f"DROP DATABASE IF EXISTS {quote_identifier(backup)};"],
            ),
            env=env,
        )
        if drop_result.returncode == 0:
            stdout.write(f"✅ Dropped: {backup}\n")
            dropped += 1
        else:
            stdout.write(f"❌ Failed to drop: {backup}\n")
            failed_drops += 1

    stdout.write("\n")
    stdout.write(f"Summary: dropped {dropped}, failed {failed_drops}\n")
    return failed_drops == 0


def _psql_argv(
    tools: PostgresTools, url: PostgresUrl, tail: Sequence[str]
) -> list[str]:
    argv = [tools.psql, "-h", url.host]
    if url.port:
        argv += ["-p", url.port]
    argv += ["-U", url.user, "-d", "postgres", *tail]
    return argv


def _child_env(url: PostgresUrl) -> dict[str, str]:
    """Minimal child env: PGPASSWORD always, PGSSLMODE only for require."""
    env = {"PGPASSWORD": url.password}
    if url.sslmode_require:
        env["PGSSLMODE"] = "require"
    return env


def _first_error_line(stderr_text: str) -> str:
    for line in stderr_text.splitlines():
        if line.strip():
            return line.strip()
    return ""
