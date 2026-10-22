"""dbt command line entry point."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .config import ConfigError, SecretsCache, resolve_secrets_file
from .models import PostgresSource, PostgresTarget, RawSecrets
from .postgres import (
    clear_postgres_backups,
    parse_postgres_source_row,
    parse_postgres_target_row,
    parse_postgres_url,
    perform_postgres_sync,
    postgres_status,
)
from .runtime import Runtime, default_runtime
from .secrets import SecretsError
from .tunnels import connect_tunnels, disconnect_tunnels, list_tunnels, test_tunnels

USAGE = """\
usage: dbt <command> [args]

commands:
  help              show this help
  connect [filter]  open SSH tunnels for the secrets file
  disconnect [..]   close SSH tunnels started by dbt
  list [--status]   show secrets entries
  test [filter]     check the hosts used for tunnels
  sync <service>    sync Postgres or MongoDB databases
    sync list --status    test database connections
    sync postgres -s <idx> -d <idx> [--verbose]
                      sync the source database onto a target
    sync postgres -c [-n] [-s idx] [-d idx]
                      delete Postgres backup databases; -n lists only
"""

_SYNC_LIST_USAGE = "usage: dbt sync list|ls [--status]\n"


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
        secrets_path = resolve_secrets_file(
            env,
            home=home,
            repo_root=Path(__file__).resolve().parents[3],
            example=Path(__file__).resolve().parent / "secrets.example",
        )
        raw_secrets = SecretsCache().load(secrets_path)
    except (SecretsError, ConfigError) as exc:
        stderr.write(f"{exc}\n")
        return 1

    runtime = dependencies if dependencies is not None else default_runtime()
    command, args = argv[0], list(argv[1:])
    rows = raw_secrets.vps
    service = args[0] if args else None

    if command in ("connect", "disconnect"):
        if len(args) > 1:
            stderr.write(f"unexpected arguments: {' '.join(args[1:])}\n" + USAGE)
            return 1
        dispatch = connect_tunnels if command == "connect" else disconnect_tunnels
        return dispatch(rows, service=service, runtime=runtime, stdout=stdout, stderr=stderr)

    if command in ("list", "ls"):
        for arg in args:
            if arg != "--status":
                stderr.write(f"unknown option: {arg}\n" + USAGE)
                return 1
        return list_tunnels(rows, service=None, runtime=runtime, stdout=stdout, stderr=stderr)

    if command == "test":
        if len(args) > 1:
            stderr.write(f"unexpected arguments: {' '.join(args[1:])}\n" + USAGE)
            return 1
        return test_tunnels(rows, service=service, runtime=runtime, stdout=stdout, stderr=stderr)

    if command == "sync":
        return _run_sync(
            args, raw_secrets=raw_secrets, runtime=runtime, stdout=stdout, stderr=stderr
        )

    stderr.write(f"unknown command: {argv[0]}\n" + USAGE)
    return 1


def _run_sync(
    args: list[str],
    *,
    raw_secrets: RawSecrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if not args:
        stderr.write("unknown sync type\n" + USAGE)
        return 1
    subcommand, rest = args[0], args[1:]
    if subcommand in ("list", "ls"):
        status = False
        for arg in rest:
            if arg != "--status":
                stderr.write(f"unknown option: {arg}\n" + _SYNC_LIST_USAGE)
                return 1
            status = True
        return _print_sync_configs(
            raw_secrets, status=status, runtime=runtime, stdout=stdout, stderr=stderr
        )
    if subcommand in ("postgres", "postgresql", "pg"):
        return _clear_postgres_backups_cli(
            rest, raw_secrets=raw_secrets, runtime=runtime, stdout=stdout, stderr=stderr
        )
    stderr.write(f"unknown sync type: {subcommand}\n" + USAGE)
    return 1


def _print_sync_configs(
    raw_secrets: RawSecrets,
    *,
    status: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Show Postgres sync entries with 1-based indexes; --status probes psql."""
    sources: list[PostgresSource] = []
    for row in raw_secrets.postgres_sources:
        source = parse_postgres_source_row(row)
        if source is None:
            stderr.write(f"skipped invalid row: {row}\n")
        else:
            sources.append(source)
    targets: list[PostgresTarget] = []
    for row in raw_secrets.postgres_targets:
        target = parse_postgres_target_row(row)
        if target is None:
            stderr.write(f"skipped invalid row: {row}\n")
        else:
            targets.append(target)

    connected = 0
    disconnected = 0
    stdout.write(f"🐘 PostgreSQL Sources ({len(sources)}):\n")
    for index, source in enumerate(sources, start=1):
        icon = ""
        if status:
            if postgres_status(source.url, runtime=runtime):
                icon = "🟢 "
                connected += 1
            else:
                icon = "🔴 "
                disconnected += 1
        host = parse_postgres_url(source.url).host
        stdout.write(f"  {icon}{index}) {source.name} → {source.database} | {host}\n")
    if not sources:
        stdout.write("  (No PostgreSQL sources configured)\n")

    stdout.write("\n")
    stdout.write(f"🎯 PostgreSQL Targets ({len(targets)}):\n")
    for index, target in enumerate(targets, start=1):
        icon = ""
        url = parse_postgres_url(target.url)
        if status:
            if postgres_status(target.url, runtime=runtime):
                icon = "🟢 "
                connected += 1
            else:
                icon = "🔴 "
                disconnected += 1
        ssl = "require" if url.sslmode_require else "disable"
        stdout.write(f"  {icon}{index}) {target.name} | {url.host} [SSL: {ssl}]\n")
    if not targets:
        stdout.write("  (No PostgreSQL targets configured)\n")

    stdout.write("\n")
    if status and (sources or targets):
        stdout.write(
            f"Summary: 🟢 {connected} connected  |  🔴 {disconnected} disconnected\n"
        )
    return 0


