from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import sys
from typing import NoReturn, TextIO

from .backup import BackupContext, run_backup_command
from .config import resolve_gt_paths
from .fetch import run_fetch_command
from .push import run_push_command
from .ui import GitToolsUi

_MISSING = object()

def _get(dependencies: object | None, name: str, default):
    if dependencies is None:
        return default
    if isinstance(dependencies, Mapping):
        return dependencies.get(name, default)
    return getattr(dependencies, name, default)

def _backup_context(
    *,
    cwd: str | Path,
    env: Mapping[str, str],
    stdout: TextIO,
    ui: GitToolsUi,
    dependencies: object | None,
) -> BackupContext:
    supplied = _get(dependencies, "backup_context", _MISSING)
    if supplied is not _MISSING:
        return supplied
    context = BackupContext(
        paths=_get(dependencies, "paths", resolve_gt_paths(env)),
        ui=ui,
        cwd=cwd,
        env=env,
        stdin=_get(dependencies, "stdin", sys.stdin),
        stdout=stdout,
    )
    for name in BackupContext.__dataclass_fields__:
        if name in {"paths", "ui", "cwd", "env", "stdin", "stdout"}:
            continue
        value = _get(dependencies, name, _MISSING)
        if value is not _MISSING:
            setattr(context, name, value)
    return context

def run_cli(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    dependencies: object | None = None,
) -> int:
    working_dir = Path.cwd() if cwd is None else cwd
    environment = os.environ if env is None else env
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    ui = _get(dependencies, "ui", GitToolsUi(out, err))

    action = argv[0] if argv else None
    args = list(argv[1:])
    if not action:
        ui.usage()
        return 1
    if action in {"help", "-h", "--help"}:
        ui.usage()
        return 0
    if action == "push":
        handler = _get(dependencies, "run_push_command", run_push_command)
        return handler(args, cwd=working_dir, env=environment, ui=ui)
    if action == "fetch":
        handler = _get(dependencies, "run_fetch_command", run_fetch_command)
        return handler(args, cwd=working_dir, env=environment, ui=ui)
    if action == "backup":
        handler = _get(dependencies, "run_backup_command", run_backup_command)
        return handler(
            args,
            context=_backup_context(
                cwd=working_dir,
                env=environment,
                stdout=out,
                ui=ui,
                dependencies=dependencies,
            ),
        )
    ui.error(f"Unknown command: {action}")
    ui.usage()
    return 1

def main() -> NoReturn:
    raise SystemExit(run_cli(sys.argv[1:]))

if __name__ == "__main__":
    main()
