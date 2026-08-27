from __future__ import annotations

import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import stat
import subprocess
import sys
import termios
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
PROMPT = b"Base32 secret: "
CTRL_C_GOLDEN = (
    "\r\n"
    "   \x1b[42m\x1b[30m 2FA \x1b[39m\x1b[49m\r\n"
    "\x1b[36m│\x1b[39m\r\n"
    "\x1b[32m◇\x1b[39m  Generate TOTP\r\n"
    "\x1b[36m│\x1b[39m\r\n"
    "\x1b[36m◆\x1b[39m  Base32 secret: "
    "\x1b[36m└\x1b[39m\r\n"
).encode("utf-8")


def _make_controlling_tty() -> None:
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())


class PtyProcess:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        master: int,
        slave: int,
        *,
        child_pid_path: Path,
        status_path: Path,
        release_path: Path,
    ):
        self.process = process
        self.master = master
        self.slave = slave
        self.child_pid_path = child_pid_path
        self.status_path = status_path
        self.release_path = release_path
        self.transcript = bytearray()

    @property
    def child_pid(self) -> int:
        return int(self.child_pid_path.read_text(encoding="utf-8"))

    def read_until(self, marker: bytes, timeout: float = 5) -> bytes:
        deadline = time.monotonic() + timeout
        while marker not in self.transcript:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"timed out waiting for {marker!r}; transcript={bytes(self.transcript)!r}"
                )
            readable, _, _ = select.select([self.master], [], [], min(0.1, remaining))
            if readable:
                self.transcript.extend(os.read(self.master, 4096))
            elif self.process.poll() is not None:
                raise AssertionError(
                    f"session keeper exited before {marker!r}; "
                    f"transcript={bytes(self.transcript)!r}"
                )
        return bytes(self.transcript)

    def finish(self, timeout: float = 5) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout
        while not self.status_path.exists():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"command did not exit; transcript={bytes(self.transcript)!r}"
                )
            readable, _, _ = select.select([self.master], [], [], min(0.1, remaining))
            if readable:
                self.transcript.extend(os.read(self.master, 4096))
            elif self.process.poll() is not None:
                raise AssertionError("session keeper exited without recording command status")

        while True:
            readable, _, _ = select.select([self.master], [], [], 0)
            if not readable:
                break
            self.transcript.extend(os.read(self.master, 4096))
        return int(self.status_path.read_text(encoding="utf-8")), bytes(
            self.transcript
        )

    def wait_until(self, predicate, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"condition not met; transcript={bytes(self.transcript)!r}"
                )
            readable, _, _ = select.select([self.master], [], [], min(0.05, remaining))
            if readable:
                self.transcript.extend(os.read(self.master, 4096))
            if self.process.poll() is not None:
                raise AssertionError("session keeper exited before condition was met")

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


def _fake_pbcopy(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "clipboard.txt"
    executable = bin_dir / "pbcopy"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['PBCOPY_CAPTURE']).write_text(sys.stdin.read())\n"
        "raise SystemExit(int(os.environ.get('PBCOPY_EXIT', '0')))\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return bin_dir, capture


def _sitecustomize(tmp_path: Path) -> Path:
    python_dir = tmp_path / "python"
    python_dir.mkdir()
    (python_dir / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "import signal\n"
        "import time\n"
        "time.time = lambda: 59\n"
        "prior = os.environ.get('TWOFA_PRIOR_SIGNAL')\n"
        "if prior == 'ignore':\n"
        "    signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "elif prior == 'callable':\n"
        "    def prior_handler(signum, frame):\n"
        "        Path(os.environ['TWOFA_SIGNAL_MARKER']).write_text(str(signum))\n"
        "    signal.signal(signal.SIGHUP, prior_handler)\n"
        "if os.environ.get('TWOFA_UNEXPECTED') == '1':\n"
        "    import pyotp\n"
        "    def fail(*args, **kwargs):\n"
        "        raise LookupError('unexpected failure')\n"
        "    pyotp.TOTP.at = fail\n",
        encoding="utf-8",
    )
    return python_dir


