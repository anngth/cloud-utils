import assert from "node:assert/strict";
import test from "node:test";
import { makeSandbox, normalizeCapture, runLegacy } from "./helpers.mjs";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GOLDEN_FILE = join(dirname(fileURLToPath(import.meta.url)), "fixtures/golden.json");
const GOLDENS = JSON.parse(readFileSync(GOLDEN_FILE, "utf8"));

test("legacy help capture has stable streams and status", (t) => {
  const sandbox = makeSandbox(t);
  const result = normalizeCapture(runLegacy(["--help"], sandbox), sandbox);

  assert.equal(result.status, 0);
  assert.equal(result.stderr, "");
  assert.match(result.stdout, /SKILLS MANAGER/);
  assert.match(result.stdout, /Usage: skm <command> \[args\]/);
});

for (const entry of GOLDENS) {
  test(`legacy golden: ${entry.name}`, (t) => {
    const sandbox = makeSandbox(t, { list: entry.list });
    const actual = normalizeCapture(runLegacy(entry.args, sandbox), sandbox);
    assert.deepEqual(actual, entry.capture);
    if (entry.fileAfter !== undefined) {
      assert.equal(readFileSync(sandbox.skillsFile, "utf8"), entry.fileAfter);
    }
  });
}

test("legacy harness preserves a source argument containing spaces", (t) => {
  const sandbox = makeSandbox(t);
  const result = runLegacy(["show", "owner/repo with space"], sandbox);
  assert.equal(result.status, 0);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","owner/repo with space","--list"]\n',
  );
});
