import json
import shutil
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _copy_bud_wrapper(tmp_path: Path) -> Path:
    scripts_dir = tmp_path / "repo" / "mac_scripts"
    scripts_dir.mkdir(parents=True)
    wrapper = scripts_dir / "bud"
    shutil.copy2(REPO_ROOT / "mac_scripts" / "bud", wrapper)
    return wrapper


def _copy_twofa_wrapper(tmp_path: Path) -> Path:
    scripts_dir = tmp_path / "repo" / "mac_scripts"
    scripts_dir.mkdir(parents=True)
    wrapper = scripts_dir / "2fa"
    shutil.copy2(REPO_ROOT / "mac_scripts" / "2fa", wrapper)
    return wrapper


def _recording_python(
    repo_root: Path,
    exit_code: int,
    *,
    invocation_variable: str = "BUD_INVOCATION",
) -> Path:
    python = repo_root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"Path(os.environ[{invocation_variable!r}]).write_text(json.dumps(sys.argv[1:]))\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    return python


def test_bud_uses_project_venv_and_forwards_arguments(tmp_path: Path) -> None:
    wrapper = _copy_bud_wrapper(tmp_path)
    repo_root = wrapper.parents[1]
    _recording_python(repo_root, exit_code=37)
    invocation_path = tmp_path / "invocation.json"

    result = subprocess.run(
        [str(wrapper), "list", "--sample"],
        env={"BUD_INVOCATION": str(invocation_path)},
        text=True,
        capture_output=True,
        check=False,
    )

    invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
    assert invocation[0].endswith("functions/brew-desired-update/__main__.py")
    assert invocation[1:] == ["list", "--sample"]
    assert result.returncode == 37


def test_bud_reports_how_to_create_missing_project_environment(tmp_path: Path) -> None:
    wrapper = _copy_bud_wrapper(tmp_path)

    result = subprocess.run(
        [str(wrapper)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "bud" in result.stderr
    assert "uv sync --locked" in result.stderr


def test_twofa_uses_project_venv_and_forwards_arguments_from_foreign_cwd(
    tmp_path: Path,
) -> None:
    wrapper = _copy_twofa_wrapper(tmp_path)
    repo_root = wrapper.parents[1]
    _recording_python(
        repo_root,
        exit_code=37,
        invocation_variable="TWOFA_INVOCATION",
    )
    invocation_path = tmp_path / "invocation.json"
    foreign_cwd = tmp_path / "foreign"
    foreign_cwd.mkdir()

    result = subprocess.run(
        [str(wrapper), "--help"],
        cwd=foreign_cwd,
        env={"TWOFA_INVOCATION": str(invocation_path)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert invocation_path.exists(), "2fa did not use the project Python"
    invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
    assert invocation == ["-m", "twofa.cli", "--help"]
    assert result.returncode == 37


def test_twofa_reports_how_to_create_missing_project_environment(
    tmp_path: Path,
) -> None:
    wrapper = _copy_twofa_wrapper(tmp_path)

    result = subprocess.run(
        [str(wrapper)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert "2fa" in stderr_lines[0]
    assert "uv sync --locked" in stderr_lines[0]
    assert not (wrapper.parents[1] / ".venv").exists()
