"""dbt command line entry point."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import NoReturn, TextIO

from .config import ConfigError, load_secrets, resolve_secrets_file
from .models import MongoTarget, PostgresTarget, Secrets, VpsTunnel
from .mongo import clear_mongo_backups, mongo_host, mongo_status, perform_mongo_sync
from .postgres import (
    clear_postgres_backups,
    parse_postgres_url,
    perform_postgres_sync,
    postgres_status,
)
from .runtime import Interrupted, Runtime, default_runtime
from .secrets import SecretsError, redact
from .tunnels import (
    connect_tunnels,
    connection_hint,
    disconnect_tunnels,
    list_tunnels,
    test_tunnels,
)

USAGE = """\
usage: dbt <command> [args]

commands:
  help              show this help
  config [path]     show the secrets file, credentials masked; `path` prints only its location
  connect [filter]  open SSH tunnels for the secrets file
  disconnect [..]   close SSH tunnels started by dbt
  list              show tunnels and whether each is UP, DOWN or BUSY
  test [filter]     check the hosts used for tunnels
  sync <service>    sync Postgres or MongoDB databases
    sync list [--status]  show sync entries; --status tests the connections
    sync postgres -s <idx> -d <idx> [--verbose]
                      sync the source database onto a target
    sync postgres -c [-n] [-s idx] [-d idx]
                      delete Postgres backup databases; -n lists only
    sync mongodb -s <idx> -d <idx> [-db idx] [-v]
                      sync the source database onto a target
    sync -c [-n]       delete backup databases on every target:
                       Postgres first, then MongoDB; -n lists only
