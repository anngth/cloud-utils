import assert from "node:assert/strict";
import test from "node:test";
import { runCli } from "../cli.mjs";
import { createUi } from "../ui.mjs";

function cliHarness(overrides = {}) {
  let stdout = "";
  let stderr = "";
  const ui = createUi({
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write: (v) => { stderr += v; } },
  });
  return {
    stdout: () => stdout,
    stderr: () => stderr,
    dependencies: {
      ui,
      readSecret: async () => {
        throw new Error("readSecret should not be called in routing tests");
      },
      copyToClipboard: async () => {
        throw new Error("copyToClipboard should not be called in routing tests");
      },
      ...overrides,
    },
  };
}

test("help aliases exit 0 and print usage", async () => {
  for (const arg of ["help", "-h", "--help"]) {
    const h = cliHarness();
    assert.equal(await runCli([arg], h.dependencies), 0);
    assert.match(h.stdout(), /Usage:/);
    assert.equal(h.stderr(), "");
  }
});

test("unknown command prints error and full help", async () => {
  const h = cliHarness();
  assert.equal(await runCli(["foo"], h.dependencies), 1);
  assert.match(h.stderr(), /Unknown command: foo/);
  assert.match(h.stdout(), /Usage:/);
});
