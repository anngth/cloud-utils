import assert from "node:assert/strict";
import test from "node:test";
import {
  partitionLists,
  initGridLayout,
  formatGrid,
  runListCommand,
} from "../list.mjs";

test("partitionLists classifies installed missing extra", () => {
  const p = partitionLists({
    desired: { formulas: ["bat", "missing"], casks: ["cursor"], taps: ["a/b"] },
    installed: { formulas: ["bat", "extra"], casks: ["cursor", "slack"], taps: ["a/b", "c/d"] },
  });
  assert.deepEqual(p.formulas.installed, ["bat"]);
  assert.deepEqual(p.formulas.missing, ["missing"]);
  assert.deepEqual(p.formulas.extra, ["extra"]);
  assert.deepEqual(p.casks.installed, ["cursor"]);
  assert.deepEqual(p.casks.missing, []);
  assert.deepEqual(p.casks.extra, ["slack"]);
  assert.deepEqual(p.taps.installed, ["a/b"]);
  assert.deepEqual(p.taps.missing, []);
  assert.deepEqual(p.taps.extra, ["c/d"]);
});

test("partitionLists skips tap sections when desired taps empty", () => {
  const p = partitionLists({
    desired: { formulas: [], casks: [], taps: [] },
    installed: { formulas: ["bat"], casks: [], taps: ["homebrew/core"] },
  });
  assert.deepEqual(p.taps, { installed: [], missing: [], extra: [] });
});

test("initGridLayout sizes columns from longest item", () => {
  const layout = initGridLayout(20, ["aa", "bb", "cc", "dd"]);
  assert.equal(layout.cell, 2);
  assert.equal(layout.cols, 5);
});

test("formatGrid is column-major", () => {
  const lines = formatGrid(["a", "b", "c", "d"], { cols: 2, cell: 1 });
  assert.equal(lines[0].trim().replace(/\s+/g, " "), "a c");
  assert.equal(lines[1].trim().replace(/\s+/g, " "), "b d");
});

test("runListCommand returns 1 when brew missing", async () => {
  let err = "";
  const code = await runListCommand([], {
    env: {},
    ui: { error: (m) => { err = m; } },
    resolveBrew: () => null,
  });
  assert.equal(code, 1);
  assert.match(err, /Homebrew not found/);
});

test("runListCommand renders partitions on success", async () => {
  let output = "";
  const ui = {
    error() {},
    info() {},
    desiredStatus() {
      output = "rendered";
    },
  };
  const code = await runListCommand([], {
    env: { PATH: "/bin" },
    stdout: { columns: 80 },
    ui,
    resolveBrew: () => "/opt/homebrew/bin/brew",
    loadDesired: async () => ({
      ok: true,
      document: { version: 1, formulas: ["bat"], casks: [], taps: [] },
    }),
    loadState: async () => ({ formulas: ["bat"], casks: [], taps: [] }),
  });
  assert.equal(code, 0);
  assert.equal(output, "rendered");
});
