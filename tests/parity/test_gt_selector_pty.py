from __future__ import annotations

import fcntl
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ALT_SCREEN_ENTER = b"\x1b[?1049h"
ALT_SCREEN_EXIT = b"\x1b[?1049l"
ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
CHILD = r'''
import os
import sys

from git_tools.selector import SelectorItem, run_selector
from git_tools.ui import GitToolsUi

items = (
    SelectorItem("git@github.com:org/a.git", "git@github.com:org/a.git"),
    SelectorItem("git@gitlab.com:acme/b.git", "git@gitlab.com:acme/b.git"),
)
ui = GitToolsUi(sys.stdout, sys.stderr)
def render(state):
    ui.render_backup_selector(
        "Select repos to backup", state, list_path="~/gt/backups.json"
    )
    if os.environ.get("GT_SELECTOR_RENDER_ERROR") == "1":
        raise LookupError("unexpected render failure")

result = run_selector(
    items,
    initial=(items[0].value,),
    multiple=True,
    input=sys.stdin,
    output=sys.stdout,
    render=render,
)
if result.kind == "cancel":
    ui.cancelled_backup_selector(
        "Select repos to backup", result.state, list_path="~/gt/backups.json"
    )
print(f"RESULT:{result.kind}:{','.join(result.selected)}", flush=True)
'''


def _make_controlling_tty() -> None:
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())


