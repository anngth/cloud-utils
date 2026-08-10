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
  assert.ok(lines.some((l) => /does not install missing/i.test(l)));
});
