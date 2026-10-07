"""Interactive and flag flows in setup.sh must upgrade only when asked."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _stub_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "sudo.log"
    _write_executable(
        bin_dir / "sudo",
        f"""
printf '%s\\n' "$*" >> {log}
exit 0
""",
    )
    _write_executable(bin_dir / "systemctl", "exit 0")
    _write_executable(
        bin_dir / "curl",
        """
out=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then
    out="$2"
    shift 2
    continue
  fi
  shift
done
if [[ -n "$out" ]]; then
  printf '%s\\n' '#!/bin/bash' > "$out"
fi
exit 0
""",
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env.pop("USER_PASSWORD", None)
    return env, log


def _run(tmp_path: Path, *args: str, stdin: str = "") -> tuple[subprocess.CompletedProcess[str], list[str]]:
    env, log = _stub_env(tmp_path)
    result = subprocess.run(
        ["bash", str(SETUP), *args],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, lines


def test_declining_every_prompt_does_not_touch_apt(tmp_path: Path) -> None:
    result, lines = _run(tmp_path, stdin="\n" * 40)

    assert result.returncode == 0, result.stderr
    assert "system package upgrades" in result.stdout
    assert "basic tools" in result.stdout
    assert result.stdout.index("system package upgrades") < result.stdout.index("basic tools")
    assert "No changes made." in result.stdout
    assert "Final Cleanup" not in result.stdout
    assert lines == []


def test_upgrade_prompt_alone_upgrades_once_then_cleans_cache(tmp_path: Path) -> None:
    answers = "\n".join(["n", "y", "n", *["n"] * 30]) + "\n"
    result, lines = _run(tmp_path, stdin=answers)

    assert result.returncode == 0, result.stderr
    assert [line for line in lines if "full-upgrade" in line] == ["apt -y full-upgrade"]
    assert "apt -y upgrade" in lines
    assert "apt clean" in lines
    assert not any("install git" in line for line in lines)


def test_basic_tools_prompt_does_not_upgrade(tmp_path: Path) -> None:
    answers = "\n".join(["n", "n", "y", *["n"] * 30]) + "\n"
    result, lines = _run(tmp_path, stdin=answers)

    assert result.returncode == 0, result.stderr
    assert any("install git tmux" in line for line in lines)
    assert not any("full-upgrade" in line or line.endswith(" upgrade") or " apt -y upgrade" in line for line in lines)
    assert "apt clean" in lines


def test_password_only_skips_cleanup(tmp_path: Path) -> None:
    answers = "\n".join(["y", *["n"] * 30]) + "\n"
    result, lines = _run(tmp_path, stdin=answers)

    assert result.returncode == 0, result.stderr
    assert "Final Cleanup" not in result.stdout
    assert lines == []


def test_other_flags_do_not_upgrade_by_default(tmp_path: Path) -> None:
    result, lines = _run(tmp_path, "-docker")

    assert result.returncode == 0, result.stderr
    assert "Running: apt-update" not in result.stdout
    assert not any("full-upgrade" in line or "upgrade" in line for line in lines)


def test_apt_update_flag_upgrades_once(tmp_path: Path) -> None:
    result, lines = _run(tmp_path, "-apt-update")

    assert result.returncode == 0, result.stderr
    assert [line for line in lines if "full-upgrade" in line] == ["apt -y full-upgrade"]
    assert "apt clean" in lines
