from __future__ import annotations

import io
import os
from pathlib import Path
from contextlib import redirect_stderr
import sys

import pytest

from shared.selector import SelectorResult, create_selector_state
from prompt_toolkit.input.vt100 import Vt100Input
from skills_manager.cli import Services, run_cli
from skills_manager.config import Catalog, CatalogSource, ConfigPaths
from skills_manager.state import InstalledSkill


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class FlushStringIO(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


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


def test_no_arguments_without_isatty_render_noninteractive_error() -> None:
    services = Services(
        initialize_config=lambda **_: pytest.fail("config touched"),
        has_command=lambda *_args, **_kwargs: pytest.fail("npx checked"),
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    assert run_cli(
        (),
        stdin=object(),  # type: ignore[arg-type]
        stdout=stdout,
        stderr=stderr,
        services=services,
    ) == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "\x1b[31m❌ skm requires an interactive terminal; use skm add, "
        "skm remove, or skm add --all\x1b[39m\n"
    )


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


def selector_services(tmp_path: Path, runner) -> Services:
    services = configured_services(tmp_path)
    services.load_installed_state = lambda **_: {}
    services.selector_runner = runner
    return services


def test_selector_suppresses_only_prompt_toolkit_pipe_warning(
    tmp_path: Path,
) -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"q")
    os.close(write_fd)
    stdin = os.fdopen(read_fd, encoding="utf-8")
    stderr = io.StringIO()
    leaked = io.StringIO()
    Vt100Input._fds_not_a_terminal.discard(read_fd)
    try:
        with redirect_stderr(leaked):
            assert run_cli(
                ("add", "1"), cwd=tmp_path, stdin=stdin,
                stdout=io.StringIO(), stderr=stderr,
                services=selector_services(tmp_path, Services().selector_runner),
            ) == 0
    finally:
        stdin.close()
        Vt100Input._fds_not_a_terminal.discard(read_fd)

    assert "Warning: Input is not a terminal" not in stderr.getvalue()
    assert leaked.getvalue() == ""


def test_selector_forwards_unknown_stderr_around_split_warning(
    tmp_path: Path,
) -> None:
    def runner(*_args, **_kwargs):
        sys.stderr.write("before")
        sys.stderr.flush()
        sys.stderr.write("\nWarning: Input is not")
        sys.stderr.flush()
        sys.stderr.write(" a terminal (fd=42).\nafter\n")
        return SelectorResult("cancel", create_selector_state(()), ())

    stderr = FlushStringIO()
    assert run_cli(
        ("add", "1"), cwd=tmp_path, stdin=io.StringIO("q"),
        stdout=io.StringIO(), stderr=stderr,
        services=selector_services(tmp_path, runner),
    ) == 0

    assert stderr.getvalue() == "before\nafter\n"
    assert stderr.flushes == 1


def test_selector_exception_forwards_stderr_and_restores_nested_global(
    tmp_path: Path,
) -> None:
    def runner(*_args, **_kwargs):
        sys.stderr.write("selector diagnostic")
        sys.stderr.flush()
        raise ValueError("selector broke")

    process_stderr = io.StringIO()
    outer_stderr = io.StringIO()
    original_stderr = sys.stderr
    with redirect_stderr(outer_stderr):
        assert run_cli(
            ("add", "1"), cwd=tmp_path, stdin=io.StringIO("q"),
            stdout=io.StringIO(), stderr=process_stderr,
            services=selector_services(tmp_path, runner),
        ) == 1
        assert sys.stderr is outer_stderr

    assert sys.stderr is original_stderr
    assert outer_stderr.getvalue() == ""
    assert process_stderr.getvalue().startswith("selector diagnostic")
    assert "selector broke" in process_stderr.getvalue()
