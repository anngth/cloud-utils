import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  makeSandbox,
  normalizeCapture,
  runJavaScript,
  runLegacy,
} from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const GOLDENS = JSON.parse(readFileSync(join(TEST_DIR, "fixtures/golden.json"), "utf8"));

for (const entry of GOLDENS) {
  test(`JavaScript matches legacy golden: ${entry.name}`, (t) => {
    const sandbox = makeSandbox(t, { list: entry.list });
    const actual = normalizeCapture(runJavaScript(entry.args, sandbox), sandbox);
    assert.deepEqual(actual, entry.capture);
    if (entry.fileAfter !== undefined) {
      assert.equal(readFileSync(sandbox.skillsFile, "utf8"), entry.fileAfter);
    }
  });
}

test("legacy and JavaScript agree on representative ordinary cases", (t) => {
  const legacySandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const jsSandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const legacy = normalizeCapture(runLegacy(["add", "b/two"], legacySandbox), legacySandbox);
  const javascript = normalizeCapture(runJavaScript(["add", "b/two"], jsSandbox), jsSandbox);
  assert.deepEqual(javascript, legacy);
  assert.equal(readFileSync(jsSandbox.skillsFile, "utf8"), readFileSync(legacySandbox.skillsFile, "utf8"));
});
