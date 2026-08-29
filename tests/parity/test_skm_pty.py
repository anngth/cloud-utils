from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import stat
import subprocess
import sys
import termios
import time


ROOT = Path(__file__).resolve().parents[2]
CLEAR = b"\x1b[2J\x1b[H"
ALT_ENTER = b"\x1b[?1049h"
ALT_EXIT = b"\x1b[?1049l"
DISCOVERY = (
    ROOT
    / "mac_scripts/functions/skills-manager/__tests__/fixtures/"
    "discovery-flat.txt"
).read_text(encoding="utf-8")
EMPTY_CATALOG = b'{\n  "version": 1,\n  "sources": []\n}\n'


@dataclass(frozen=True, slots=True)
class PtySnapshot:
    returncode: int
    final_stdout: bytes
    stderr: bytes
    calls: tuple[dict[str, object], ...]
    catalog: bytes
    terminal_restored: bool


def _make_controlling_tty() -> None:
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())


def _write_session_keeper(case: Path) -> tuple[Path, Path, Path, Path]:
    keeper = case / "session_keeper.py"
    child_pid = case / "child.pid"
    status = case / "child.status"
    release = case / "release"
    keeper.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "import signal\n"
        "import sys\n"
        "import time\n"
        "child_pid, status, release = map(Path, sys.argv[1:4])\n"
        "argv = sys.argv[4:]\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.setpgid(0, 0)\n"
        "    os.execvpe(argv[0], argv, os.environ)\n"
        "os.setpgid(child, child)\n"
        "signal.signal(signal.SIGTTOU, signal.SIG_IGN)\n"
        "os.tcsetpgrp(0, child)\n"
        "child_pid.write_text(str(child))\n"
        "_, value = os.waitpid(child, 0)\n"
        "os.tcsetpgrp(0, os.getpgrp())\n"
        "status.write_text(str(os.waitstatus_to_exitcode(value)))\n"
        "while not release.exists():\n"
        "    time.sleep(0.01)\n",
        encoding="utf-8",
    )
    return keeper, child_pid, status, release


def _write_fake_npx(case: Path) -> tuple[Path, Path]:
    bin_dir = case / "bin"
    bin_dir.mkdir()
    calls = case / "npx-calls.jsonl"
    executable = bin_dir / "npx"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "with open(os.environ['SKM_NPX_CALLS'], 'a') as output:\n"
        "    output.write(json.dumps({\n"
        "        'argv': sys.argv[1:], 'cwd': os.getcwd(), 'status': 0,\n"
        "    }) + '\\n')\n"
        "sys.stdout.write(os.environ['SKM_DISCOVERY'])\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    calls.touch()
    return bin_dir, calls


def _read_available(master: int, transcript: bytearray, timeout: float) -> None:
    readable, _, _ = select.select([master], [], [], timeout)
    if not readable:
        return
    try:
        chunk = os.read(master, 65_536)
    except OSError:
        return
    transcript.extend(chunk)


def _run_cancel(runtime: str, case: Path) -> tuple[PtySnapshot, bytes]:
    home = case / "home"
    config = case / "config"
    project = case / "project"
    for directory in (home, config / "skm", project):
        directory.mkdir(parents=True, exist_ok=True)
    catalog = config / "skm/sources.json"
    catalog.write_bytes(EMPTY_CATALOG)
    bin_dir, calls_path = _write_fake_npx(case)
    keeper, child_pid, status, release = _write_session_keeper(case)
    env = os.environ.copy()
    env.update(
        {
            "CLOUD_UTILS_CONFIG_DIR": str(config),
            "HOME": str(home),
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT / "mac_scripts/functions"), env.get("PYTHONPATH", "")]
            ),
            "SKM_DISCOVERY": DISCOVERY,
            "SKM_NPX_CALLS": str(calls_path),
        }
    )
    command = (
        ["node", str(ROOT / "mac_scripts/functions/skills-manager/cli.mjs")]
        if runtime == "javascript"
        else [str(ROOT / ".venv/bin/python"), "-m", "skills_manager.cli"]
    )
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    process = subprocess.Popen(
        [
            sys.executable,
            str(keeper),
            str(child_pid),
            str(status),
            str(release),
            *command,
            "source",
            "add",
            "owner/new",
        ],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.PIPE,
        cwd=project,
        env=env,
        preexec_fn=_make_controlling_tty,
    )
    transcript = bytearray()
    try:
        deadline = time.monotonic() + 5
        while b"SKILLS MANAGER" not in transcript:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"selector did not render: {transcript!r}")
            _read_available(master, transcript, min(0.1, remaining))
        os.write(master, b"q")
        deadline = time.monotonic() + 5
        while not status.exists():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"selector did not cancel: {transcript!r}")
            _read_available(master, transcript, min(0.1, remaining))
        for _ in range(10):
            before = len(transcript)
            _read_available(master, transcript, 0)
            if len(transcript) == before:
                break
        restored = termios.tcgetattr(slave) == original
        release.touch()
        process.wait(timeout=5)
        stderr = process.stderr.read() if process.stderr is not None else b""
        calls = tuple(
            json.loads(line)
            for line in calls_path.read_text(encoding="utf-8").splitlines()
        )
        output = bytes(transcript)
        snapshot = PtySnapshot(
            int(status.read_text(encoding="utf-8")),
            CLEAR + output.rsplit(CLEAR, 1)[1],
            stderr,
            calls,
            catalog.read_bytes(),
            restored,
        )
        return snapshot, output
    finally:
        if process.poll() is None:
            if child_pid.exists():
                try:
                    os.killpg(
                        int(child_pid.read_text(encoding="utf-8")),
                        signal.SIGKILL,
                    )
                except ProcessLookupError:
                    pass
            release.touch()
            process.kill()
            process.wait(timeout=5)
        os.close(master)
        os.close(slave)


def test_source_selection_q_cancellation_matches_javascript(
    tmp_path: Path,
) -> None:
    case = tmp_path / "case"
    expected, javascript_output = _run_cancel("javascript", case)
    shutil.rmtree(case)
    actual, python_output = _run_cancel("python", case)

    assert actual == expected
    assert expected.returncode == 0
    assert expected.stderr == b""
    assert expected.calls == (
        {
            "argv": ["skills", "add", "owner/new", "--list"],
            "cwd": str(case / "project"),
            "status": 0,
        },
    )
    assert expected.catalog == EMPTY_CATALOG
    assert b"Selection cancelled" in expected.final_stdout
    assert expected.terminal_restored
    for output in (javascript_output, python_output):
        assert ALT_ENTER in output
        assert ALT_EXIT in output
