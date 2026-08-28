from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Literal

import pytest


ROOT = Path(__file__).resolve().parents[2]
Runtime = Literal["javascript", "python"]


@dataclass(frozen=True, slots=True)
class CommandSnapshot:
    returncode: int
    stdout: bytes
    stderr: bytes
    calls: tuple[dict[str, object], ...]
    files: Mapping[str, bytes]


def runtime_argv(runtime: Runtime) -> list[str]:
    if runtime == "javascript":
        return ["node", str(ROOT / "mac_scripts/functions/skills-manager/cli.mjs")]
    return [str(ROOT / ".venv/bin/python"), "-m", "skills_manager.cli"]


def _write_json_or_bytes(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
        return
    path.write_text(f"{json.dumps(value, indent=2)}\n", encoding="utf-8")


def _write_npx(bin_dir: Path, rules_path: Path, calls_path: Path) -> None:
    npx = bin_dir / "npx"
    npx.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "\n"
        "rules_path = os.environ['SKM_NPX_RULES']\n"
        "calls_path = os.environ['SKM_NPX_CALLS']\n"
        "argv = sys.argv[1:]\n"
        "with open(rules_path, encoding='utf-8') as rules_file:\n"
        "    rules = json.load(rules_file)\n"
        "response = next((rule for rule in rules if rule['argv'] == argv), None)\n"
        "if response is None:\n"
        "    response = {\n"
        "        'status': 1,\n"
        "        'stdout': '',\n"
        "        'stderr': f'Unexpected npx invocation: {argv!r}\\n',\n"
        "    }\n"
        "call = {'argv': argv, 'cwd': os.getcwd(), 'status': response['status']}\n"
        "with open(calls_path, 'a', encoding='utf-8') as calls_file:\n"
        "    calls_file.write(json.dumps(call) + '\\n')\n"
        "sys.stdout.write(response.get('stdout', ''))\n"
        "sys.stderr.write(response.get('stderr', ''))\n"
        "raise SystemExit(response['status'])\n",
        encoding="utf-8",
    )
    npx.chmod(npx.stat().st_mode | stat.S_IXUSR)
    rules_path.write_text("[]", encoding="utf-8")
    calls_path.touch()


def _npx_rules(
    responses: Mapping[Sequence[str], Mapping[str, object]] | None,
) -> list[dict[str, object]]:
    if responses is None:
        return []
    return [
        {"argv": list(argv), **response}
        for argv, response in responses.items()
    ]


def _snapshot_files(config_dir: Path) -> dict[str, bytes]:
    if not config_dir.exists():
        return {}
    return {
        path.relative_to(config_dir).as_posix(): path.read_bytes()
        for path in sorted(config_dir.rglob("*"))
        if path.is_file()
    }


def _snapshot_calls(calls_path: Path) -> tuple[dict[str, object], ...]:
    return tuple(
        json.loads(line)
        for line in calls_path.read_text(encoding="utf-8").splitlines()
    )


def run_skm(
    runtime: Runtime,
    tmp_path: Path,
    argv: Sequence[str],
    *,
    sources: object | None = None,
    legacy: object | None = None,
    npx_responses: Mapping[Sequence[str], Mapping[str, object]] | None = None,
    stdin: bytes = b"",
) -> CommandSnapshot:
    home = tmp_path / "home"
    config = tmp_path / "config"
    project = tmp_path / "project"
    bin_dir = tmp_path / "bin"
    rules_path = tmp_path / "npx-rules.json"
    calls_path = tmp_path / "npx-calls.jsonl"
    for directory in (home, config, project, bin_dir):
        directory.mkdir(parents=True, exist_ok=True)
    _write_npx(bin_dir, rules_path, calls_path)
    rules_path.write_text(json.dumps(_npx_rules(npx_responses)), encoding="utf-8")
    if sources is not None:
        _write_json_or_bytes(config / "skm" / "sources.json", sources)
    if legacy is not None:
        _write_json_or_bytes(config / "skm" / "list.json", legacy)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CLOUD_UTILS_CONFIG_DIR": str(config),
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "PYTHONPATH": f"{ROOT / 'mac_scripts/functions'}{os.pathsep}{env.get('PYTHONPATH', '')}",
            "SKM_NPX_RULES": str(rules_path),
            "SKM_NPX_CALLS": str(calls_path),
        }
    )
    completed = subprocess.run(
        [*runtime_argv(runtime), *argv],
        cwd=project,
        env=env,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return CommandSnapshot(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        calls=_snapshot_calls(calls_path),
        files=_snapshot_files(config),
    )


@pytest.fixture
def skm_runner():
    return run_skm
