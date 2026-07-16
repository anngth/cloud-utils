import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  makeSandbox,
  normalizeCapture,
  runJavaScript,
} from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const GOLDENS = JSON.parse(readFileSync(join(TEST_DIR, "fixtures/golden.json"), "utf8"));
const REPRESENTATIVE_LEGACY_CAPTURE = {
  status: 0,
  signal: null,
  stdout:
    "\n   \u001b[46m\u001b[30m SKILLS MANAGER \u001b[39m\u001b[49m\n" +
    "\u001b[36m│\u001b[39m\n" +
    "\u001b[32m◇\u001b[39m  Source list: <sandbox>/config/skm/list.json\n" +
    "\u001b[36m│\u001b[39m\n" +
    "\u001b[32m◇\u001b[39m  Add request: 1 sources\n" +
    "\u001b[36m│\u001b[39m\n" +
    "\u001b[36m◆\u001b[39m  Add sources\n" +
    "\u001b[36m│\u001b[39m  \u001b[32m■\u001b[39m Added: b/two\n" +
    "\u001b[36m└\u001b[39m  Added 1 sources, skipped 0 existing\n",
  stderr: "",
};
const REPRESENTATIVE_LEGACY_FILE = `[
  {
    "source": "a/one"
  },
  {
    "source": "b/two"
  }
]
`;

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

test("JavaScript matches the legacy capture for a representative ordinary case", (t) => {
  const jsSandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const javascript = normalizeCapture(runJavaScript(["add", "b/two"], jsSandbox), jsSandbox);
  assert.deepEqual(javascript, REPRESENTATIVE_LEGACY_CAPTURE);
  assert.equal(readFileSync(jsSandbox.skillsFile, "utf8"), REPRESENTATIVE_LEGACY_FILE);
});
