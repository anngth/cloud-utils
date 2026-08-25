import io
import re
import subprocess
import unittest
from unittest.mock import patch

from brew import BrewRunner
from detect import detect_brew_type
from manage import run_add, run_remove
from messages import fmt
from ui import Ui

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _empty_doc(**overrides):
    doc = {"version": 1, "formulas": [], "casks": [], "taps": []}
    doc.update(overrides)
    return doc


class FakeRunner:
    def __init__(self, mapping=None, default_code=1):
        self.calls = []
        self.mapping = mapping or {}
        self.default_code = default_code

    def run(self, args, ui):
        self.calls.append(list(args))
        ui.command("$ brew " + " ".join(args))
        key = tuple(args)
        if key in self.mapping:
            code, stdout = self.mapping[key]
            return subprocess.CompletedProcess(
                args=args, returncode=code, stdout=stdout, stderr=""
            )
        return subprocess.CompletedProcess(
            args=args, returncode=self.default_code, stdout="", stderr=""
        )


def _info_map(name, formula=None, cask=None):
    mapping = {}
    if formula is None:
        mapping[("info", "--formula", name)] = (1, "")
    else:
        mapping[("info", "--formula", name)] = (0, f"==> {formula}: desc\n")
    if cask is None:
        mapping[("info", "--cask", name)] = (1, "")
    else:
        mapping[("info", "--cask", name)] = (0, f"==> {cask}: desc\n")
    return mapping


class DetectBrewTypeTests(unittest.TestCase):
    def _detect(self, name, mapping):
        out = io.StringIO()
        err = io.StringIO()
        ui = Ui(stdout=out, stderr=err)
        result = detect_brew_type(name, FakeRunner(mapping), ui)
        return result, strip_ansi(out.getvalue()), err.getvalue()

    def test_returns_formula_when_only_formula_matches(self):
        result, plain, err = self._detect("bat", _info_map("bat", formula="bat"))
        self.assertEqual(result, "formula")
        self.assertEqual(err, "")
        self.assertNotIn("exists as both", plain)
        self.assertNotIn("not found", plain)

    def test_returns_cask_when_only_cask_matches(self):
        result, _, err = self._detect("cursor", _info_map("cursor", cask="cursor"))
        self.assertEqual(result, "cask")
        self.assertEqual(err, "")

    def test_returns_none_and_warns_when_both_match(self):
        result, plain, err = self._detect(
            "wget", _info_map("wget", formula="wget", cask="wget")
        )
        self.assertIsNone(result)
        self.assertEqual(err, "")
        self.assertIn(fmt("dual.type", name="wget"), plain)
        self.assertIn(fmt("dual.hint", name="wget"), plain)

    def test_returns_none_and_warns_when_missing(self):
        result, plain, err = self._detect("nope", _info_map("nope"))
        self.assertIsNone(result)
        self.assertEqual(err, "")
        self.assertIn(fmt("not.found.brew", name="nope"), plain)
        self.assertIn(fmt("not.found.brew.hint", name="nope"), plain)

    def test_matches_tap_prefixed_formula_token_by_short_name(self):
        result, _, _ = self._detect(
            "acli", _info_map("acli", formula="atlassian/acli/acli")
        )
        self.assertEqual(result, "formula")


