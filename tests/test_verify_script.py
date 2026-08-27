from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/zsh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _verification_repo(tmp_path: Path, *, production_lines: int = 1) -> tuple[Path, Path]:
    assert VERIFY_SCRIPT.is_file(), "scripts/verify is missing"
    assert os.access(VERIFY_SCRIPT, os.X_OK), "scripts/verify is not executable"

    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(VERIFY_SCRIPT, root / "scripts" / "verify")

    functions = root / "mac_scripts" / "functions"
    for package in ("shared", "twofa", "git_tools"):
        package_dir = functions / package
        package_dir.mkdir(parents=True)
        (package_dir / "module.py").write_text("line\n" * production_lines, encoding="utf-8")

    skm_tests = functions / "skills-manager" / "__tests__"
    skm_tests.mkdir(parents=True)
    (skm_tests / "smoke.test.mjs").write_text("// fixture\n", encoding="utf-8")

    log = tmp_path / "commands.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "uv", 'print -r -- "uv $*" >> "$VERIFY_LOG"')
    _write_executable(fake_bin / "node", 'print -r -- "node $*" >> "$VERIFY_LOG"')
    _write_executable(fake_bin / "git", 'print -r -- "git $*" >> "$VERIFY_LOG"')

    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    _write_executable(
        python,
        'print -r -- "python $* coverage=${COVERAGE_FILE:-}" >> "$VERIFY_LOG"',
    )
    return root, log


def _run_verify(root: Path, log: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path / 'bin'}:{env['PATH']}",
            "TMPDIR": str(tmp_path),
            "VERIFY_LOG": str(log),
        }
    )
    return subprocess.run(
        [root / "scripts" / "verify"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_verify_runs_every_gate_from_outside_the_repository(tmp_path: Path) -> None:
    root, log = _verification_repo(tmp_path)

    result = _run_verify(root, log, tmp_path)

    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="utf-8").splitlines()
    assert commands[0] == "uv sync --locked"
    assert commands[1] == "git diff --exit-code -- uv.lock"
    assert commands[2].startswith(
        "python -m pytest --cov=shared --cov=twofa --cov=git_tools coverage="
    )
    assert commands[3].startswith(
        "node --test mac_scripts/functions/skills-manager/__tests__/"
    )
    assert commands[3].endswith("smoke.test.mjs")
    assert commands[4] == "git diff --check"
    coverage_file = Path(commands[2].split("coverage=", 1)[1])
    assert coverage_file.parent == tmp_path
    assert not coverage_file.exists()
    assert "Verification complete" in result.stdout


def test_verify_rejects_production_source_over_budget(tmp_path: Path) -> None:
    root, log = _verification_repo(tmp_path, production_lines=1104)

    result = _run_verify(root, log, tmp_path)

    assert result.returncode == 1
    assert "Production Python source is 3312 lines; limit is 3309" in result.stderr
    assert "git diff --check" not in log.read_text(encoding="utf-8")
