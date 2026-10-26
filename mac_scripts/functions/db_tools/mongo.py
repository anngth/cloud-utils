"""Mongo host parsing, client discovery, status, and backup cleanup.

Every mongosh call is an argv list. A database name never enters the
`--eval` script text: it travels only in the child's DBT_MONGO_DB
environment variable, which the fixed scripts read via
`process.env.DBT_MONGO_DB`, a Node API: only `mongosh` is supported.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TextIO

from shared.process import CommandResult

from .models import MongoTarget
from .runtime import Runtime, run_with_interrupts

MONGO_EXISTS_SCRIPT = (
    "db.getSiblingDB(process.env.DBT_MONGO_DB).getCollectionNames().length > 0"
)
MONGO_DROP_SCRIPT = "db.getSiblingDB(process.env.DBT_MONGO_DB).dropDatabase()"
MONGO_LIST_SCRIPT = (
    "db.getMongo().getDBNames().filter((name) =>"
    ' /_backup_[0-9]{8}_[0-9]{6}$/.test(name)).sort().join("\\n")'
)
MONGO_PING_SCRIPT = "db.adminCommand('ping')"

# Cleanup drops only names of this shape, whatever else mongosh prints.
_BACKUP_NAME = re.compile(r"^[^\s/\\.\"$*<>:|?]+_backup_[0-9]{8}_[0-9]{6}$")

_CLIENT_NAMES = ("mongosh",)
_SERVER_SELECTION_TIMEOUT_MS = 5000

_TOOLS_NOT_FOUND = (
    "❌ Error: MongoDB client tools not found (dbt needs mongosh; the legacy"
    " mongo shell is not supported)\n"
    "   Install mongosh: brew install mongosh\n"
    "   Install mongodump and mongorestore: brew install database-tools\n"
    "   Then ensure mongosh, mongodump, and mongorestore are available on PATH\n"
)


@dataclass(frozen=True, slots=True)
class MongoTools:
    client: str
    mongodump: str
    mongorestore: str


def mongo_host(uri: str) -> str:
    """Return the host part of a Mongo URI.

    The value is taken after the `@`, before the first `/`, `?`, or `:`.
    The zsh helper's `cut -d@ -f2` yields "" for credential-less URIs;
    dbt keeps the remaining host segment instead.
    """
    for prefix in ("mongodb+srv://", "mongodb://"):
        if uri.startswith(prefix):
            uri = uri[len(prefix) :]
            break
    if "@" in uri:
        uri = uri.partition("@")[2]
    return uri.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]


def mongo_port(uri: str) -> int:
    """Port of the first host in a Mongo URI; 27017 when unspecified."""
    for prefix in ("mongodb+srv://", "mongodb://"):
        if uri.startswith(prefix):
            uri = uri[len(prefix) :]
            break
    if "@" in uri:
        uri = uri.rpartition("@")[2]
    host = uri.split("/", 1)[0].split("?", 1)[0].split(",", 1)[0]
    _name, _sep, port = host.rpartition(":")
    return int(port) if port.isdigit() else 27017


def with_server_selection_timeout(
    uri: str, timeout_ms: int = _SERVER_SELECTION_TIMEOUT_MS
) -> str:
    """Add a server-selection timeout (5s by default) unless the URI sets one."""
    if "serverselectiontimeoutms=" in uri.lower():
        return uri
    timeout = f"serverSelectionTimeoutMS={timeout_ms}"
    scheme, sep, rest = uri.partition("://")
    if not sep:
        return uri
    credentials, at, endpoint = rest.rpartition("@")
    if "?" in endpoint:
        base, query = endpoint.split("?", 1)
        query = f"{query}&{timeout}" if query else timeout
        endpoint = f"{base}?{query}"
    else:
        slash = "" if "/" in endpoint else "/"
        endpoint = f"{endpoint}{slash}?{timeout}"
    return f"{scheme}://{credentials}{at}{endpoint}"


def mongo_uri_database(uri: str) -> str:
    """The database named by the URI path (`.../name?...`), or `""`."""
    for prefix in ("mongodb+srv://", "mongodb://"):
        if uri.startswith(prefix):
            uri = uri[len(prefix) :]
            break
    if "@" in uri:
        uri = uri.partition("@")[2]
    _host, sep, rest = uri.partition("/")
    if not sep:
        return ""
    return rest.split("?", 1)[0].split("/", 1)[0]


def _find_client(runtime: Runtime) -> str | None:
    for name in _CLIENT_NAMES:
        path = runtime.which(name)
        if path is not None:
            return path
    return None


def find_mongo_tools(runtime: Runtime) -> MongoTools | None:
    """All three of the client, mongodump, and mongorestore must exist."""
    client = _find_client(runtime)
    if client is None:
        return None
    mongodump = runtime.which("mongodump")
    mongorestore = runtime.which("mongorestore")
    if mongodump is None or mongorestore is None:
        return None
    return MongoTools(client=client, mongodump=mongodump, mongorestore=mongorestore)


def mongo_eval_argv(
    client: str,
    uri: str,
    database: str | None,
    script: str,
    *,
    timeout_ms: int = _SERVER_SELECTION_TIMEOUT_MS,
) -> tuple[list[str], dict[str, str]]:
    """Build one `--eval` invocation; the database rides in env only."""
    argv = [client, with_server_selection_timeout(uri, timeout_ms), "--quiet", "--eval", script]
    env = {} if database is None else {"DBT_MONGO_DB": database}
    return argv, env


def mongo_status(uri: str, *, runtime: Runtime, timeout_ms: int | None = None) -> bool | None:
    """Ping with the fixed script; None when mongosh is not installed.

    `timeout_ms` replaces the default server-selection timeout for a quick probe.
    """
    client = _find_client(runtime)
    if client is None:
        return None
    argv, env = mongo_eval_argv(
        client, uri, None, MONGO_PING_SCRIPT,
        timeout_ms=timeout_ms if timeout_ms is not None else _SERVER_SELECTION_TIMEOUT_MS,
    )
    return runtime.run_command(argv, env=env).returncode == 0


def clear_mongo_backups(
    targets: Sequence[MongoTarget],
    *,
    dry_run: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Drop `_backup_YYYYMMDD_HHMMSS` databases on every Mongo target.

    The DB list script already matches the backup suffix, so no source
    filter is applied. Each target keeps its drop script simple: the
    database name travels only in DBT_MONGO_DB. A failed target does not
    stop the remaining ones; the command exits 1 when any target failed.
    """
    tools = find_mongo_tools(runtime)
    if tools is None:
        stderr.write(_TOOLS_NOT_FOUND)
        return 1
    failed = False
    for target in targets:
        stdout.write(f"Target: {target.name}\n")
        if not _clear_target(target, tools, dry_run, runtime, stdout, stderr):
            failed = True
        stdout.write("\n")
    return 1 if failed else 0


