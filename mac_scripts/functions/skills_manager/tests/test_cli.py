from __future__ import annotations

import io
from pathlib import Path

import pytest

from skills_manager.cli import Services, run_cli
from skills_manager.config import Catalog, CatalogSource, ConfigPaths
from skills_manager.state import InstalledSkill


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def catalog() -> Catalog:
    return Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/catalog", skills=("demo",)),
        ),
    )


def configured_services(
    tmp_path: Path,
    calls: list[object] | None = None,
) -> Services:
    recorded = [] if calls is None else calls
    paths = ConfigPaths.for_config_dir(tmp_path / "config")
    installed = InstalledSkill(
        "demo",
        str(tmp_path / ".agents/skills/demo"),
        ("Codex",),
        "owner/catalog",
        "tracked",
    )
    return Services(
        has_command=lambda name, **kwargs: recorded.append(
            ("has_command", name, kwargs)
        )
        or True,
        initialize_config=lambda **kwargs: recorded.append(
            ("initialize_config", kwargs)
        )
        or paths,
        read_config=lambda value: recorded.append(("read_config", value))
        or catalog(),
        resolve_project_root=lambda **kwargs: recorded.append(
            ("resolve_project_root", kwargs)
        )
        or tmp_path,
        load_installed_state=lambda **kwargs: recorded.append(
            ("load_installed_state", kwargs)
        )
        or {"demo": installed},
    )


@pytest.mark.parametrize("argv", [("help",), ("-h",), ("--help",)])
def test_help_bypasses_config_and_npx(argv: tuple[str, ...]) -> None:
    services = Services(
        initialize_config=lambda **_: pytest.fail("config touched"),
        has_command=lambda *_args, **_kwargs: pytest.fail("npx checked"),
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    assert run_cli(
        argv,
        stdout=stdout,
        stderr=stderr,
        services=services,
    ) == 0
    assert "Usage: skm [command]" in stdout.getvalue()
    assert stderr.getvalue() == ""


@pytest.mark.parametrize("name", ["constructor", "toString", "__proto__"])
def test_inherited_route_names_are_unknown_without_side_effects(
    name: str,
) -> None:
    services = Services(
        initialize_config=lambda **_: pytest.fail("config touched"),
        has_command=lambda *_args, **_kwargs: pytest.fail("npx checked"),
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    assert run_cli(
        (name,),
        stdout=stdout,
        stderr=stderr,
        services=services,
    ) == 1
    assert f"Unknown command: {name}" in stderr.getvalue()
    assert "Usage: skm [command]" in stdout.getvalue()


def test_unknown_route_prints_help_without_bootstrapping() -> None:
    services = Services(
        initialize_config=lambda **_: pytest.fail("config touched"),
        has_command=lambda *_args, **_kwargs: pytest.fail("npx checked"),
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    assert run_cli(
        ("wat",),
        stdout=stdout,
        stderr=stderr,
        services=services,
    ) == 1
    assert "Unknown command: wat" in stderr.getvalue()
    assert "Usage: skm [command]" in stdout.getvalue()


@pytest.mark.parametrize("tty_stream", ["stdin", "stdout"])
def test_no_arguments_require_both_streams_to_be_ttys(
    tty_stream: str,
) -> None:
    streams = {"stdin": io.StringIO(), "stdout": io.StringIO()}
    streams[tty_stream] = TtyStringIO()
    services = Services(
        initialize_config=lambda **_: pytest.fail("config touched"),
        has_command=lambda *_args, **_kwargs: pytest.fail("npx checked"),
    )
    stderr = io.StringIO()

    assert run_cli(
        (),
        stdin=streams["stdin"],
        stdout=streams["stdout"],
        stderr=stderr,
        services=services,
    ) == 1
    assert "skm requires an interactive terminal" in stderr.getvalue()
    assert "skm add" in stderr.getvalue()
    assert "skm remove" in stderr.getvalue()
    assert "--all" in stderr.getvalue()


def test_no_arguments_with_ttys_bootstrap_without_operation_stubs(
    tmp_path: Path,
) -> None:
    calls: list[object] = []
    services = configured_services(tmp_path, calls)
    services.execute_install_plan = lambda *_args, **_kwargs: pytest.fail(
        "install stub reached"
    )
    services.execute_uninstall_plan = lambda *_args, **_kwargs: pytest.fail(
        "uninstall stub reached"
    )

    assert run_cli(
        (),
        cwd=tmp_path,
        env={"HOME": str(tmp_path)},
        stdin=TtyStringIO(),
        stdout=TtyStringIO(),
        stderr=io.StringIO(),
        services=services,
    ) == 0
    assert [call[0] for call in calls] == [
        "initialize_config",
        "read_config",
    ]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("source", "add"), "Usage: skm source add"),
        (("source", "edit"), "Usage: skm source edit"),
        (
            ("source", "add", "owner/repo", "--all", "--no-skills"),
            "mutually exclusive",
        ),
        (("add", "--unsupported"), "Unknown option: --unsupported"),
        (("add", "-f"), "Unknown option: -f"),
        (
            ("remove", "--all", "extra"),
            "Cannot combine --all with explicit source targets",
        ),
        (("status", "extra"), "Unexpected argument: extra"),
    ],
)
def test_grammar_errors_precede_npx_and_config(
    argv: tuple[str, ...],
    message: str,
) -> None:
    services = Services(
        initialize_config=lambda **_: pytest.fail("config touched"),
        has_command=lambda *_args, **_kwargs: pytest.fail("npx checked"),
    )
    stderr = io.StringIO()

    assert run_cli(argv, stderr=stderr, services=services) == 1
    assert message in stderr.getvalue()


