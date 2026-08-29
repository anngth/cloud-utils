from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from skills_manager.planner import (
    DesiredConflict,
    InstallPlan,
    Requirement,
    UninstallPlan,
    requirement_key,
)
import skills_manager.upstream as upstream


def requirement(source: str, skill: str) -> Requirement:
    return Requirement(requirement_key(source, skill), source, skill)


def install_plan(**changes: object) -> InstallPlan:
    values = {
        "install": (),
        "skip": (),
        "conflicts": (),
        "extras": (),
        "desired_conflicts": (),
    }
    values.update(changes)
    return InstallPlan(**values)  # type: ignore[arg-type]


def uninstall_plan(**changes: object) -> UninstallPlan:
    values = {
        "remove": (),
        "retain": (),
        "absent": (),
        "conflicts": (),
        "unlink_profiles": (),
        "desired_conflicts": (),
    }
    values.update(changes)
    return UninstallPlan(**values)  # type: ignore[arg-type]


def test_install_batches_by_first_seen_source_and_continues_after_failure(
    tmp_path: Path,
) -> None:
    plan = install_plan(
        install=(
            requirement("owner/a", "one"),
            requirement("owner/b", "three"),
            requirement("owner/a", "two"),
        )
    )
    calls: list[tuple[tuple[str, ...], Path]] = []
    statuses = iter((7, 0))

    def mutate(args, *, cwd):
        calls.append((tuple(args), Path(cwd)))
        return next(statuses)

    result = upstream.execute_install_plan(
        plan, project_root=tmp_path, run_mutation=mutate
    )

    assert calls == [
        (
            (
                "skills", "add", "owner/a", "--skill", "one",
                "--skill", "two",
            ),
            tmp_path,
        ),
        (("skills", "add", "owner/b", "--skill", "three"), tmp_path),
    ]
    assert result.ok is False
    assert tuple(record.status for record in result.failed) == (7,)
    assert tuple(record.status for record in result.succeeded) == (0,)


def test_install_appends_yes_last_and_emits_frozen_records(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    events: list[object] = []

    result = upstream.execute_install_plan(
        install_plan(
            install=(
                requirement("owner/a", "one"),
                requirement("owner/a", "two"),
            )
        ),
        yes=True,
        project_root=tmp_path,
        run_mutation=lambda args, **_: calls.append(tuple(args)) or 0,
        on_event=events.append,
    )

    assert calls == [
        (
            "skills", "add", "owner/a", "--skill", "one",
            "--skill", "two", "--yes",
        )
    ]
    assert tuple(events) == result.succeeded
    assert result.failed == ()
    assert result.succeeded[0].skills == ("one", "two")
    with pytest.raises(FrozenInstanceError):
        result.succeeded[0].status = 4


def test_install_conflicts_do_not_block_eligible_batches_but_fail_result(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    result = upstream.execute_install_plan(
        install_plan(
            install=(requirement("owner/a", "safe"),),
            conflicts=(requirement("owner/a", "blocked"),),
        ),
        project_root=tmp_path,
        run_mutation=lambda args, **_: calls.append(tuple(args)) or 0,
    )

    assert calls == [("skills", "add", "owner/a", "--skill", "safe")]
    assert result.ok is False
    assert len(result.succeeded) == 1


def test_install_desired_conflict_short_circuits_calls_and_events(
    tmp_path: Path,
) -> None:
    calls: list[object] = []
    events: list[object] = []
    result = upstream.execute_install_plan(
        install_plan(
            install=(requirement("owner/a", "safe"),),
            desired_conflicts=(
                DesiredConflict("review", ("owner/a", "owner/b")),
            ),
        ),
        project_root=tmp_path,
        run_mutation=lambda *args, **kwargs: calls.append((args, kwargs)) or 0,
        on_event=events.append,
    )

    assert calls == []
    assert events == []
    assert result.ok is False
    assert result.succeeded == result.failed == ()


def test_empty_install_plan_succeeds_without_mutation(tmp_path: Path) -> None:
    result = upstream.execute_install_plan(
        install_plan(),
        project_root=tmp_path,
        run_mutation=lambda *_args, **_kwargs: pytest.fail("mutation called"),
    )

    assert result.ok is True
    assert result.succeeded == result.failed == ()


def test_uninstall_uses_one_batch_of_only_remove_names_with_yes_last(
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], Path]] = []
    events: list[object] = []
    plan = uninstall_plan(
        remove=(requirement("owner/a", "one"), requirement("owner/b", "two")),
        retain=(requirement("owner/a", "shared"),),
        absent=(requirement("owner/a", "absent"),),
    )

    result = upstream.execute_uninstall_plan(
        plan,
        yes=True,
        project_root=tmp_path,
        run_mutation=lambda args, *, cwd: calls.append(
            (tuple(args), Path(cwd))
        ) or 0,
        on_event=events.append,
    )

    assert calls == [
        (("skills", "remove", "one", "two", "--yes"), tmp_path)
    ]
    assert result.ok is True
    assert tuple(events) == result.succeeded
    assert result.succeeded[0].action == "uninstall"
    assert result.succeeded[0].source is None
    assert result.succeeded[0].skills == ("one", "two")


def test_uninstall_failure_and_plan_conflict_report_exact_failed_batch(
    tmp_path: Path,
) -> None:
    events: list[object] = []
    result = upstream.execute_uninstall_plan(
        uninstall_plan(
            remove=(requirement("owner/a", "one"),),
            conflicts=(requirement("owner/a", "blocked"),),
        ),
        project_root=tmp_path,
        run_mutation=lambda *_args, **_kwargs: 4,
        on_event=events.append,
    )

    assert result.ok is False
    assert result.succeeded == ()
    assert result.failed == tuple(events)
    assert result.failed[0].status == 4


@pytest.mark.parametrize(
    ("plan", "ok"),
    [
        (uninstall_plan(), True),
        (
            uninstall_plan(
                conflicts=(requirement("owner/a", "blocked"),)
            ),
            False,
        ),
    ],
)
def test_empty_uninstall_plan_returns_conflict_status_without_mutation(
    tmp_path: Path, plan: UninstallPlan, ok: bool
) -> None:
    result = upstream.execute_uninstall_plan(
        plan,
        project_root=tmp_path,
        run_mutation=lambda *_args, **_kwargs: pytest.fail("mutation called"),
    )

    assert result.ok is ok
    assert result.succeeded == result.failed == ()


def test_uninstall_desired_conflict_short_circuits_mutation(tmp_path: Path) -> None:
    result = upstream.execute_uninstall_plan(
        uninstall_plan(
            remove=(requirement("owner/a", "one"),),
            desired_conflicts=(
                DesiredConflict("review", ("owner/a", "owner/b")),
            ),
        ),
        project_root=tmp_path,
        run_mutation=lambda *_args, **_kwargs: pytest.fail("mutation called"),
    )

    assert result.ok is False
    assert result.succeeded == result.failed == ()
