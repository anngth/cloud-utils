import os
import select
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


_MANAGED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
_TTY_ERROR_MESSAGES = {
    "interactive terminal required",
    "failed to disable terminal echo",
    "failed to restore terminal echo",
}
_CLIPBOARD_ERROR_MESSAGE = "failed to copy code to clipboard"


class _CliFailure(Exception):
    pass


class _SignalGuard:
    def __init__(self) -> None:
        self.received: list[int] = []
        self._previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, _MANAGED_SIGNALS
        )
        self._previous_handlers: dict[int, object] = {}
        self._previous_wakeup_fd = -1
        self._read_fd = -1
        self._write_fd = -1
        self._active = False

        try:
            self._read_fd, self._write_fd = os.pipe()
            os.set_blocking(self._read_fd, False)
            os.set_blocking(self._write_fd, False)
            self._previous_wakeup_fd = signal.set_wakeup_fd(self._write_fd)

            for signum in _MANAGED_SIGNALS:
                previous = signal.getsignal(signum)
                self._previous_handlers[signum] = previous
                if previous != signal.SIG_IGN:
                    signal.signal(signum, self._record)
        except BaseException:
            self.restore()
            raise

    @property
    def wakeup_fd(self) -> int:
        return self._read_fd

    def _record(self, signum: int, _frame: FrameType | None) -> None:
        self.received.append(signum)

    def activate(self) -> None:
        signal.pthread_sigmask(signal.SIG_SETMASK, self._previous_mask)
        self._active = True

    def block(self) -> None:
        signal.pthread_sigmask(signal.SIG_BLOCK, _MANAGED_SIGNALS)
        self._active = False

    def restore(self) -> None:
        if self._active:
            self.block()

        first_error: BaseException | None = None
        for signum, previous in reversed(self._previous_handlers.items()):
            try:
                signal.signal(signum, previous)
            except BaseException as error:
                if first_error is None:
                    first_error = error

        if self._write_fd >= 0:
            try:
                signal.set_wakeup_fd(self._previous_wakeup_fd)
            except BaseException as error:
                if first_error is None:
                    first_error = error

        for fd in (self._read_fd, self._write_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError as error:
                    if first_error is None:
                        first_error = error

        self._read_fd = -1
        self._write_fd = -1

        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, self._previous_mask)
        except BaseException as error:
            if first_error is None:
                first_error = error

        if first_error is not None:
            raise first_error


def _write_tty(fd: int, text: str) -> None:
    remaining = text.encode("utf-8")
    while remaining:
        written = os.write(fd, remaining)
        if written == 0:
            raise OSError("terminal write made no progress")
        remaining = remaining[written:]


def _drain_wakeup(fd: int) -> None:
    while True:
        try:
            if not os.read(fd, 4096):
                return
        except BlockingIOError:
            return


def _read_hidden_line(
    tty_fd: int, guard: _SignalGuard
) -> tuple[str | None, list[int]]:
    data = bytearray()

    while True:
        if guard.received:
            return None, list(guard.received)

        try:
            readable, _, _ = select.select([tty_fd, guard.wakeup_fd], [], [])
        except InterruptedError:
            continue

        if guard.wakeup_fd in readable:
            _drain_wakeup(guard.wakeup_fd)
            if guard.received:
                return None, list(guard.received)

        if tty_fd not in readable:
            continue

        chunk = os.read(tty_fd, 1)
        if not chunk or chunk in {b"\n", b"\r"}:
            return data.decode("utf-8"), []
        data.extend(chunk)


def _read_secret_once(
    prompt: str,
    *,
    tty_path: str,
    show_prompt: bool,
) -> tuple[str | None, list[int]]:
    guard = _SignalGuard()
    tty = None
    original = None

    try:
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

        try:
            original = termios.tcgetattr(tty.fileno())
        except (OSError, termios.error):
            raise RuntimeError("interactive terminal required") from None

        hidden = list(original)
        hidden[3] &= ~termios.ECHO

        try:
            termios.tcsetattr(tty.fileno(), termios.TCSADRAIN, hidden)
        except (OSError, termios.error):
            raise RuntimeError("failed to disable terminal echo") from None

        if show_prompt:
            try:
                _write_tty(tty.fileno(), prompt)
            except OSError:
                raise RuntimeError("interactive terminal required") from None

        guard.activate()
        try:
            try:
                secret, caught_signals = _read_hidden_line(tty.fileno(), guard)
            except (OSError, UnicodeError):
                raise RuntimeError("interactive terminal required") from None
        finally:
            guard.block()

        if guard.received:
            return None, list(guard.received)

        try:
            _write_tty(tty.fileno(), "\n")
        except OSError:
            raise RuntimeError("interactive terminal required") from None
        return secret, caught_signals
    finally:
        try:
            if original is not None and tty is not None:
                try:
                    termios.tcsetattr(tty.fileno(), termios.TCSADRAIN, original)
                except (OSError, termios.error):
                    raise RuntimeError("failed to restore terminal echo") from None
        finally:
            try:
                if tty is not None:
                    tty.close()
            finally:
                guard.restore()


def read_secret(prompt: str, *, tty_path: str = "/dev/tty") -> str:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("interactive terminal required")

    show_prompt = True
    while True:
        secret, caught_signals = _read_secret_once(
            prompt,
            tty_path=tty_path,
            show_prompt=show_prompt,
        )
        if not caught_signals:
            return secret or ""

        for signum in caught_signals:
            os.kill(os.getpid(), signum)

        show_prompt = False


def run_cli(
    argv: Sequence[str],
    *,
    ui: TwoFactorUi | None = None,
    read_secret_fn: Callable[[str], str] = read_secret,
    copy_fn: Callable[[str], None] = copy_to_clipboard,
    now: float | int | None = None,
) -> int:
    resolved_ui = ui if ui is not None else TwoFactorUi(sys.stdout, sys.stderr)
    frame_open = False

    def close_frame() -> None:
        nonlocal frame_open
        if frame_open:
            resolved_ui.end()
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

        try:
            secret = read_secret_fn(resolved_ui.secret_prompt())
        except RuntimeError as error:
            if str(error) not in _TTY_ERROR_MESSAGES:
                raise
            raise _CliFailure(str(error)) from None

        try:
            code = generate_totp(secret, now=now)
        except ValueError as error:
            raise _CliFailure(str(error)) from None

        try:
            copy_fn(code)
        except RuntimeError as error:
            if str(error) != _CLIPBOARD_ERROR_MESSAGE:
                raise
            raise _CliFailure(str(error)) from None

        resolved_ui.success_copied(code)
        frame_open = False
        return 0

    try:
        context = command.make_context("2fa", list(argv))
        with context:
            result = command.invoke(context)
    except (KeyboardInterrupt, click.Abort):
        close_frame()
        return 130
    except (_CliFailure, click.ClickException) as error:
        close_frame()
        resolved_ui.error(str(error))
        return 1
    except BaseException:
        close_frame()
        raise

    return int(result)


def main() -> NoReturn:
    raise SystemExit(run_cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
