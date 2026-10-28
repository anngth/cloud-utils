"""Interrupt mechanism: the child dies first, the rollback runs second.

The real-process test runs in a child interpreter so this process's
signal handlers are never touched.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import signal
import subprocess
import sys

import pytest
from shared.process import CommandResult

from db_tools.runtime import Interrupted, Runtime, SignalHooks, run_with_interrupts


def _runtime(signals: SignalHooks) -> Runtime:
    return Runtime(
        run_command=lambda *_a, **_k: CommandResult(0, "", ""),
        run_pipe=lambda *_a, **_k: CommandResult(0, "", ""),
        which=lambda _name: None,
        path_exists=lambda _path: False,
        process_alive=lambda _pid: False,
        signal=lambda _pid, _signum: True,
        sleep=lambda _seconds: None,
        now=datetime.now,
        signals=signals,
    )


class _RecordingSignals(SignalHooks):
    def __init__(self) -> None:
        super().__init__()
        self.bound: list[object] = []

    def bind(self, handler) -> None:
        self.bound.append(handler)
        self.handler = handler


def test_dispose_raises_interrupted_without_running_the_handler() -> None:
    hooks = SignalHooks()
    ran: list[bool] = []

    def rollback() -> int:
        ran.append(True)
        return 130

    hooks.handler = rollback

    with pytest.raises(Interrupted) as exc:
        hooks._dispose(signal.SIGTERM, None)

    assert exc.value.signum == signal.SIGTERM
    assert ran == []


def test_bind_installs_and_then_restores_the_previous_os_handlers() -> None:
    hooks = SignalHooks()
    def sentinel_int(_signum, _frame):
        return None

    def sentinel_term(_signum, _frame):
        return None

    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        signal.signal(signal.SIGINT, sentinel_int)
        signal.signal(signal.SIGTERM, sentinel_term)

        hooks.bind(lambda: 130)
        assert signal.getsignal(signal.SIGINT) == hooks._dispose
        assert signal.getsignal(signal.SIGTERM) == hooks._dispose

        hooks.bind(lambda: 131)  # re-arming keeps the originals
        hooks.bind(None)
        assert signal.getsignal(signal.SIGINT) is sentinel_int
        assert signal.getsignal(signal.SIGTERM) is sentinel_term
        assert hooks.handler is None
    finally:
        for signum, previous in saved.items():
            signal.signal(signum, previous)


def test_handler_runs_after_the_body_unwound_and_is_unbound_first() -> None:
    signals = _RecordingSignals()
    runtime = _runtime(signals)
    events: list[str] = []

    def rollback() -> int:
        events.append(f"rollback(handler={signals.handler})")
        return 130

    def body() -> int:
        signals.bind(rollback)
        events.append("child running")
        raise Interrupted()

    assert run_with_interrupts(runtime, body) == 130
    assert events == ["child running", "rollback(handler=None)"]
    assert signals.handler is None


def test_interrupt_with_nothing_bound_propagates() -> None:
    signals = _RecordingSignals()

    def body() -> int:
        raise Interrupted()

    with pytest.raises(Interrupted):
        run_with_interrupts(_runtime(signals), body)
    assert signals.bound == []


def test_normal_completion_unbinds_any_leftover_handler() -> None:
    signals = _RecordingSignals()

    def body() -> int:
        signals.bind(lambda: 130)
        return 0

    assert run_with_interrupts(_runtime(signals), body) == 0
    assert signals.handler is None


def test_other_exceptions_unbind_and_propagate() -> None:
    signals = _RecordingSignals()

    def body() -> int:
        signals.bind(lambda: 130)
        raise OSError("disk full")

    with pytest.raises(OSError):
        run_with_interrupts(_runtime(signals), body)
    assert signals.handler is None


def test_body_result_passes_through_untouched() -> None:
    signals = _RecordingSignals()
    assert run_with_interrupts(_runtime(signals), lambda: 7) == 7
    assert signals.bound == []


# Child interpreter: interrupts a real `sleep 30` and exits 0 only if the
# rollback ran after that child was dead, with nothing left bound.
_ORDERING_DRIVER = """\
import os
import signal
import sys
import threading
import time
from pathlib import Path

from db_tools.runtime import SignalHooks, default_runtime, run_with_interrupts

pid_file = Path(sys.argv[1])
runtime = default_runtime()
sigint_before = signal.getsignal(signal.SIGINT)
seen = {}
main_thread = threading.get_ident()


def child_alive() -> bool:
    try:
        os.kill(int(pid_file.read_text()), 0)
    except (ProcessLookupError, FileNotFoundError, ValueError):
        return False
    return True


def rollback() -> int:
    seen["alive_during_rollback"] = child_alive()
    seen["handler_during_rollback"] = runtime.signals.handler
    return 130


def interrupt_when_ready() -> None:
    deadline = time.monotonic() + 10
    while not pid_file.exists():
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)
    time.sleep(0.05)
    signal.pthread_kill(main_thread, signal.SIGINT)


def body() -> int:
    runtime.signals.bind(rollback)
    threading.Thread(target=interrupt_when_ready, daemon=True).start()
    runtime.run_command(["sh", "-c", f"echo $$ > {pid_file}; exec sleep 30"])
    return 0


code = run_with_interrupts(runtime, body)
ok = (
    code == 130
    and seen.get("alive_during_rollback") is False
    and seen.get("handler_during_rollback") is None
    and runtime.signals.handler is None
    and signal.getsignal(signal.SIGINT) is sigint_before
)
if not ok:
    print(f"code={code} seen={seen} sigint={signal.getsignal(signal.SIGINT)}", file=sys.stderr)
os._exit(0 if ok else 1)
"""


def test_real_interrupt_kills_the_child_before_the_rollback_runs(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _ORDERING_DRIVER, str(tmp_path / "child.pid")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_interrupted_is_not_an_exception_subclass() -> None:
    assert not issubclass(Interrupted, Exception)
    assert issubclass(Interrupted, BaseException)
