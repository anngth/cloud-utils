"""dbt command line entry point (click)."""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import NoReturn, TextIO

import click

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
from .tunnels import connect_tunnels, connection_hint, disconnect_tunnels, list_tunnels

_CONTEXT = {"help_option_names": ["-h", "--help"], "max_content_width": 100}
_MAX_PROBES = 8
_LIST_HINT = "   Use 'dbt sync list' to list available configurations\n"
_INDEX_HELP = "1-based index from `dbt sync list`"


class _Fatal(click.ClickException):
    """A failure already worded for the user: printed as is, exit code 1."""


@dataclass
class _App:
    """Per-invocation state; the secrets file is loaded on first use."""

    env: Mapping[str, str]
    stdout: TextIO
    stderr: TextIO
    runtime: Runtime
    _loaded: tuple[Path, Secrets] | None = None

    def load(self) -> tuple[Path, Secrets]:
        if self._loaded is None:
            home_text = self.env.get("HOME", "")
            home = Path(home_text) if home_text else Path.home()
            try:
                resolved = resolve_secrets_file(
                    self.env,
                    home=home,
                    example=Path(__file__).resolve().parent / "secrets.example.toml",
                )
                self.stderr.writelines(f"ℹ️  {notice}\n" for notice in resolved.notices)
                secrets = load_secrets(resolved.path)
            except (SecretsError, ConfigError) as exc:
                raise _Fatal(str(exc)) from exc
            self._loaded = (resolved.path, secrets)
        return self._loaded

    @property
    def secrets(self) -> Secrets:
        return self.load()[1]

    @property
    def secrets_path(self) -> Path:
        return self.load()[0]


class _Group(click.Group):
    """A group whose commands are also reachable by aliases (`ls`, `pg`, `mongo`)."""

    def __init__(self, *args: object, aliases: Mapping[str, str] | None = None, **kwargs: object):
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.aliases = dict(aliases or {})

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        return super().get_command(ctx, self.aliases.get(cmd_name, cmd_name))

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """List the commands and, one level in, the commands of each nested group."""
        rows: list[tuple[str, click.Command]] = []
        for name in self.list_commands(ctx):
            command = self.get_command(ctx, name)
            if command is None or command.hidden:
                continue
            rows.append((name, command))
            if isinstance(command, click.Group):
                nested = click.Context(command, info_name=name, parent=ctx)
                for sub_name in command.list_commands(nested):
                    sub_command = command.get_command(nested, sub_name)
                    if sub_command is not None and not sub_command.hidden:
                        rows.append((f"{name} {sub_name}", sub_command))
        if not rows:
            return
        limit = formatter.width - 6 - max(len(name) for name, _ in rows)
        with formatter.section("Commands"):
            formatter.write_dl([(name, cmd.get_short_help_str(limit)) for name, cmd in rows])


def run_cli(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    dependencies: Runtime | None = None,
) -> int:
    """Run `dbt` with the given arguments; click's own output goes to `stdout`/`stderr`."""
    app = _App(
        env=env,
        stdout=stdout,
        stderr=stderr,
        runtime=dependencies if dependencies is not None else default_runtime(),
    )
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = dbt.main(args=list(argv), prog_name="dbt", standalone_mode=False, obj=app)
    except _Fatal as error:
        stderr.write(f"{error.format_message()}\n")
        return 1
    except click.ClickException as error:
        if isinstance(error, click.UsageError) and error.ctx is not None:
            stderr.write(error.ctx.get_usage() + "\n")
            stderr.write(f"Try '{error.ctx.command_path} --help' for help.\n\n")
        stderr.write(f"Error: {error.format_message()}\n")
        return 1
    return int(result or 0)


# --- top level ------------------------------------------------------------------


