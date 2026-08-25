import unittest

from grid import Layout, format_grid, init_layout


class InitLayoutTests(unittest.TestCase):
    def test_sizes_columns_from_longest_item(self):
        layout = init_layout(20, ["aa", "bb", "cc", "dd"])
        self.assertEqual(layout.cell, 2)
        self.assertEqual(layout.cols, 5)

    def test_single_column_when_item_exceeds_width(self):
        layout = init_layout(5, ["toolong"])
        self.assertEqual(layout.cols, 1)
        self.assertEqual(layout.cell, 5)

    def test_at_least_one_column_when_width_below_item(self):
        layout = init_layout(0, ["a"])
        self.assertEqual(layout.cols, 1)
        self.assertEqual(layout.cell, 0)


class FormatGridTests(unittest.TestCase):
    def test_column_major_two_by_two(self):
        lines = format_grid(["a", "b", "c", "d"], init_layout(4, ["a", "b", "c", "d"]))
        # usable 4, maxLen 1 → cols = floor((4+2)/(1+2)) = 2, cell = 1
        # column-major: col0=a,b  col1=c,d
        self.assertEqual(lines[0].strip(), "a  c")
        self.assertEqual(lines[1].strip(), "b  d")

    def test_column_major_explicit_layout(self):
        lines = format_grid(["a", "b", "c", "d"], Layout(cols=2, cell=1))
        self.assertEqual([line.strip() for line in lines], ["a  c", "b  d"])

    def test_empty_items_yield_no_lines(self):
        self.assertEqual(format_grid([], Layout(cols=2, cell=1)), [])

    def test_zero_cols_yield_no_lines(self):
        self.assertEqual(format_grid(["a"], Layout(cols=0, cell=1)), [])

    def test_truncates_to_cell_width(self):
        lines = format_grid(["toolong"], Layout(cols=1, cell=5))
        self.assertEqual(lines, ["  toolo"])

    def test_pads_cells_and_prefixes_two_spaces(self):
        lines = format_grid(["a", "bb"], Layout(cols=2, cell=2))
        self.assertEqual(lines, ["  a   bb"])
