"""Real-subprocess tests for the pipe runners.

These spawn actual children (no fakes): the OS pipe buffer and signal
delivery cannot be simulated, so deadlocks and interrupt teardown are
only observable for real.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

import pytest

from db_tools.process import run_pipe

# 200 KB is far beyond the 64 KB macOS pipe buffer: the left child can
# only finish if its stderr is drained while the right child still runs.
BIG_STDERR = 200_000


def _wait_for_files(*paths: Path, timeout: float = 10.0) -> None:
    """Block until every path exists; fail loudly rather than hang forever."""
    deadline = time.monotonic() + timeout
    while not all(path.exists() for path in paths):
        if time.monotonic() >= deadline:
            raise AssertionError(f"children never wrote {paths}")
        time.sleep(0.01)


def test_captured_pipe_drains_a_left_stderr_larger_than_the_pipe_buffer() -> None:
    """A chatty left child must not deadlock the captured pipe."""
    outcomes: list = []
    errors: list[BaseException] = []

    def pipe() -> None:
        try:
            outcomes.append(
                run_pipe(
                    ["sh", "-c", f"yes | head -c {BIG_STDERR} >&2; echo ok"],
                    ["cat"],
                )
            )
        except BaseException as exc:  # noqa: BLE001 - re-checked in the main thread
            errors.append(exc)

    worker = threading.Thread(target=pipe, daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "run_pipe deadlocked on a full left-stderr pipe"
    assert errors == []
    result = outcomes[0]
    assert result.returncode == 0
    assert result.stdout == "ok\n"
    assert len(result.stderr) == BIG_STDERR  # drained AND captured, not dropped


@pytest.mark.parametrize("stream", [False, True])
def test_interrupt_mid_pipe_kills_both_children(tmp_path: Path, stream: bool) -> None:
    """A bound handler's SystemExit must not leave children running."""
    left_pid = tmp_path / "left.pid"
    right_pid = tmp_path / "right.pid"
    left = ["sh", "-c", f"echo $$ > {left_pid}; echo noise >&2; sleep 30"]
    right = ["sh", "-c", f"echo $$ > {right_pid}; cat"]

    def raise_system_exit(_signum: int, _frame: object) -> None:
        raise SystemExit(130)

    def interrupt_when_ready() -> None:
        _wait_for_files(left_pid, right_pid)
        os.kill(os.getpid(), signal.SIGINT)

    sniper = threading.Thread(target=interrupt_when_ready, daemon=True)
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, raise_system_exit)
    try:
        sniper.start()
        with pytest.raises(SystemExit) as exc:
            run_pipe(left, right, stream=stream)
    finally:
        signal.signal(signal.SIGINT, previous)

    assert exc.value.code == 130
    for path in (left_pid, right_pid):
        with pytest.raises(ProcessLookupError):
            os.kill(int(path.read_text().strip()), 0)
