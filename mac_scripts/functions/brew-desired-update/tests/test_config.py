import json
import os
import tempfile
import unittest
from unittest.mock import patch

from config import BudPaths, load_desired, normalize, resolve_paths, save_desired
from messages import fmt


def _valid_doc(**overrides):
    doc = {
        "version": 1,
        "formulas": ["gh"],
        "casks": ["cursor"],
        "taps": ["mongodb/brew"],
    }
    doc.update(overrides)
    return doc


class ResolvePathsTests(unittest.TestCase):
    def test_uses_cloud_utils_config_dir(self):
        paths = resolve_paths(
            {"CLOUD_UTILS_CONFIG_DIR": "/tmp/cu", "HOME": "/Users/me"}
        )
        self.assertEqual(paths.config_dir, "/tmp/cu")
        self.assertEqual(paths.desired_file, "/tmp/cu/bud/desired.json")

    def test_defaults_to_icloud_backups(self):
        paths = resolve_paths({"HOME": "/Users/me"})
        self.assertEqual(
            paths.config_dir,
            "/Users/me/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
        )
        self.assertEqual(
            paths.desired_file,
            "/Users/me/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils/bud/desired.json",
        )

    def test_example_file_is_package_template(self):
        import config

        paths = resolve_paths({"HOME": "/Users/me"})
        self.assertEqual(
            paths.example_file,
            os.path.join(os.path.dirname(config.__file__), "desired.example.json"),
        )


class NormalizeTests(unittest.TestCase):
    def test_unique_sorted_lists(self):
        doc = normalize(
            {
                "version": 1,
                "formulas": ["gh", "bat", "bat"],
                "casks": ["zoom", "cursor"],
                "taps": ["homebrew/cask", "mongodb/brew"],
            }
        )
        self.assertEqual(doc["version"], 1)
        self.assertEqual(doc["formulas"], ["bat", "gh"])
        self.assertEqual(doc["casks"], ["cursor", "zoom"])
        self.assertEqual(doc["taps"], ["homebrew/cask", "mongodb/brew"])


class SaveDesiredTests(unittest.TestCase):
    def test_writes_normalized_json_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bud", "desired.json")
            save_desired(
                path,
                {
                    "version": 1,
                    "formulas": ["gh", "bat", "bat"],
                    "casks": ["cursor"],
                    "taps": ["mongodb/brew"],
                },
            )
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
            self.assertEqual(
                json.loads(raw),
                {
                    "version": 1,
                    "formulas": ["bat", "gh"],
                    "casks": ["cursor"],
                    "taps": ["mongodb/brew"],
                },
            )
            self.assertTrue(raw.endswith("\n"))
            leftovers = [
                name
                for name in os.listdir(os.path.dirname(path))
                if name.endswith(".tmp") or name.startswith(".desired.")
            ]
            self.assertEqual(leftovers, [])

    def test_raises_when_parent_cannot_be_created(self):
        with tempfile.TemporaryDirectory() as root:
            blocker = os.path.join(root, "bud")
            with open(blocker, "w", encoding="utf-8") as handle:
                handle.write("not a directory\n")
            with self.assertRaises(OSError) as ctx:
                save_desired(os.path.join(blocker, "desired.json"), _valid_doc())
            self.assertEqual(str(ctx.exception), fmt("config.dir"))


class LoadDesiredTests(unittest.TestCase):
    def test_returns_existing_document(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bud", "desired.json")
            os.makedirs(os.path.dirname(path))
            existing = _valid_doc(formulas=["eza", "bat"])
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(existing, handle)
            doc = load_desired({"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"})
            self.assertEqual(doc, existing)

    def test_bootstraps_from_example_when_missing(self):
        with tempfile.TemporaryDirectory() as root:
            doc = load_desired({"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"})
            self.assertEqual(
                doc,
                {
                    "version": 1,
                    "formulas": ["bat", "eza", "gh"],
                    "casks": ["cursor"],
                    "taps": ["mongodb/brew"],
                },
            )
            desired = os.path.join(root, "bud", "desired.json")
            self.assertTrue(os.path.exists(desired))
            with open(desired, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), doc)

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bud", "desired.json")
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{")
            with self.assertRaises(ValueError) as ctx:
                load_desired({"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"})
            self.assertEqual(
                str(ctx.exception), fmt("desired.invalid", file=path)
            )
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{")

    def test_invalid_shape_raises_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bud", "desired.json")
            os.makedirs(os.path.dirname(path))
            raw = '{"version":1}\n'
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(raw)
            with self.assertRaises(ValueError) as ctx:
                load_desired({"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"})
            self.assertEqual(
                str(ctx.exception), fmt("desired.invalid", file=path)
            )
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), raw)

    def test_json_null_array_and_zero_raise_and_preserve_file(self):
        for raw in ("null", "[]", "0"):
            with self.subTest(raw=raw):
                with tempfile.TemporaryDirectory() as root:
                    path = os.path.join(root, "bud", "desired.json")
                    os.makedirs(os.path.dirname(path))
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(raw)
                    with self.assertRaises(ValueError) as ctx:
                        load_desired(
                            {"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"}
                        )
                    self.assertEqual(
                        str(ctx.exception), fmt("desired.invalid", file=path)
                    )
                    with open(path, encoding="utf-8") as handle:
                        self.assertEqual(handle.read(), raw)

    def test_rejects_empty_string_items(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bud", "desired.json")
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(_valid_doc(formulas=[""]), handle)
            with self.assertRaises(ValueError) as ctx:
                load_desired({"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"})
            self.assertEqual(
                str(ctx.exception), fmt("desired.invalid", file=path)
            )

    def test_seeds_empty_taps_from_brew_taps_fn(self):
        with tempfile.TemporaryDirectory() as root:
            example = os.path.join(root, "empty-example.json")
            desired = os.path.join(root, "bud", "desired.json")
            with open(example, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "version": 1,
                        "formulas": ["bat"],
                        "casks": [],
                        "taps": [],
                    },
                    handle,
                )
            paths = BudPaths(
                config_dir=root, desired_file=desired, example_file=example
            )
            with patch("config.resolve_paths", return_value=paths):
                doc = load_desired(
                    {"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"},
                    brew_taps_fn=lambda: ["homebrew/core"],
                )
            self.assertEqual(doc["taps"], ["homebrew/core"])
            self.assertEqual(doc["formulas"], ["bat"])
            with open(desired, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["taps"], ["homebrew/core"])

    def test_does_not_seed_taps_when_file_already_exists(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bud", "desired.json")
            os.makedirs(os.path.dirname(path))
            existing = _valid_doc(taps=[])
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(existing, handle)
            called = []

            def brew_taps_fn():
                called.append(True)
                return ["homebrew/core"]

            doc = load_desired(
                {"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"},
                brew_taps_fn=brew_taps_fn,
            )
            self.assertEqual(doc["taps"], [])
            self.assertEqual(called, [])

    def test_missing_example_raises(self):
        with tempfile.TemporaryDirectory() as root:
            desired = os.path.join(root, "bud", "desired.json")
            missing_example = os.path.join(root, "no-such-example.json")
            paths = BudPaths(
                config_dir=root,
                desired_file=desired,
                example_file=missing_example,
            )
            with patch("config.resolve_paths", return_value=paths):
                with self.assertRaises(FileNotFoundError) as ctx:
                    load_desired({"CLOUD_UTILS_CONFIG_DIR": root, "HOME": "/x"})
            self.assertEqual(
                str(ctx.exception),
                fmt("desired.example.missing", file=missing_example),
            )
            self.assertFalse(os.path.exists(desired))
