"""Postgres URL parsing, client discovery, status, and backup cleanup.

Every psql call is an argv list. The password only ever travels in the
child's PGPASSWORD environment variable, and cleanup always connects to
the `postgres` database.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from shared.process import CommandResult

from .models import PostgresSource, PostgresTarget
from .runtime import Runtime

_BACKUP_SUFFIX = "_backup_[0-9]{8}_[0-9]{6}$"
_LIBPQ_BIN = "/opt/homebrew/opt/libpq/bin"
_PG18_BIN = "/opt/homebrew/opt/postgresql@18/bin"
_CLIENTS = ("psql", "pg_dump", "pg_restore")

_TOOLS_NOT_FOUND = (
    "❌ Error: PostgreSQL client tools not found\n"
    "   Install client-only tools: brew install libpq\n"
    "   Or install PostgreSQL 18: brew install postgresql@18\n"
    "   Then ensure psql, pg_dump, and pg_restore are available on PATH\n"
)

_VERSION_QUERY = "SELECT version();"
_TABLE_COUNT_QUERY = (
    "SELECT COUNT(*) FROM information_schema.tables"
    " WHERE table_schema NOT IN ('pg_catalog', 'information_schema');"
)
_SCHEMA_SQL = (
    "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
    " GRANT ALL ON SCHEMA public TO postgres;"
    " GRANT ALL ON SCHEMA public TO public;"
)


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
        stderr.write(_TOOLS_NOT_FOUND)
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
    tools: PostgresTools, url: PostgresUrl, tail: Sequence[str], *, database: str = "postgres"
) -> list[str]:
    argv = [tools.psql, "-h", url.host]
    if url.port:
        argv += ["-p", url.port]
    argv += ["-U", url.user, "-d", database, *tail]
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


def _major_version(version_text: str) -> str:
    """Return the client/server major number, `""` when unreadable."""
    match = re.search(r"PostgreSQL\s+(\d+)", version_text)
    return match.group(1) if match else ""


def perform_postgres_sync(
    source: PostgresSource,
    target: PostgresTarget,
    *,
    verbose: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Restore `source.database` onto the target, keeping the old one.

    The signal handler is bound only once the target content is safely
    preserved and unbound again before this function returns. Sync
    progress and errors go to stdout; missing client tools go to stderr.
    """
    tools = find_postgres_tools(runtime)
    if tools is None:
        stderr.write(_TOOLS_NOT_FOUND)
        return 1
    src = parse_postgres_url(source.url)
    tgt = parse_postgres_url(target.url)
    src_env = _child_env(src)
    tgt_env = _child_env(tgt)
    is_supabase = "supabase" in tgt.host.lower()
    tgt_db_name = source.database
    if is_supabase or tgt.database == "postgres":
        tgt_db_name = "postgres"
        stdout.write(
            "ℹ️  Target is Supabase or uses 'postgres' database"
            " - will restore to 'postgres'\n"
        )
    stdout.write("=====================================\n")
    stdout.write(f"PostgreSQL Sync: {source.database}\n")
    stdout.write(f"Source: {src.user}@{src.host}:{src.port}\n")
    stdout.write(f"Target: {tgt.user}@{tgt.host}:{tgt.port}\n")
    stdout.write(f"Client: {tools.source}\n")
    stdout.write("-------------------------------------\n")

    def psql(
        url: PostgresUrl,
        tail: Sequence[str],
        *,
        env: dict[str, str],
        database: str = "postgres",
        input_text: str | None = None,
    ) -> CommandResult:
        return runtime.run_command(
            _psql_argv(tools, url, tail, database=database),
            env=env,
            input_text=input_text,
        )

    def new_dump_file() -> str:
        handle, path = tempfile.mkstemp(prefix="dbt_pg_sync_", suffix=".dump")
        os.close(handle)
        return path

    def discard(path: str) -> None:
        Path(path).unlink(missing_ok=True)

    def report_error(result: CommandResult) -> None:
        message = _first_error_line(result.stderr)
        if message:
            stdout.write(f"   Error: {message}\n")

    def report_dump_backup(ok: bool, error_text: str, dump_file: str) -> None:
        if ok:
            stdout.write("✅ Dump backup restored successfully\n")
            discard(dump_file)
        else:
            stdout.write(
                "⚠️  Failed to restore dump backup. Backup file is still"
                f" available: {dump_file}\n"
            )
            message = _first_error_line(error_text)
            if message:
                stdout.write(f"   Error: {message}\n")

    def unbind() -> None:
        runtime.signals.bind(None)

    def restore_dump_backup(database: str, backup_file: str) -> tuple[bool, str]:
        """Best effort: wipe the public schema, then pg_restore the file."""
        cleanup = psql(tgt, ["-c", _SCHEMA_SQL], env=tgt_env, database=database)
        if cleanup.returncode != 0:
            return False, cleanup.stderr
        restore = runtime.run_command(
            [tools.pg_restore]
            + (["--verbose"] if verbose else [])
            + [
                "-h",
                tgt.host,
                "-p",
                tgt.port,
                "-U",
                tgt.user,
                "-d",
                database,
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-acl",
                backup_file,
            ],
            env=tgt_env,
        )
        return restore.returncode == 0, cleanup.stderr + restore.stderr

    def rename_two_way(first: str, second: str) -> CommandResult:
        return runtime.run_command(
            _psql_argv(
                tools,
                tgt,
                [
                    "-c",
                    f"ALTER DATABASE {quote_identifier(first)}"
                    f" RENAME TO {quote_identifier(second)};",
                ],
            ),
            env=tgt_env,
        )

    def drop_database(name: str) -> None:
        """Best effort drop; connections keep working on failure."""
        psql(tgt, ["-c", f"DROP DATABASE IF EXISTS {quote_identifier(name)};"], env=tgt_env)

    def report_backup_database(backup_db: str, renamed: CommandResult) -> None:
        if renamed.returncode == 0:
            stdout.write("✅ Backup database restored successfully\n")
        else:
            stdout.write(
                "⚠️  Failed to restore backup. Original database"
                f" is available as '{backup_db}'\n"
            )
            report_error(renamed)

    def interrupt_restore_backup_database(backup_db: str) -> Callable[[], int]:
        def handler() -> int:
            stdout.write("\n⚠️  PostgreSQL sync interrupted\n\n")
            stdout.write(f"🔄 Restoring backup database '{backup_db}'...\n")
            drop_database(tgt_db_name)
            report_backup_database(backup_db, rename_two_way(backup_db, tgt_db_name))
            unbind()
            return 130

        return handler

    def interrupt_restore_dump_file(backup_file: str) -> Callable[[], int]:
        def handler() -> int:
            stdout.write("\n⚠️  PostgreSQL sync interrupted\n\n")
            stdout.write("🔄 Restoring dump backup...\n")
            ok, error_text = restore_dump_backup(tgt_db_name, backup_file)
            report_dump_backup(ok, error_text, backup_file)
            unbind()
            return 130

        return handler

    def backup_existing_database() -> str | None:
        """Rename the target into a `_backup_*` copy; None means fatal."""
        exists = psql(
            tgt,
            [
                "-t",
                "-c",
                "SELECT 1 FROM pg_database"
                f" WHERE datname={quote_literal(tgt_db_name)};",
            ],
            env=tgt_env,
        )
        if exists.stdout.strip() != "1":
            return ""
        backup_db = f"{tgt_db_name}_backup_{runtime.now().strftime('%Y%m%d_%H%M%S')}"
        stdout.write(
            f"💾 Backing up existing database '{tgt_db_name}' to '{backup_db}'...\n"
        )
        psql(
            tgt,
            [
                "-t",
                "-c",
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                f" WHERE datname={quote_literal(tgt_db_name)}"
                " AND pid <> pg_backend_pid();",
            ],
            env=tgt_env,
        )
        renamed = rename_two_way(tgt_db_name, backup_db)
        if renamed.returncode != 0:
            stdout.write(f"❌ Failed to backup existing database '{tgt_db_name}'\n")
            report_error(renamed)
            unbind()
            return None
        # The target is now safely preserved; bind interrupt recovery.
        runtime.signals.bind(interrupt_restore_backup_database(backup_db))
        return backup_db

    def create_report(backup_db: str) -> int:
        stdout.write(f"🆕 Creating fresh target database '{tgt_db_name}'...\n")
        created = runtime.run_command(
            _psql_argv(
                tools, tgt, ["-c", f"CREATE DATABASE {quote_identifier(tgt_db_name)};"]
            ),
            env=tgt_env,
        )
        if created.returncode != 0:
            stdout.write(f"❌ Failed to create database '{tgt_db_name}'\n")
            report_error(created)
            if backup_db:
                stdout.write("🔄 Attempting to restore backup...\n")
                report_backup_database(backup_db, rename_two_way(backup_db, tgt_db_name))
            unbind()
            return 1
        return 0

    def backup_target_for_schema_cleanup() -> str | None:
        """Dump the target first; Supabase/postgres targets cannot rename."""
        stdout.write(
            f"💾 Backing up target database '{tgt_db_name}' before schema cleanup...\n"
        )
        backup_file = new_dump_file()
        backed = runtime.run_command(
            [tools.pg_dump]
            + (["-v"] if verbose else [])
            + [
                "-h",
                tgt.host,
                "-p",
                tgt.port,
                "-U",
                tgt.user,
                "-d",
                tgt_db_name,
                "-Fc",
                "--no-owner",
                "--no-acl",
                "-f",
                backup_file,
            ],
            env=tgt_env,
        )
        if backed.returncode != 0:
            stdout.write("❌ Failed to backup target database before cleanup\n")
            report_error(backed)
            discard(backup_file)
            unbind()
            return None
        runtime.signals.bind(interrupt_restore_dump_file(backup_file))
        stdout.write("✅ Backup file created\n")
        stdout.write(
            "🧹 Cleaning existing objects in target database (Supabase/postgres)...\n"
        )
        cleaned = runtime.run_command(
            _psql_argv(tools, tgt, ["-c", _SCHEMA_SQL], database=tgt_db_name),
            env=tgt_env,
        )
        if cleaned.returncode != 0:
            stdout.write("❌ Failed to clean target database before restore\n")
            report_error(cleaned)
            stdout.write("🔄 Restoring dump backup...\n")
            ok, error_text = restore_dump_backup(tgt_db_name, backup_file)
            report_dump_backup(ok, error_text, backup_file)
            unbind()
            return None
        return backup_file

    def restore_new_database() -> int:
        nonlocal restore_error
        stdout.write("🔍 Detecting database versions...\n")
        source_version = _major_version(
            psql(src, ["-t", "-c", _VERSION_QUERY], env=src_env).stdout
        )
        target_version = _major_version(
            psql(tgt, ["-t", "-c", _VERSION_QUERY], env=tgt_env).stdout
        )
        if source_version:
            stdout.write(f"   Source version: PostgreSQL {source_version}\n")
        if target_version:
            stdout.write(f"   Target version: PostgreSQL {target_version}\n")
        # The shell port showed a dots spinner during the dump and restore;
        # the Runtime surface has no spinner hook, so progress stays
        # invisible in captured (non-stream) mode.
        dump_argv = (
            [tools.pg_dump]
            + (["-v"] if verbose else [])
            + ["-h", src.host, "-p", src.port, "-U", src.user, "-d", source.database]
        )
        restore_error_inner = ""
        try:
            if target_version and int(target_version) <= 16:
                dump = runtime.run_command(
                    dump_argv + ["--no-owner", "--no-acl", "--clean", "--if-exists"],
                    env=src_env,
                )
                if dump.returncode != 0:
                    return dump.returncode or 1
                lines = [
                    line
                    for line in dump.stdout.splitlines()
                    if "transaction_timeout" not in line
                ]
                sql = "\n".join(lines) + "\n" if lines else ""
                restored = runtime.run_command(
                    [tools.psql, "-h", tgt.host, "-p", tgt.port, "-U", tgt.user]
                    + ["-d", tgt_db_name]
                    + ([] if verbose else ["-q"]),
                    env=tgt_env,
                    input_text=sql,
                )
                restore_error_inner = restored.stderr
                if restored.returncode != 0:
                    return restored.returncode or 1
            else:
                dump_file = new_dump_file()
                try:
                    dump = runtime.run_command(
                        dump_argv + ["-Fc", "-f", dump_file], env=src_env
                    )
                    if dump.returncode != 0:
                        return dump.returncode or 1
                    restored = runtime.run_command(
                        [tools.pg_restore]
                        + (["--verbose"] if verbose else [])
                        + [
                            "-h",
                            tgt.host,
                            "-p",
                            tgt.port,
                            "-U",
                            tgt.user,
                            "-d",
                            tgt_db_name,
                            "--clean",
                            "--if-exists",
                            "--no-owner",
                            "--no-acl",
                            dump_file,
                        ],
                        env=tgt_env,
                    )
                    restore_error_inner = restored.stderr
                    if restored.returncode != 0:
                        return restored.returncode or 1
                finally:
                    discard(dump_file)
            return 0
        finally:
            restore_error = restore_error_inner

    def fail_sync() -> int:
        stdout.write(f"❌ Sync failed for '{source.database}'\n")
        if backup_file:
            stdout.write("\n🔄 Restoring dump backup...\n")
            ok, error_text = restore_dump_backup(tgt_db_name, backup_file)
            report_dump_backup(ok, error_text, backup_file)
        elif backup_db:
            stdout.write("\n🔄 Restoring backup database...\n")
            drop_database(tgt_db_name)
            report_backup_database(backup_db, rename_two_way(backup_db, tgt_db_name))
        unbind()
        return 1

    def finish_sync() -> int:
        counted = psql(
            tgt, ["-t", "-c", _TABLE_COUNT_QUERY], env=tgt_env, database=tgt_db_name
        )
        table_count = counted.stdout.strip()
        has_error = "error" in restore_error.lower() or "failed" in restore_error.lower()
        if table_count in ("", "0") or has_error:
            stdout.write(f"⚠️  '{source.database}' sync completed with warnings\n")
            if table_count in ("", "0"):
                stdout.write("   ⚠️  WARNING: No tables found in restored database!\n")
            else:
                stdout.write(f"   Tables: {table_count}\n")
            message = _first_error_line(restore_error)
            if message:
                stdout.write(f"   Error: {message}\n")
            if backup_db:
                stdout.write(f"\n💾 Backup database '{backup_db}' kept for safety\n")
            if backup_file:
                stdout.write(f"\n💾 Backup file kept for safety: {backup_file}\n")
            unbind()
            return 1
        stdout.write(f"✅ '{source.database}' synced successfully to '{tgt_db_name}'\n")
        stdout.write(f"   Tables: {table_count}\n")
        if backup_file:
            discard(backup_file)
        if backup_db:
            stdout.write(f"🗑️  Removing backup database '{backup_db}'...\n")
            runtime.run_command(
                _psql_argv(
                    tools,
                    tgt,
                    ["-c", f"DROP DATABASE IF EXISTS {quote_identifier(backup_db)};"],
                ),
                env=tgt_env,
            )
        unbind()
        return 0

    backup_db = ""
    backup_file = ""
    restore_error = ""
    if not is_supabase and tgt_db_name != "postgres":
        backup_db = backup_existing_database()
        if backup_db is None:
            return 1
        result = create_report(backup_db)
        if result != 0:
            return result
    else:
        backup_file = backup_target_for_schema_cleanup()
        if backup_file is None:
            return 1
    if restore_new_database() != 0:
        return fail_sync()
    return finish_sync()
