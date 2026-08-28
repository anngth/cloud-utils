from __future__ import annotations

import os
from pathlib import Path

import pytest

from shared.process import CommandResult
from skills_manager.upstream import (
    AvailableSkill,
    DiscoveryParseError,
    InstalledRecord,
    UpstreamError,
    discover_available_skills,
    has_command,
    list_installed_skills,
    parse_available_skills,
    run_skills_mutation,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_has_command_searches_supplied_path_without_shell(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "npx"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    env = {"PATH": os.pathsep.join((str(tmp_path / "missing"), str(bin_dir)))}
    assert has_command("npx", env=env) is True
    assert has_command("missing", env=env) is False
    assert has_command("npx", env={"UNRELATED": "kept"}) is False


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        (
            "discovery-flat.txt",
            (
                AvailableSkill(
                    "frontend-design", "Build distinctive frontend interfaces."
                ),
                AvailableSkill(
                    "react-best-practices",
                    "Review React code for performance.",
                ),
            ),
        ),
        (
            "discovery-grouped.txt",
            (
                AvailableSkill(
                    "react-best-practices",
                    "Review React code for performance.",
                ),
                AvailableSkill("docs", "Create concise documentation."),
            ),
        ),
        (
            "discovery-superpowers-1.5.19.txt",
            (
                AvailableSkill(
                    "brainstorming",
                    "You MUST use this before any creative work - creating "
                    "features, building components, adding functionality, or "
                    "modifying behavior. Explores user intent, requirements and "
                    "design before implementation.",
                ),
                AvailableSkill(
                    "dispatching-parallel-agents",
                    "Use when facing 2+ independent tasks that can be worked on "
                    "without shared state or sequential dependencies",
                ),
            ),
        ),
    ],
)
def test_parse_available_skills_matches_discovery_fixtures(
    fixture_name: str, expected: tuple[AvailableSkill, ...]
) -> None:
    output = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    assert parse_available_skills(output) == expected


def test_parse_available_skills_strips_ansi_before_finding_records() -> None:
    output = (FIXTURES / "discovery-flat.txt").read_text(encoding="utf-8")
    decorated = output.replace("Available Skills", "\x1b[32mAvailable Skills\x1b[0m")
    assert parse_available_skills(decorated)[0] == AvailableSkill(
        "frontend-design", "Build distinctive frontend interfaces."
    )


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("Available Skills\nmaybe", "Could not parse available skills"),
        (
            "◇ Available Skills\n└ Use --skill <name> to install specific skills",
            "No skill records found",
        ),
        (
            "\n".join(
                (
                    "◇  Available Skills",
                    "│    brainstorming",
                    "│",
                    "│    dispatching-parallel-agents",
                    "│      Use independent agents.",
                    "└  Use --skill <name> to install specific skills",
                )
            ),
            "Missing description for skill: brainstorming",
        ),
        (
            "\n".join(
                (
                    "◇  Available Skills",
                    "│    duplicate",
                    "│      First description.",
                    "│    duplicate",
                    "│      Second description.",
                    "└  Use --skill <name> to install specific skills",
                )
            ),
            "Duplicate skill names in discovery output",
        ),
    ],
)
def test_parse_available_skills_rejects_uncertain_output(
    output: str, message: str
) -> None:
    with pytest.raises(DiscoveryParseError, match=f"^{message}$"):
        parse_available_skills(output)


def test_discovery_uses_captured_npx_without_shell(tmp_path: Path) -> None:
    discovery_fixture = (FIXTURES / "discovery-flat.txt").read_text(
        encoding="utf-8"
    )
    calls: list[tuple[tuple[str, ...], Path, bool]] = []

    def runner(argv, *, cwd, capture, **_):
        calls.append((tuple(argv), Path(cwd), capture))
        return CommandResult(0, discovery_fixture, "")

    result = discover_available_skills(
        "anthropics/skills", cwd=tmp_path, runner=runner
    )
    assert calls == [
        (("npx", "skills", "add", "anthropics/skills", "--list"), tmp_path, True)
    ]
    assert result[0] == AvailableSkill(
        "frontend-design", "Build distinctive frontend interfaces."
    )


def test_discovery_rejects_nonzero_command_even_with_parseable_stdout(
    tmp_path: Path,
) -> None:
    output = (FIXTURES / "discovery-flat.txt").read_text(encoding="utf-8")

    def runner(*_args, **_kwargs):
        return CommandResult(7, output, "failed")

    with pytest.raises(
        UpstreamError, match="^Could not discover available skills$"
    ):
        discover_available_skills("owner/repo", cwd=tmp_path, runner=runner)


def test_discovery_wraps_process_start_failure_as_stable_upstream_error(
    tmp_path: Path,
) -> None:
    def runner(*_args, **_kwargs):
        raise OSError("npx unavailable")

    with pytest.raises(
        UpstreamError, match="^Could not discover available skills$"
    ) as caught:
        discover_available_skills("owner/repo", cwd=tmp_path, runner=runner)
    assert isinstance(caught.value.__cause__, OSError)