def _session_keeper(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    keeper = tmp_path / "session_keeper.py"
    child_pid_path = tmp_path / "child.pid"
    status_path = tmp_path / "child.status"
    release_path = tmp_path / "release"
    keeper.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "import signal\n"
        "import sys\n"
        "import time\n"
        "child_pid_path, status_path, release_path = map(Path, sys.argv[1:])\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.setpgid(0, 0)\n"
        "    os.execv(sys.executable, [sys.executable, '-m', 'twofa.cli'])\n"
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
    return keeper, child_pid_path, status_path, release_path


def _start_twofa(
    tmp_path: Path,
    *,
    clipboard_exit: int = 0,
    unexpected: bool = False,
    prior_signal: str | None = None,
) -> tuple[PtyProcess, list[object], Path]:
    bin_dir, capture = _fake_pbcopy(tmp_path)
    python_dir = _sitecustomize(tmp_path)
    keeper, child_pid_path, status_path, release_path = _session_keeper(tmp_path)
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "PBCOPY_CAPTURE": str(capture),
            "PBCOPY_EXIT": str(clipboard_exit),
            "PYTHONPATH": os.pathsep.join(
                [str(python_dir), str(REPO_ROOT / "mac_scripts" / "functions")]
            ),
        }
    )
    if unexpected:
        env["TWOFA_UNEXPECTED"] = "1"
    if prior_signal is not None:
        env["TWOFA_PRIOR_SIGNAL"] = prior_signal
        env["TWOFA_SIGNAL_MARKER"] = str(tmp_path / "signal.marker")

    process = subprocess.Popen(
        [
            sys.executable,
            str(keeper),
            str(child_pid_path),
            str(status_path),
            str(release_path),
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=REPO_ROOT,
        env=env,
        preexec_fn=_make_controlling_tty,
    )
    return (
        PtyProcess(
            process,
            master,
            slave,
            child_pid_path=child_pid_path,
            status_path=status_path,
            release_path=release_path,
        ),
        original,
        capture,
    )


def _assert_restored(session: PtyProcess, original: list[object]) -> None:
    assert termios.tcgetattr(session.slave) == original


def test_success_hides_secret_copies_code_and_restores_tty(tmp_path: Path) -> None:
    session, original, capture = _start_twofa(tmp_path)
    try:
        session.read_until(PROMPT)
        os.write(session.master, f"{SECRET}\n".encode())
        returncode, transcript = session.finish()

        assert returncode == 0
        assert SECRET.encode() not in transcript
        assert b"Code copied: 287082" in transcript
        assert capture.read_text(encoding="utf-8") == "287082"
        _assert_restored(session, original)
    finally:
        session.close()


@pytest.mark.parametrize(
    ("input_bytes", "clipboard_exit", "expected_error"),
    [
        (b"!!!!\n", 0, b"invalid Base32 character"),
        (f"{SECRET}\n".encode(), 7, b"failed to copy code to clipboard"),
        (b"\x04", 0, b"empty secret"),
    ],
)
def test_expected_failures_hide_input_and_restore_tty(
    tmp_path: Path,
    input_bytes: bytes,
    clipboard_exit: int,
    expected_error: bytes,
) -> None:
    session, original, _capture = _start_twofa(
        tmp_path, clipboard_exit=clipboard_exit
    )
    try:
        session.read_until(PROMPT)
        os.write(session.master, input_bytes)
        returncode, transcript = session.finish()

        assert returncode == 1
        assert SECRET.encode() not in transcript
        assert b"!!!!" not in transcript
        assert expected_error in transcript
        _assert_restored(session, original)
    finally:
        session.close()


def test_ctrl_c_restores_tty(tmp_path: Path) -> None:
    session, original, _capture = _start_twofa(tmp_path)
    try:
        session.read_until(PROMPT)
        os.write(session.master, b"\x03")
        returncode, transcript = session.finish()

        assert returncode == 130
        assert transcript == CTRL_C_GOLDEN
        assert SECRET.encode() not in transcript
        _assert_restored(session, original)
    finally:
        session.close()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_termination_signal_restores_tty_before_process_terminates(
    tmp_path: Path, signum: int
) -> None:
    session, original, _capture = _start_twofa(tmp_path)
    try:
        session.read_until(PROMPT)
        os.kill(session.child_pid, signum)
        returncode, transcript = session.finish()

        assert returncode == -signum
        assert SECRET.encode() not in transcript
        _assert_restored(session, original)
    finally:
        session.close()


def test_ignored_prior_sighup_handler_keeps_reading_hidden_input(
    tmp_path: Path,
) -> None:
    session, original, capture = _start_twofa(tmp_path, prior_signal="ignore")
    try:
        session.read_until(PROMPT)
        os.kill(session.child_pid, signal.SIGHUP)
        os.write(session.master, f"{SECRET}\n".encode())
        returncode, transcript = session.finish()

        assert returncode == 0
        assert transcript.count(PROMPT) == 1
        assert SECRET.encode() not in transcript
        assert capture.read_text(encoding="utf-8") == "287082"
        _assert_restored(session, original)
    finally:
        session.close()


def test_callable_prior_sighup_handler_runs_after_cleanup_then_read_resumes(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "signal.marker"
    session, original, capture = _start_twofa(tmp_path, prior_signal="callable")
    try:
        session.read_until(PROMPT)
        os.kill(session.child_pid, signal.SIGHUP)
        session.wait_until(marker.exists)
        assert marker.read_text(encoding="utf-8") == str(signal.SIGHUP)
        session.wait_until(
            lambda: not (termios.tcgetattr(session.slave)[3] & termios.ECHO)
        )
        os.write(session.master, f"{SECRET}\n".encode())
        returncode, transcript = session.finish()

        assert returncode == 0
        assert transcript.count(PROMPT) == 1
        assert SECRET.encode() not in transcript
        assert capture.read_text(encoding="utf-8") == "287082"
        _assert_restored(session, original)
    finally:
        session.close()


def test_unexpected_failure_after_input_restores_tty(tmp_path: Path) -> None:
    session, original, _capture = _start_twofa(tmp_path, unexpected=True)
    try:
        session.read_until(PROMPT)
        os.write(session.master, f"{SECRET}\n".encode())
        returncode, transcript = session.finish()

        assert returncode == 1
        assert b"LookupError: unexpected failure" in transcript
        assert SECRET.encode() not in transcript
        _assert_restored(session, original)
    finally:
        session.close()