def _clear_postgres_backups_cli(
    args: list[str],
    *,
    raw_secrets: RawSecrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Route `sync postgres -c [-n] [-s idx] [-d idx]` (flags in any order)."""
    clear_backups = False
    dry_run = False
    verbose = False
    source_text: str | None = None
    destination_text: str | None = None
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
            # -db/--database: accepted for parity; it selects no database.
            continue
        stderr.write(f"unknown option: {arg}\n" + USAGE)
        return 1

    if not clear_backups:
        if destination_text is None:
            stderr.write("❌ Error: PostgreSQL sync requires --destination\n")
            stderr.write("   Use 'dbt list' to list available configurations\n")
            return 1
        if source_text is None:
            stderr.write("❌ Error: PostgreSQL sync requires --source\n")
            stderr.write("   Use 'dbt list' to list available configurations\n")
            return 1

        # -db/--database is accepted for parity; the source names the database.
        destinations = _select_targets(
            raw_secrets.postgres_targets, destination_text, stderr
        )
        if destinations is None:
            return 1
        source = _select_source(raw_secrets.postgres_sources, source_text, stderr)
        if source is None:
            return 1
        stdout.write("\n🐘 PostgreSQL Sync\n")
        stdout.write("==================\n")
        stdout.write(f"  From: {source.name} → Database: {source.database}\n")
        stdout.write(f"  To: {destinations[0].name}\n")
        stdout.write("\n")
        return perform_postgres_sync(
            source,
            destinations[0],
            verbose=verbose,
            runtime=runtime,
            stdout=stdout,
            stderr=stderr,
        )

    source_database: str | None = None
    if source_text is not None:
        source = _select_source(raw_secrets.postgres_sources, source_text, stderr)
        if source is None:
            return 1
        source_database = source.database

    destinations = _select_targets(
        raw_secrets.postgres_targets, destination_text, stderr
    )
    if destinations is None:
        return 1
    if destination_text is None and not destinations:
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


def _select_source(
    rows: tuple[str, ...], text: str, stderr: TextIO
) -> PostgresSource | None:
    try:
        index = int(text)
    except ValueError:
        index = 0
    if not 1 <= index <= len(rows):
        stderr.write(f"❌ Error: Invalid PostgreSQL source index: {text}\n")
        stderr.write(f"   Available sources: 1-{len(rows)}\n")
        return None
    source = parse_postgres_source_row(rows[index - 1])
    if source is None:
        stderr.write(f"❌ Error: PostgreSQL source row {index} is invalid\n")
        return None
    return source


def _select_targets(
    rows: tuple[str, ...], text: str | None, stderr: TextIO
) -> list[PostgresTarget] | None:
    """`-d` selects one raw row; otherwise every valid row (invalid → warn)."""
    if text is None:
        targets: list[PostgresTarget] = []
        for row in rows:
            target = parse_postgres_target_row(row)
            if target is None:
                stderr.write(f"skipped invalid row: {row}\n")
            else:
                targets.append(target)
        return targets
    try:
        index = int(text)
    except ValueError:
        index = 0
    if not 1 <= index <= len(rows):
        stderr.write(f"❌ Error: Invalid PostgreSQL destination index: {text}\n")
        stderr.write(f"   Available destinations: 1-{len(rows)}\n")
        return None
    target = parse_postgres_target_row(rows[index - 1])
    if target is None:
        stderr.write(f"❌ Error: PostgreSQL destination row {index} is invalid\n")
        return None
    return [target]
