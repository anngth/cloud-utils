"""Process, filesystem, clock, and signal dependencies injected into dbt."""

from __future__ import annotations

import os
import shutil
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from shared.process import CommandResult

from .process import run_command, run_pipe

_MANAGED_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class Interrupted(BaseException):
    """SIGINT/SIGTERM raised out of a child wait, after the child is killed.

    A BaseException so no ``except Exception`` can swallow it.
    """

    def __init__(self, signum: int = signal.SIGINT) -> None:
        super().__init__(signum)
        self.signum = signum


@dataclass
class SignalHooks:
    handler: Callable[[], int] | None = None
    _previous: dict[int, object] = field(default_factory=dict, repr=False)

    def bind(self, handler: Callable[[], int] | None) -> None:
        """Install the OS hook, or restore the handlers it displaced."""
        self.handler = handler
        if handler is None:
            for signum, previous in self._previous.items():
                signal.signal(signum, previous)  # type: ignore[arg-type]
            self._previous.clear()
            return
        if not self._previous:
            for signum in _MANAGED_SIGNALS:
                self._previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self._dispose)

    def _dispose(self, signum: int, _frame: object) -> None:
        raise Interrupted(signum)


@dataclass
class Runtime:
    run_command: Callable[..., CommandResult]
    run_pipe: Callable[..., CommandResult]
    which: Callable[[str], str | None]
    path_exists: Callable[[str], bool]
    process_alive: Callable[[int], bool]
    signal: Callable[[int, int], bool]
    sleep: Callable[[float], None]
    now: Callable[[], datetime]
    signals: SignalHooks


def run_with_interrupts(runtime: Runtime, body: Callable[[], int]) -> int:
    """Run ``body``; on Interrupted, unbind and run the rollback handler.

    No handler bound means nothing to roll back: the interrupt re-raises.
    """
    try:
        return body()
    except Interrupted:
        handler = runtime.signals.handler
        if handler is None:
            raise
        runtime.signals.bind(None)
        return handler()
    finally:
        if runtime.signals.handler is not None:
            runtime.signals.bind(None)


def _path_exists(value: str) -> bool:
    return Path(value).is_file()


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _signal(pid: int, signum: int) -> bool:
    try:
        os.kill(pid, signum)
    except OSError:
        return False
    return True


def default_runtime() -> Runtime:
    return Runtime(
        run_command=run_command,
        run_pipe=run_pipe,
        which=shutil.which,
        path_exists=_path_exists,
        process_alive=_process_alive,
        signal=_signal,
        sleep=time.sleep,
        now=datetime.now,
        signals=SignalHooks(),
    )
