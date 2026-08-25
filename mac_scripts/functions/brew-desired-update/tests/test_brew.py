import io
import re
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)

from brew import (
    BrewRunner,
    ensure_tap,
    format_brew_command,
    is_probe,
    load_brew_state,
    resolve_brew_binary,
)
from ui import Ui


class ResolveBrewBinaryTests(unittest.TestCase):
    def test_prefers_path_then_opt_homebrew(self):
        executable = {
            "/custom/bin/brew",
            "/opt/homebrew/bin/brew",
            "/usr/local/bin/brew",
        }

        def isfile(path):
            return path in executable

        def access(path, mode):
            return path in executable

        with patch("brew.os.path.isfile", isfile), patch("brew.os.access", access):
            self.assertEqual(
                resolve_brew_binary({"PATH": "/custom/bin"}),
                "/custom/bin/brew",
            )
            self.assertEqual(
                resolve_brew_binary({"PATH": "/missing"}),
                "/opt/homebrew/bin/brew",
            )
            executable.discard("/opt/homebrew/bin/brew")
            self.assertEqual(
                resolve_brew_binary({"PATH": "/missing"}),
                "/usr/local/bin/brew",
            )
            executable.discard("/usr/local/bin/brew")
            self.assertIsNone(resolve_brew_binary({"PATH": "/missing"}))


class FormatBrewCommandTests(unittest.TestCase):
    def test_quotes_args_with_spaces(self):
        self.assertEqual(format_brew_command(["update"]), "$ brew update")
        self.assertEqual(
            format_brew_command(["upgrade", "--cask", "-y", "my cask"]),
            "$ brew upgrade --cask -y 'my cask'",
        )


class IsProbeTests(unittest.TestCase):
    def test_classifies_probe_commands(self):
        self.assertTrue(is_probe(["list", "--formula"]))
        self.assertTrue(is_probe(["info", "--cask", "x"]))
        self.assertTrue(is_probe(["tap"]))
        self.assertTrue(is_probe(["trust", "--help"]))
        self.assertTrue(is_probe(["--help"]))
        self.assertFalse(is_probe(["tap", "mongodb/brew"]))
        self.assertFalse(is_probe(["tap", "--repair"]))
        self.assertFalse(is_probe(["trust", "--tap", "mongodb/brew"]))
        self.assertFalse(is_probe(["upgrade", "--cask", "-y", "x"]))
        self.assertFalse(is_probe(["update"]))
        self.assertFalse(is_probe(["--version"]))


class BrewRunnerTests(unittest.TestCase):
    @patch("brew.subprocess.run")
    def test_probe_captures_output_and_logs_command(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="bat\n", stderr="warn\n"
        )
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        runner = BrewRunner("/brew")
        result = runner.run(["list", "--formula"], ui)

        mock_run.assert_called_once()
        call = mock_run.call_args
        self.assertEqual(call.args[0], ["/brew", "list", "--formula"])
        self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(call.kwargs["capture_output"])
        self.assertEqual(call.kwargs["text"], True)
        self.assertIn("$ brew list --formula", strip_ansi(buf.getvalue()))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "bat\n")
        self.assertEqual(result.stderr, "warn\n")

    @patch("brew.subprocess.run")
    def test_interactive_inherits_stdio(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=None, stderr=None
        )
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        runner = BrewRunner("/brew")
        result = runner.run(["upgrade", "--formula", "-y"], ui)

        call = mock_run.call_args
        self.assertNotIn("capture_output", call.kwargs)
        self.assertNotIn("stdout", call.kwargs)
        self.assertNotIn("stderr", call.kwargs)
        self.assertIn("$ brew upgrade --formula -y", strip_ansi(buf.getvalue()))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    @patch("brew.subprocess.run")
    def test_version_is_interactive_not_probe(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=None, stderr=None
        )
        runner = BrewRunner("/brew")
        ui = Ui(stdout=io.StringIO())
        runner.run(["--version"], ui)
        self.assertNotIn("capture_output", mock_run.call_args.kwargs)

    @patch("brew.subprocess.run")
    def test_invokes_ui_command_before_spawn(self, mock_run):
        commands = []
        ui = Ui(stdout=io.StringIO())
        ui.command = lambda line: commands.append(line)
        runner = BrewRunner("/nonexistent-brew-bin-for-test")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )
        result = runner.run(["update"], ui)
        self.assertEqual(commands, ["$ brew update"])
        self.assertEqual(result.returncode, 1)

    @patch("brew.subprocess.run")
    def test_probe_does_not_write_brew_output_to_ui_stdout(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="bat\n", stderr=""
        )
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        runner = BrewRunner("/brew")
        result = runner.run(["list", "--formula"], ui)
        self.assertEqual(strip_ansi(buf.getvalue()).strip(), "$ brew list --formula")
        self.assertEqual(result.stdout, "bat\n")


class LoadBrewStateTests(unittest.TestCase):
    def test_strips_tap_prefixes(self):
        class MockRunner:
            def run(self, args, ui):
                if "--formula" in args:
                    return subprocess.CompletedProcess(
                        args=args,
                        returncode=0,
                        stdout="bat\nfoo/bar/jq\n",
                        stderr="",
                    )
                if "--cask" in args:
                    return subprocess.CompletedProcess(
                        args=args,
                        returncode=0,
                        stdout="homebrew/cask/cursor\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="mongodb/brew\n",
                    stderr="",
                )

        state = load_brew_state(MockRunner())
        self.assertEqual(state["formulas"], ["bat", "jq"])
        self.assertEqual(state["casks"], ["cursor"])
        self.assertEqual(state["taps"], ["mongodb/brew"])

    def test_runs_list_commands_in_parallel(self):
        pending = 0
        max_pending = 0
        lock = threading.Lock()

        class TrackingRunner:
            def run(self, args, ui):
                nonlocal pending, max_pending
                with lock:
                    pending += 1
                    max_pending = max(max_pending, pending)
                time.sleep(0.01)
                with lock:
                    pending -= 1
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="", stderr=""
                )

        load_brew_state(TrackingRunner())
        self.assertEqual(max_pending, 3)


class EnsureTapTests(unittest.TestCase):
    def test_runs_trust_when_available_then_tap(self):
        calls = []

        class MockRunner:
            def run(self, args, ui):
                calls.append(args)
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="", stderr=""
                )

        self.assertTrue(ensure_tap("mongodb/brew", MockRunner()))
        self.assertEqual(
            calls,
            [
                ["trust", "--help"],
                ["trust", "--tap", "mongodb/brew"],
                ["tap", "mongodb/brew"],
            ],
        )

    def test_skips_trust_when_unavailable(self):
        calls = []

        class MockRunner:
            def run(self, args, ui):
                calls.append(args)
                if args[0] == "trust" and args[1] == "--help":
                    return subprocess.CompletedProcess(
                        args=args, returncode=1, stdout="", stderr=""
                    )
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="", stderr=""
                )

        self.assertTrue(ensure_tap("mongodb/brew", MockRunner()))
        self.assertEqual(
            calls,
            [
                ["trust", "--help"],
                ["tap", "mongodb/brew"],
            ],
        )
