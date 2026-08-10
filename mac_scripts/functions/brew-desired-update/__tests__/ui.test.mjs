import assert from "node:assert/strict";
import test from "node:test";
import { createUi, formatNameList } from "../ui.mjs";

const stripAnsi = (text) => text.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, "");

test("formatNameList truncates after limit", () => {
  assert.equal(formatNameList([]), "");
  assert.equal(formatNameList(["a", "b"]), "a b");
  assert.equal(formatNameList(["a", "b", "c"], { limit: 2 }), "a b … (+1 more)");
  assert.equal(
    formatNameList(["1", "2", "3", "4", "5", "6", "7", "8", "9"]),
    "1 2 3 4 5 6 7 8 … (+1 more)",
  );
});

test("usage matches skm-style sections with BREW DESIRED UPDATE badge", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write() {} },
  });
  ui.usage();
  const lines = stripAnsi(stdout).split(/\r?\n/).map((l) => l.trimEnd());

  assert.ok(lines.some((l) => l.includes("BREW DESIRED UPDATE")));
  assert.ok(!lines.some((l) => l.trim() === "BUD" || l.includes(" BUD ")));
  assert.equal(lines.find((l) => l.includes("Usage:")), "◇  Usage: bud [command]");
  for (const section of ["Update", "Lists", "Notes"]) {
    assert.ok(lines.includes(`◆  ${section}`), section);
  }
  assert.ok(lines.some((l) => l.includes("bud (help | -h | --help)")));
  assert.ok(lines.some((l) => l.includes("bud [(-e | --exclude) <cask>...]")));
  assert.ok(lines.some((l) => l.includes("bud (ls | list)")));
  assert.ok(lines.some((l) => l.includes("bud add <name...>")));
  assert.ok(lines.some((l) => l.includes("bud remove <name...>")));
  assert.ok(/does not install missing/i.test(stdout));
});

test("desiredStatus renders sections and skips empty", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write() {} },
  });
  ui.desiredStatus({
    formulas: { installed: ["bat"], missing: ["missing"], extra: [] },
    taps: { installed: [], missing: [], extra: [] },
    casks: { installed: [], missing: [], extra: ["slack"] },
  }, {
    columns: 80,
    desiredCounts: { formulas: 2, taps: 0, casks: 1 },
  });

  const text = stripAnsi(stdout);
  assert.ok(text.includes("BREW DESIRED UPDATE"));
  assert.ok(text.includes("Desired vs installed · 2 formulae · 0 taps · 1 casks"));
  assert.ok(text.includes("Formulae · in list, installed"));
  assert.ok(text.includes("Formulae · in list, not installed"));
  assert.ok(text.includes("Casks · installed, not in list"));
  assert.ok(text.includes("bat"));
  assert.ok(text.includes("missing"));
  assert.ok(text.includes("slack"));
  assert.doesNotMatch(text, /Formulae · installed, not in list/);
  assert.doesNotMatch(text, /Taps ·/);
  assert.ok(text.includes("└"));
});
