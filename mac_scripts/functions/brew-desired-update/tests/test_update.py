import io
import re
import subprocess
import unittest

from brew import format_brew_command
from messages import fmt
from ui import Ui
from update import collect_casks_to_upgrade, filter_excluded, run_update

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _doc(**overrides):
    doc = {"version": 1, "formulas": [], "casks": [], "taps": []}
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
    mapping = {}
    formulas = formulas or []
    casks = casks or []
    taps = taps or []
    mapping[("list", "--formula")] = (0, "".join(f"{n}\n" for n in formulas))
    mapping[("list", "--cask", "--full-name")] = (0, "".join(f"{n}\n" for n in casks))
    mapping[("tap",)] = (0, "".join(f"{n}\n" for n in taps))
    return mapping


def _run(exclude, document, mapping=None, runner=None):
    out = io.StringIO()
    err = io.StringIO()
    ui = Ui(stdout=out, stderr=err)
    runner = runner or FakeRunner(mapping or {})
    code = run_update(exclude, document, runner, ui)
    return code, runner, strip_ansi(out.getvalue()), err.getvalue()


def _first_index(calls, prefix):
    for i, call in enumerate(calls):
        if call[: len(prefix)] == list(prefix):
            return i
    raise AssertionError(f"missing call prefix {prefix!r} in {calls!r}")


class CollectCasksToUpgradeTests(unittest.TestCase):
    def test_keeps_installed_desired_casks_in_desired_order(self):
        self.assertEqual(
            collect_casks_to_upgrade(
                ["cursor", "slack", "missing"],
                ["cursor", "slack", "extra"],
            ),
            ["cursor", "slack"],
        )


class FilterExcludedTests(unittest.TestCase):
    def test_removes_excluded_names_preserving_order(self):
        self.assertEqual(
            filter_excluded(["cursor", "slack", "postman"], ["slack"]),
            ["cursor", "postman"],
        )


