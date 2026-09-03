import sys

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
GRAY = "\033[38;5;247m"
GRAY_240 = "\033[38;5;240m"
BG_GREEN = "\033[42m"
FG_BLACK = "\033[30m"
FG_RESET = "\033[39m"
BG_RESET = "\033[49m"
RESET = "\033[0m"


def _fg(color: str, text: str) -> str:
    return f"{color}{text}{FG_RESET}"


class Ui:
    def __init__(self, stdout=None, stderr=None):
        self._stdout = sys.stdout if stdout is None else stdout
        self._stderr = sys.stderr if stderr is None else stderr
        self._last_was_blank = True

    def _out(self, line: str = "") -> None:
        self._stdout.write(f"{line}\n")
        self._last_was_blank = line == ""

    def _err(self, line: str) -> None:
        self._stderr.write(f"{line}\n")

    def title(self) -> None:
        self._out()
        self._out(f"  {BG_GREEN}{FG_BLACK} BREW DESIRED UPDATE {FG_RESET}{BG_RESET}")
        self._out()

    def step(self, text: str) -> None:
        if not self._last_was_blank:
            self._out()
        self._out(f"{_fg(GREEN, '◇')} {text}")

    def active(self, text: str) -> None:
        self._out(f"{GREEN}◆ {text}{RESET}")

    def warn(self, text: str) -> None:
        self._out(f"{_fg(YELLOW, '■')} {text}")

    def info(self, text: str) -> None:
        self._out(f"{GRAY}{text}{FG_RESET}")

    def command(self, line: str) -> None:
        if line.startswith("$"):
            self._out(f"{_fg(GREEN, '$')}{line[1:]}")
            return
        self._out(line)

    def error(self, text: str) -> None:
        self._err(_fg(RED, text))

    def usage(self) -> None:
        def command(syntax: str, description: str = "") -> None:
            desc = f"  {_fg(GRAY_240, description)}" if description else ""
            self._out(f"{_fg(GREEN, syntax)}{desc}")

        self.active("Usage: bud [command]")
        command("bud (help | -h | --help)", "Show this help")
        self.active("Update")
        command(
            "bud [(-e | --exclude) <cask>...]",
            "Update brew; upgrade desired casks",
        )
        self.active("Lists")
        command("bud (ls | list)", "Desired vs installed (formulae, taps, casks)")
        command("bud add <name...> [--cask | --formula | --tap]")
        command("bud remove <name...>")
        self.active("Options")
        command("--cask", "Force cask type (add only)")
        command("--formula", "Force formula type (add only)")
        command("--tap", "Force tap type (add only)")
        self.active("Notes")
        self.info(
            "Bare bud does not install missing packages; it upgrades installed desired casks."
        )
        self.info("user/repo = tap; user/repo/formula adds tap + formula.")
