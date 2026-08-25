import os
import shutil
import sys

from brew import BrewRunner, load_brew_state, resolve_brew_binary
from config import load_desired, resolve_paths, save_desired
from list_cmd import run_list
from manage import run_add, run_remove
from messages import fmt
from parse import parse_argv
from ui import Ui
from update import run_update


def _listed_taps(runner, ui) -> list[str]:
    result = runner.run(["tap"], ui)
    return [line for line in (result.stdout or "").splitlines() if line]


def main(
    argv: list[str] | None = None,
    *,
    env: dict | None = None,
    ui=None,
    runner=None,
    columns: int | None = None,
    resolve_brew=None,
) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if env is None:
        env = os.environ
    if ui is None:
        ui = Ui()
    if resolve_brew is None:
        resolve_brew = resolve_brew_binary

    brew_bin = resolve_brew(env)
    if not brew_bin:
        ui.error(fmt("homebrew.missing"))
        return 1

    if runner is None:
        runner = BrewRunner(brew_bin)

    try:
        document = load_desired(env, brew_taps_fn=lambda: _listed_taps(runner, ui))
    except (OSError, ValueError) as err:
        ui.error(str(err))
        return 1

    ui.title()
    parsed = parse_argv(argv)
    if parsed.exclude and parsed.action is not None:
        ui.error(fmt("exclude.invalid"))
        return 1

    paths = resolve_paths(env)

    def save_fn(document):
        save_desired(paths.desired_file, document)
        ui.info(fmt("changes.saved", file=paths.desired_file))

    if parsed.action == "help":
        ui.usage()
        return 0
    if parsed.action == "list":
        if parsed.rest:
            ui.error(fmt("list.extra_args"))
            return 1
        state = load_brew_state(runner, ui)
        if columns is None:
            columns = shutil.get_terminal_size(fallback=(120, 24)).columns
        return run_list(ui, document, state, columns)
    if parsed.action == "add":
        if not parsed.names:
            ui.error(fmt("add.missing_name"))
            return 1
        return run_add(parsed.names, parsed.force_type, document, runner, ui, save_fn)
    if parsed.action == "remove":
        if not parsed.names:
            ui.error(fmt("remove.missing_name"))
            return 1
        return run_remove(parsed.names, document, ui, save_fn)
    if parsed.action is None:
        return run_update(parsed.exclude, document, runner, ui)

    ui.error(fmt("unknown.command", token=parsed.action))
    ui.error(fmt("unknown.hint"))
    return 1