"""

_SYNC_LIST_USAGE = "usage: dbt sync list|ls [--status]\n"
_MAX_PROBES = 8
_LIST_HINT = "   Use 'dbt sync list' to list available configurations\n"


def run_cli(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    dependencies: Runtime | None = None,
) -> int:
    if not argv or argv[0] in ("help", "-h", "--help"):
        stdout.write(USAGE)
        return 0

    home_text = env.get("HOME", "")
    home = Path(home_text) if home_text else Path.home()
    try:
        resolved = resolve_secrets_file(
            env,
            home=home,
            repo_root=Path(__file__).resolve().parents[3],
            example=Path(__file__).resolve().parent / "secrets.example.toml",
        )
        stderr.writelines(f"ℹ️  {notice}\n" for notice in resolved.notices)
        secrets = load_secrets(resolved.path)
    except (SecretsError, ConfigError) as exc:
        stderr.write(f"{exc}\n")
        return 1

    runtime = dependencies if dependencies is not None else default_runtime()
    command, args = argv[0], list(argv[1:])
    tunnels = secrets.tunnels
    service = args[0] if args else None

    if command == "config":
        return _config_cli(args, secrets=secrets, path=resolved.path, stdout=stdout, stderr=stderr)

    if command in ("connect", "disconnect", "test"):
        if len(args) > 1:
            stderr.write(f"unexpected arguments: {' '.join(args[1:])}\n" + USAGE)
            return 1
        dispatch = {
            "connect": connect_tunnels,
            "disconnect": disconnect_tunnels,
            "test": test_tunnels,
        }[command]
        return dispatch(tunnels, service=service, runtime=runtime, stdout=stdout, stderr=stderr)

    if command in ("list", "ls"):
        if args == ["--status"]:
            stderr.write(
                "dbt list always shows tunnel status; "
                "for database connections use: dbt sync list --status\n"
            )
            return 1
        if args:
            stderr.write(f"unknown option: {args[0]}\n" + USAGE)
            return 1
        return list_tunnels(tunnels, runtime=runtime, stdout=stdout, stderr=stderr)

    if command == "sync":
        return _run_sync(
            args, secrets=secrets, runtime=runtime, stdout=stdout, stderr=stderr
        )

    stderr.write(f"unknown command: {argv[0]}\n" + USAGE)
    return 1


def _run_sync(
    args: list[str],
    *,
    secrets: Secrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if not args:
        stderr.write("unknown sync type\n" + USAGE)
        return 1
    subcommand, rest = args[0], args[1:]
    if subcommand in ("-c", "--clear-backups"):
        return _clear_backups_cli(
            rest, secrets=secrets, runtime=runtime, stdout=stdout, stderr=stderr
        )
    if subcommand in ("list", "ls"):
        status = False
        for arg in rest:
            if arg != "--status":
                stderr.write(f"unknown option: {arg}\n" + _SYNC_LIST_USAGE)
                return 1
            status = True
        return _print_sync_configs(
            secrets, status=status, runtime=runtime, stdout=stdout, stderr=stderr
        )
    if subcommand in ("postgres", "postgresql", "pg"):
        return _postgres_cli(
            rest, secrets=secrets, runtime=runtime, stdout=stdout, stderr=stderr
        )
    if subcommand == "mongodb":
        return _mongo_sync_cli(
            rest, secrets=secrets, runtime=runtime, stdout=stdout, stderr=stderr
        )
    stderr.write(f"unknown sync type: {subcommand}\n" + USAGE)
    return 1


# --- secrets rows ---------------------------------------------------------------


def _select[T](items: Sequence[T], text: str, kind: str, stderr: TextIO) -> T | None:
    """The entry at a 1-based index, or None after an error message."""
    try:
        index = int(text)
    except ValueError:
        index = 0
    if not 1 <= index <= len(items):
        stderr.write(f"❌ Error: Invalid {kind} index: {text}\n")
        stderr.write(f"   Available {kind.rsplit(' ', 1)[-1]}s: 1-{len(items)}\n")
        return None
    return items[index - 1]


def _status_icon(status: bool | None) -> str:
    if status is None:
        return "⚪ "
    return "🟢 " if status else "🔴 "


# --- sync list ------------------------------------------------------------------


def _print_sync_configs(
    secrets: Secrets,
    *,
    status: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Show sync entries with 1-based indexes; --status probes every entry at once."""
    sources = secrets.postgres_sources
    targets = secrets.postgres_targets
    mongo_sources = secrets.mongo_sources
    mongo_targets = secrets.mongo_targets

    probes: list[Callable[[], bool | None]] = []
    if status:
        probes += [
            partial(postgres_status, s.url, runtime=runtime, database=s.database)
            for s in sources
        ]
        probes += [partial(postgres_status, t.url, runtime=runtime) for t in targets]
        probes += [partial(mongo_status, s.uri, runtime=runtime) for s in mongo_sources]
        probes += [partial(mongo_status, t.uri, runtime=runtime) for t in mongo_targets]
    results = iter(_probe_all(probes))
    counts: dict[bool | None, int] = {True: 0, False: 0, None: 0}

    def mark() -> str:
        if not status:
            return ""
        result = next(results)
        counts[result] += 1
        return _status_icon(result)

    stdout.write(f"🐘 PostgreSQL Sources ({len(sources)}):\n")
    for index, source in enumerate(sources, start=1):
        host = parse_postgres_url(source.url).host
        stdout.write(f"  {mark()}{index}) {source.name} → {source.database} | {host}\n")
    if not sources:
        stdout.write("  (No PostgreSQL sources configured)\n")

    stdout.write(f"\n🎯 PostgreSQL Targets ({len(targets)}):\n")
    for index, target in enumerate(targets, start=1):
        url = parse_postgres_url(target.url)
        ssl = "require" if url.sslmode_require else "disable"
        stdout.write(f"  {mark()}{index}) {target.name} | {url.host} [SSL: {ssl}]\n")
    if not targets:
        stdout.write("  (No PostgreSQL targets configured)\n")

    stdout.write(f"\n🍃 MongoDB Sources ({len(mongo_sources)}):\n")
    for index, msource in enumerate(mongo_sources, start=1):
        stdout.write(
            f"  {mark()}{index}) {msource.name} → {','.join(msource.databases)}"
            f" | {mongo_host(msource.uri)}\n"
        )
    if not mongo_sources:
        stdout.write("  (No MongoDB sources configured)\n")

    stdout.write(f"\n🎯 MongoDB Targets ({len(mongo_targets)}):\n")
    for index, mtarget in enumerate(mongo_targets, start=1):
        stdout.write(f"  {mark()}{index}) {mtarget.name} | {mongo_host(mtarget.uri)}\n")
    if not mongo_targets:
        stdout.write("  (No MongoDB targets configured)\n")

    stdout.write("\n")
    if status and probes:
        summary = f"Summary: 🟢 {counts[True]} connected  |  🔴 {counts[False]} disconnected"
        if counts[None]:
            summary += f"  |  ⚪ {counts[None]} unknown (client not installed)"
        stdout.write(summary + "\n")
    return 0