class RunAddTests(unittest.TestCase):
    def _add(self, names, force_type, document, mapping=None, runner=None):
        out = io.StringIO()
        err = io.StringIO()
        ui = Ui(stdout=out, stderr=err)
        saved = []
        code = run_add(
            names,
            force_type,
            document,
            runner or FakeRunner(mapping or {}),
            ui,
            saved.append,
        )
        return code, saved, strip_ansi(out.getvalue()), err.getvalue()

    def test_auto_formula_saves_and_returns_zero(self):
        doc = _empty_doc()
        code, saved, plain, err = self._add(
            ["bat"], None, doc, _info_map("bat", formula="bat")
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["formulas"], ["bat"])
        self.assertEqual(doc["formulas"], ["bat"])
        self.assertIn(fmt("added.item", name="bat", type="formula", n=1), plain)

    def test_tap_formula_spec_adds_tap_and_short_name(self):
        doc = _empty_doc()
        code, saved, plain, err = self._add(
            ["atlassian/acli/acli"], None, doc, mapping={}
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(saved[0]["formulas"], ["acli"])
        self.assertEqual(saved[0]["taps"], ["atlassian/acli"])
        self.assertIn(
            fmt("added.tap", name="atlassian/acli", n=1),
            plain,
        )
        self.assertIn(fmt("added.item", name="acli", type="formula", n=1), plain)

    def test_two_segment_name_adds_tap(self):
        doc = _empty_doc()
        code, saved, _, err = self._add(["mongodb/brew"], None, doc, mapping={})
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(saved[0]["taps"], ["mongodb/brew"])
        self.assertEqual(saved[0]["formulas"], [])

    def test_force_tap_rejects_three_segment_name(self):
        doc = _empty_doc()
        code, saved, plain, _ = self._add(
            ["atlassian/acli/acli"], "tap", doc, mapping={}
        )
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        self.assertEqual(doc["taps"], [])
        self.assertIn(fmt("tap.name.invalid"), plain)

    def test_duplicate_fails_without_save(self):
        doc = _empty_doc(formulas=["bat"])
        code, saved, plain, _ = self._add(
            ["bat"], None, doc, _info_map("bat", formula="bat")
        )
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        self.assertEqual(doc["formulas"], ["bat"])
        self.assertIn(fmt("already.in.list", name="bat", type="formula"), plain)
        self.assertNotIn("Added '", plain)

    def test_dual_type_fails_without_save(self):
        doc = _empty_doc()
        code, saved, plain, _ = self._add(
            ["wget"], None, doc, _info_map("wget", formula="wget", cask="wget")
        )
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        self.assertEqual(doc["formulas"], [])
        self.assertEqual(doc["casks"], [])
        self.assertIn(fmt("dual.type", name="wget"), plain)

    def test_force_formula_validates_once_via_info(self):
        runner = FakeRunner(_info_map("bat", formula="bat"))
        doc = _empty_doc()
        code, saved, _, _ = self._add(["bat"], "formula", doc, runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["formulas"], ["bat"])
        info_calls = [c for c in runner.calls if c[:2] == ["info", "--formula"]]
        self.assertEqual(info_calls, [["info", "--formula", "bat"]])

    def test_force_cask_warns_when_info_fails(self):
        doc = _empty_doc()
        code, saved, plain, _ = self._add(
            ["nope"], "cask", doc, {("info", "--cask", "nope"): (1, "")}
        )
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        self.assertIn(fmt("not.found.type", name="nope", type="cask"), plain)

    def test_formula_token_with_slash_adds_tap(self):
        doc = _empty_doc()
        code, saved, _, _ = self._add(
            ["acli"],
            None,
            doc,
            _info_map("acli", formula="atlassian/acli/acli"),
        )
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["formulas"], ["acli"])
        self.assertEqual(saved[0]["taps"], ["atlassian/acli"])

    def test_one_step_active_pair_per_name(self):
        mapping = {
            **_info_map("bat", formula="bat"),
            **_info_map("gh", formula="gh"),
        }
        doc = _empty_doc()
        _, _, plain, _ = self._add(["bat", "gh"], None, doc, mapping)
        self.assertIn("Adding bat", plain)
        self.assertIn("Adding gh", plain)
        self.assertIn(fmt("added.item", name="bat", type="formula", n=1), plain)
        self.assertIn(fmt("added.item", name="gh", type="formula", n=2), plain)


class RunRemoveTests(unittest.TestCase):
    def _remove(self, names, document):
        out = io.StringIO()
        err = io.StringIO()
        ui = Ui(stdout=out, stderr=err)
        saved = []
        code = run_remove(names, document, ui, saved.append)
        return code, saved, strip_ansi(out.getvalue()), err.getvalue()

    def test_removes_cask_and_saves(self):
        doc = _empty_doc(casks=["cursor", "slack"])
        code, saved, plain, err = self._remove(["cursor"], doc)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(saved[0]["casks"], ["slack"])
        self.assertEqual(doc["casks"], ["slack"])
        self.assertIn(fmt("removed.item", name="cursor", type="cask", n=1), plain)

    def test_removes_formula_then_tap(self):
        doc = _empty_doc(formulas=["bat"], taps=["a/b"])
        code, saved, _, _ = self._remove(["bat"], doc)
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["formulas"], [])
        code, saved, _, _ = self._remove(["a/b"], doc)
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["taps"], [])

    def test_missing_name_does_not_save(self):
        doc = _empty_doc()
        code, saved, plain, _ = self._remove(["missing"], doc)
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        self.assertIn(fmt("not.in.list", name="missing"), plain)
        self.assertNotIn("Removed '", plain)
        self.assertIn("Removing missing", plain)


