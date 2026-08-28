from __future__ import annotations

from typing import TYPE_CHECKING

from .planner import StatusResult, catalog_requirements, classify_status

if TYPE_CHECKING:
    from .cli import CommandContext


def status_is_ok(status: StatusResult) -> bool:
    return not (
        status.missing
        or status.mismatches
        or status.untracked
        or status.desired_conflicts
    )


def run_status(context: CommandContext) -> int:
    try:
        project_root = context.services.resolve_project_root(cwd=context.cwd)
        merged = catalog_requirements(context.catalog)
        installed = context.services.load_installed_state(
            project_root=project_root,
            env=context.env,
        )
        status = classify_status(merged, installed)
        context.ui.status(
            project_root=project_root,
            profile_names=(),
            catalog=context.catalog,
            status=status,
        )
        return 0 if status_is_ok(status) else 1
    except Exception as error:
        context.ui.error(str(error))
        return 1