@click.group(
    cls=_Group, aliases={"ls": "list"}, invoke_without_command=True, context_settings=_CONTEXT
)
@click.pass_context
def dbt(ctx: click.Context) -> int:
    """Database tools: SSH tunnels and PostgreSQL/MongoDB syncs from the secrets file."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
    return 0


@dbt.command("help")
@click.pass_context
def help_command(ctx: click.Context) -> int:
    """Show this help."""
    assert ctx.parent is not None
    click.echo(ctx.parent.get_help())
    return 0


@dbt.command("config", short_help="Show the secrets file, credentials masked.")
@click.argument("what", type=click.Choice(["path"]), required=False)
@click.pass_obj
def config_command(app: _App, what: str | None) -> int:
    """Show the secrets file, credentials masked; `path` prints only its location."""
    return _config_cli(
        app.secrets, path=app.secrets_path, only_path=what == "path", stdout=app.stdout
    )


@dbt.command("connect", short_help="Open the SSH tunnels; SERVICE filters by name or port.")
@click.argument("service", required=False)
@click.pass_obj
def connect_command(app: _App, service: str | None) -> int:
    """Open the SSH tunnels of the secrets file (SERVICE filters by name or port)."""
    return connect_tunnels(
        app.secrets.tunnels,
        service=service,
        runtime=app.runtime,
        stdout=app.stdout,
        stderr=app.stderr,
    )


@dbt.command("disconnect", short_help="Close the SSH tunnels dbt started.")
@click.argument("service", required=False)
@click.pass_obj
def disconnect_command(app: _App, service: str | None) -> int:
    """Close the SSH tunnels started by dbt (SERVICE filters by name or port)."""
    return disconnect_tunnels(
        app.secrets.tunnels,
        service=service,
        runtime=app.runtime,
        stdout=app.stdout,
        stderr=app.stderr,
    )


@dbt.command("list", short_help="Show every tunnel: UP, DOWN or BUSY (alias: ls).")
@click.pass_obj
def list_command(app: _App) -> int:
    """Show every tunnel and whether it is UP, DOWN or BUSY (alias: ls)."""
    return list_tunnels(
        app.secrets.tunnels, runtime=app.runtime, stdout=app.stdout, stderr=app.stderr
    )


@dbt.command("status", short_help="Tunnel status with SSH check, then probe every database.")
@click.pass_obj
def status_command(app: _App) -> int:
    """Show tunnel status (DOWN rows get an SSH check) and probe every database."""
    tunnels_code = list_tunnels(
        app.secrets.tunnels,
        runtime=app.runtime,
        stdout=app.stdout,
        stderr=app.stderr,
        ssh_check=True,
    )
    app.stdout.write("\n")
    sync_code = _print_sync_configs(
        app.secrets, status=True, runtime=app.runtime, stdout=app.stdout, stderr=app.stderr
    )
    return 1 if tunnels_code or sync_code else 0


# --- sync -----------------------------------------------------------------------


@dbt.group(
    "sync",
    cls=_Group,
    short_help="Sync PostgreSQL/MongoDB databases, or clean their backups.",
    aliases={"ls": "list", "postgresql": "postgres", "pg": "postgres", "mongo": "mongodb"},
    invoke_without_command=True,
)
@click.option(
    "-c",
    "--clear-backups",
    is_flag=True,
    help="Delete backup databases on every target: PostgreSQL first, then MongoDB.",
)
@click.option(
    "-n", "--dry-run", is_flag=True, help="With -c: list the backups without dropping them."
)
@click.pass_context
def sync_group(ctx: click.Context, clear_backups: bool, dry_run: bool) -> int:
    """Sync PostgreSQL or MongoDB databases, or clean up their backups."""
    if ctx.invoked_subcommand is not None:
        if clear_backups or dry_run:
            raise click.UsageError(
                f"put -c/-n after the sync type: dbt sync {ctx.invoked_subcommand} -c [-n]", ctx
            )
        return 0
    if not clear_backups:
        raise click.UsageError("missing sync type: postgres, mongodb or list (or -c)", ctx)
    app: _App = ctx.obj
    return _clear_backups_cli(
        app.secrets, dry_run=dry_run, runtime=app.runtime, stdout=app.stdout, stderr=app.stderr
    )


@sync_group.command("list")
@click.option("--status", is_flag=True, help="Probe every connection.")
@click.pass_obj
def sync_list_command(app: _App, status: bool) -> int:
    """Show sync entries with their indexes (alias: ls)."""
    return _print_sync_configs(
        app.secrets, status=status, runtime=app.runtime, stdout=app.stdout, stderr=app.stderr
    )


@sync_group.command(
    "postgres", short_help="Sync a PostgreSQL database onto a target, or clean backups."
)
@click.option("-s", "--source", help=f"Source row ({_INDEX_HELP}).")
@click.option("-d", "--destination", help=f"Target row ({_INDEX_HELP}).")
@click.option("-db", "--database", "database", help="Ignored: the source row names the database.")
@click.option(
    "-c", "--clear-backups", is_flag=True, help="Delete `*_backup_*` databases instead of syncing."
)
@click.option(
    "-n", "--dry-run", is_flag=True, help="With -c: list the backups without dropping them."
)
@click.option("-v", "--verbose", is_flag=True, help="Stream pg_dump/pg_restore output.")
@click.pass_obj
def sync_postgres_command(
    app: _App,
    source: str | None,
    destination: str | None,
    database: str | None,
    clear_backups: bool,
    dry_run: bool,
    verbose: bool,
) -> int:
    """Sync a PostgreSQL database onto a target, or clean its backups (aliases: postgresql, pg).

    The target keeps the old database as `<name>_backup_YYYYMMDD_HHMMSS`;
    `-c` drops those (scoped with -s/-d), `-n` only lists them.
    """
    if database is not None:
        app.stderr.write("ℹ️  -db is ignored for PostgreSQL; the source row names the database\n")
    if clear_backups:
        return _clear_postgres_backups_cli(
            source,
            destination,
            dry_run=dry_run,
            secrets=app.secrets,
            runtime=app.runtime,
            stdout=app.stdout,
            stderr=app.stderr,
        )
    return _postgres_sync_cli(
        source,
        destination,
        verbose=verbose,
        secrets=app.secrets,
        runtime=app.runtime,
        stdout=app.stdout,
        stderr=app.stderr,
    )


@sync_group.command("mongodb", short_help="Sync a MongoDB database onto a target (alias: mongo).")
@click.option("-s", "--source", help=f"Source row ({_INDEX_HELP}).")
@click.option("-d", "--destination", help=f"Target row ({_INDEX_HELP}).")
@click.option(
    "-db", "--database", "database", help="Database of the source row (1-based index, default 1)."
)
@click.option("-v", "--verbose", is_flag=True, help="Stream mongodump/mongorestore output.")
@click.pass_obj
def sync_mongodb_command(
    app: _App, source: str | None, destination: str | None, database: str | None, verbose: bool
) -> int:
    """Sync a MongoDB database onto a target (alias: mongo).

    The target keeps the old database as `<name>_backup_YYYYMMDD_HHMMSS`
    and is restored from it when the sync fails.
    """
    return _mongo_sync_cli(
        source,
        destination,
        database,
        verbose=verbose,
        secrets=app.secrets,
        runtime=app.runtime,
        stdout=app.stdout,
        stderr=app.stderr,
    )


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


def _postgres_sync_cli(
    source_text: str | None,
    destination_text: str | None,
    *,
    verbose: bool,
    secrets: Secrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
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
            return 0

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


# --- sync mongodb ---------------------------------------------------------------


def _mongo_sync_cli(
    source_text: str | None,
    destination_text: str | None,
    database_text: str | None,
    *,
    verbose: bool,
    secrets: Secrets,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
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
    secrets: Secrets,
    *,
    dry_run: bool,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """`sync -c [-n]`: clean Postgres targets, then Mongo targets."""
    targets: Sequence[PostgresTarget] = secrets.postgres_targets
    mongo_targets: Sequence[MongoTarget] = secrets.mongo_targets
    if not targets and not mongo_targets:
        stdout.write("ℹ️  No PostgreSQL or MongoDB targets configured\n")
        return 0

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


def _config_cli(secrets: Secrets, *, path: Path, only_path: bool, stdout: TextIO) -> int:
    """`dbt config` shows the file with credentials masked; `dbt config path` only its path."""
    if only_path:
        stdout.write(f"{path}\n")
        return 0

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
