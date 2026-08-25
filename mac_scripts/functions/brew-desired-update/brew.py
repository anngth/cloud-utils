import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

OPT_BREW = "/opt/homebrew/bin/brew"
INTEL_BREW = "/usr/local/bin/brew"


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def resolve_brew_binary(env: dict) -> str | None:
    path_value = env.get("PATH", "")
    for directory in path_value.split(":"):
        if not directory:
            continue
        candidate = os.path.join(directory, "brew")
        if _is_executable(candidate):
            return candidate
    if _is_executable(OPT_BREW):
        return OPT_BREW
    if _is_executable(INTEL_BREW):
        return INTEL_BREW
    return None


def format_brew_command(args: list[str]) -> str:
    quoted = []
    for arg in args:
        if arg == "":
            quoted.append("''")
        elif re.fullmatch(r"[A-Za-z0-9_./:=+-]+", arg):
            quoted.append(arg)
        else:
            quoted.append("'{}'".format(arg.replace("'", "'\\''")))
    return f"$ brew {' '.join(quoted)}"


def is_probe(args: list[str]) -> bool:
    if not args:
        return False
    cmd = args[0]
    rest = args[1:]
    if cmd in ("list", "info", "search"):
        return True
    if cmd == "tap" and not rest:
        return True
    if cmd == "trust" and "--help" in rest:
        return True
    if cmd == "--help":
        return True
    return False


class _NoOpUi:
    def command(self, line: str) -> None:
        pass


class BrewRunner:
    def __init__(self, brew_bin: str) -> None:
        self.brew_bin = brew_bin

    def run(self, args: list[str], ui) -> subprocess.CompletedProcess:
        ui.command(format_brew_command(args))
        command = [self.brew_bin, *args]
        if is_probe(args):
            return subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
        result = subprocess.run(command, check=False)
        return subprocess.CompletedProcess(
            args=command,
            returncode=result.returncode,
            stdout="",
            stderr="",
        )


def _parse_lines(text: str) -> list[str]:
    return [line for line in re.split(r"\r?\n", text) if line]


def _short_name(name: str) -> str:
    slash = name.rfind("/")
    return name[slash + 1:] if slash != -1 else name


def load_brew_state(runner: BrewRunner, ui=None) -> dict:
    log_ui = ui if ui is not None else _NoOpUi()

    def run_probe(args: list[str]) -> subprocess.CompletedProcess:
        return runner.run(args, log_ui)

    with ThreadPoolExecutor(max_workers=3) as pool:
        formulas_future = pool.submit(run_probe, ["list", "--formula"])
        casks_future = pool.submit(run_probe, ["list", "--cask", "--full-name"])
        taps_future = pool.submit(run_probe, ["tap"])
        formulas_result = formulas_future.result()
        casks_result = casks_future.result()
        taps_result = taps_future.result()

    return {
        "formulas": [_short_name(n) for n in _parse_lines(formulas_result.stdout)],
        "casks": [_short_name(n) for n in _parse_lines(casks_result.stdout)],
        "taps": _parse_lines(taps_result.stdout),
    }


def ensure_tap(tap: str, runner: BrewRunner) -> bool:
    silent = _NoOpUi()
    trust_help = runner.run(["trust", "--help"], silent)
    if trust_help.returncode == 0:
        trust = runner.run(["trust", "--tap", tap], silent)
        if trust.returncode != 0:
            return False
    tap_result = runner.run(["tap", tap], silent)
    return tap_result.returncode == 0