def test_status_checks_npx_before_config() -> None:
    calls: list[str] = []
    services = Services(
        has_command=lambda *_args, **_kwargs: calls.append("npx") or False,
        initialize_config=lambda **_: calls.append("config")
        or pytest.fail("config touched"),
    )
    stderr = io.StringIO()

    assert run_cli(("status",), stderr=stderr, services=services) == 1
    assert calls == ["npx"]
    assert "npx is required to run skills commands" in stderr.getvalue()


@pytest.mark.parametrize(
    ("action", "target", "flag"),
    [
        ("add", "other/repo", "-n"),
        ("add", "other/repo", "--no-skills"),
        ("edit", "1", "-n"),
        ("edit", "1", "--no-skills"),
    ],
)
def test_source_no_skills_bypasses_npx_preflight(
    tmp_path: Path,
    action: str,
    target: str,
    flag: str,
) -> None:
    calls: list[str] = []
    paths = ConfigPaths.for_config_dir(tmp_path / "config")
    services = Services(
        has_command=lambda *_args, **_kwargs: pytest.fail("npx checked"),
        initialize_config=lambda **_: calls.append("config") or paths,
        read_config=lambda _paths: calls.append("read") or catalog(),
        execute_install_plan=lambda *_args, **_kwargs: pytest.fail(
            "install stub reached"
        ),
        execute_uninstall_plan=lambda *_args, **_kwargs: pytest.fail(
            "uninstall stub reached"
        ),
    )

    assert run_cli(
        ("source", action, target, flag),
        cwd=tmp_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        services=services,
    ) == 1
    assert calls == ["config", "read"]


def test_configuration_creation_failure_is_normalized() -> None:
    services = Services(
        has_command=lambda *_args, **_kwargs: True,
        initialize_config=lambda **_: (_ for _ in ()).throw(
            OSError("disk unavailable")
        ),
    )
    stderr = io.StringIO()

    assert run_cli(("status",), stderr=stderr, services=services) == 1
    assert "Could not create config directory." in stderr.getvalue()
    assert "disk unavailable" not in stderr.getvalue()


def test_configuration_read_failure_is_rendered_once(tmp_path: Path) -> None:
    paths = ConfigPaths.for_config_dir(tmp_path / "config")
    services = Services(
        has_command=lambda *_args, **_kwargs: True,
        initialize_config=lambda **_: paths,
        read_config=lambda _paths: (_ for _ in ()).throw(
            ValueError("invalid sources")
        ),
    )
    stderr = io.StringIO()

    assert run_cli(("status",), stderr=stderr, services=services) == 1
    assert stderr.getvalue().count("invalid sources") == 1


def test_run_cli_returns_an_integer_instead_of_raising_system_exit() -> None:
    result = run_cli(("wat",), stdout=io.StringIO(), stderr=io.StringIO())

    assert result == 1
    assert type(result) is int