class SelectorProcess:
    def __init__(self, tmp_path: Path, *, render_error: bool = False) -> None:
        child_script = tmp_path / "selector_child.py"
        child_script.write_text(CHILD, encoding="utf-8")
        self.child_pid_path = tmp_path / "child.pid"
        self.status_path = tmp_path / "child.status"
        self.release_path = tmp_path / "release"
        keeper_script = tmp_path / "session_keeper.py"
        keeper_script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "import signal\n"
            "import sys\n"
            "import time\n"
            "child_script, child_pid_path, status_path, release_path = "
            "(Path(value) for value in sys.argv[1:])\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    os.setpgid(0, 0)\n"
            "    os.execv(sys.executable, [sys.executable, str(child_script)])\n"
            "os.setpgid(child, child)\n"
            "signal.signal(signal.SIGTTOU, signal.SIG_IGN)\n"
            "os.tcsetpgrp(0, child)\n"
            "child_pid_path.write_text(str(child))\n"
            "_, status = os.waitpid(child, 0)\n"
            "os.tcsetpgrp(0, os.getpgrp())\n"
            "status_path.write_text(str(os.waitstatus_to_exitcode(status)))\n"
            "while not release_path.exists():\n"
            "    time.sleep(0.01)\n",
            encoding="utf-8",
        )
        self.master, self.slave = pty.openpty()
        self.original_attributes = termios.tcgetattr(self.slave)
        self.transcript = bytearray()
        env = os.environ.copy()
        if render_error:
            env["GT_SELECTOR_RENDER_ERROR"] = "1"
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(keeper_script),
                str(child_script),
                str(self.child_pid_path),
                str(self.status_path),
                str(self.release_path),
            ],
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            cwd=REPO_ROOT,
            env=env,
            preexec_fn=_make_controlling_tty,
        )

    @property
    def child_pid(self) -> int:
        return int(self.child_pid_path.read_text(encoding="utf-8"))

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def _read_available(self, timeout: float) -> None:
        readable, _, _ = select.select([self.master], [], [], timeout)
        if readable:
            try:
                self.transcript.extend(os.read(self.master, 65_536))
            except OSError:
                pass

    def read_until(self, marker: bytes, *, count: int = 1, timeout: float = 5) -> bytes:
        deadline = time.monotonic() + timeout
        while self.transcript.count(marker) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"timed out waiting for {marker!r} x{count}; "
                    f"transcript={bytes(self.transcript)!r}"
                )
            self._read_available(min(0.1, remaining))
            if self.process.poll() is not None and self.transcript.count(marker) < count:
                self._read_available(0)
                raise AssertionError(
                    f"selector exited before {marker!r} x{count}; "
                    f"status={self.process.returncode}, "
                    f"transcript={bytes(self.transcript)!r}"
                )
        return bytes(self.transcript)

    def finish(self, timeout: float = 5) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout
        while not self.status_path.exists():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"selector did not exit; transcript={bytes(self.transcript)!r}"
                )
            self._read_available(min(0.1, remaining))
        for _ in range(10):
            before = len(self.transcript)
            self._read_available(0)
            if len(self.transcript) == before:
                break
        return int(self.status_path.read_text(encoding="utf-8")), bytes(self.transcript)

    def close(self) -> None:
        if not self.status_path.exists() and self.child_pid_path.exists():
            try:
                os.killpg(self.child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.release_path.touch()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        os.close(self.master)
        os.close(self.slave)


@pytest.fixture
def selector(tmp_path: Path) -> SelectorProcess:
    session = SelectorProcess(tmp_path)
    try:
        session.read_until(b"REPO BACKUP")
        yield session
    finally:
        session.close()


def _plain(transcript: bytes) -> str:
    return ANSI_RE.sub(b"", transcript).decode("utf-8", errors="replace")


def _assert_terminal_recovered(selector: SelectorProcess, transcript: bytes) -> None:
    assert ALT_SCREEN_EXIT in transcript
    assert termios.tcgetattr(selector.slave) == selector.original_attributes


def test_submit_preserves_key_behavior_visible_frame_and_terminal_state(
    selector: SelectorProcess,
) -> None:
    selector.send(b"\x1b[B \r")

    returncode, transcript = selector.finish()

    plain = _plain(transcript)
    assert returncode == 0
    assert "REPO BACKUP" in plain
    assert "1  " in plain and "2  " in plain
    assert (
        "RESULT:submit:git@github.com:org/a.git,git@gitlab.com:acme/b.git"
        in plain
    )
    assert ALT_SCREEN_ENTER in transcript
    assert transcript.index(ALT_SCREEN_ENTER) < transcript.index(b"REPO BACKUP")
    _assert_terminal_recovered(selector, transcript)


@pytest.mark.parametrize("key", [b"q", b"\x03"])
def test_cancel_keys_render_cancelled_frame_and_restore_terminal(
    selector: SelectorProcess, key: bytes
) -> None:
    selector.send(key)

    returncode, transcript = selector.finish()

    plain = _plain(transcript)
    assert returncode == 0
    assert "Selection cancelled" in plain
    assert "RESULT:cancel:" in plain
    _assert_terminal_recovered(selector, transcript)


def test_eof_cancels_and_restores_terminal(selector: SelectorProcess) -> None:
    selector.send(b"\x04")

    returncode, transcript = selector.finish()

    assert returncode == 0
    assert "Selection cancelled" in _plain(transcript)
    _assert_terminal_recovered(selector, transcript)


def test_ctrl_z_exits_alternate_screen_then_reenters_after_sigcont(
    selector: SelectorProcess,
) -> None:
    selector.send(b"\x1a")
    selector.read_until(ALT_SCREEN_EXIT)

    os.kill(selector.child_pid, signal.SIGCONT)
    selector.read_until(ALT_SCREEN_ENTER, count=2)
    selector.send(b"q")
    returncode, transcript = selector.finish()

    assert returncode == 0
    assert transcript.find(ALT_SCREEN_EXIT) < transcript.rfind(ALT_SCREEN_ENTER)
    assert "Selection cancelled" in _plain(transcript)
    _assert_terminal_recovered(selector, transcript)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_termination_signals_cleanup_then_terminate_with_the_same_signal(
    selector: SelectorProcess, signum: signal.Signals
) -> None:
    os.kill(selector.child_pid, signum)

    returncode, transcript = selector.finish()

    assert returncode == -signum
    _assert_terminal_recovered(selector, transcript)


def test_render_exception_exits_alternate_screen_and_restores_terminal(
    tmp_path: Path,
) -> None:
    selector = SelectorProcess(tmp_path, render_error=True)
    try:
        selector.read_until(b"REPO BACKUP")
        returncode, transcript = selector.finish()

        assert returncode == 1
        assert "Press ENTER to continue" not in _plain(transcript)
        _assert_terminal_recovered(selector, transcript)
    finally:
        selector.close()