class AutoTapTests(unittest.TestCase):
    def _add(self, names, mapping, force_type="cask"):
        out = io.StringIO()
        err = io.StringIO()
        ui = Ui(stdout=out, stderr=err)
        saved = []
        runner = FakeRunner(mapping)
        doc = _empty_doc()
        code = run_add(names, force_type, doc, runner, ui, saved.append)
        return code, saved, doc, runner, strip_ansi(out.getvalue()), err.getvalue()

    def test_auto_detect_cask_still_auto_taps(self):
        mapping = {
            ("info", "--formula", "phpmon"): (1, ""),
            ("info", "--cask", "phpmon"): (0, "==> phpmon: app\n"),
            ("search", "--cask", "phpmon"): (
                0,
                "nicoverbruggen/homebrew-cask/phpmon\n",
            ),
            ("tap",): (0, "homebrew/core\n"),
            ("trust", "--help"): (1, ""),
            ("tap", "nicoverbruggen/homebrew-cask"): (0, ""),
        }
        code, saved, _, runner, _, _ = self._add(["phpmon"], mapping, force_type=None)
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["casks"], ["phpmon"])
        self.assertIn(["tap", "nicoverbruggen/homebrew-cask"], runner.calls)

    def test_taps_third_party_cask_from_search(self):
        mapping = {
            ("info", "--cask", "phpmon"): (0, "==> phpmon: app\n"),
            ("search", "--cask", "phpmon"): (
                0,
                "phpmon\nnicoverbruggen/homebrew-cask/phpmon\n",
            ),
            ("tap",): (0, "homebrew/core\n"),
            ("trust", "--help"): (1, ""),
            ("tap", "nicoverbruggen/homebrew-cask"): (0, ""),
        }
        code, saved, doc, runner, _, err = self._add(["phpmon"], mapping)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(saved[0]["casks"], ["phpmon"])
        self.assertEqual(doc["taps"], [])
        self.assertIn(["search", "--cask", "phpmon"], runner.calls)
        self.assertIn(["tap", "nicoverbruggen/homebrew-cask"], runner.calls)

    def test_skips_homebrew_cask_official_tap(self):
        mapping = {
            ("info", "--cask", "firefox"): (0, "==> firefox: app\n"),
            ("search", "--cask", "firefox"): (
                0,
                "firefox\nhomebrew/cask/firefox\n",
            ),
        }
        code, saved, _, runner, _, _ = self._add(["firefox"], mapping)
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["casks"], ["firefox"])
        self.assertFalse(any(len(c) == 2 and c[0] == "tap" for c in runner.calls))

    def test_skips_homebrew_cask_fonts(self):
        mapping = {
            ("info", "--cask", "font-hack"): (0, "==> font-hack: font\n"),
            ("search", "--cask", "font-hack"): (
                0,
                "font-hack\nhomebrew/cask-fonts/font-hack\n",
            ),
        }
        code, saved, _, runner, _, _ = self._add(["font-hack"], mapping)
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["casks"], ["font-hack"])
        self.assertFalse(any(len(c) == 2 and c[0] == "tap" for c in runner.calls))

    def test_skips_ensure_when_already_tapped(self):
        mapping = {
            ("info", "--cask", "phpmon"): (0, "==> phpmon: app\n"),
            ("search", "--cask", "phpmon"): (
                0,
                "nicoverbruggen/homebrew-cask/phpmon\n",
            ),
            ("tap",): (0, "nicoverbruggen/homebrew-cask\n"),
        }
        code, saved, _, runner, _, _ = self._add(["phpmon"], mapping)
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["casks"], ["phpmon"])
        self.assertIn(["tap"], runner.calls)
        self.assertNotIn(["tap", "nicoverbruggen/homebrew-cask"], runner.calls)

    def test_failed_tap_does_not_add_cask(self):
        mapping = {
            ("info", "--cask", "phpmon"): (0, "==> phpmon: app\n"),
            ("search", "--cask", "phpmon"): (
                0,
                "nicoverbruggen/homebrew-cask/phpmon\n",
            ),
            ("tap",): (0, "homebrew/core\n"),
            ("trust", "--help"): (1, ""),
            ("tap", "nicoverbruggen/homebrew-cask"): (1, ""),
        }
        code, saved, doc, _, plain, err = self._add(["phpmon"], mapping)
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        self.assertEqual(doc["casks"], [])
        self.assertIn(fmt("tap.failed", name="nicoverbruggen/homebrew-cask"), err + plain)


class BrewRunnerSearchProbeTests(unittest.TestCase):
    @patch("brew.subprocess.run")
    def test_auto_tap_reads_search_stdout_from_brew_runner(self, mock_run):
        def fake_run(command, **kwargs):
            args = command[1:]
            if args[:2] == ["search", "--cask"]:
                self.assertTrue(kwargs.get("capture_output"))
                return subprocess.CompletedProcess(
                    command, 0, "phpmon\nuser/repo/phpmon\n", ""
                )
            if args == ["tap"]:
                return subprocess.CompletedProcess(command, 0, "homebrew/core\n", "")
            if args == ["info", "--cask", "phpmon"]:
                return subprocess.CompletedProcess(command, 0, "==> phpmon: app\n", "")
            if args == ["trust", "--help"]:
                return subprocess.CompletedProcess(command, 1, "", "")
            if args == ["tap", "user/repo"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "")

        mock_run.side_effect = fake_run
        out = io.StringIO()
        ui = Ui(stdout=out, stderr=io.StringIO())
        saved = []
        doc = _empty_doc()
        code = run_add(
            ["phpmon"], "cask", doc, BrewRunner("/brew"), ui, saved.append
        )
        self.assertEqual(code, 0)
        self.assertEqual(saved[0]["casks"], ["phpmon"])
        tap_cmds = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(["/brew", "tap", "user/repo"], tap_cmds)


if __name__ == "__main__":
    unittest.main()
