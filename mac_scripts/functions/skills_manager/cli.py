from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

import click

from shared.selector import SelectorItem, SelectorResult, run_selector
from shared.streams import is_tty

from .catalog_commands import (
    SkmUsageError, parse_source_command, run_source_command,
)
from .config import (
    Catalog,
    ConfigPaths,
    initialize_config,
    read_config,
    write_catalog,
)
from .state import InstalledState, load_installed_state, resolve_project_root
from .ui import SkmUi
from .upstream import AvailableSkill, discover_available_skills, has_command


def _operation_not_available(*_args: object, **_kwargs: object) -> object:
    raise NotImplementedError("Lifecycle operations are not available yet")


@dataclass(slots=True)
class Services:
    has_command: Callable[..., bool] = has_command
    initialize_config: Callable[..., ConfigPaths] = initialize_config
    read_config: Callable[[ConfigPaths], Catalog] = read_config
    write_catalog: Callable[[ConfigPaths, Catalog], None] = write_catalog
    resolve_project_root: Callable[..., Path] = resolve_project_root
    load_installed_state: Callable[..., InstalledState] = load_installed_state
    discover_available_skills: Callable[..., tuple[AvailableSkill, ...]] = (
        discover_available_skills
    )
    execute_install_plan: Callable[..., object] = _operation_not_available
    execute_uninstall_plan: Callable[..., object] = _operation_not_available
    selector_runner: Callable[..., SelectorResult] = run_selector


@dataclass(slots=True)
class CommandContext:
    cwd: Path
    env: Mapping[str, str]
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    paths: ConfigPaths
    catalog: Catalog
    ui: SkmUi
    services: Services
    select_items: Callable[..., SelectorResult]
    confirm: Callable[[object], bool]
    confirm_apply: Callable[..., bool]


def normalize_click_error(error: click.ClickException) -> str:
    return error.format_message()


def _parse_lifecycle_options(
    args: Sequence[str],
    *,
    allow_all: bool,
    allow_positionals: bool,
) -> tuple[tuple[str, ...], bool]:
    tokens: list[str] = []
    all_sources = False
    flags = {"-y", "--yes", "-d", "--dry-run"}
    if allow_all:
        flags.update(("--all",))
    for argument in args:
        if argument in flags:
            if argument == "--all":
                all_sources = True
        elif argument.startswith("-"):
            raise SkmUsageError(f"Unknown option: {argument}")
        elif not allow_positionals:
            raise SkmUsageError(f"Unexpected argument: {argument}")
        elif argument not in tokens:
            tokens.append(argument)
    if all_sources and tokens:
        raise SkmUsageError(
            "Cannot combine --all with explicit source targets"
        )
    return tuple(tokens), all_sources


def _validate_route_grammar(action: str | None, args: Sequence[str]) -> None:
    if action == "source":
        parse_source_command(args)
    elif action == "status":
        _parse_lifecycle_options(
            args, allow_all=False, allow_positionals=False
        )
    elif action in {"add", "remove"}:
        _parse_lifecycle_options(args, allow_all=True, allow_positionals=True)


def _requires_npx(action: str | None, args: Sequence[str]) -> bool:
    if action in {"status", "add", "remove"}:
        return True
    request = parse_source_command(args) if action == "source" else None
    return bool(
        request
        and request.action in {"add", "edit"}
        and not request.no_skills
    )


class _ValidatedCommand(click.Command):
    def __init__(
        self,
        name: str,
        *,
        validator: Callable[[Sequence[str]], None],
        callback: Callable[..., int],
    ) -> None:
        self._validator = validator
        parameter = click.Argument(
            ["tokens"], nargs=-1, type=click.UNPROCESSED, required=False
        )
        settings = {"ignore_unknown_options": True, "allow_extra_args": True}
        super().__init__(
            name, callback=callback, params=[parameter], add_help_option=False,
            context_settings=settings,
        )

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        self._validator(tuple(args))
        return super().parse_args(ctx, args)


