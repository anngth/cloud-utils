from __future__ import annotations

from shared.ui import (
    BRIGHT_GREEN,
    CYAN,
    GRAY,
    GREEN,
    RED,
    FrameUi,
    _fg,
    _split_lines,
)

from .selector import SelectorState

WHITE = "\033[97m"
RESET = "\033[0m"

class GitToolsUi(FrameUi):
    def usage(self) -> None:
        self.title("GT")
        self.step("Usage: gt <command>")
        self.command("gt (help | -h | --help)", "Show this help")

        self.section("Core")
        self.command("gt push", "Force push (safe --force-with-lease)")
        self.command(
            "gt fetch [--sync-upstream]", "Fetch with optional upstream sync"
        )

        self.section("Backup")
        self.command("gt backup [(-f | --force) | --dry-run]")
        self._continuation(
            "", "Interactive select; force and dry-run are mutually exclusive"
        )
        self.command("gt backup --all [(-f | --force) | --dry-run]")
        self._continuation("", "Backup or preview every listed repo")
        self.command(
            "gt backup stale [--days <n>] [--all] "
            "[(-f | --force) | --dry-run]"
        )
        self._continuation("", "Stale repos only (default 7 days)")
        self.command(
            "gt backup add <ssh-url> [<ssh-url> ...]",
            "Add SSH URL(s) to managed list",
        )
        self.command(
            "gt backup remove <index|ssh-url>",
            "Remove by 1-based index or URL",
        )

        self.section("Notes")
        self.note("Selector: space toggle, a all, c clear, enter start, q quit")
        self.note("Remove indexes are 1-based (same as skm).")
        self.note(
            "--force skips fingerprint short-circuit; cannot combine with --dry-run."
        )
        self.end()

    def _continuation(self, syntax: str, description: str) -> None:
        if syntax:
            suffix = f"  {_fg(GRAY, description)}" if description else ""
            self._out(f"{self.pipe}      {_fg(GREEN, syntax)}{suffix}")
        else:
            self._out(f"{self.pipe}      {_fg(GRAY, description)}")

    def success(self, text: object) -> None:
        self._out(f"{_fg(GREEN, '◇')}  {_fg(BRIGHT_GREEN, text)}")
        self._out(self.pipe)

    def warn(self, text: object) -> None:
        self.item(text, tone="warning")

    def list_end(self, text: object = "") -> None:
        self.end(text)

    def usage_line(self, message: object) -> None:
        self._err(_fg(GRAY, message))

    def begin(
        self,
        headline: object,
        *,
        label: object = "GT",
        section: object = "Progress",
    ) -> None:
        self.title(label)
        self.step(headline)
        self.active(section)
        self._out(self.pipe)

    def status(
        self,
        message: object,
        *,
        tone: str = "success",
        marker: str | None = None,
    ) -> None:
        self.item(message, tone=tone, marker=marker)

    def error_detail(self, message: object) -> None:
        for line in _split_lines(message):
            self._err(_fg(GRAY, f"   {line}"))

    def render_backup_selector(
        self,
        heading: object,
        state: SelectorState,
        *,
        list_path: object | None = None,
        now: object | None = None,
        cancelled: bool = False,
    ) -> None:
        del now
        self.stdout.write("\033[2J\033[H")
        self.title("REPO BACKUP")
        if list_path:
            self.step(list_path)
        self.step(heading)
        self.active(
            f"Select repos {_fg(WHITE, '(space toggle, a all, c clear, enter to start, q to quit)')}"
        )
        self._out(self.pipe)
        for index, entry in enumerate(state.items):
            selected = index in state.selected
            box = "■" if selected else "□"
            box_color = BRIGHT_GREEN if selected else GRAY
            label_color = WHITE if index == state.cursor else GRAY
            self._out(
                f"{self.pipe}  {index + 1}  {box_color}{box}{RESET}  "
                f"{_fg(label_color, entry.label)}"
            )
            self._out(self.pipe)
        if cancelled:
            self._out(f"{_fg(CYAN, '└')}  {_fg(RED, 'Selection cancelled')}")
        else:
            self.end()

    def cancelled_backup_selector(
        self,
        heading: object,
        state: SelectorState,
        *,
        list_path: object | None = None,
        now: object | None = None,
    ) -> None:
        self.render_backup_selector(
            heading,
            state,
            list_path=list_path,
            now=now,
            cancelled=True,
        )
