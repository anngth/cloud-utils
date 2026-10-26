"""dbt command line entry point (click)."""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Protocol, TextIO

import click
from click.shell_completion import CompletionItem, ZshComplete
from shared.selector import SelectorItem, SelectorState, run_selector
from shared.streams import is_tty
from shared.ui import BRIGHT_GREEN, GRAY

from .config import ConfigError, load_secrets, resolve_secrets_file
from .listing import render_listing
from .models import (
    MongoSource,
    MongoTarget,
    PostgresSource,
    PostgresTarget,
    Secrets,
    VpsTunnel,
)
from .mongo import clear_mongo_backups, perform_mongo_sync
from .postgres import clear_postgres_backups, perform_postgres_sync, plan_postgres_sync
from .runtime import Interrupted, Runtime, default_runtime
from .secrets import SecretsError
from .tunnels import connect_tunnels, connection_hint, disconnect_tunnels

_CONTEXT = {"help_option_names": ["-h", "--help"], "max_content_width": 100}
_CLEAR_SCREEN = "\033[2J\033[H"
_WHITE = "\033[97m"
_RESET = "\033[0m"
_LIST_HINT = "   Use 'dbt sync list' to list available configurations\n"
_ROW_HELP = "name or 1-based index from `dbt sync list`"
_POSTGRES_ALIASES = ("postgres", "postgresql", "pg")
_MONGO_ALIASES = ("mongodb", "mongo")


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

    @property
    def interactive(self) -> bool:
        """True when a prompt or a selector can be shown."""
        return is_tty(sys.stdin) and is_tty(self.stdout)


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


# --- interaction hooks (module level so tests can replace them) ---------------------


def _pick(title: str, items: Sequence[SelectorItem], app: _App) -> str | None:
    """Arrow-key selection of one item; None when cancelled."""

    def render(state: SelectorState) -> None:
        # Redraw from the top-left on every key, as gt's selector does; the
        # selector runs on the alternate screen, so nothing is left behind.
        app.stdout.write(_CLEAR_SCREEN)
        app.stdout.write(f"{title}  {GRAY}(↑/↓ move, Enter select, q cancel){_RESET}\n\n")
        for index, item in enumerate(state.items):
            current = index == state.cursor
            marker = f"{BRIGHT_GREEN}▶{_RESET}" if current else " "
            color = _WHITE if current else GRAY
            app.stdout.write(f" {marker} {color}{index + 1}) {item.label}{_RESET}\n")
        app.stdout.flush()

    result = run_selector(items, multiple=False, output=app.stdout, render=render)
    return result.selected[0] if result.kind == "submit" and result.selected else None


def _edit(path: Path) -> None:
    click.edit(filename=str(path))


# --- row selection ----------------------------------------------------------------


class _Named(Protocol):
    @property
    def name(self) -> str: ...


def _select[T: _Named](items: Sequence[T], text: str, kind: str, stderr: TextIO) -> T | None:
    """The entry named `text`, or at that 1-based index; None after an error message."""
    names = [item.name for item in items]
    if text in names:
        return items[names.index(text)]
    try:
        index = int(text)
    except ValueError:
        index = 0
    if not 1 <= index <= len(items):
        stderr.write(f"❌ Error: Unknown {kind}: {text}\n")
        choices = ", ".join(f"{i}) {name}" for i, name in enumerate(names, start=1)) or "none"
        stderr.write(f"   Available {kind.rsplit(' ', 1)[-1]}s: {choices}\n")
        return None
    return items[index - 1]


def _choose[T: _Named](
    items: Sequence[T],
    text: str | None,
    *,
    kind: str,
    flag: str,
    label: Callable[[T], str],
    app: _App,
) -> T | None:
    """Resolve `text` (name or index); when absent, pick interactively or explain."""
    if text is not None:
        return _select(items, text, kind, app.stderr)
    if not items:
        app.stderr.write(f"❌ Error: No {kind} configured\n")
        return None
    if not app.interactive:
        app.stderr.write(f"❌ Error: {flag} is required outside a terminal\n" + _LIST_HINT)
        return None
    options = [SelectorItem(label(item), item.name) for item in items]
    chosen = _pick(f"Select the {kind}", options, app)
    if chosen is None:
        app.stderr.write("Cancelled\n")
        return None
    return _select(items, chosen, kind, app.stderr)