def _clear_target(
    target: MongoTarget,
    tools: MongoTools,
    dry_run: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> bool:
    list_argv, list_env = mongo_eval_argv(
        tools.client, target.uri, None, MONGO_LIST_SCRIPT
    )
    listed = runtime.run_command(list_argv, env=list_env)
    if listed.returncode != 0:
        stderr.write("❌ Failed to list MongoDB backup databases\n")
        message = _first_error_line(listed.stderr)
        if message:
            stderr.write(f"   Error: {message}\n")
        return False

    backups = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    skipped = [line for line in backups if _BACKUP_NAME.match(line) is None]
    backups = [line for line in backups if _BACKUP_NAME.match(line) is not None]
    stderr.writelines(f"ignored non-backup output line: {line}\n" for line in skipped)
    if not backups:
        stdout.write("✅ No MongoDB backup databases found\n")
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
        drop_argv, drop_env = mongo_eval_argv(
            tools.client, target.uri, backup, MONGO_DROP_SCRIPT
        )
        drop_result = runtime.run_command(drop_argv, env=drop_env)
        if drop_result.returncode == 0:
            stdout.write(f"✅ Dropped: {backup}\n")
            dropped += 1
        else:
            stdout.write(f"❌ Failed to drop: {backup}\n")
            failed_drops += 1

    stdout.write("\n")
    stdout.write(f"Summary: dropped {dropped}, failed {failed_drops}\n")
    return failed_drops == 0


def _first_error_line(stderr_text: str) -> str:
    for line in stderr_text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _dump_argv(tools: MongoTools, uri: str, database: str) -> list[str]:
    """One `mongodump --uri=... --db=<database> --archive --gzip` invocation."""
    return [tools.mongodump, f"--uri={uri}", f"--db={database}", "--archive", "--gzip"]


def _restore_argv(
    tools: MongoTools, uri: str, *, drop: bool, ns_from: str, ns_to: str
) -> list[str]:
    """One `mongorestore` invocation; namespaces come from the caller."""
    argv = [tools.mongorestore, f"--uri={uri}"]
    if drop:
        argv.append("--drop")
    argv += ["--archive", "--gzip", f"--nsFrom={ns_from}.*", f"--nsTo={ns_to}.*"]
    return argv


def _database_exists(
    tools: MongoTools, uri: str, database: str, runtime: Runtime
) -> bool | None:
    """The exists script answers on stdout; its last line decides.

    Returns None when the probe itself fails: the sync must fail closed
    instead of reading a failed probe as "no collections", because that
    reading would let a `--drop` sync destroy target data with no backup.
    (The zsh port's `tail -1 | grep -q '^true$'` treated a failed probe
    as "no collections"; that behavior is deliberately not kept.)
    """
    argv, env = mongo_eval_argv(tools.client, uri, database, MONGO_EXISTS_SCRIPT)
    result = runtime.run_command(argv, env=env)
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == "true"


def perform_mongo_sync(
    source_uri: str,
    database: str,
    target_uri: str,
    *,
    verbose: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
    hint: Callable[[str, int], str] | None = None,
) -> int:
    """Dump `database` from the source into the target, keeping the old one.

    The backup pipe copies the existing target database into
    `{database}_backup_YYYYMMDD_HHMMSS`; the sync pipe then dumps the source
    database onto the target with `--drop`, and a failure restores the backup
    first. The rollback handler is bound only once the backup exists and
    runs only after an interrupted pipe is dead. A failed exists-probe
    aborts before any pipe runs.
    """
    for label, uri in (("source", source_uri), ("target", target_uri)):
        named = mongo_uri_database(uri)
        if named and named != database:
            stderr.write(
                f"❌ Error: {label} URI names database '{named}' but the sync database is"
                f" '{database}'; mongodump rejects that combination. Remove /{named} from"
                f" the URI (use ?authSource={named} if it was for authentication)\n"
            )
            return 1
    for label, uri in (("source", source_uri), ("target", target_uri)):
        if mongo_status(uri, runtime=runtime) is False:
            stderr.write(f"❌ Cannot proceed - {label} connection failed\n")
            text = hint(mongo_host(uri), mongo_port(uri)) if hint is not None else ""
            if text:
                stderr.write(f"   {text}\n")
            return 1
    tools = find_mongo_tools(runtime)
    if tools is None:
        stderr.write(_TOOLS_NOT_FOUND)
        return 1
    return run_with_interrupts(
        runtime,
        lambda: _sync(
            tools,
            source_uri,
            database,
            target_uri,
            verbose=verbose,
            runtime=runtime,
            stdout=stdout,
            stderr=stderr,
        ),
    )


def _sync(
    tools: MongoTools,
    source_uri: str,
    database: str,
    target_uri: str,
    *,
    verbose: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    backup_db = ""
    bound = False

    def report_pipe_failure(result: CommandResult) -> None:
        message = _first_error_line(result.stderr)
        if message:
            stdout.write(f"   Error: {message}\n")
        elif not verbose:
            stdout.write("   Run again with --verbose for the full output\n")
    exists = _database_exists(tools, target_uri, database, runtime)
    if exists is None:
        # Fail closed: a failed probe must not read as "no backup needed".
        stderr.write(
            f"❌ MongoDB probe failed on host {mongo_host(target_uri)};"
            " not attempting a sync without a backup probe\n"
        )
        return 1
    if exists:
        backup_db = f"{database}_backup_{runtime.now().strftime('%Y%m%d_%H%M%S')}"
        stdout.write(f"💾 Backing up target database '{database}' to '{backup_db}'...\n")
        backed_up = runtime.run_pipe(
            _dump_argv(tools, target_uri, database),
            _restore_argv(tools, target_uri, drop=False, ns_from=database, ns_to=backup_db),
            stream=verbose,
            stdout=stdout,
            stderr=stderr,
        )
        if backed_up.returncode != 0:
            stdout.write("❌ Failed to backup target database\n")
            report_pipe_failure(backed_up)
            return 1
        stdout.write(f"✅ Backup created: {backup_db}\n")

        def interrupt() -> int:
            stdout.write("\n⚠️  MongoDB sync interrupted\n")
            restore_backup()
            return 130

        # The target content is now safely preserved; bind interrupt recovery.
        runtime.signals.bind(interrupt)
        bound = True
    else:
        stdout.write(
            f"ℹ️  Target database '{database}' does not exist; skipping backup\n"
        )

    def restore_backup() -> bool:
        """Restore the backup onto the target; a failed restore keeps it."""
        restored = runtime.run_pipe(
            _dump_argv(tools, target_uri, backup_db),
            _restore_argv(
                tools, target_uri, drop=True, ns_from=backup_db, ns_to=database
            ),
            stream=verbose,
            stdout=stdout,
            stderr=stderr,
        )
        if restored.returncode != 0:
            stdout.write(
                "⚠️  Failed to restore backup. Backup database is still"
                f" available as '{backup_db}'\n"
            )
            report_pipe_failure(restored)
            return False
        stdout.write("✅ Backup restored successfully\n")
        drop_backup(fail_line=f"⚠️  Backup restored, but failed to remove '{backup_db}'")
        return True

    def drop_backup(*, fail_line: str) -> None:
        drop_argv, drop_env = mongo_eval_argv(
            tools.client, target_uri, backup_db, MONGO_DROP_SCRIPT
        )
        dropped = runtime.run_command(drop_argv, env=drop_env)
        if dropped.returncode == 0:
            stdout.write("✅ Backup removed\n")
        else:
            stdout.write(
                f"{fail_line}\n   You can remove it manually after verifying the sync\n"
            )

    if not verbose:
        # The shell port showed a dots spinner here ("sync_dots_start"); the
        # Runtime surface has no spinner hook, so this line stands in for it
        # and per-stream progress stays invisible in captured (non-verbose)
        # mode. Verbose streams the dump|restore output instead.
        stdout.write("📤 Syncing database\n")
    synced = runtime.run_pipe(
        _dump_argv(tools, source_uri, database),
        _restore_argv(tools, target_uri, drop=True, ns_from=database, ns_to=database),
        stream=verbose,
        stdout=stdout,
        stderr=stderr,
    )
    # The sync pipe is over; no further interrupt can restore anything.
    if bound:
        runtime.signals.bind(None)

    if synced.returncode == 0:
        stdout.write("✅ MongoDB synced successfully\n")
        if backup_db:
            stdout.write(f"🗑️  Removing backup database '{backup_db}'...\n")
            drop_backup(fail_line=f"⚠️  Failed to remove backup database '{backup_db}'")
        return 0

    stdout.write("❌ MongoDB sync failed\n")
    report_pipe_failure(synced)
    if backup_db:
        stdout.write(f"\n🔄 Restoring backup database '{backup_db}'...\n")
        restore_backup()
    return 1
