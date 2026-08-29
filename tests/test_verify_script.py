from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/zsh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _verification_repo(
    tmp_path: Path,
    *,
    line_counts: dict[str, int] | None = None,
    include_npx: bool = True,
) -> tuple[Path, Path]:
    assert VERIFY_SCRIPT.is_file(), "scripts/verify is missing"
    assert os.access(VERIFY_SCRIPT, os.X_OK), "scripts/verify is not executable"

    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(VERIFY_SCRIPT, root / "scripts" / "verify")

    functions = root / "mac_scripts" / "functions"
    counts = line_counts or {
        "shared": 1,
        "twofa": 1,
        "git_tools": 1,
        "skills_manager": 1,
    }
    for package, production_lines in counts.items():
        package_dir = functions / package
        package_dir.mkdir(parents=True)
        (package_dir / "module.py").write_text(
            "line\n" * production_lines,
            encoding="utf-8",
        )

    log = tmp_path / "commands.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "uv", 'print -r -- "uv $*" >> "$VERIFY_LOG"')
    if include_npx:
        _write_executable(
            fake_bin / "npx",
            'print -r -- "npx $*" >> "$VERIFY_LOG"',
        )
    _write_executable(fake_bin / "git", 'print -r -- "git $*" >> "$VERIFY_LOG"')

    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    _write_executable(
        python,
        'print -r -- "python $* coverage=${COVERAGE_FILE:-}" >> "$VERIFY_LOG"',
    )
    return root, log


def _run_verify(
    root: Path,
    log: Path,
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
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
        "python -m pytest --cov=shared --cov=twofa --cov=git_tools "
        "--cov=skills_manager coverage="
    )
    assert commands[3].startswith(
        "python -m coverage report "
        "--include=*/skills_manager/*.py --fail-under=95 coverage="
    )
    assert commands[4] == "git diff --check"
    coverage_file = Path(commands[2].split("coverage=", 1)[1])
    assert commands[3].endswith(f"coverage={coverage_file}")
    assert coverage_file.parent == tmp_path
    assert not coverage_file.exists()
    assert not any(command.startswith("node --test") for command in commands)
    assert "Verification complete" in result.stdout


def test_verify_requires_npx_before_running_gates(tmp_path: Path) -> None:
    root, log = _verification_repo(tmp_path, include_npx=False)

    result = _run_verify(root, log, tmp_path)

    assert result.returncode == 1
    assert result.stderr == "❌ Missing required command: npx\n"
    assert not log.exists()


@pytest.mark.parametrize(
    ("line_counts", "returncode"),
    [
        (
            {
                "shared": 1527,
                "twofa": 1526,
                "git_tools": 1526,
                "skills_manager": 1526,
            },
            0,
        ),
        (
            {
                "shared": 1527,
                "twofa": 1527,
                "git_tools": 1527,
                "skills_manager": 1527,
            },
            1,
        ),
    ],
)
def test_verify_enforces_combined_source_ceiling(
    tmp_path: Path,
    line_counts: dict[str, int],
    returncode: int,
) -> None:
    root, log = _verification_repo(tmp_path, line_counts=line_counts)

    result = _run_verify(root, log, tmp_path)

    assert result.returncode == returncode
    expected_total = sum(line_counts.values())
    message = f"Production Python source is {expected_total} lines; limit is 6105"
    assert (message in result.stderr) is (returncode == 1)
    commands = log.read_text(encoding="utf-8").splitlines()
    assert any(command.startswith("python -m coverage report") for command in commands)
    assert not any(command.startswith("node --test") for command in commands)
    assert ("git diff --check" in commands) is (returncode == 0)
