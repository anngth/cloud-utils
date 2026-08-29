from __future__ import annotations

import fcntl
import json
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


ROOT = Path(__file__).resolve().parents[2]
ALT_ENTER = b"\x1b[?1049h"
ALT_EXIT = b"\x1b[?1049l"
CLEAR = b"\x1b[2J\x1b[H"
ANSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]")
CATALOG = {
    "version": 1,
    "sources": [
        {"source": "owner/catalog", "skills": ["alpha", "beta"]},
    ],
}

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="SKM supports macOS PTYs only"
)


def _controlling_tty() -> None:
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())


class SkmProcess:
    def __init__(
        self,
        tmp_path: Path,
        *,
        runtime: str = "python",
        render_error: bool = False,
    ) -> None:
        root = tmp_path / runtime
        config = root / "config" / "skm"
        project = root / "project"
        bin_dir = root / "bin"
        for directory in (config, project, bin_dir, root / "home"):
            directory.mkdir(parents=True)
        (config / "sources.json").write_text(
            json.dumps(CATALOG, indent=2) + "\n", encoding="utf-8"
        )
        self.argv_log = root / "argv.jsonl"
        self.tty_state = root / "mutation-tty.txt"
        npx = bin_dir / "npx"
        npx.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys, termios\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['SKM_PTY_ARGV'], 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(args) + '\\n')\n"
            "if args == ['skills', 'list', '--json']:\n"
            "    print('[]')\n"
            "else:\n"
            "    state = repr(termios.tcgetattr(0))\n"
            "    open(os.environ['SKM_PTY_STATE'], 'w').write(state)\n"
            "    print('__SKM_NPX_READY__', flush=True)\n",
            encoding="utf-8",
        )
        npx.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "CLOUD_UTILS_CONFIG_DIR": str(root / "config"),
                "HOME": str(root / "home"),
                "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                "PYTHONPATH": str(ROOT / "mac_scripts/functions"),
                "SKM_PTY_ARGV": str(self.argv_log),
                "SKM_PTY_STATE": str(self.tty_state),
                "TERM": "xterm-256color",
            }
        )
        if runtime == "javascript":
            command = (
                "node",
                str(ROOT / "mac_scripts/functions/skills-manager/cli.mjs"),
            )
        elif render_error:
            driver = root / "driver.py"
            driver.write_text(
                "from skills_manager.ui import SkmUi\n"
                "def fail(*_args, **_kwargs): raise LookupError('render failure')\n"
                "SkmUi.catalog_selector = fail\n"
                "from skills_manager.cli import main\n"
                "main()\n",
                encoding="utf-8",
            )
            command = (sys.executable, str(driver))
        else:
            command = (sys.executable, "-m", "skills_manager.cli")
        self.child_pid_path = root / "child.pid"
        self.status_path = root / "child.status"
        self.release_path = root / "release"
        keeper = root / "keeper.py"
        keeper.write_text(
            "import json, os, signal, sys, termios, time\n"
            "command = json.loads(sys.argv[1])\n"
            "pid_path, status_path, release_path = sys.argv[2:]\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    os.setpgid(0, 0)\n"
            "    os.execvpe(command[0], command, os.environ)\n"
            "os.setpgid(child, child)\n"
            "signal.signal(signal.SIGTTOU, signal.SIG_IGN)\n"
            "os.tcsetpgrp(0, child)\n"
            "open(pid_path, 'w').write(str(child))\n"
            "_, status = os.waitpid(child, 0)\n"
            "os.tcsetpgrp(0, os.getpgrp())\n"
            "open(status_path, 'w').write(str(os.waitstatus_to_exitcode(status)))\n"
            "while not os.path.exists(release_path): time.sleep(0.01)\n",
            encoding="utf-8",
        )
        self.master, self.slave = pty.openpty()
        self.original = termios.tcgetattr(self.slave)
        self.transcript = bytearray()
        self.process = subprocess.Popen(
            (
                sys.executable,
                str(keeper),
                json.dumps(command),
                str(self.child_pid_path),
                str(self.status_path),
                str(self.release_path),
            ),
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            cwd=project,
            env=env,
            preexec_fn=_controlling_tty,
        )

    @property
    def child_pid(self) -> int:
        return int(self.child_pid_path.read_text())

    def send(self, value: bytes) -> None:
        os.write(self.master, value)

    def _read(self, timeout: float) -> None:
        ready, _, _ = select.select([self.master], [], [], timeout)
        if ready:
            try:
                self.transcript.extend(os.read(self.master, 65_536))
            except OSError:
                pass

    def read_until(self, marker: bytes, *, count: int = 1) -> bytes:
        deadline = time.monotonic() + 8
        while self.transcript.count(marker) < count:
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"timed out waiting for {marker!r}; {bytes(self.transcript)!r}"
                )
            self._read(0.1)
            if self.status_path.exists():
                self._read(0)
                if self.transcript.count(marker) < count:
                    raise AssertionError(
                        f"SKM exited {self.status_path.read_text()}; "
                        f"{bytes(self.transcript)!r}"
                    )
        return bytes(self.transcript)

    def finish(self) -> tuple[int, bytes]:
        deadline = time.monotonic() + 8
        while not self.status_path.exists():
            if time.monotonic() >= deadline:
                raise AssertionError(f"SKM did not exit; {bytes(self.transcript)!r}")
            self._read(0.1)
        for _ in range(10):
            before = len(self.transcript)
            self._read(0)
            if len(self.transcript) == before:
                break
        return int(self.status_path.read_text()), bytes(self.transcript)

    def close(self) -> None:
        if not self.status_path.exists() and self.child_pid_path.exists():
            try:
                os.killpg(self.child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.release_path.touch()
        self.process.wait(timeout=5)
        os.close(self.master)
        os.close(self.slave)


def _plain(value: bytes) -> str:
    return ANSI.sub(b"", value).decode("utf-8", errors="replace").replace("\r", "")


def _final_frame(value: bytes) -> tuple[str, ...]:
    plain = _plain(value.rsplit(CLEAR, 1)[-1])
    return tuple(
        re.sub(r"\s+", " ", line).strip()
        for line in plain.splitlines()
        if any(
            word in line
            for word in ("Apply these changes?", "Install", "alpha", "beta")
        )
    )


def _restored(session: SkmProcess, transcript: bytes) -> None:
    assert ALT_EXIT in transcript
    assert termios.tcgetattr(session.slave) == session.original


def _preview(tmp_path: Path, runtime: str, keys: tuple[bytes, ...]):
    session = SkmProcess(tmp_path, runtime=runtime)
    try:
        session.read_until(b"Choose skills")
        for key in keys:
            session.send(key)
        session.read_until(b"Apply these changes?")
        session.send(b"q")
        status, transcript = session.finish()
        _restored(session, transcript)
        return status, transcript
    finally:
        session.close()


@pytest.mark.parametrize(
    "keys",
    [
        (b"a", b"c", b"j", b" ", b"\r"),
        (b"\x1b[", b"B", b"j", b" ", b"\r"),
        (b"j", b"j", b"k", b" ", b"\r"),
    ],
)
def test_visible_frames_and_arrow_vim_toggle_all_clear_match_javascript(
    tmp_path: Path, keys: tuple[bytes, ...]
) -> None:
    js_status, javascript = _preview(tmp_path, "javascript", keys)
    py_status, python = _preview(tmp_path, "python", keys)

    assert py_status == js_status == 1
    assert _final_frame(python) == _final_frame(javascript)
    assert ALT_ENTER in python
    assert python.index(ALT_ENTER) < python.index(b"Choose skills")


@pytest.mark.parametrize("key", [b"q", b"\x03", b"\x04"])
def test_q_ctrl_c_and_eof_cancel_and_restore_terminal(
    tmp_path: Path, key: bytes
) -> None:
    session = SkmProcess(tmp_path)
    try:
        session.read_until(b"Choose skills")
        session.send(key)
        status, transcript = session.finish()
        assert status == 1
        assert "Selection cancelled" in _plain(transcript)
        _restored(session, transcript)
    finally:
        session.close()


def test_ctrl_z_exits_alt_screen_resumes_redraws_and_accepts_input(
    tmp_path: Path,
) -> None:
    session = SkmProcess(tmp_path)
    try:
        session.read_until(b"Choose skills")
        session.send(b"\x1a")
        session.read_until(ALT_EXIT)
        os.kill(session.child_pid, signal.SIGCONT)
        session.read_until(ALT_ENTER, count=2)
        session.send(b"q")
        status, transcript = session.finish()
        assert status == 1
        assert transcript.find(ALT_EXIT) < transcript.rfind(ALT_ENTER)
        _restored(session, transcript)
    finally:
        session.close()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_termination_restores_then_exits_with_same_signal(
    tmp_path: Path, signum: signal.Signals
) -> None:
    session = SkmProcess(tmp_path)
    try:
        session.read_until(b"Choose skills")
        os.kill(session.child_pid, signum)
        status, transcript = session.finish()
        assert status == -signum
        _restored(session, transcript)
    finally:
        session.close()


def test_render_exception_restores_terminal_and_alt_screen(tmp_path: Path) -> None:
    session = SkmProcess(tmp_path, render_error=True)
    try:
        status, transcript = session.finish()
        assert status == 1
        assert "render failure" in _plain(transcript)
        _restored(session, transcript)
    finally:
        session.close()


def test_enter_restores_terminal_before_inherited_npx_stdio(tmp_path: Path) -> None:
    session = SkmProcess(tmp_path)
    try:
        session.read_until(b"Choose skills")
        session.send(b"j \r")
        session.read_until(b"Apply these changes?")
        session.send(b"\r")
        session.read_until(b"__SKM_NPX_READY__")
        status, transcript = session.finish()

        assert status == 0
        assert session.tty_state.read_text() == repr(session.original)
        calls = tuple(
            json.loads(line) for line in session.argv_log.read_text().splitlines()
        )
        assert calls[-1] == [
            "skills", "add", "owner/catalog", "--skill", "alpha"
        ]
        _restored(session, transcript)
    finally:
        session.close()