def _wire_context(
    *, cwd: Path, env: Mapping[str, str], stdin: TextIO, stdout: TextIO,
    stderr: TextIO, paths: ConfigPaths, catalog: Catalog, ui: SkmUi,
    services: Services,
) -> CommandContext:
    context: CommandContext

    def select_items(
        items: Sequence[SelectorItem], *, initial: Sequence[str] = (),
        multiple: bool = True, title: object = "Select items",
        render: Callable[[object], object] | None = None,
    ) -> SelectorResult:
        renderer = render or (
            lambda state: ui.selector(
                title, state, mode="install" if multiple else "select"
            )
        )
        result = services.selector_runner(
            items, initial=initial, multiple=multiple, input=stdin,
            output=stdout, render=renderer,
        )
        if result.kind == "cancel" and render is None:
            ui.cancelled_selector(
                title, result.state, mode="install" if multiple else "select"
            )
        return result

    def confirm(message: object) -> bool:
        ui.confirm(message)
        result = select_items(
            (SelectorItem("Yes", "yes"), SelectorItem("No", "no")),
            multiple=False, title=message,
        )
        return result.kind == "submit" and result.selected == ("yes",)

    def confirm_apply(
        *, install: Sequence[object], remove: Sequence[object], catalog: Catalog,
    ) -> bool:
        heading = "Apply these changes?"
        result = select_items(
            (SelectorItem("Yes", "yes"), SelectorItem("No", "no")),
            multiple=False, title=heading,
            render=lambda state: ui.apply_preview(
                install=install, remove=remove, catalog=catalog,
                heading=heading, confirm_state=state,
            ),
        )
        return result.kind == "submit" and result.selected == ("yes",)

    context = CommandContext(
        cwd, env, stdin, stdout, stderr, paths, catalog, ui, services,
        select_items, confirm, confirm_apply,
    )
    return context


def _run_lifecycle_placeholder(
    action: str,
    args: Sequence[str],
    context: CommandContext,
) -> int:
    del context
    tokens, all_sources = _parse_lifecycle_options(
        args,
        allow_all=True,
        allow_positionals=True,
    )
    if not all_sources and not tokens:
        raise SkmUsageError(
            f"Usage: skm {action} <source|index...> [--all] "
            "[(-y | --yes)] [(-d | --dry-run)]"
        )
    return 1


def _dispatch(action: str | None, args: Sequence[str], context: CommandContext) -> int:
    if action is None:
        return 0
    if action == "source":
        return run_source_command(args, context)
    if action == "status":
        from .lifecycle import run_status

        return run_status(context)
    return _run_lifecycle_placeholder(action, args, context)


def run_cli(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    services: Services | None = None,
) -> int:
    actual_cwd = Path.cwd() if cwd is None else Path(cwd)
    actual_env = os.environ if env is None else env
    actual_stdin = sys.stdin if stdin is None else stdin
    actual_stdout = sys.stdout if stdout is None else stdout
    actual_stderr = sys.stderr if stderr is None else stderr
    actual_services = Services() if services is None else services
    ui = SkmUi(actual_stdout, actual_stderr)
    action = argv[0] if argv else None
    args = tuple(argv[1:])

    if action in {"help", "-h", "--help"}:
        ui.usage()
        return 0
    routes = frozenset(("source", "status", "add", "remove"))
    if action is not None and action not in routes:
        ui.error(f"Unknown command: {action}")
        ui.usage()
        return 1
    if action is None and (
        not is_tty(actual_stdin) or not is_tty(actual_stdout)
    ):
        ui.error(
            "skm requires an interactive terminal; use skm add, skm remove, "
            "or skm add --all"
        )
        return 1

    def callback(tokens: tuple[str, ...]) -> int:
        if _requires_npx(action, tokens) and not actual_services.has_command(
            "npx", env=actual_env
        ):
            ui.error("npx is required to run skills commands")
            return 1
        try:
            paths = actual_services.initialize_config(env=actual_env)
        except Exception:
            ui.error("Could not create config directory.")
            return 1
        try:
            catalog = actual_services.read_config(paths)
        except Exception as error:
            ui.error(str(error))
            return 1
        context = _wire_context(
            cwd=actual_cwd, env=actual_env, stdin=actual_stdin,
            stdout=actual_stdout, stderr=actual_stderr, paths=paths,
            catalog=catalog, ui=ui, services=actual_services,
        )
        return _dispatch(action, tokens, context)

    command = _ValidatedCommand(
        "skm", validator=lambda value: _validate_route_grammar(action, value),
        callback=callback,
    )
    try:
        result = command.main(
            args=list(args), prog_name="skm", standalone_mode=False,
        )
    except SkmUsageError as error:
        ui.error(str(error))
        return 1
    except click.ClickException as error:
        ui.error(normalize_click_error(error))
        return 1
    return int(result)


def main() -> NoReturn:
    raise SystemExit(run_cli(tuple(sys.argv[1:])))


if __name__ == "__main__":
    main()
