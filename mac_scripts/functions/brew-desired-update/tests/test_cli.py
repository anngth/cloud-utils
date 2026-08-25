import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from brew import format_brew_command
from messages import fmt
from ui import Ui

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _doc(**overrides):
    doc = {"version": 1, "formulas": ["bat"], "casks": ["cursor"], "taps": []}
    doc.update(overrides)
    return doc


class FakeRunner:
    def __init__(self, mapping=None, default_code=0):
        self.calls = []
        self.mapping = mapping or {}
        self.default_code = default_code

    def run(self, args, ui):
        self.calls.append(list(args))
        ui.command(format_brew_command(args))
        key = tuple(args)
        if key in self.mapping:
            code, stdout = self.mapping[key]
            return subprocess.CompletedProcess(
                args=args, returncode=code, stdout=stdout, stderr=""
            )
        return subprocess.CompletedProcess(
            args=args, returncode=self.default_code, stdout="", stderr=""
        )


def _state_map(formulas=None, casks=None, taps=None):
    formulas = formulas if formulas is not None else ["bat"]
    casks = casks if casks is not None else ["cursor"]
    taps = taps if taps is not None else []
    return {
        ("list", "--formula"): (0, "".join(f"{n}\n" for n in formulas)),
        ("list", "--cask", "--full-name"): (0, "".join(f"{n}\n" for n in casks)),
        ("tap",): (0, "".join(f"{n}\n" for n in taps)),
    }


class CliMainTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.env = {
            "CLOUD_UTILS_CONFIG_DIR": self.root.name,
            "HOME": self.root.name,
            "PATH": "",
        }
        self.desired = os.path.join(self.root.name, "bud", "desired.json")
        os.makedirs(os.path.dirname(self.desired))
        self._write_desired(_doc())

    def _write_desired(self, document):
        with open(self.desired, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
            handle.write("\n")

    def _run(self, argv, **kwargs):
        from cli import main

        out = io.StringIO()
        err = io.StringIO()
        ui = kwargs.pop("ui", None) or Ui(stdout=out, stderr=err)
        defaults = {
            "env": self.env,
            "ui": ui,
            "runner": FakeRunner(_state_map()),
            "columns": 80,
            "resolve_brew": lambda _env: "/opt/homebrew/bin/brew",
        }
        defaults.update(kwargs)
        code = main(argv, **defaults)
        return code, strip_ansi(out.getvalue()), strip_ansi(err.getvalue())

    def test_homebrew_missing_errors_before_banner(self):
        code, out, err = self._run(["help"], resolve_brew=lambda _env: None)
        self.assertEqual(code, 1)
        self.assertIn(fmt("homebrew.missing"), err)
        self.assertNotIn("BREW DESIRED UPDATE", out)
        self.assertNotIn("Usage: bud", out)

    def test_invalid_desired_json_errors_before_banner(self):
        with open(self.desired, "w", encoding="utf-8") as handle:
            handle.write("{not-json")
        code, out, err = self._run(["help"])
        self.assertEqual(code, 1)
        self.assertIn(fmt("desired.invalid", file=self.desired), err)
        self.assertNotIn("BREW DESIRED UPDATE", out)

    def test_help_prints_banner_once_then_usage(self):
        code, out, err = self._run(["help"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out.count("BREW DESIRED UPDATE"), 1)
        self.assertIn("◆ Usage: bud [command]", out)
        self.assertLess(out.index("BREW DESIRED UPDATE"), out.index("Usage: bud"))

    def test_help_flag_aliases(self):
        for argv in (["-h"], ["--help"]):
            code, out, err = self._run(argv)
            self.assertEqual(code, 0, argv)
            self.assertEqual(err, "")
            self.assertIn("◆ Usage: bud [command]", out)

    def test_help_ignores_trailing_tokens(self):
        code, out, err = self._run(["help", "extra"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("◆ Usage: bud [command]", out)

    def test_unknown_command_prints_hint_not_usage(self):
        code, out, err = self._run(["frobnicate"])
        self.assertEqual(code, 1)
        self.assertIn(fmt("unknown.command", token="frobnicate"), err)
        self.assertIn(fmt("unknown.hint"), err)
        self.assertNotIn("Usage: bud", out)
        self.assertIn("BREW DESIRED UPDATE", out)

    def test_exclude_with_subcommand_errors_without_usage(self):
        code, out, err = self._run(["-e", "slack", "list"])
        self.assertEqual(code, 1)
        self.assertIn(fmt("exclude.invalid"), err)
        self.assertNotIn("Usage: bud", out)
        self.assertNotIn("Desired vs installed", out)

    def test_list_extra_args_error(self):
        code, out, err = self._run(["list", "extra"])
        self.assertEqual(code, 1)
        self.assertIn(fmt("list.extra_args"), err)
        self.assertNotIn("Desired vs installed", out)

    def test_add_missing_name_error(self):
        code, _, err = self._run(["add"])
        self.assertEqual(code, 1)
        self.assertIn(fmt("add.missing_name"), err)

    def test_remove_missing_name_error(self):
        code, _, err = self._run(["remove"])
        self.assertEqual(code, 1)
        self.assertIn(fmt("remove.missing_name"), err)

    def test_list_dispatches_and_prints_banner_once(self):
        code, out, err = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out.count("BREW DESIRED UPDATE"), 1)
        self.assertIn("Desired vs installed", out)
        self.assertIn("bat", out)

    def test_ls_alias_dispatches_list(self):
        code, out, err = self._run(["ls"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Desired vs installed", out)

    def test_add_saves_and_prints_changes_saved(self):
        mapping = {
            **_state_map(),
            ("info", "--formula", "ripgrep"): (0, "==> ripgrep: desc\n"),
            ("info", "--cask", "ripgrep"): (1, ""),
        }
        code, out, err = self._run(["add", "ripgrep"], runner=FakeRunner(mapping))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn(fmt("added.item", name="ripgrep", type="formula", n=2), out)
        self.assertIn(fmt("changes.saved", file=self.desired), out)
        with open(self.desired, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertIn("ripgrep", saved["formulas"])

    def test_remove_saves_and_prints_changes_saved(self):
        code, out, err = self._run(["remove", "bat"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn(fmt("removed.item", name="bat", type="formula", n=0), out)
        self.assertIn(fmt("changes.saved", file=self.desired), out)
        with open(self.desired, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertNotIn("bat", saved["formulas"])

    def test_bare_update_dispatches_pipeline(self):
        mapping = {
            **_state_map(casks=["cursor"]),
            ("update",): (0, ""),
            ("upgrade", "--formula", "-y"): (0, ""),
            ("tap", "--repair"): (0, ""),
            ("upgrade", "--cask", "-y", "cursor"): (0, ""),
            ("cleanup", "--prune=1"): (0, ""),
        }
        runner = FakeRunner(mapping)
        code, out, err = self._run([], runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out.count("BREW DESIRED UPDATE"), 1)
        self.assertIn("Cleaned up Homebrew", out)
        self.assertIn(["cleanup", "--prune=1"], runner.calls)

    def test_bare_update_with_exclude(self):
        mapping = {
            **_state_map(casks=["cursor"]),
            ("update",): (0, ""),
            ("upgrade", "--formula", "-y"): (0, ""),
            ("tap", "--repair"): (0, ""),
            ("cleanup", "--prune=1"): (0, ""),
        }
        runner = FakeRunner(mapping)
        code, out, err = self._run(["--exclude", "cursor"], runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn(fmt("exclude.info", n=1, names="cursor"), out)
        self.assertFalse(
            any(c[:3] == ["upgrade", "--cask", "-y"] for c in runner.calls)
        )

    def test_main_defaults_argv_from_sys(self):
        with patch("sys.argv", ["bud", "help"]):
            code, out, err = self._run(None)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("◆ Usage: bud [command]", out)


if __name__ == "__main__":
    unittest.main()
