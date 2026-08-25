import unittest
from parse import parse_argv


class ParseArgvTests(unittest.TestCase):
    def test_bare_update(self):
        r = parse_argv([])
        self.assertIsNone(r.action)
        self.assertEqual(r.exclude, [])

    def test_exclude_anywhere_bare(self):
        r = parse_argv(["--exclude", "slack", "postman"])
        self.assertIsNone(r.action)
        self.assertEqual(r.exclude, ["slack", "postman"])

    def test_exclude_before_subcommand_still_parsed(self):
        r = parse_argv(["-e", "slack", "list"])
        self.assertEqual(r.action, "list")
        self.assertEqual(r.exclude, ["slack"])

    def test_add_with_flags(self):
        r = parse_argv(["add", "--formula", "bat", "gh"])
        self.assertEqual(r.action, "add")
        self.assertEqual(r.force_type, "formula")
        self.assertEqual(r.names, ["bat", "gh"])

    def test_list_extra_args_in_rest(self):
        r = parse_argv(["list", "extra"])
        self.assertEqual(r.action, "list")
        self.assertEqual(r.rest, ["extra"])

    def test_help_subcommand(self):
        r = parse_argv(["help"])
        self.assertEqual(r.action, "help")
