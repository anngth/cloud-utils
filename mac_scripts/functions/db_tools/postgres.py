"""Postgres URL parsing, client discovery, status, sync, and backup cleanup.

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
from .runtime import Runtime, run_with_interrupts

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
_TERMINATE_SQL = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
    " WHERE datname = {literal} AND pid <> pg_backend_pid();"
)

# Servers up to 16 reject this GUC; only statements go, never COPY data.
_TRANSACTION_TIMEOUT_LINE = re.compile(
    rb"^\s*(?:SET|ALTER\s+(?:DATABASE|ROLE)\b[^\n]*?\bSET)\s+transaction_timeout\b"
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
    """Split `postgresql://user:pass@host:port/db?query` into its parts.

    The user ends at the first colon, the password keeps remaining
    colons, and the host starts after the last `@`. A missing database
    is `""`. `sslmode=require` anywhere in the URL sets `sslmode_require`.
    """
    rest = url.removeprefix("postgresql://")
    auth_part, sep, host_part = rest.rpartition("@")
    if not sep:
        auth_part, host_part = "", rest
    user, _, password = auth_part.partition(":")
    host_part, _, _query = host_part.partition("?")
    host_part, _, path_part = host_part.partition("/")
    host, _, port = host_part.partition(":")
    database = path_part.split("/", 1)[0]
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
    psql, pg_dump, pg_restore = (runtime.which(name) for name in _CLIENTS)
    if psql is not None and pg_dump is not None and pg_restore is not None:
        return PostgresTools(psql=psql, pg_dump=pg_dump, pg_restore=pg_restore, source="PATH")
    for prefix, source in (
        (_LIBPQ_BIN, "Homebrew libpq"),
        (_PG18_BIN, "Homebrew postgresql@18"),
    ):
        paths = [f"{prefix}/{name}" for name in _CLIENTS]
        if all(runtime.path_exists(path) for path in paths):
            return PostgresTools(
                psql=paths[0], pg_dump=paths[1], pg_restore=paths[2], source=source
            )
    return None


def postgres_status(url: str, *, runtime: Runtime) -> bool | None:
    """`SELECT 1` on the `postgres` database; None when no client is installed."""
    tools = find_postgres_tools(runtime)
    if tools is None:
        return None
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
        if not _clear_target(target, tools, source_database, dry_run, runtime, stdout, stderr):
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
        prefix_literal = quote_literal(f"{source_database}_backup_")
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
    stdout.writelines(f"  - {backup}\n" for backup in backups)
    stdout.write("\n")

    if dry_run:
        stdout.write("ℹ️  Dry run only; no databases were dropped\n")
        return True

    dropped = 0
    failed_drops = 0
    for backup in backups:
        terminate = _TERMINATE_SQL.format(literal=quote_literal(backup))
        runtime.run_command(_psql_argv(tools, url, ["-t", "-c", terminate]), env=env)
        drop_result = runtime.run_command(
            _psql_argv(tools, url, ["-c", f"DROP DATABASE IF EXISTS {quote_identifier(backup)};"]),
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


def _connection_argv(url: PostgresUrl, database: str) -> list[str]:
    return ["-h", url.host, "-p", url.port, "-U", url.user, "-d", database]


def _child_env(url: PostgresUrl) -> dict[str, str]:
    """PGPASSWORD always, PGSSLMODE only for require."""
    env = {"PGPASSWORD": url.password}
    if url.sslmode_require:
        env["PGSSLMODE"] = "require"
    return env


def _first_error_line(stderr_text: str) -> str:
    for line in stderr_text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _error_lines(stderr_text: str, limit: int) -> list[str]:
    lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
    return lines[:limit]


def _major_version(version_text: str) -> str:
    """Return the client/server major number, `""` when unreadable."""
    match = re.search(r"PostgreSQL\s+(\d+)", version_text)
    return match.group(1) if match else ""


def _strip_transaction_timeout(source_path: str, destination_path: str) -> None:
    """Copy a plain-SQL dump without `transaction_timeout` statements (bytes, streamed)."""
    with open(source_path, "rb") as source, open(destination_path, "wb") as destination:
        for line in source:
            if _TRANSACTION_TIMEOUT_LINE.match(line) is None:
                destination.write(line)


def _new_dump_file() -> str:
    handle, path = tempfile.mkstemp(prefix="dbt_pg_sync_", suffix=".dump")
    os.close(handle)
    return path


def _discard(path: str) -> None:
    Path(path).unlink(missing_ok=True)


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

    The rollback handler is bound only once the target is preserved and
    runs only after an interrupted dump/restore is dead.
    """
    tools = find_postgres_tools(runtime)
    if tools is None:
        stderr.write(_TOOLS_NOT_FOUND)
        return 1
    if not parse_postgres_url(target.url).database:
        stderr.write(
            f"❌ Error: target '{target.name}' URL names no database;"
            " add /<database> to the URL in the secrets file\n"
        )
        return 1
    sync = _PostgresSync(
        source, target, tools, verbose=verbose, runtime=runtime, stdout=stdout, stderr=stderr
    )
    return run_with_interrupts(runtime, sync.run)


