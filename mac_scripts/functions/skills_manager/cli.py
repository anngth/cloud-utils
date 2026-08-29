from __future__ import annotations
import os, re, sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import NoReturn, TextIO
import click
from shared.selector import SelectorItem, SelectorResult, run_selector
from shared.streams import is_tty
from .catalog_commands import SkmUsageError, parse_source_command, run_source_command
from .config import Catalog, ConfigPaths, initialize_config, read_config, write_catalog
from .lifecycle import (
    parse_lifecycle_command, run_add, run_interactive, run_remove, run_status,
)
from .state import InstalledState, load_installed_state, resolve_project_root
from .ui import SkmUi
from .upstream import (
    AvailableSkill, ExecutionResult, discover_available_skills,
    execute_install_plan, execute_uninstall_plan, has_command,
)
_SELECTOR_WARNING = re.compile(
    r"(?m)^Warning: Input is not a terminal \(fd=-?\d+\)\.\n"
)
@dataclass(slots=True)
class Services:
    has_command: Callable[..., bool] = has_command
    initialize_config: Callable[..., ConfigPaths] = initialize_config
    read_config: Callable[[ConfigPaths], Catalog] = read_config
    write_catalog: Callable[[ConfigPaths, Catalog], None] = write_catalog
    resolve_project_root: Callable[..., Path] = resolve_project_root
    load_installed_state: Callable[..., InstalledState] = load_installed_state
    discover_available_skills: Callable[..., tuple[AvailableSkill, ...]] = (
        discover_available_skills)
    execute_install_plan: Callable[..., ExecutionResult] = execute_install_plan
    execute_uninstall_plan: Callable[..., ExecutionResult] = execute_uninstall_plan
    selector_runner: Callable[..., SelectorResult] = run_selector
@dataclass(slots=True)
class CommandContext:
    cwd: Path; env: Mapping[str, str]
    stdin: TextIO; stdout: TextIO; stderr: TextIO
    paths: ConfigPaths; catalog: Catalog; ui: SkmUi; services: Services
    select_items: Callable[..., SelectorResult]
    confirm: Callable[[object], bool]; confirm_apply: Callable[..., bool]
def _parse_request(action, args):
    if action == "source":
        request = parse_source_command(args)
        return request, bool(
            request and request.action in {"add", "edit"} and not request.no_skills)
    if action in {"status", "add", "remove"}:
        return parse_lifecycle_command(action, args), True
    return None, False
def _wire_context(*, cwd, env, stdin, stdout, stderr, paths, catalog, ui,
                  services) -> CommandContext:
    def select_items(
        items: Sequence[SelectorItem], *, initial: Sequence[str] = (),
        multiple: bool = True, title: object = "Select items",
        render: Callable[[object], object] | None = None,
        reducer: Callable[..., SelectorResult] | None = None,
    ) -> SelectorResult:
        mode = "install" if multiple else "select"
        renderer = render or (lambda state: ui.selector(title, state, mode=mode))
        diagnostics = StringIO()
        try:
            with redirect_stderr(diagnostics):
                reducer_option = {} if reducer is None else {"reducer": reducer}
                result = services.selector_runner(
                    items, initial=initial, multiple=multiple, input=stdin,
                    output=stdout, render=renderer, **reducer_option)
        finally:
            stderr.write(_SELECTOR_WARNING.sub("", diagnostics.getvalue()))
            stderr.flush()
        if result.kind == "cancel" and render is None:
            ui.cancelled_selector(title, result.state, mode=mode)
        return result
    def answer(message, render=None) -> bool:
        if render is None:
            ui.confirm(message)
        result = select_items(
            (SelectorItem("Yes", "yes"), SelectorItem("No", "no")),
            multiple=False, title=message, render=render)
        return result.kind == "submit" and result.selected == ("yes",)
    def confirm_apply(*, install, remove, catalog) -> bool:
        heading = "Apply these changes?"
        def render(state):
            return ui.apply_preview(
                install=install, remove=remove, catalog=catalog,
                heading=heading, confirm_state=state)
        return answer(heading, render)
    return CommandContext(
        cwd, env, stdin, stdout, stderr, paths, catalog, ui, services,
        select_items, answer, confirm_apply)
def _dispatch(action, args, request, context) -> int:
    if action is None: return run_interactive(context)
    if action == "source": return run_source_command(args, context)
    if action == "status": return run_status(context)
    operation = run_add if action == "add" else run_remove
    tokens, all_sources, yes, dry_run = request
    return operation(
        tokens, all_sources=all_sources, yes=yes, dry_run=dry_run, context=context)
def run_cli(
    argv: Sequence[str], *, cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None, stdin: TextIO | None = None,
    stdout: TextIO | None = None, stderr: TextIO | None = None,
    services: Services | None = None,
) -> int:
    cwd = Path.cwd() if cwd is None else Path(cwd)
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    services = Services() if services is None else services
    ui = SkmUi(stdout, stderr)
    action, args = (argv[0], tuple(argv[1:])) if argv else (None, ())
    if action in {"help", "-h", "--help"}:
        ui.usage(); return 0
    if action is not None and action not in {"source", "status", "add", "remove"}:
        ui.error(f"Unknown command: {action}"); ui.usage()
        return 1
    if action is None and (not is_tty(stdin) or not is_tty(stdout)):
        ui.error(
            "skm requires an interactive terminal; use skm add, skm remove, "
            "or skm add --all")
        return 1
    def callback(tokens: tuple[str, ...]) -> int:
        if needs_npx and not services.has_command("npx", env=env):
            ui.error("npx is required to run skills commands"); return 1
        try: paths = services.initialize_config(env=env)
        except Exception:
            ui.error("Could not create config directory."); return 1
        try: catalog = services.read_config(paths)
        except Exception as error:
            ui.error(str(error)); return 1
        context = _wire_context(
            cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
            paths=paths, catalog=catalog, ui=ui, services=services)
        return _dispatch(action, tokens, request, context)
    command = click.Command(
        "skm", callback=callback,
        params=[click.Argument(["tokens"], nargs=-1, type=click.UNPROCESSED)],
        add_help_option=False,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )
    try:
        request, needs_npx = _parse_request(action, args)
        result = command.main(args=list(args), prog_name="skm", standalone_mode=False)
    except (SkmUsageError, click.ClickException) as error:
        message = (error.format_message()
                   if isinstance(error, click.ClickException) else str(error))
        ui.error(message)
        return 1
    return int(result)
def main() -> NoReturn: raise SystemExit(run_cli(tuple(sys.argv[1:])))
if __name__ == "__main__":
    main()