class RunUpdatePipelineTests(unittest.TestCase):
    def test_seven_step_order_excludes_casks_and_returns_zero(self):
        mapping = {
            **_state_map(casks=["cursor", "slack"]),
            ("update",): (0, ""),
            ("upgrade", "--formula", "-y"): (0, ""),
            ("tap", "--repair"): (0, ""),
            ("upgrade", "--cask", "-y", "cursor"): (0, ""),
            ("cleanup", "--prune=1"): (0, ""),
        }
        code, runner, _, err = _run(
            ["slack"],
            _doc(casks=["cursor", "slack"]),
            mapping,
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        calls = runner.calls
        i_update = _first_index(calls, ["update"])
        i_formula = _first_index(calls, ["upgrade", "--formula", "-y"])
        i_repair = _first_index(calls, ["tap", "--repair"])
        i_cask = _first_index(calls, ["upgrade", "--cask", "-y", "cursor"])
        i_cleanup = _first_index(calls, ["cleanup", "--prune=1"])
        self.assertLess(i_update, i_formula)
        self.assertLess(i_formula, i_repair)
        self.assertLess(i_repair, i_cask)
        self.assertLess(i_cask, i_cleanup)
        self.assertTrue(
            any(c[:3] == ["upgrade", "--cask", "-y"] and "slack" not in c for c in calls)
        )
        list_calls = [c for c in calls if c[0] == "list" or c == ["tap"]]
        self.assertGreaterEqual(len(list_calls), 6)

    def test_ensures_missing_desired_taps_before_update(self):
        mapping = {
            **_state_map(taps=[]),
            ("trust", "--help"): (1, ""),
            ("tap", "mongodb/brew"): (0, ""),
        }
        code, runner, _, err = _run(
            [],
            _doc(taps=["mongodb/brew"]),
            mapping,
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        i_tap = _first_index(runner.calls, ["tap", "mongodb/brew"])
        i_update = _first_index(runner.calls, ["update"])
        self.assertLess(i_tap, i_update)

    def test_skips_ensure_when_taps_already_present(self):
        mapping = _state_map(taps=["a/b"])
        code, runner, plain, _ = _run([], _doc(taps=["a/b"]), mapping)
        self.assertEqual(code, 0)
        self.assertNotIn(["tap", "a/b"], runner.calls)
        self.assertIn("Ensured taps · all present", plain)
        self.assertNotIn("Ensuring taps · all present", plain)

    def test_logs_pipeline_counts_and_brew_actions(self):
        mapping = {
            **_state_map(formulas=["bat"], casks=["cursor", "slack"], taps=[]),
            ("trust", "--help"): (1, ""),
            ("tap", "mongodb/brew"): (0, ""),
            ("upgrade", "--cask", "-y", "cursor"): (0, ""),
        }
        code, _, plain, err = _run(
            ["slack"],
            _doc(
                formulas=["bat"],
                casks=["cursor", "slack"],
                taps=["mongodb/brew"],
            ),
            mapping,
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertNotIn("BREW DESIRED UPDATE", plain)
        self.assertIn("Desired lists: 1 formulae · 1 taps · 2 casks", plain)
        self.assertIn("Loading Homebrew state", plain)
        self.assertIn("Loaded Homebrew state", plain)
        self.assertIn("Ensuring taps · 1 missing: mongodb/brew", plain)
        self.assertIn("Ensured taps · 1 missing: mongodb/brew", plain)
        self.assertIn("Updating Homebrew", plain)
        self.assertIn("Updated Homebrew", plain)
        self.assertNotIn("Updating Homebrew (brew update)", plain)
        self.assertIn("Upgrading formulae", plain)
        self.assertIn("Upgraded formulae", plain)
        self.assertIn("Repairing taps", plain)
        self.assertIn("Reloading Homebrew state", plain)
        self.assertIn("Reloaded Homebrew state", plain)
        self.assertIn("Upgrading casks · 1 of 2 eligible", plain)
        self.assertIn("Upgraded casks · 1 of 2 eligible", plain)
        self.assertNotIn("eligible: cursor", plain)
        self.assertIn(fmt("exclude.info", n=1, names="slack"), plain)
        self.assertIn("Cleaning up Homebrew", plain)
        self.assertIn("Cleaned up Homebrew", plain)
        self.assertNotIn("Cleanup (brew cleanup --prune=1)", plain)

    def test_load_brew_state_logs_through_passed_ui(self):
        mapping = _state_map()
        code, runner, plain, _ = _run([], _doc(), mapping)
        self.assertEqual(code, 0)
        self.assertIn("$ brew list --formula", plain)
        self.assertIn("$ brew list --cask --full-name", plain)
        self.assertIn("$ brew tap\n", plain + "\n")

    def test_all_eligible_casks_excluded_skips_cask_upgrade(self):
        mapping = _state_map(casks=["cursor"])
        code, runner, plain, _ = _run(
            ["cursor"],
            _doc(casks=["cursor"]),
            mapping,
        )
        self.assertEqual(code, 0)
        self.assertFalse(any(c[:3] == ["upgrade", "--cask", "-y"] for c in runner.calls))
        self.assertIn(fmt("casks.all_excluded"), plain)

    def test_no_installed_desired_casks_skips_cask_upgrade(self):
        mapping = _state_map(casks=["other"])
        code, runner, plain, _ = _run([], _doc(casks=["cursor"]), mapping)
        self.assertEqual(code, 0)
        self.assertFalse(any(c[:3] == ["upgrade", "--cask", "-y"] for c in runner.calls))
        self.assertIn(fmt("casks.none_installed"), plain)

    def test_exclude_list_truncates_after_eight_names(self):
        names = [f"cask{i}" for i in range(9)]
        mapping = _state_map(casks=names)
        code, _, plain, _ = _run(names, _doc(casks=names), mapping)
        self.assertEqual(code, 0)
        self.assertIn(
            fmt(
                "exclude.info",
                n=9,
                names="cask0 cask1 cask2 cask3 cask4 cask5 cask6 cask7 … (+1 more)",
            ),
            plain,
        )


class RunUpdateFailureTests(unittest.TestCase):
    def test_failed_tap_returns_one(self):
        mapping = {
            **_state_map(),
            ("trust", "--help"): (1, ""),
            ("tap", "mongodb/brew"): (1, ""),
        }
        code, runner, _, err = _run([], _doc(taps=["mongodb/brew"]), mapping)
        self.assertEqual(code, 1)
        self.assertIn(fmt("tap.failed", name="mongodb/brew"), err)
        self.assertNotIn(["update"], runner.calls)

    def test_brew_update_failure_returns_one(self):
        mapping = {**_state_map(), ("update",): (1, "")}
        code, runner, _, err = _run([], _doc(), mapping)
        self.assertEqual(code, 1)
        self.assertIn(fmt("brew.update.failed"), err)
        self.assertNotIn(["upgrade", "--formula", "-y"], runner.calls)

    def test_formula_upgrade_failure_returns_one(self):
        mapping = {**_state_map(), ("upgrade", "--formula", "-y"): (1, "")}
        code, runner, _, err = _run([], _doc(), mapping)
        self.assertEqual(code, 1)
        self.assertIn(fmt("brew.upgrade.formula.failed"), err)
        self.assertNotIn(["tap", "--repair"], runner.calls)

    def test_tap_repair_failure_returns_one(self):
        mapping = {**_state_map(), ("tap", "--repair"): (1, "")}
        code, runner, _, err = _run([], _doc(), mapping)
        self.assertEqual(code, 1)
        self.assertIn(fmt("brew.tap.repair.failed"), err)
        self.assertNotIn(["cleanup", "--prune=1"], runner.calls)

    def test_cask_upgrade_failure_returns_one(self):
        mapping = {
            **_state_map(casks=["cursor"]),
            ("upgrade", "--cask", "-y", "cursor"): (1, ""),
        }
        code, runner, _, err = _run([], _doc(casks=["cursor"]), mapping)
        self.assertEqual(code, 1)
        self.assertIn(fmt("brew.upgrade.cask.failed"), err)
        self.assertNotIn(["cleanup", "--prune=1"], runner.calls)

    def test_cleanup_failure_returns_one(self):
        mapping = {**_state_map(), ("cleanup", "--prune=1"): (1, "")}
        code, _, _, err = _run([], _doc(), mapping)
        self.assertEqual(code, 1)
        self.assertIn(fmt("brew.cleanup.failed"), err)


if __name__ == "__main__":
    unittest.main()