def test_list_installed_uses_captured_npx_and_returns_frozen_records(
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], Path, bool]] = []

    def runner(argv, *, cwd, capture, **_):
        calls.append((tuple(argv), Path(cwd), capture))
        return CommandResult(
            0,
            '[{"name":"review","path":"/repo/.agents/skills/review",'
            '"scope":"project","agents":["Codex"]}]',
            "",
        )

    assert list_installed_skills(cwd=tmp_path, runner=runner) == (
        InstalledRecord(
            "review", "/repo/.agents/skills/review", "project", ("Codex",)
        ),
    )
    assert calls == [
        (("npx", "skills", "list", "--json"), tmp_path, True)
    ]


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (CommandResult(1, "[]", "failed"), "Could not list installed skills"),
        (CommandResult(0, "{broken", ""), "Could not parse installed skills"),
        (
            CommandResult(
                0,
                '[{"name":"review","path":"/x","scope":"project",'
                '"agents":["Codex"],"extra":NaN}]',
                "",
            ),
            "Could not parse installed skills",
        ),
        (CommandResult(0, "{}", ""), "Invalid installed skills output"),
        (
            CommandResult(
                0,
                '[{"name":"review","path":"/x","scope":"project"}]',
                "",
            ),
            "Invalid installed skills output",
        ),
        (
            CommandResult(
                0,
                '[{"name":"review","path":"/x","scope":"project",'
                '"agents":["Codex",4]}]',
                "",
            ),
            "Invalid installed skills output",
        ),
    ],
)
def test_list_installed_rejects_command_and_shape_failures(
    tmp_path: Path, result: CommandResult, message: str
) -> None:
    with pytest.raises(UpstreamError, match=f"^{message}$"):
        list_installed_skills(
            cwd=tmp_path, runner=lambda *_args, **_kwargs: result
        )


def test_list_installed_wraps_process_start_failure_as_stable_upstream_error(
    tmp_path: Path,
) -> None:
    def runner(*_args, **_kwargs):
        raise OSError("npx unavailable")

    with pytest.raises(
        UpstreamError, match="^Could not list installed skills$"
    ) as caught:
        list_installed_skills(cwd=tmp_path, runner=runner)
    assert isinstance(caught.value.__cause__, OSError)


def test_mutation_inherits_stdio_and_returns_signal_failure_as_one(
    tmp_path: Path,
) -> None:
    seen: list[tuple[tuple[str, ...], Path, bool]] = []

    def runner(argv, *, cwd, capture, **_):
        seen.append((tuple(argv), Path(cwd), capture))
        return CommandResult(1)

    assert (
        run_skills_mutation(
            ["skills", "remove", "demo"], cwd=tmp_path, runner=runner
        )
        == 1
    )
    assert seen == [
        (("npx", "skills", "remove", "demo"), tmp_path, False)
    ]


def test_mutation_maps_negative_subprocess_signal_status_to_one(
    tmp_path: Path,
) -> None:
    def runner(*_args, **_kwargs):
        return CommandResult(-15)

    assert (
        run_skills_mutation(
            ["skills", "remove", "demo"], cwd=tmp_path, runner=runner
        )
        == 1
    )


def test_mutation_maps_process_start_failure_to_one(tmp_path: Path) -> None:
    def runner(*_args, **_kwargs):
        raise OSError("npx unavailable")

    assert (
        run_skills_mutation(
            ["skills", "remove", "demo"], cwd=tmp_path, runner=runner
        )
        == 1
    )


def test_mutation_explicitly_forwards_default_inherited_environment(
    tmp_path: Path,
) -> None:
    seen: list[object] = []

    def runner(argv, *, cwd, capture, env):
        seen.append((tuple(argv), Path(cwd), capture, env))
        return CommandResult(0)

    assert (
        run_skills_mutation(
            ["skills", "remove", "demo"], cwd=tmp_path, runner=runner
        )
        == 0
    )
    assert seen == [
        (("npx", "skills", "remove", "demo"), tmp_path, False, None)
    ]


def test_mutation_forwards_supplied_environment_without_losing_entries(
    tmp_path: Path,
) -> None:
    supplied = {"PATH": "/controlled/bin", "SKM_SENTINEL": "preserved"}
    seen: list[object] = []

    def runner(argv, *, cwd, capture, env):
        seen.append((tuple(argv), Path(cwd), capture, env))
        return CommandResult(0)

    assert (
        run_skills_mutation(
            ["skills", "add", "owner/repo with | %"],
            cwd=tmp_path,
            env=supplied,
            runner=runner,
        )
        == 0
    )
    assert seen == [
        (
            ("npx", "skills", "add", "owner/repo with | %"),
            tmp_path,
            False,
            supplied,
        )
    ]
