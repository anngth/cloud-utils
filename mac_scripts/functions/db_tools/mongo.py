"""Mongo host parsing, client discovery, status, and backup cleanup.

Every mongosh/mongo call is an argv list. A database name never enters
the `--eval` script text: it travels only in the child's DBT_MONGO_DB
environment variable, which the fixed scripts read via
`process.env.DBT_MONGO_DB`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from .models import MongoSource, MongoTarget
from .runtime import Runtime

MONGO_EXISTS_SCRIPT = (
    "db.getSiblingDB(process.env.DBT_MONGO_DB).getCollectionNames().length > 0"
)
MONGO_DROP_SCRIPT = "db.getSiblingDB(process.env.DBT_MONGO_DB).dropDatabase()"
MONGO_LIST_SCRIPT = (
    "db.getMongo().getDBNames().filter((name) =>"
    ' /_backup_[0-9]{8}_[0-9]{6}$/.test(name)).sort().join("\\n")'
)
MONGO_PING_SCRIPT = "db.adminCommand('ping')"

_CLIENT_NAMES = ("mongosh", "mongo")

_TOOLS_NOT_FOUND = (
    "❌ Error: MongoDB client tools not found\n"
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


def parse_mongo_source_row(row: str) -> MongoSource | None:
    """Parse `name|uri|db1,db2,db3`; None when one of the three is missing."""
    name, sep, rest = row.partition("|")
    if not sep:
        return None
    uri, sep, databases = rest.partition("|")
    if not sep or not name or not uri or not databases:
        return None
    return MongoSource(name=name, uri=uri, databases=databases)


def parse_mongo_target_row(row: str) -> MongoTarget | None:
    """Parse `name|uri`; None when one of the two is missing."""
    name, sep, uri = row.partition("|")
    if not sep or not name or not uri:
        return None
    return MongoTarget(name=name, uri=uri)


def _find_client(runtime: Runtime) -> str | None:
    """`mongosh` wins over the legacy `mongo` shell."""
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
    client: str, uri: str, database: str | None, script: str
) -> tuple[list[str], dict[str, str]]:
    """Build one `--eval` invocation; the database rides in env only."""
    argv = [client, uri, "--quiet", "--eval", script]
    env = {} if database is None else {"DBT_MONGO_DB": database}
    return argv, env


def mongo_status(uri: str, *, runtime: Runtime) -> bool:
    """Ping the server with the fixed ping script; no client counts as up."""
    client = _find_client(runtime)
    if client is None:
        return True
    argv, env = mongo_eval_argv(client, uri, None, MONGO_PING_SCRIPT)
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
    if not backups:
        stdout.write("✅ No MongoDB backup databases found\n")
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
) -> bool:
    """The exists script answers on stdout; its last line decides.

    The shell port piped the probe through `tail -1 | grep -q '^true$'`, so a
    failed or silent probe reads as "no collections" the same way here.
    """
    argv, env = mongo_eval_argv(tools.client, uri, database, MONGO_EXISTS_SCRIPT)
    result = runtime.run_command(argv, env=env)
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
) -> int:
    """Dump `database` from the source into the target, keeping the old one.

    The backup pipe copies the existing target database into
    `{database}_backup_YYYYMMDD_HHMMSS`; the sync pipe then dumps the source
    database onto the target with `--drop`, and a failure restores the backup
    first. The signal handler is bound only once the backup database exists on
    the target: a database with no collections has no backup, so an interrupt
    during the sync pipe has nothing to restore. Sync progress and errors go
    to stdout; missing tools and connection failures go to stderr.
    """
    if not mongo_status(source_uri, runtime=runtime):
        stderr.write("❌ Cannot proceed - source connection failed\n")
        return 1
    if not mongo_status(target_uri, runtime=runtime):
        stderr.write("❌ Cannot proceed - target connection failed\n")
        return 1
    tools = find_mongo_tools(runtime)
    if tools is None:
        stderr.write(_TOOLS_NOT_FOUND)
        return 1

    backup_db = ""
    bound = False
    if _database_exists(tools, target_uri, database, runtime):
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
            return 1
        stdout.write(f"✅ Backup created: {backup_db}\n")

        def interrupt() -> int:
            stdout.write("\n⚠️  MongoDB sync interrupted\n")
            restore_backup()
            runtime.signals.bind(None)
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
    if backup_db:
        stdout.write(f"\n🔄 Restoring backup database '{backup_db}'...\n")
        restore_backup()
    return 1
