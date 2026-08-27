import os
import signal
import sys
import termios
import threading
from types import FrameType
from typing import Callable, NoReturn, Sequence

import click

from .clipboard import copy_to_clipboard
from .totp import generate_totp
from .ui import TwoFactorUi


class _TerminationSignal(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def _raise_termination_signal(signum: int, _frame: FrameType | None) -> None:
    raise _TerminationSignal(signum)


def _write_tty(fd: int, text: str) -> None:
    remaining = text.encode("utf-8")
    while remaining:
        written = os.write(fd, remaining)
        remaining = remaining[written:]


def read_secret(prompt: str, *, tty_path: str = "/dev/tty") -> str:
    try:
        tty_fd = os.open(tty_path, os.O_RDWR)
    except OSError:
        raise RuntimeError("interactive terminal required") from None

    try:
        tty = open(
            tty_fd,
            "r",
            encoding="utf-8",
            buffering=1,
            closefd=True,
        )
    except (OSError, ValueError):
        os.close(tty_fd)
        raise RuntimeError("interactive terminal required") from None

    pending_signal: int | None = None
    previous_handlers: dict[int, signal.Handlers] = {}

    try:
        with tty:
            try:
                original = termios.tcgetattr(tty.fileno())
            except (OSError, termios.error):
                raise RuntimeError("interactive terminal required") from None

            hidden = list(original)
            hidden[3] &= ~termios.ECHO

            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGTERM, signal.SIGHUP):
                    previous_handlers[signum] = signal.getsignal(signum)
                    signal.signal(signum, _raise_termination_signal)

            try:
                try:
                    termios.tcsetattr(tty.fileno(), termios.TCSADRAIN, hidden)
                except (OSError, termios.error):
                    raise RuntimeError("failed to disable terminal echo") from None

                try:
                    _write_tty(tty.fileno(), prompt)
                    secret = tty.readline().rstrip("\r\n")
                    _write_tty(tty.fileno(), "\n")
                except (OSError, UnicodeError, termios.error):
                    raise RuntimeError("interactive terminal required") from None
            except _TerminationSignal as interrupted:
                pending_signal = interrupted.signum
                secret = ""
            finally:
                try:
                    termios.tcsetattr(tty.fileno(), termios.TCSADRAIN, original)
                except (OSError, termios.error):
                    raise RuntimeError("failed to restore terminal echo") from None
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)

    if pending_signal is not None:
        os.kill(os.getpid(), pending_signal)
        raise _TerminationSignal(pending_signal)

    return secret


def run_cli(
    argv: Sequence[str],
    *,
    ui: TwoFactorUi | None = None,
    read_secret_fn: Callable[[str], str] = read_secret,
    copy_fn: Callable[[str], None] = copy_to_clipboard,
    now: float | int | None = None,
) -> int:
    resolved_ui = ui or TwoFactorUi(sys.stdout, sys.stderr)
    frame_open = False

    @click.command(
        add_help_option=False,
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    def command() -> int:
        nonlocal frame_open
        action = argv[0] if argv else None

        if action in {"help", "-h", "--help"}:
            resolved_ui.usage()
            return 0

        if action is not None:
            resolved_ui.error(f"Unknown command: {action}")
            resolved_ui.usage()
            return 1

        resolved_ui.begin_totp()
        frame_open = True
        secret = read_secret_fn(resolved_ui.secret_prompt())
        code = generate_totp(secret, now=now)
        copy_fn(code)
        resolved_ui.success_copied(code)
        frame_open = False
        return 0

    try:
        result = command.main(args=list(argv), prog_name="2fa", standalone_mode=False)
    except click.Abort:
        if frame_open:
            resolved_ui.end()
        return 130
    except (ValueError, RuntimeError, click.ClickException) as error:
        if frame_open:
            resolved_ui.end()
        resolved_ui.error(str(error))
        return 1

    return int(result)


def main() -> NoReturn:
    raise SystemExit(run_cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
