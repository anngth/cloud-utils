from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gt.jsonl"
SOURCE_A = "git@github.com:org/a.git"
SOURCE_B = "git@gitlab.com:acme/b.git"
OLD = "2000-01-01T00:00:00.000Z"
FUTURE = "2999-01-01T00:00:00.000Z"


def _repo(url: str, *, checked: str | None = None) -> dict[str, object]:
    return {
        "url": url,
        "lastBackupAt": None,
        "lastCheckedAt": checked,
        "selectedLast": False,
    }


SCENARIOS: tuple[dict[str, object], ...] = (
    {"name": "help", "argv": ["--help"]},
    {"name": "unknown-command", "argv": ["unknown"]},
    {"name": "push-success", "argv": ["push"]},
    {"name": "push-failure", "argv": ["push"]},
    {"name": "fetch-feature-branch", "argv": ["fetch"]},
    {"name": "fetch-sync-upstream", "argv": ["fetch", "--sync-upstream"]},
    {"name": "fetch-failure", "argv": ["fetch"]},
    {
        "name": "backup-add-multi-partial",
        "argv": ["backup", "add", SOURCE_A, "not-an-ssh-url", SOURCE_B],
    },
    {
        "name": "backup-remove-index",
        "argv": ["backup", "remove", "1"],
        "document": {"version": 4, "repos": [_repo(SOURCE_A), _repo(SOURCE_B)]},
    },
    {
        "name": "backup-all-create",
        "argv": ["backup", "--all"],
        "document": {"version": 4, "repos": [_repo(SOURCE_A)]},
    },
    {
        "name": "backup-all-unchanged",
        "argv": ["backup", "--all"],
        "document": {"version": 4, "repos": [_repo(SOURCE_A)]},
    },
    {
        "name": "backup-all-mixed-failure",
        "argv": ["backup", "--all"],
        "document": {"version": 4, "repos": [_repo(SOURCE_A), _repo(SOURCE_B)]},
    },
    {
        "name": "backup-all-dry-run",
        "argv": ["backup", "--all", "--dry-run"],
        "document": {"version": 4, "repos": [_repo(SOURCE_A), _repo(SOURCE_B)]},
    },
    {
        "name": "backup-all-force",
        "argv": ["backup", "--all", "--force"],
        "document": {"version": 4, "repos": [_repo(SOURCE_A)]},
    },
    {
        "name": "backup-stale-empty",
        "argv": ["backup", "stale", "--all"],
        "document": {"version": 4, "repos": [_repo(SOURCE_A, checked=FUTURE)]},
    },
    {
        "name": "backup-stale-all",
        "argv": ["backup", "stale", "--all", "--dry-run"],
        "document": {"version": 4, "repos": [_repo(SOURCE_A, checked=OLD)]},
    },
    {
        "name": "config-v1-migration",
        "argv": ["backup", "add", SOURCE_B],
        "document": {"version": 1, "repos": [SOURCE_A]},
    },
    {
        "name": "config-v2-migration",
        "argv": ["backup", "add", SOURCE_B],
        "document": {
            "version": 2,
            "repos": [{"url": SOURCE_A, "lastBackupAt": OLD}],
        },
    },
    {
        "name": "config-v3-migration",
        "argv": ["backup", "add", SOURCE_B],
        "document": {
            "version": 3,
            "repos": [
                {"url": SOURCE_A, "lastBackupAt": OLD, "lastCheckedAt": OLD}
            ],
        },
    },
    {
        "name": "invalid-timestamp",
        "argv": ["backup", "--all", "--dry-run"],
        "document": {
            "version": 4,
            "repos": [
                {
                    "url": SOURCE_A,
                    "lastBackupAt": "not-a-date",
                    "lastCheckedAt": None,
                    "selectedLast": False,
                }
            ],
        },
    },
)


FAKE_TOOL = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote

exe = Path(sys.argv[0]).name
args = sys.argv[1:]
scenario = os.environ["GT_PARITY_SCENARIO"]
stdin = sys.stdin.read()
stdout = ""
stderr = ""
code = 0

