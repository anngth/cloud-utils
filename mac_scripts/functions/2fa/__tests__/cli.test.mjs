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
    copied: undefined,
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

test("bare 2fa copies totp and prints success", async () => {
  const h = cliHarness({
    readSecret: async () => "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
    copyToClipboard: async (text) => {
      h.copied = text;
    },
    now: 59,
  });
  assert.equal(await runCli([], h.dependencies), 0);
  assert.equal(h.copied, "287082");
  assert.match(h.stdout().replace(/\u001b\[[0-9;]*m/g, ""), /Code copied: 287082/);
  assert.equal(h.stderr(), "");
});

test("bare 2fa invalid secret exits 1", async () => {
  const h = cliHarness({
    readSecret: async () => "!!!!",
    copyToClipboard: async () => {
      throw new Error("should not copy");
    },
  });
  assert.equal(await runCli([], h.dependencies), 1);
  assert.match(h.stderr(), /invalid|Base32/i);
});

test("bare 2fa clipboard failure exits 1", async () => {
  const h = cliHarness({
    readSecret: async () => "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
    copyToClipboard: async () => {
      throw new Error("failed to copy code to clipboard");
    },
    now: 59,
  });
  assert.equal(await runCli([], h.dependencies), 1);
  assert.match(h.stderr(), /clipboard/i);
});

test("bare 2fa without tty exits 1", async () => {
  const h = cliHarness({
    readSecret: async () => {
      throw new Error("interactive terminal required");
    },
  });
  assert.equal(await runCli([], h.dependencies), 1);
  assert.match(h.stderr(), /interactive terminal required/);
});