def _select_mongo_database(names: Sequence[str], text: str, stderr: TextIO) -> str | None:
    """A database of the source row, by name or 1-based index."""
    if text in names:
        return text
    try:
        index = int(text)
    except ValueError:
        index = 0
    if not 1 <= index <= len(names):
        stderr.write(f"❌ Error: Unknown MongoDB database: {text}\n")
        stderr.write(f"   Available databases: {', '.join(names)}\n")
        return None
    return names[index - 1]


def _complete_rows(attribute: str) -> Callable[..., list[CompletionItem]]:
    """Shell completion of `--from`/`--to` with the names in the secrets file."""

    def complete(ctx: click.Context, _param: object, incomplete: str) -> list[CompletionItem]:
        app = ctx.find_object(_App)
        if app is None:
            return []
        try:
            rows = getattr(app.secrets, attribute)
        except click.ClickException:
            return []
        return [CompletionItem(row.name) for row in rows if row.name.startswith(incomplete)]

    return complete


def _source_option(attribute: str) -> Callable[[Callable[..., int]], Callable[..., int]]:
    return click.option(
        "-s",
        "--source",
        "--from",
        "source",
        help=f"Source row ({_ROW_HELP}).",
        shell_complete=_complete_rows(attribute),
    )


def _target_option(attribute: str) -> Callable[[Callable[..., int]], Callable[..., int]]:
    return click.option(
        "-d",
        "--destination",
        "--to",
        "target",
        help=f"Target row ({_ROW_HELP}).",
        shell_complete=_complete_rows(attribute),
    )


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