def _probe_all(probes: Sequence[Callable[[], bool | None]]) -> list[bool | None]:
    """Run every probe concurrently; results keep the probes' order."""
    if not probes:
        return []
    with ThreadPoolExecutor(max_workers=min(len(probes), _MAX_PROBES)) as pool:
        return list(pool.map(lambda probe: probe(), probes))


def _connection_hint(
    tunnels: Sequence[VpsTunnel], runtime: Runtime
) -> Callable[[str, int], str]:
    return lambda host, port: connection_hint(tunnels, host, port, runtime=runtime)


# --- sync postgres --------------------------------------------------------------


def _postgres_cli(
    args: list[str],
    *,
    secrets: Secrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Route `sync postgres`: a sync (`-s -d`) or a cleanup (`-c [-n] [-s] [-d]`)."""
    clear_backups = False
    dry_run = False
    verbose = False
    source_text: str | None = None
    destination_text: str | None = None
    database_text: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-c", "--clear-backups"):
            clear_backups = True
            index += 1
            continue
        if arg in ("-n", "--dry-run"):
            dry_run = True
            index += 1
            continue
        if arg in ("-v", "--verbose"):
            verbose = True
            index += 1
            continue
        if arg in ("-s", "--source", "-d", "--destination", "-db", "--database"):
            value, index = _flag_value(args, index)
            if value is None:
                stderr.write(f"missing value for {arg}\n" + USAGE)
                return 1
            if arg in ("-s", "--source"):
                source_text = value
            elif arg in ("-d", "--destination"):
                destination_text = value
            else:
                database_text = value
            continue
        stderr.write(f"unknown option: {arg}\n" + USAGE)
        return 1

    if database_text is not None:
        stderr.write("ℹ️  -db is ignored for PostgreSQL; the source row names the database\n")
    if clear_backups:
        return _clear_postgres_backups_cli(
            source_text,
            destination_text,
            dry_run=dry_run,
            secrets=secrets,
            runtime=runtime,
            stdout=stdout,
            stderr=stderr,
        )

    if destination_text is None:
        stderr.write("❌ Error: PostgreSQL sync requires --destination\n" + _LIST_HINT)
        return 1
    if source_text is None:
        stderr.write("❌ Error: PostgreSQL sync requires --source\n" + _LIST_HINT)
        return 1
    destination = _select(
        secrets.postgres_targets,
        destination_text,
        "PostgreSQL destination",
        stderr,
    )
    if destination is None:
        return 1
    source = _select(
        secrets.postgres_sources,
        source_text,
        "PostgreSQL source",
        stderr,
    )
    if source is None:
        return 1
    stdout.write("\n🐘 PostgreSQL Sync\n")
    stdout.write("==================\n")
    stdout.write(f"  From: {source.name} → Database: {source.database}\n")
    stdout.write(f"  To: {destination.name}\n")
    stdout.write("\n")
    return perform_postgres_sync(
        source,
        destination,
        verbose=verbose,
        runtime=runtime,
        stdout=stdout,
        stderr=stderr,
        hint=_connection_hint(secrets.tunnels, runtime),
    )


def _clear_postgres_backups_cli(
    source_text: str | None,
    destination_text: str | None,
    *,
    dry_run: bool,
    secrets: Secrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    source_database: str | None = None
    if source_text is not None:
        source = _select(
            secrets.postgres_sources,
            source_text,
            "PostgreSQL source",
            stderr,
        )
        if source is None:
            return 1
        source_database = source.database

    if destination_text is not None:
        target = _select(
            secrets.postgres_targets,
            destination_text,
            "PostgreSQL destination",
            stderr,
        )
        if target is None:
            return 1
        destinations = [target]
    else:
        destinations = list(secrets.postgres_targets)
        if not destinations:
            stdout.write("ℹ️  No PostgreSQL targets configured\n")
            return 1

    stdout.write("\n🧹 PostgreSQL Backup Cleanup\n")
    stdout.write("============================\n")
    if destination_text is not None:
        stdout.write(f"  Target: {destinations[0].name}\n")
    else:
        stdout.write(f"  Target: all PostgreSQL targets ({len(destinations)})\n")
    if source_database:
        stdout.write(f"  Scope: {source_database}_backup_*\n")
    else:
        stdout.write("  Scope: *_backup_YYYYMMDD_HHMMSS\n")
    stdout.write("\n")
    return clear_postgres_backups(
        destinations,
        source_database=source_database,
        dry_run=dry_run,
        runtime=runtime,
        stdout=stdout,
        stderr=stderr,
    )


def _flag_value(args: list[str], index: int) -> tuple[str | None, int]:
    if index + 1 < len(args):
        return args[index + 1], index + 2
    return None, index + 1


# --- sync mongodb ---------------------------------------------------------------


def _mongo_sync_cli(
    args: list[str],
    *,
    secrets: Secrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Route `sync mongodb -s idx -d idx [-db idx] [-v]` (flags in any order)."""
    verbose = False
    source_text: str | None = None
    destination_text: str | None = None
    database_text: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-v", "--verbose"):
            verbose = True
            index += 1
            continue
        if arg in ("-s", "--source", "-d", "--destination", "-db", "--database"):
            value, index = _flag_value(args, index)
            if value is None:
                stderr.write(f"missing value for {arg}\n" + USAGE)
                return 1
            if arg in ("-s", "--source"):
                source_text = value
            elif arg in ("-d", "--destination"):
                destination_text = value
            else:
                database_text = value
            continue
        stderr.write(f"unknown option: {arg}\n" + USAGE)
        return 1

    if destination_text is None:
        stderr.write("❌ Error: MongoDB sync requires --destination\n" + _LIST_HINT)
        return 1
    if source_text is None:
        stderr.write("❌ Error: MongoDB sync requires --source\n" + _LIST_HINT)
        return 1

    source = _select(
        secrets.mongo_sources, source_text, "MongoDB source", stderr
    )
    if source is None:
        return 1
    target = _select(
        secrets.mongo_targets,
        destination_text,
        "MongoDB destination",
        stderr,
    )
    if target is None:
        return 1
    database = _select_mongo_database(
        source.databases, database_text if database_text is not None else "1", stderr
    )
    if database is None:
        return 1

    stdout.write("\n🍃 MongoDB Sync\n")
    stdout.write("==================\n")
    stdout.write(f"  From: {source.name} → Database: {database}\n")
    stdout.write(f"  To: {target.name}\n")
    stdout.write("\n")
    return perform_mongo_sync(
        source.uri,
        database,
        target.uri,
        verbose=verbose,
        runtime=runtime,
        stdout=stdout,
        stderr=stderr,
        hint=_connection_hint(secrets.tunnels, runtime),
    )


def _select_mongo_database(names: Sequence[str], text: str, stderr: TextIO) -> str | None:
    """1-based index into the source's database list."""
    try:
        index = int(text)
    except ValueError:
        index = 0
    if not 1 <= index <= len(names):
        stderr.write(f"❌ Error: Invalid MongoDB database index: {text}\n")
        stderr.write(f"   Available databases: 1-{len(names)}\n")
        return None
    return names[index - 1]


# --- sync -c --------------------------------------------------------------------


def _clear_backups_cli(
    args: list[str],
    *,
    secrets: Secrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Route `sync -c [-n]`: clean Postgres targets, then Mongo targets."""
    dry_run = False
    for arg in args:
        if arg in ("-n", "--dry-run"):
            dry_run = True
            continue
        stderr.write(f"unknown option: {arg}\n" + USAGE)
        return 1

    targets: Sequence[PostgresTarget] = secrets.postgres_targets
    mongo_targets: Sequence[MongoTarget] = secrets.mongo_targets
    if not targets and not mongo_targets:
        stdout.write("ℹ️  No PostgreSQL or MongoDB targets configured\n")
        return 1

    stdout.write("\n🧹 Backup Cleanup\n")
    stdout.write("=================\n")
    stdout.write(f"  PostgreSQL targets: {len(targets)}\n")
    stdout.write(f"  MongoDB targets: {len(mongo_targets)}\n")
    stdout.write("\n")
    # An engine with no targets is a notice; the other still runs.
    postgres_code = 0
    if targets:
        postgres_code = clear_postgres_backups(
            targets,
            source_database=None,
            dry_run=dry_run,
            runtime=runtime,
            stdout=stdout,
            stderr=stderr,
        )
    else:
        stdout.write("ℹ️  No PostgreSQL targets configured\n\n")
    mongo_code = 0
    if mongo_targets:
        mongo_code = clear_mongo_backups(
            mongo_targets, dry_run=dry_run, runtime=runtime, stdout=stdout, stderr=stderr
        )
    else:
        stdout.write("ℹ️  No MongoDB targets configured\n")
    return 1 if postgres_code or mongo_code else 0


# --- config ----------------------------------------------------------------------


def _config_cli(
    args: list[str], *, secrets: Secrets, path: Path, stdout: TextIO, stderr: TextIO
) -> int:
    """`dbt config` shows the file with credentials masked; `dbt config path` only its path."""
    if args == ["path"]:
        stdout.write(f"{path}\n")
        return 0
    if args:
        stderr.write(f"unknown option: {args[0]}\nusage: dbt config [path]\n")
        return 1

    stdout.write(f"Secrets file: {path}\n")
    sections: list[tuple[str, list[str]]] = [
        (
            "🔀 Tunnels",
            [
                f"{t.name}: {t.user}@{t.host}  {t.service}"
                f"  :{t.local_port} -> {t.target_host}:{t.target_port}"
                for t in secrets.tunnels
            ],
        ),
        (
            "🐘 PostgreSQL sources",
            [f"{s.name} → {s.database}  {redact(s.url)}" for s in secrets.postgres_sources],
        ),
        ("🎯 PostgreSQL targets", [f"{t.name}  {redact(t.url)}" for t in secrets.postgres_targets]),
        (
            "🍃 MongoDB sources",
            [f"{s.name} → {','.join(s.databases)}  {redact(s.uri)}" for s in secrets.mongo_sources],
        ),
        ("🎯 MongoDB targets", [f"{t.name}  {redact(t.uri)}" for t in secrets.mongo_targets]),
    ]
    for title, lines in sections:
        stdout.write(f"\n{title} ({len(lines)}):\n")
        stdout.writelines(f"  {index}) {line}\n" for index, line in enumerate(lines, start=1))
        if not lines:
            stdout.write("  (none)\n")
    return 0


def main() -> NoReturn:
    try:
        code = run_cli(sys.argv[1:], env=os.environ, stdout=sys.stdout, stderr=sys.stderr)
    except (KeyboardInterrupt, Interrupted):
        sys.stderr.write("\nInterrupted\n")
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
