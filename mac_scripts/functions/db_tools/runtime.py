"""Process, filesystem, clock, and signal dependencies injected into dbt."""

from __future__ import annotations

import os
import shutil
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shared.process import CommandResult

from .process import run_pipe, run_command


@dataclass
class SignalHooks:
    handler: Callable[[], int] | None = None

    def bind(self, handler: Callable[[], int] | None) -> None:
        """Install handler() on SIGINT/SIGTERM, or restore SIG_DFL for None."""
        self.handler = handler
        if handler is None:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            return
        signal.signal(signal.SIGINT, self._dispose)
        signal.signal(signal.SIGTERM, self._dispose)

    def _dispose(self, _signum: int, _frame: object) -> None:
        handler = self.handler
        raise SystemExit(130 if handler is None else handler())


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