@dbt.command("config", short_help="Show the secrets file, credentials masked; `path`, `edit`.")
@click.argument("what", type=click.Choice(["path", "edit"]), required=False)
@click.pass_obj
def config_command(app: _App, what: str | None) -> int:
    """Show the secrets file with credentials masked.

    `dbt config path` prints only its location; `dbt config edit` opens it in $EDITOR.
    """
    if what == "path":
        app.stdout.write(f"{app.secrets_path}\n")
        return 0
    if what == "edit":
        _edit(app.secrets_path)
        return 0
    app.stdout.write(f"Secrets file: {app.secrets_path}\n\n")
    return render_listing(
        app.secrets,
        runtime=app.runtime,
        stdout=app.stdout,
        detail="url",
        tunnel_status=False,
        probe=False,
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


@dbt.command("list", short_help="Show tunnels (UP/DOWN/BUSY) and every sync row (alias: ls).")
@click.pass_obj
def list_command(app: _App) -> int:
    """Show every tunnel with its local state and every database row with its index.

    No network call is made; `dbt status` adds the connection probes.
    """
    return render_listing(
        app.secrets,
        runtime=app.runtime,
        stdout=app.stdout,
        detail="host",
        tunnel_status=True,
        probe=False,
    )


@dbt.command("status", short_help="Like list, plus an SSH check on DOWN tunnels and DB probes.")
@click.pass_obj
def status_command(app: _App) -> int:
    """Show tunnel status (DOWN rows get an SSH check) and probe every database."""
    return render_listing(
        app.secrets,
        runtime=app.runtime,
        stdout=app.stdout,
        detail="host",
        tunnel_status=True,
        probe=True,
        ssh_check=True,
    )


@dbt.command("completion", short_help="Print the shell completion script.")
@click.argument("shell", type=click.Choice(["zsh"]))
@click.pass_context
def completion_command(ctx: click.Context, shell: str) -> int:
    """Print the completion script; add `eval "$(dbt completion zsh)"` to ~/.zshrc."""
    assert ctx.parent is not None
    click.echo(ZshComplete(ctx.parent.command, {}, "dbt", "_DBT_COMPLETE").source())
    return 0


# --- clean ------------------------------------------------------------------------


@dbt.command("clean", short_help="Delete `*_backup_*` databases on the targets (-n lists only).")
@click.argument("engine", type=click.Choice([*_POSTGRES_ALIASES, *_MONGO_ALIASES]), required=False)
@click.option("-n", "--dry-run", is_flag=True, help="List the backups without dropping them.")
@_source_option("postgres_sources")
@_target_option("postgres_targets")
@click.pass_obj
def clean_command(
    app: _App, engine: str | None, dry_run: bool, source: str | None, target: str | None
) -> int:
    """Delete the `<database>_backup_YYYYMMDD_HHMMSS` databases a sync left behind.

    Without ENGINE every PostgreSQL target is cleaned, then every MongoDB
    target. `--from` limits the PostgreSQL cleanup to one source database
    and `--to` to one target.
    """
    postgres = engine is None or engine in _POSTGRES_ALIASES
    mongo = engine is None or engine in _MONGO_ALIASES
    if (source or target) and not postgres:
        raise click.UsageError("--from/--to only apply to PostgreSQL")
    secrets = app.secrets
    if not (postgres and secrets.postgres_targets) and not (mongo and secrets.mongo_targets):
        engines = " or ".join(
            name for name, on in (("PostgreSQL", postgres), ("MongoDB", mongo)) if on
        )
        app.stdout.write(f"ℹ️  No {engines} targets configured\n")
        return 0

    app.stdout.write("\n🧹 Backup Cleanup\n")
    app.stdout.write("=================\n")
    failed = False
    if postgres:
        code = _clean_postgres(app, source, target, dry_run=dry_run)
        failed = failed or code == 1
        if code == 2:
            return 1
    if mongo:
        if secrets.mongo_targets:
            app.stdout.write(f"  MongoDB targets: {len(secrets.mongo_targets)}\n\n")
            failed = (
                clear_mongo_backups(
                    secrets.mongo_targets,
                    dry_run=dry_run,
                    runtime=app.runtime,
                    stdout=app.stdout,
                    stderr=app.stderr,
                )
                != 0
            ) or failed
        else:
            app.stdout.write("ℹ️  No MongoDB targets configured\n")
    return 1 if failed else 0


def _clean_postgres(app: _App, source: str | None, target: str | None, *, dry_run: bool) -> int:
    """0 ok, 1 a target failed, 2 a bad selection (nothing ran)."""
    secrets = app.secrets
    source_database: str | None = None
    if source is not None:
        row = _select(secrets.postgres_sources, source, "PostgreSQL source", app.stderr)
        if row is None:
            return 2
        source_database = row.database
    if target is not None:
        chosen = _select(secrets.postgres_targets, target, "PostgreSQL target", app.stderr)
        if chosen is None:
            return 2
        targets = [chosen]
    else:
        targets = list(secrets.postgres_targets)
    if not targets:
        app.stdout.write("ℹ️  No PostgreSQL targets configured\n\n")
        return 0
    label = targets[0].name if target is not None else f"all PostgreSQL targets ({len(targets)})"
    app.stdout.write(f"  PostgreSQL: {label}\n")
    scope = f"{source_database}_backup_*" if source_database else "*_backup_YYYYMMDD_HHMMSS"
    app.stdout.write(f"  Scope: {scope}\n\n")
    return clear_postgres_backups(
        targets,
        source_database=source_database,
        dry_run=dry_run,
        runtime=app.runtime,
        stdout=app.stdout,
        stderr=app.stderr,
    )


# --- sync -----------------------------------------------------------------------


@dbt.group(
    "sync",
    cls=_Group,
    aliases={"ls": "list", "postgresql": "postgres", "pg": "postgres", "mongo": "mongodb"},
    short_help="Sync a PostgreSQL or MongoDB database onto a target.",
)
def sync_group() -> int:
    """Sync a PostgreSQL or MongoDB database onto a target (backups: `dbt clean`)."""
    return 0


@sync_group.command("list", short_help="Show the sync rows with their indexes (alias: ls).")
@click.option("--status", is_flag=True, help="Probe every connection.")
@click.pass_obj
def sync_list_command(app: _App, status: bool) -> int:
    """Show every source and target with the index `--from`/`--to` accept."""
    return render_listing(
        app.secrets,
        runtime=app.runtime,
        stdout=app.stdout,
        detail="host",
        tunnel_status=False,
        probe=status,
    )


def _sync_options(
    source_attribute: str, target_attribute: str
) -> Callable[[Callable[..., int]], Callable[..., int]]:
    """`--from`, `--to`, `-n`, `-v`: the options both sync commands share."""
    options: list[Callable[[Callable[..., int]], Callable[..., int]]] = [
        _source_option(source_attribute),
        _target_option(target_attribute),
        click.option("-n", "--dry-run", is_flag=True, help="Show what would happen and stop."),
        click.option("-v", "--verbose", is_flag=True, help="Stream the client output."),
    ]

    def decorate(function: Callable[..., int]) -> Callable[..., int]:
        for option in reversed(options):
            function = option(function)
        return function

    return decorate


@sync_group.command(
    "postgres", short_help="Sync a PostgreSQL database onto a target (aliases: postgresql, pg)."
)
@_sync_options("postgres_sources", "postgres_targets")
@click.pass_obj
def sync_postgres_command(
    app: _App,
    source: str | None,
    target: str | None,
    dry_run: bool,
    verbose: bool,
) -> int:
    """Sync a PostgreSQL database onto a target.

    The target keeps its old database as `<name>_backup_YYYYMMDD_HHMMSS`
    until the sync succeeds (see `dbt clean`). Without --from/--to a
    terminal gets a picker; a script must pass them. `-n` prints the plan
    and stops.
    """
    secrets = app.secrets
    chosen_target = _choose(
        secrets.postgres_targets,
        target,
        kind="PostgreSQL target",
        flag="--to",
        label=_postgres_target_label,
        app=app,
    )
    if chosen_target is None:
        return 1
    chosen_source = _choose(
        secrets.postgres_sources,
        source,
        kind="PostgreSQL source",
        flag="--from",
        label=_postgres_source_label,
        app=app,
    )
    if chosen_source is None:
        return 1
    plan = plan_postgres_sync(chosen_source, chosen_target)
    app.stdout.write("\n🐘 PostgreSQL Sync\n")
    app.stdout.write("==================\n")
    app.stdout.write(f"  From: {chosen_source.name} → Database: {chosen_source.database}\n")
    app.stdout.write(f"  To: {chosen_target.name} → Database: {plan.target_database}\n")
    app.stdout.write(f"  Backup: {plan.backup}\n")
    app.stdout.write("\n")
    if dry_run:
        app.stdout.write("Dry run: nothing was changed\n")
        return 0
    return perform_postgres_sync(
        chosen_source,
        chosen_target,
        verbose=verbose,
        runtime=app.runtime,
        stdout=app.stdout,
        stderr=app.stderr,
        hint=_connection_hint(secrets.tunnels, app.runtime),
    )


@sync_group.command("mongodb", short_help="Sync a MongoDB database onto a target (alias: mongo).")
@_sync_options("mongo_sources", "mongo_targets")
@click.option("--db", "database", help="Database of the source row (name or index, default 1).")
@click.pass_obj
def sync_mongodb_command(
    app: _App,
    source: str | None,
    target: str | None,
    dry_run: bool,
    verbose: bool,
    database: str | None,
) -> int:
    """Sync a MongoDB database onto a target.

    The target keeps its old database as `<name>_backup_YYYYMMDD_HHMMSS`
    and is restored from it when the sync fails. Without --from/--to a
    terminal gets a picker; a script must pass them. `-n` prints the plan
    and stops.
    """
    secrets = app.secrets
    chosen_target = _choose(
        secrets.mongo_targets,
        target,
        kind="MongoDB target",
        flag="--to",
        label=_mongo_target_label,
        app=app,
    )
    if chosen_target is None:
        return 1
    chosen_source = _choose(
        secrets.mongo_sources,
        source,
        kind="MongoDB source",
        flag="--from",
        label=_mongo_source_label,
        app=app,
    )
    if chosen_source is None:
        return 1
    chosen_database = _select_mongo_database(
        chosen_source.databases, database if database is not None else "1", app.stderr
    )
    if chosen_database is None:
        return 1

    app.stdout.write("\n🍃 MongoDB Sync\n")
    app.stdout.write("==================\n")
    app.stdout.write(f"  From: {chosen_source.name} → Database: {chosen_database}\n")
    app.stdout.write(f"  To: {chosen_target.name} → Database: {chosen_database}\n")
    app.stdout.write(
        f"  Backup: {chosen_database}_backup_YYYYMMDD_HHMMSS when the target already has it\n"
    )
    app.stdout.write("\n")
    if dry_run:
        app.stdout.write("Dry run: nothing was changed\n")
        return 0
    return perform_mongo_sync(
        chosen_source.uri,
        chosen_database,
        chosen_target.uri,
        verbose=verbose,
        runtime=app.runtime,
        stdout=app.stdout,
        stderr=app.stderr,
        hint=_connection_hint(secrets.tunnels, app.runtime),
    )


def _postgres_source_label(source: PostgresSource) -> str:
    return f"{source.name} → {source.database}"


def _postgres_target_label(target: PostgresTarget) -> str:
    return target.name


def _mongo_source_label(source: MongoSource) -> str:
    return f"{source.name} → {','.join(source.databases)}"


def _mongo_target_label(target: MongoTarget) -> str:
    return target.name


def _connection_hint(
    tunnels: Sequence[VpsTunnel], runtime: Runtime
) -> Callable[[str, int], str]:
    return lambda host, port: connection_hint(tunnels, host, port, runtime=runtime)


def main() -> NoReturn:
    try:
        code = run_cli(sys.argv[1:], env=os.environ, stdout=sys.stdout, stderr=sys.stderr)
    except (KeyboardInterrupt, Interrupted):
        sys.stderr.write("\nInterrupted\n")
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