if exe == "git":
    if args == ["rev-parse", "--git-dir"]:
        stdout = ".git\n"
    elif args == ["rev-parse", "--abbrev-ref", "HEAD"]:
        stdout = "main\n" if scenario == "fetch-sync-upstream" else "feature/demo\n"
    elif args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
        stdout = "origin/feature/demo\n"
    elif args[:3] == ["show-ref", "--verify", "--quiet"]:
        ref = args[3]
        if scenario == "fetch-sync-upstream":
            code = 0 if ref in {"refs/remotes/origin/main", "refs/heads/main"} else 1
        elif scenario == "fetch-feature-branch":
            code = 0 if ref in {"refs/heads/main", "refs/heads/feature/demo"} else 1
        elif scenario.startswith("backup-") and "/refs/remotes/" not in ref:
            code = 0 if ref.endswith("/main") or ref.endswith("/develop") else 1
        else:
            code = 1
    elif args[:4] == ["ls-remote", "--exit-code", "--heads", "origin"]:
        code = 2
    elif args[:3] == ["ls-remote", "--heads", "origin"]:
        if scenario == "fetch-sync-upstream" and args[-1] == "main":
            stdout = "a" * 40 + "\trefs/heads/main\n"
    elif args[:3] == ["ls-remote", "--heads", "upstream"]:
        if scenario == "fetch-sync-upstream":
            stdout = "b" * 40 + "\trefs/heads/main\n"
    elif args[:1] == ["ls-remote"]:
        stdout = "a" * 40 + "\trefs/heads/main\n" + "b" * 40 + "\trefs/tags/v1\n"
    elif args == ["fetch", "--all", "--prune"]:
        code = 1 if scenario == "fetch-failure" else 0
        stderr = "all remotes failed\n" if code else ""
    elif args == ["fetch", "origin", "--prune"]:
        code = 1 if scenario == "fetch-failure" else 0
        stderr = "origin failed\n" if code else ""
    elif args[:1] == ["push"] and "--force-with-lease" in args:
        if scenario == "push-failure":
            code = 1
            stdout = "remote rejected\n"
            stderr = "lease rejected\n"
        else:
            stdout = "pushed feature/demo\n"
    elif args[:3] == ["remote", "get-url", "upstream"]:
        stdout = "git@github.com:upstream/repo.git\n"
    elif args == ["branch", "-vv"]:
        if scenario == "fetch-feature-branch":
            stdout = "  old abc [origin/old: gone] old work\n"
    elif args[:2] == ["for-each-ref", "--format=%(refname:short)"]:
        if scenario == "fetch-feature-branch":
            stdout = "feature/done\nfeature/demo\n"
    elif args[:2] == ["clone", "--mirror"]:
        if scenario == "backup-all-mixed-failure" and args[2].endswith("/b.git"):
            code = 1
            stderr = "source unavailable\n"
        else:
            Path(args[3]).mkdir(parents=True, exist_ok=True)
    elif args[:2] == ["push", "--prune"]:
        stdout = "mirror pushed\n"
    elif args == ["fetch"]:
        stdout = "fetched\n"
    elif args[:1] in (["fetch"], ["pull"], ["merge"], ["update-ref"], ["branch"], ["diff"]):
        pass
    elif args[:2] == ["merge-base", "--is-ancestor"]:
        pass
elif exe == "glab":
    if args == ["auth", "status"]:
        stdout = "authenticated\n"
    elif args[:2] == ["api", "groups/anngth-dev%2Fbackups"]:
        stdout = '{"id": 101, "path": "backups"}\n'
    elif args[:1] == ["api"] and len(args) >= 2 and args[1].startswith("projects/") and "--method" not in args:
        requested = unquote(args[1]).rsplit("/", 1)[-1]
        if scenario in {"backup-all-unchanged", "backup-all-force"}:
            stdout = json.dumps({"path": requested}) + "\n"
        else:
            code = 1
            stderr = "404 Not Found\n"
    elif args[:2] == ["repo", "create"]:
        stdout = "project created\n"

