"""Real-subprocess tests for the pipe runners.

These spawn actual children (no fakes): the OS pipe buffer and signal
delivery cannot be simulated, so deadlocks and interrupt teardown are
only observable for real.

The interrupt scenarios run in a disposable child Python process: a
handler that raises inside the test process leaves the interpreter's
signal machinery re-scheduled, which perturbs later signal-sensitive
tests sharing this process.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from db_tools.process import run_pipe

# 200 KB is far beyond the 64 KB macOS pipe buffer: the left child can
# only finish if its stderr is drained while the right child still runs.
BIG_STDERR = 200_000

# Runs inside a child interpreter: installs a SIGINT handler that raises
# SystemExit(130) like dbt's bound interrupt handlers, interrupts run_pipe
# mid-flight, then reports whether both children were killed. Exit code 0
# means the interrupt unwound the pipe and no child survived it.
_INTERRUPT_DRIVER = """\
import os
import signal
import sys
import threading
import time
from pathlib import Path

from db_tools.process import run_pipe

stream = sys.argv[1] == "1"
left_pid, right_pid = (Path(argument) for argument in sys.argv[2:4])
# `exec` makes the sh BECOME the sleeper/reader: the recorded pid is the
# long-lived process itself, so killing it cannot leave an orphan behind
# (a forked grandchild would inherit this driver's capture pipes and
# outlive the test by 30 seconds).
left = ["sh", "-c", f"echo $$ > {left_pid}; echo noise >&2; exec sleep 30"]
right = ["sh", "-c", f"echo $$ > {right_pid}; exec cat"]
main_thread = threading.get_ident()


def interrupt_when_ready() -> None:
    deadline = time.monotonic() + 10
    while not (left_pid.exists() and right_pid.exists()):
        if time.monotonic() >= deadline:
            return  # no signal; the main flow fails on its own
        time.sleep(0.01)
    # Target the main thread: a process-wide kill could land here on this
    # helper thread instead, leaving the blocked poll()/waitpid() unaware
    # until the 30s child exits on its own.
    signal.pthread_kill(main_thread, signal.SIGINT)


def raise_system_exit(_signum: int, _frame: object) -> None:
    raise SystemExit(130)


signal.signal(signal.SIGINT, raise_system_exit)
threading.Thread(target=interrupt_when_ready, daemon=True).start()
exit_code = None
try:
    run_pipe(left, right, stream=stream)
except SystemExit as exc:
    exit_code = exc.code

survivors = []
for path in (left_pid, right_pid):
    try:
        pid = int(path.read_text())
        os.kill(pid, 0)
    except (FileNotFoundError, ValueError):
        survivors.append(str(path))  # no pid to check: fail loudly
    except ProcessLookupError:
        continue  # the child is gone; the interrupt killed it
    else:
        survivors.append(str(path))
os._exit(0 if exit_code == 130 and not survivors else 1)
"""


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
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _INTERRUPT_DRIVER,
            "1" if stream else "0",
            str(tmp_path / "left.pid"),
            str(tmp_path / "right.pid"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
