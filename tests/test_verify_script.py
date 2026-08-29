from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

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


def _verify_env(
    root: Path,
    log: Path,
    tmp_path: Path,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "PYTHONPATH": str(root / "mac_scripts" / "functions"),
            "TMPDIR": str(tmp_path),
            "VERIFY_LOG": str(log),
        }
    )
    return env


def _run_verify(
    root: Path,
    log: Path,
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [root / "scripts" / "verify"],
        cwd=tmp_path,
        env=_verify_env(root, log, tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )


def _real_coverage_repo(
    tmp_path: Path,
    *,
    total_statements: int,
) -> tuple[Path, Path]:
    root, log = _verification_repo(tmp_path)
    functions = root / "mac_scripts" / "functions"
    for package in ("shared", "twofa", "git_tools", "skills_manager"):
        (functions / package / "__init__.py").write_text("", encoding="utf-8")

    covered_assignments = total_statements - 2
    module_lines = [
        *(f"covered_{index} = {index}" for index in range(covered_assignments)),
        "def uncovered():",
        "    return 1",
    ]
    (functions / "skills_manager" / "module.py").write_text(
        "\n".join(module_lines) + "\n",
        encoding="utf-8",
    )
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_coverage.py").write_text(
        "import git_tools\n"
        "import shared\n"
        "import skills_manager.module\n"
        "import twofa\n"
        "\n"
        "def test_imports_real_coverage_cohort():\n"
        "    assert skills_manager.module.covered_0 == 0\n",
        encoding="utf-8",
    )

    python = root / ".venv" / "bin" / "python"
    _write_executable(
        python,
        f'exec {shlex.quote(sys.executable)} "$@"',
    )
    return root, log


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
        "--include=*/skills_manager/*.py coverage="
    )
    assert commands[4].startswith(
        "python -m coverage json --include=*/skills_manager/*.py -q -o "
    )
    assert commands[5].startswith("python - ")
    assert commands[6] == "git diff --check"
    coverage_file = Path(commands[2].split("coverage=", 1)[1])
    assert commands[3].endswith(f"coverage={coverage_file}")
    assert commands[4].endswith(f"coverage={coverage_file}")
    coverage_json_file = Path(commands[4].split(" -o ", 1)[1].split()[0])
    assert commands[5].startswith(f"python - {coverage_json_file} ")
    assert coverage_file.parent == tmp_path
    assert coverage_json_file.parent == tmp_path
    assert not coverage_file.exists()
    assert not coverage_json_file.exists()
    assert not any(command.startswith("node --test") for command in commands)
    assert "Verification complete" in result.stdout


def test_verify_requires_npx_before_running_gates(tmp_path: Path) -> None:
    root, log = _verification_repo(tmp_path, include_npx=False)

    result = _run_verify(root, log, tmp_path)

    assert result.returncode == 1
    assert result.stderr == "❌ Missing required command: npx\n"
    assert not log.exists()


def test_verify_cleans_all_coverage_files_on_signal(tmp_path: Path) -> None:
    root, log = _verification_repo(tmp_path)
    marker = tmp_path / "pytest.started"
    _write_executable(
        root / ".venv" / "bin" / "python",
        'print -r -- "python $* coverage=${COVERAGE_FILE:-}" '
        '>> "$VERIFY_LOG"\n'
        'if [[ "$1" == "-m" && "$2" == "pytest" ]]; then\n'
        '  : > "$VERIFY_BLOCK_MARKER"\n'
        "  while true; do sleep 1; done\n"
        "fi",
    )
    env = _verify_env(root, log, tmp_path)
    env["VERIFY_BLOCK_MARKER"] = str(marker)
    process = subprocess.Popen(
        [root / "scripts" / "verify"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "verify did not reach pytest"
        created = tuple(tmp_path.glob("cloud-utils-verify.*"))
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)

    assert len(created) == 2
    assert process.returncode == 143
    assert not tuple(tmp_path.glob("cloud-utils-verify.*"))


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


@pytest.mark.parametrize(
    ("total_statements", "returncode", "message"),
    [
        (
            19,
            1,
            "skills_manager statement coverage is 18/19 "
            "(94.7368%); minimum is 95%",
        ),
        (20, 0, ""),
    ],
)
def test_verify_enforces_unrounded_statement_coverage(
    tmp_path: Path,
    total_statements: int,
    returncode: int,
    message: str,
) -> None:
    root, log = _real_coverage_repo(
        tmp_path,
        total_statements=total_statements,
    )

    result = _run_verify(root, log, tmp_path)

    assert result.returncode == returncode
    if message:
        assert message in result.stderr
    else:
        assert "skills_manager statement coverage is" not in result.stderr
    assert not tuple(tmp_path.glob("cloud-utils-verify.*"))