record = {
    "executable": exe,
    "argv": args,
    "cwd": os.getcwd(),
    "env": {
        key: os.environ.get(key)
        for key in ("HOME", "CLOUD_UTILS_CONFIG_DIR", "GT_PARITY_SCENARIO")
    },
    "stdin": stdin,
    "stdout": stdout,
    "stderr": stderr,
    "returncode": code,
}
with open(os.environ["GT_PARITY_PROCESS_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
sys.stdout.write(stdout)
sys.stderr.write(stderr)
raise SystemExit(code)
'''


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _prepare_case(base: Path, scenario: dict[str, object]) -> tuple[Path, dict[str, str], Path]:
    root = base / "root"
    home = root / "home"
    cwd = root / "work"
    config = home / "config"
    runtime = base / "runtime"
    fake_bin = runtime / "bin"
    process_log = runtime / "process.jsonl"
    home.mkdir(parents=True)
    cwd.mkdir(parents=True)
    fake_bin.mkdir(parents=True)
    for executable in ("git", "glab"):
        path = fake_bin / executable
        path.write_text(FAKE_TOOL, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    document = scenario.get("document")
    if document is not None:
        _write_json(config / "gt" / "backups.json", document)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CLOUD_UTILS_CONFIG_DIR": str(config),
            "GT_PARITY_SCENARIO": str(scenario["name"]),
            "GT_PARITY_PROCESS_LOG": str(process_log),
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "LC_ALL": "C",
            "LANG": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return root, env, process_log


_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3,6})?Z")
_BACKUP_TEMP_RE = re.compile(r"(?:/[^\s\"']+)?/gt-backup-[A-Za-z0-9_-]+")
_BACKUP_NAME_RE = re.compile(r"gt-backup-[A-Za-z0-9_-]+")


def _normalize_text(value: str, *, root: Path) -> str:
    normalized = value.replace(str(root), "$ROOT")
    normalized = normalized.replace(str(REPO_ROOT), "$REPO")
    normalized = _TIMESTAMP_RE.sub("<ISO-TIMESTAMP>", normalized)
    normalized = _BACKUP_TEMP_RE.sub("$TMP/gt-backup-ID", normalized)
    return _BACKUP_NAME_RE.sub("gt-backup-ID", normalized)


def _normalize(value: Any, *, root: Path) -> Any:
    if isinstance(value, str):
        return _normalize_text(value, root=root)
    if isinstance(value, list):
        return [_normalize(item, root=root) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item, root=root) for key, item in value.items()}
    return value


def snapshot_tree(root: Path) -> list[dict[str, object]]:
    snapshot: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_relative_to(root / "home" / "Library" / "Caches"):
            continue
        content = path.read_bytes()
        try:
            text = _normalize_text(content.decode("utf-8"), root=root)
            encoded = base64.b64encode(text.encode()).decode("ascii")
        except UnicodeDecodeError:
            encoded = base64.b64encode(content).decode("ascii")
        snapshot.append(
            {
                "path": path.relative_to(root).as_posix(),
                "mode": stat.S_IMODE(path.stat().st_mode),
                "content": encoded,
            }
        )
    return snapshot


def _run_case(scenario: dict[str, object]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"gt-parity-{scenario['name']}-") as temp:
        base = Path(temp)
        root, env, log_path = _prepare_case(base, scenario)
        command = [sys.executable, "-m", "git_tools.cli", *scenario["argv"]]
        completed = subprocess.run(
            command,
            cwd=root / "work",
            env=env,
            input="",
            text=True,
            capture_output=True,
            check=False,
        )
        process_log = []
        if log_path.exists():
            process_log = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
        return {
            "name": scenario["name"],
            "returncode": completed.returncode,
            "stdout": base64.b64encode(
                _normalize_text(completed.stdout, root=root).encode()
            ).decode("ascii"),
            "stderr": base64.b64encode(
                _normalize_text(completed.stderr, root=root).encode()
            ).decode("ascii"),
            "process_log": _normalize(process_log, root=root),
            "tree": snapshot_tree(root),
        }


def _load_records() -> dict[str, dict[str, object]]:
    return {
        record["name"]: record
        for record in (
            json.loads(line)
            for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
        )
    }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda row: row["name"])
def test_python_matches_reviewed_javascript_record(
    scenario: dict[str, object],
) -> None:
    expected = _load_records()[str(scenario["name"])]
    assert _run_case(scenario) == expected
