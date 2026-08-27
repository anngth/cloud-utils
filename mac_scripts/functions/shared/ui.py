from types import MappingProxyType
from typing import Final

from .streams import TextWriter

CYAN: Final = "\033[36m"
GREEN: Final = "\033[32m"
BRIGHT_GREEN: Final = "\033[92m"
RED: Final = "\033[31m"
YELLOW: Final = "\033[33m"
GRAY: Final = "\033[90m"
BLACK: Final = "\033[30m"
BG_GREEN: Final = "\033[42m"
FG_RESET: Final = "\033[39m"
BG_RESET: Final = "\033[49m"

TONE_COLOR: Final = MappingProxyType(
    {
        "success": GREEN,
        "warning": YELLOW,
        "failure": RED,
        "muted": GRAY,
    }
)

def _fg(color: str, text: object) -> str:
    return f"{color}{text}{FG_RESET}"

def _split_lines(text: object) -> list[str]:
    return str(text).replace("\r\n", "\n").split("\n")

class FrameUi:
    def __init__(self, stdout: TextWriter, stderr: TextWriter) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.pipe = _fg(CYAN, "│")

    def _out(self, text: str = "") -> None:
        self.stdout.write(f"{text}\n")
        self.stdout.flush()

    def _err(self, text: str) -> None:
        self.stderr.write(f"{text}\n")
        self.stderr.flush()

    def line(self, text: object = "") -> None:
        self._out(str(text))

    def title(self, label: object) -> None:
        self._out()
        self._out(f"   {BG_GREEN}{BLACK} {label} {FG_RESET}{BG_RESET}")
        self._out(self.pipe)

    def step(self, text: object) -> None:
        self._out(f"{_fg(GREEN, '◇')}  {text}")
        self._out(self.pipe)

    def active(self, text: object) -> None:
        self._out(f"{_fg(CYAN, '◆')}  {text}")

    def section(self, text: object) -> None:
        self._out(self.pipe)
        self.active(text)

    def command(self, syntax: object, description: object = "") -> None:
        suffix = f"  {_fg(GRAY, description)}" if description else ""
        self._out(f"{self.pipe}  {_fg(GREEN, syntax)}{suffix}")

    def note(self, text: object) -> None:
        self._out(f"{self.pipe}  {_fg(GRAY, text)}")

    def prompt_text(self, text: object) -> str:
        return f"{_fg(CYAN, '◆')}  {text}"

    def item(
        self,
        text: object,
        *,
        tone: str = "success",
        marker: str | None = None,
    ) -> None:
        resolved = tone if tone in TONE_COLOR else "muted"
        symbol = marker or ("□" if resolved == "muted" else "■")
        first, *rest = _split_lines(text)
        self._out(f"{self.pipe}  {_fg(TONE_COLOR[resolved], symbol)} {first}")
        for continuation in rest:
            self.detail(continuation)

    def detail(self, text: object, *, tone: str = "muted") -> None:
        resolved = tone if tone in TONE_COLOR else "muted"
        for line in _split_lines(text):
            self._out(f"{self.pipe}      {_fg(TONE_COLOR[resolved], line)}")

    def end(self, text: object = "") -> None:
        suffix = f"  {_fg(BRIGHT_GREEN, text)}" if text else ""
        self._out(f"{_fg(CYAN, '└')}{suffix}")

    def error(self, text: object) -> None:
        self._err(_fg(RED, f"❌ {text}"))
