import assert from "node:assert/strict";
import test from "node:test";
import { createUi } from "../ui.mjs";

const stripAnsi = (text) => text.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, "");

test("usage matches skm-style BUD sections", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write() {} },
  });
  ui.usage();
  const lines = stripAnsi(stdout).split(/\r?\n/).map((l) => l.trimEnd());

  assert.ok(lines.some((l) => l.includes("BUD")));
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
  }, { columns: 80 });

  const text = stripAnsi(stdout);
  assert.ok(text.includes("BUD"));
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