class _PostgresSync:
    """One sync run. Two strategies, picked by the target:

    * rename: the existing target database is renamed to a `_backup_*`
      copy and a fresh one is created (rolled back by renaming it back);
    * dump file: Supabase and `postgres` targets cannot be renamed, so
      they are dumped to a temp file and their public schema is wiped
      (rolled back by restoring that file).
    """

    def __init__(
        self,
        source: PostgresSource,
        target: PostgresTarget,
        tools: PostgresTools,
        *,
        verbose: bool,
        runtime: Runtime,
        stdout: TextIO,
        stderr: TextIO,
    ) -> None:
        self.source = source
        self.tools = tools
        self.verbose = verbose
        self.runtime = runtime
        self.stdout = stdout
        self.stderr = stderr
        self.src = parse_postgres_url(source.url)
        self.tgt = parse_postgres_url(target.url)
        self.src_env = _child_env(self.src)
        self.tgt_env = _child_env(self.tgt)
        self.uses_dump_file = (
            "supabase" in self.tgt.host.lower() or self.tgt.database == "postgres"
        )
        self.tgt_db_name = "postgres" if self.uses_dump_file else source.database
        self.backup_db = ""
        self.backup_file = ""
        self.restore_error = ""

    # --- orchestration ----------------------------------------------------------

    def run(self) -> int:
        out = self.stdout
        if self.uses_dump_file:
            out.write(
                "ℹ️  Target is Supabase or uses 'postgres' database"
                " - will restore to 'postgres'\n"
            )
        out.write("=====================================\n")
        out.write(f"PostgreSQL Sync: {self.source.database}\n")
        out.write(f"Source: {self.src.user}@{self.src.host}:{self.src.port}\n")
        out.write(f"Target: {self.tgt.user}@{self.tgt.host}:{self.tgt.port}\n")
        out.write(f"Client: {self.tools.source}\n")
        out.write("-------------------------------------\n")

        if self.uses_dump_file:
            if not self.backup_target_to_file():
                return 1
        elif not (self.backup_existing_database() and self.create_fresh_database()):
            return 1
        if self.restore_new_database() != 0:
            return self.fail_sync()
        return self.finish_sync()

    # --- process helpers --------------------------------------------------------

    def psql(
        self, url: PostgresUrl, tail: Sequence[str], *, database: str = "postgres"
    ) -> CommandResult:
        env = self.tgt_env if url is self.tgt else self.src_env
        return self.runtime.run_command(
            _psql_argv(self.tools, url, tail, database=database), env=env
        )

    def tgt_sql(self, sql: str, *, database: str = "postgres") -> CommandResult:
        return self.psql(self.tgt, ["-c", sql], database=database)

    def stream(self, argv: Sequence[str], **kwargs: object) -> CommandResult:
        """Dump/restore step: live under --verbose, captured otherwise."""
        return self.runtime.run_command(
            argv, stream=self.verbose, stdout=self.stdout, stderr=self.stderr, **kwargs
        )

    def pg_dump_argv(self, url: PostgresUrl, database: str) -> list[str]:
        verbose = ["-v"] if self.verbose else []
        return [self.tools.pg_dump, *verbose, *_connection_argv(url, database)]

    def pg_restore_argv(self, database: str, dump_file: str) -> list[str]:
        return [
            self.tools.pg_restore,
            *(["--verbose"] if self.verbose else []),
            *_connection_argv(self.tgt, database),
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            dump_file,
        ]

    # --- reporting --------------------------------------------------------------

    def report_error(self, result: CommandResult) -> None:
        message = _first_error_line(result.stderr)
        if message:
            self.stdout.write(f"   Error: {message}\n")

    def report_step_failure(self, step: str, result: CommandResult, limit: int) -> None:
        self.stdout.write(f"❌ {step} failed (exit code {result.returncode})\n")
        lines = _error_lines(result.stderr, limit)
        if lines:
            self.stdout.write(f"   {step} errors:\n")
            self.stdout.writelines(f"     {line}\n" for line in lines)
        if not self.verbose:
            self.stdout.write("   Run again with --verbose for the full output\n")

    def report_dump_backup(self, ok: bool, error_text: str) -> None:
        if ok:
            self.stdout.write("✅ Dump backup restored successfully\n")
            _discard(self.backup_file)
            return
        self.stdout.write(
            "⚠️  Failed to restore dump backup. Backup file is still"
            f" available: {self.backup_file}\n"
        )
        message = _first_error_line(error_text)
        if message:
            self.stdout.write(f"   Error: {message}\n")

    def report_backup_database(self, renamed: CommandResult) -> None:
        if renamed.returncode == 0:
            self.stdout.write("✅ Backup database restored successfully\n")
        else:
            self.stdout.write(
                "⚠️  Failed to restore backup. Original database"
                f" is available as '{self.backup_db}'\n"
            )
            self.report_error(renamed)

    # --- rollback primitives ----------------------------------------------------

    def rename(self, first: str, second: str) -> CommandResult:
        return self.tgt_sql(
            f"ALTER DATABASE {quote_identifier(first)} RENAME TO {quote_identifier(second)};"
        )

    def drop_database(self, name: str) -> CommandResult:
        return self.tgt_sql(f"DROP DATABASE IF EXISTS {quote_identifier(name)};")

    def restore_dump_backup(self) -> tuple[bool, str]:
        """Best effort: wipe the public schema, then pg_restore the file."""
        cleanup = self.tgt_sql(_SCHEMA_SQL, database=self.tgt_db_name)
        if cleanup.returncode != 0:
            return False, cleanup.stderr
        restore = self.stream(
            self.pg_restore_argv(self.tgt_db_name, self.backup_file), env=self.tgt_env
        )
        return restore.returncode == 0, cleanup.stderr + restore.stderr

    def rollback_to_backup_database(self) -> None:
        self.drop_database(self.tgt_db_name)
        self.report_backup_database(self.rename(self.backup_db, self.tgt_db_name))

    def rollback_to_dump_file(self) -> None:
        ok, error_text = self.restore_dump_backup()
        self.report_dump_backup(ok, error_text)

    def interrupt_handler(self, rollback: Callable[[], None], what: str) -> Callable[[], int]:
        def handler() -> int:
            self.stdout.write("\n⚠️  PostgreSQL sync interrupted\n\n")
            self.stdout.write(f"🔄 Restoring {what}...\n")
            rollback()
            return 130

        return handler

    # --- steps ------------------------------------------------------------------

    def backup_existing_database(self) -> bool:
        """Rename the target into a `_backup_*` copy; False means fatal."""
        name = self.tgt_db_name
        exists = self.psql(
            self.tgt,
            ["-t", "-c", f"SELECT 1 FROM pg_database WHERE datname={quote_literal(name)};"],
        )
        if exists.stdout.strip() != "1":
            return True
        self.backup_db = f"{name}_backup_{self.runtime.now().strftime('%Y%m%d_%H%M%S')}"
        self.stdout.write(
            f"💾 Backing up existing database '{name}' to '{self.backup_db}'...\n"
        )
        self.psql(self.tgt, ["-t", "-c", _TERMINATE_SQL.format(literal=quote_literal(name))])
        renamed = self.rename(name, self.backup_db)
        if renamed.returncode != 0:
            self.stdout.write(f"❌ Failed to backup existing database '{name}'\n")
            self.report_error(renamed)
            self.backup_db = ""
            return False
        self.runtime.signals.bind(
            self.interrupt_handler(
                self.rollback_to_backup_database, f"backup database '{self.backup_db}'"
            )
        )
        return True

    def create_fresh_database(self) -> bool:
        self.stdout.write(f"🆕 Creating fresh target database '{self.tgt_db_name}'...\n")
        created = self.tgt_sql(f"CREATE DATABASE {quote_identifier(self.tgt_db_name)};")
        if created.returncode == 0:
            return True
        self.stdout.write(f"❌ Failed to create database '{self.tgt_db_name}'\n")
        self.report_error(created)
        if self.backup_db:
            self.stdout.write("🔄 Attempting to restore backup...\n")
            self.report_backup_database(self.rename(self.backup_db, self.tgt_db_name))
        return False

    def backup_target_to_file(self) -> bool:
        """Dump the target first, then wipe its public schema."""
        name = self.tgt_db_name
        self.stdout.write(f"💾 Backing up target database '{name}' before schema cleanup...\n")
        self.backup_file = _new_dump_file()
        backed = self.stream(
            [
                *self.pg_dump_argv(self.tgt, name),
                "-Fc",
                "--no-owner",
                "--no-acl",
                "-f",
                self.backup_file,
            ],
            env=self.tgt_env,
        )
        if backed.returncode != 0:
            self.stdout.write("❌ Failed to backup target database before cleanup\n")
            self.report_error(backed)
            _discard(self.backup_file)
            self.backup_file = ""
            return False
        self.runtime.signals.bind(
            self.interrupt_handler(self.rollback_to_dump_file, "dump backup")
        )
        self.stdout.write("✅ Backup file created\n")
        self.stdout.write(
            "🧹 Cleaning existing objects in target database (Supabase/postgres)...\n"
        )
        cleaned = self.tgt_sql(_SCHEMA_SQL, database=name)
        if cleaned.returncode != 0:
            self.stdout.write("❌ Failed to clean target database before restore\n")
            self.report_error(cleaned)
            self.stdout.write("🔄 Restoring dump backup...\n")
            self.rollback_to_dump_file()
            return False
        return True

    def restore_new_database(self) -> int:
        self.stdout.write("🔍 Detecting database versions...\n")
        source_version = _major_version(
            self.psql(self.src, ["-t", "-c", _VERSION_QUERY]).stdout
        )
        target_version = _major_version(
            self.psql(self.tgt, ["-t", "-c", _VERSION_QUERY]).stdout
        )
        if source_version:
            self.stdout.write(f"   Source version: PostgreSQL {source_version}\n")
        if target_version:
            self.stdout.write(f"   Target version: PostgreSQL {target_version}\n")
        # No spinner: without --verbose the dump/restore is silent.
        if target_version and int(target_version) <= 16:
            return self.restore_plain_sql()
        return self.restore_custom_format()

    def restore_plain_sql(self) -> int:
        """Servers up to 16: plain SQL through temp files, minus `transaction_timeout`."""
        dump_path = _new_dump_file()
        sql_path = _new_dump_file()
        try:
            dump = self.stream(
                [
                    *self.pg_dump_argv(self.src, self.source.database),
                    "--no-owner",
                    "--no-acl",
                    "--clean",
                    "--if-exists",
                    "-f",
                    dump_path,
                ],
                env=self.src_env,
            )
            if dump.returncode != 0:
                self.report_step_failure("Dump", dump, limit=5)
                return dump.returncode or 1
            _strip_transaction_timeout(dump_path, sql_path)
            restored = self.stream(
                [self.tools.psql, *_connection_argv(self.tgt, self.tgt_db_name)]
                + ([] if self.verbose else ["-q"]),
                env=self.tgt_env,
                input_file=sql_path,
            )
            self.restore_error = restored.stderr
            if restored.returncode != 0:
                self.report_step_failure("Restore", restored, limit=10)
                return restored.returncode or 1
            return 0
        finally:
            _discard(dump_path)
            _discard(sql_path)

    def restore_custom_format(self) -> int:
        dump_file = _new_dump_file()
        try:
            dump = self.stream(
                [*self.pg_dump_argv(self.src, self.source.database), "-Fc", "-f", dump_file],
                env=self.src_env,
            )
            if dump.returncode != 0:
                self.report_step_failure("Dump", dump, limit=5)
                return dump.returncode or 1
            restored = self.stream(
                self.pg_restore_argv(self.tgt_db_name, dump_file), env=self.tgt_env
            )
            self.restore_error = restored.stderr
            if restored.returncode != 0:
                self.report_step_failure("Restore", restored, limit=10)
                return restored.returncode or 1
            return 0
        finally:
            _discard(dump_file)

    def fail_sync(self) -> int:
        self.stdout.write(f"❌ Sync failed for '{self.source.database}'\n")
        if self.backup_file:
            self.stdout.write("\n🔄 Restoring dump backup...\n")
            self.rollback_to_dump_file()
        elif self.backup_db:
            self.stdout.write("\n🔄 Restoring backup database...\n")
            self.rollback_to_backup_database()
        return 1

    def finish_sync(self) -> int:
        counted = self.psql(
            self.tgt, ["-t", "-c", _TABLE_COUNT_QUERY], database=self.tgt_db_name
        )
        table_count = counted.stdout.strip()
        lowered = self.restore_error.lower()
        has_error = "error" in lowered or "failed" in lowered
        if table_count in ("", "0") or has_error:
            self.stdout.write(f"⚠️  '{self.source.database}' sync completed with warnings\n")
            if table_count in ("", "0"):
                self.stdout.write("   ⚠️  WARNING: No tables found in restored database!\n")
            else:
                self.stdout.write(f"   Tables: {table_count}\n")
            message = _first_error_line(self.restore_error)
            if message:
                self.stdout.write(f"   Error: {message}\n")
            if self.backup_db:
                self.stdout.write(f"\n💾 Backup database '{self.backup_db}' kept for safety\n")
            if self.backup_file:
                self.stdout.write(f"\n💾 Backup file kept for safety: {self.backup_file}\n")
            return 1
        self.stdout.write(
            f"✅ '{self.source.database}' synced successfully to '{self.tgt_db_name}'\n"
        )
        self.stdout.write(f"   Tables: {table_count}\n")
        if self.backup_file:
            _discard(self.backup_file)
        if self.backup_db:
            self.stdout.write(f"🗑️  Removing backup database '{self.backup_db}'...\n")
            self.drop_database(self.backup_db)
        return 0
