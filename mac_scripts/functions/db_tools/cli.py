"""dbt command line entry point (click)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import NoReturn, Protocol, TextIO, TypedDict

import click
from click.shell_completion import CompletionItem, ZshComplete
from shared.selector import SelectorItem, SelectorState, run_selector
from shared.streams import is_tty
from shared.ui import BRIGHT_GREEN, GRAY

from .config import ConfigError, load_secrets, resolve_secrets_file
from .listing import render_listing
from .models import Secrets
from .mongo import clear_mongo_backups, perform_mongo_sync
from .postgres import clear_postgres_backups, perform_postgres_sync, plan_postgres_sync
from .runtime import Interrupted, Runtime, default_runtime
from .secrets import SecretsError
from .tunnels import connect_tunnels, connection_hint, disconnect_tunnels

_CONTEXT = {"help_option_names": ["-h", "--help"], "max_content_width": 100}
_CLEAR_SCREEN = "\033[2J\033[H"
_WHITE = "\033[97m"
_RESET = "\033[0m"
_LIST_HINT = "   Use 'dbt list' to list available configurations\n"
_ROW_HELP = "name or 1-based index from `dbt list`"
_POSTGRES_ALIASES = ("postgres", "pg")
_MONGO_ALIASES = ("mongodb", "mongo")


class _Io(TypedDict):
    """The keyword arguments every engine function takes."""

    runtime: Runtime
    stdout: TextIO
    stderr: TextIO


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


def _position(names: Sequence[str], text: str, kind: str) -> int:
    """Position of the name `text`, or of that 1-based index; `_Fatal` otherwise."""
    if text in names:
        return names.index(text)
    if text.isdigit() and 1 <= int(text) <= len(names):
        return int(text) - 1
    choices = ", ".join(f"{i}) {name}" for i, name in enumerate(names, start=1)) or "none"
    noun = kind.rsplit(" ", 1)[-1]
    raise _Fatal(f"❌ Error: Unknown {kind}: {text}\n   Available {noun}s: {choices}")


def _select[T: _Named](items: Sequence[T], text: str, kind: str) -> T:
    return items[_position([item.name for item in items], text, kind)]


def _choose[T: _Named](
    items: Sequence[T], text: str | None, *, kind: str, flag: str, label: Callable[[T], str],
    app: _App,
) -> T:
    """Resolve `text` (name or index); when absent, pick interactively or explain."""
    if text is None:
        if not items:
            raise _Fatal(f"❌ Error: No {kind} configured")
        if not app.interactive:
            raise _Fatal(f"❌ Error: {flag} is required outside a terminal\n{_LIST_HINT}".rstrip())
        options = [SelectorItem(label(item), item.name) for item in items]
        text = _pick(f"Select the {kind}", options, app)
        if text is None:
            raise _Fatal("Cancelled")
    return _select(items, text, kind)


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


@dbt.command("config", short_help="Print the secrets file path; `config edit` opens it.")
@click.argument("action", type=click.Choice(["edit"]), required=False)
@click.pass_obj
def config_command(app: _App, action: str | None) -> int:
    """Print where the secrets file is; `dbt config edit` opens it in $EDITOR."""
    if action == "edit":
        _edit(app.secrets_path)
    else:
        app.stdout.write(f"{app.secrets_path}\n")
    return 0


@dbt.command("connect", short_help="Open the SSH tunnels; SERVICE filters by name or port.")
@click.argument("service", required=False)
@click.pass_obj
def connect_command(app: _App, service: str | None) -> int:
    """Open the SSH tunnels of the secrets file (SERVICE filters by name or port)."""
    return connect_tunnels(app.secrets.tunnels, service=service, **_io(app))


@dbt.command("disconnect", short_help="Close the SSH tunnels dbt started.")
@click.argument("service", required=False)
@click.pass_obj
def disconnect_command(app: _App, service: str | None) -> int:
    """Close the SSH tunnels started by dbt (SERVICE filters by name or port)."""
    return disconnect_tunnels(app.secrets.tunnels, service=service, **_io(app))


@dbt.command("list", short_help="Show tunnels (UP/DOWN/BUSY) and every sync row (alias: ls).")
@click.pass_obj
def list_command(app: _App) -> int:
    """Show every tunnel with its local state and every database row with its index.

    No network call is made; `dbt status` adds the connection probes.
    """
    return _show(app, tunnel_status=True, probe=False)


@dbt.command("status", short_help="Like list, plus an SSH check on DOWN tunnels and DB probes.")
@click.pass_obj
def status_command(app: _App) -> int:
    """Show tunnel status (DOWN rows get an SSH check) and probe every database."""
    return _show(app, tunnel_status=True, probe=True, ssh_check=True)


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
@click.pass_obj
def clean_command(app: _App, engine: str | None, dry_run: bool) -> int:
    """Delete the `<database>_backup_YYYYMMDD_HHMMSS` databases a sync left behind.

    Without ENGINE every PostgreSQL target is cleaned, then every MongoDB target.
    """
    secrets = app.secrets
    pg, mongo = secrets.postgres_targets, secrets.mongo_targets
    # (engine, how many targets, the cleanup itself)
    engines: list[tuple[str, int, Callable[[], int]]] = []
    if engine is None or engine in _POSTGRES_ALIASES:
        engines.append(
            ("PostgreSQL", len(pg), lambda: clear_postgres_backups(pg, dry_run=dry_run, **_io(app)))
        )
    if engine is None or engine in _MONGO_ALIASES:
        engines.append(
            ("MongoDB", len(mongo), lambda: clear_mongo_backups(mongo, dry_run=dry_run, **_io(app)))
        )
    if not any(count for _name, count, _clear in engines):
        names = " or ".join(name for name, _count, _clear in engines)
        app.stdout.write(f"ℹ️  No {names} targets configured\n")
        return 0

    app.stdout.write("\n🧹 Backup Cleanup\n")
    app.stdout.write("=================\n")
    failed = False
    for name, count, clear in engines:
        if not count:
            app.stdout.write(f"ℹ️  No {name} targets configured\n\n")
            continue
        app.stdout.write(f"  {name} targets: {count}\n\n")
        failed = clear() != 0 or failed
    return 1 if failed else 0


# --- sync -----------------------------------------------------------------------


@dbt.group(
    "sync",
    cls=_Group,
    aliases={"pg": "postgres", "mongo": "mongodb"},
    short_help="Sync a PostgreSQL or MongoDB database onto a target.",
)
def sync_group() -> int:
    """Sync a PostgreSQL or MongoDB database onto a target (backups: `dbt clean`)."""
    return 0


def _sync_options(
    source_attribute: str, target_attribute: str
) -> Callable[[Callable[..., int]], Callable[..., int]]:
    """`--from`, `--to`, `-n`, `-v`: the options both sync commands share."""
    options: list[Callable[[Callable[..., int]], Callable[..., int]]] = [
        click.option(
            "--from",
            "source",
            help=f"Source row ({_ROW_HELP}).",
            shell_complete=_complete_rows(source_attribute),
        ),
        click.option(
            "--to",
            "target",
            help=f"Target row ({_ROW_HELP}).",
            shell_complete=_complete_rows(target_attribute),
        ),
        click.option("-n", "--dry-run", is_flag=True, help="Show what would happen and stop."),
        click.option("-v", "--verbose", is_flag=True, help="Stream the client output."),
    ]

    def decorate(function: Callable[..., int]) -> Callable[..., int]:
        for option in reversed(options):
            function = option(function)
        return function

    return decorate


def _print_plan(app: _App, title: str, source: str, target: str, backup: str) -> None:
    app.stdout.write(f"\n{title}\n{'=' * 18}\n")
    app.stdout.write(f"  From: {source}\n  To: {target}\n  Backup: {backup}\n\n")


@sync_group.command(
    "postgres", short_help="Sync a PostgreSQL database onto a target (alias: pg)."
)
@_sync_options("postgres_sources", "postgres_targets")
@click.pass_obj
def sync_postgres_command(
    app: _App, source: str | None, target: str | None, dry_run: bool, verbose: bool
) -> int:
    """Sync a PostgreSQL database onto a target.

    The target keeps its old database as `<name>_backup_YYYYMMDD_HHMMSS`
    until the sync succeeds (see `dbt clean`). Without --from/--to a
    terminal gets a picker; a script must pass them. `-n` prints the plan
    and stops.
    """
    secrets = app.secrets
    to = _choose(
        secrets.postgres_targets, target, kind="PostgreSQL target", flag="--to",
        label=lambda t: t.name, app=app,
    )
    src = _choose(
        secrets.postgres_sources, source, kind="PostgreSQL source", flag="--from",
        label=lambda s: f"{s.name} → {s.database}", app=app,
    )
    plan = plan_postgres_sync(src, to)
    _print_plan(
        app,
        "🐘 PostgreSQL Sync",
        f"{src.name} → Database: {src.database}",
        f"{to.name} → Database: {plan.target_database}",
        plan.backup,
    )
    if dry_run:
        app.stdout.write("Dry run: nothing was changed\n")
        return 0
    return perform_postgres_sync(
        src, to, verbose=verbose, hint=_connection_hint(app), **_io(app)
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
    to = _choose(
        secrets.mongo_targets, target, kind="MongoDB target", flag="--to",
        label=lambda t: t.name, app=app,
    )
    src = _choose(
        secrets.mongo_sources, source, kind="MongoDB source", flag="--from",
        label=lambda s: f"{s.name} → {','.join(s.databases)}", app=app,
    )
    name = src.databases[_position(src.databases, database or "1", "MongoDB database")]
    _print_plan(
        app,
        "🍃 MongoDB Sync",
        f"{src.name} → Database: {name}",
        f"{to.name} → Database: {name}",
        f"{name}_backup_YYYYMMDD_HHMMSS when the target already has it",
    )
    if dry_run:
        app.stdout.write("Dry run: nothing was changed\n")
        return 0
    return perform_mongo_sync(
        src.uri, name, to.uri, verbose=verbose, hint=_connection_hint(app), **_io(app)
    )


def _io(app: _App) -> _Io:
    return {"runtime": app.runtime, "stdout": app.stdout, "stderr": app.stderr}


def _connection_hint(app: _App) -> Callable[[str, int], str]:
    tunnels = app.secrets.tunnels
    return lambda host, port: connection_hint(tunnels, host, port, runtime=app.runtime)


def _show(
    app: _App, *, tunnel_status: bool, probe: bool, ssh_check: bool = False
) -> int:
    """The secrets file as `list` and `status` show it."""
    return render_listing(
        app.secrets,
        runtime=app.runtime,
        stdout=app.stdout,
        tunnel_status=tunnel_status,
        probe=probe,
        ssh_check=ssh_check,
    )


def main() -> NoReturn:
    try:
        code = run_cli(sys.argv[1:], env=os.environ, stdout=sys.stdout, stderr=sys.stderr)
    except (KeyboardInterrupt, Interrupted):
        sys.stderr.write("\nInterrupted\n")
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
