import assert from "node:assert/strict";
import test from "node:test";
import { runCli } from "../cli.mjs";
import { createUi } from "../ui.mjs";

function cliHarness() {
  const calls = [];
  let stdout = "";
  let stderr = "";
  const handler = (name) => async (args, context) => {
    calls.push([name, args]);
    return 0;
  };
  return {
    calls,
    stdout: () => stdout,
    stderr: () => stderr,
    dependencies: {
      cwd: "/repo",
      env: process.env,
      stdout: { write: (v) => { stdout += v; } },
      stderr: { write: (v) => { stderr += v; } },
      runPushCommand: handler("push"),
      runFetchCommand: handler("fetch"),
      runBackupCommand: handler("backup"),
      ui: {
        usage() { stdout += "usage\n"; },
        error(message) { stderr += `${message}\n`; },
        usageLine(message) { stderr += `${message}\n`; },
        status() {},
      },
    },
  };
}

test("routes push", async () => {
  const h = cliHarness();
  const code = await runCli(["push"], h.dependencies);
  assert.equal(code, 0);
  assert.deepEqual(h.calls, [["push", []]]);
});

test("routes fetch with flags", async () => {
  const h = cliHarness();
  const code = await runCli(["fetch", "--sync-upstream"], h.dependencies);
  assert.equal(code, 0);
  assert.deepEqual(h.calls, [["fetch", ["--sync-upstream"]]]);
});

test("help exits 0", async () => {
  const h = cliHarness();
  assert.equal(await runCli(["--help"], h.dependencies), 0);
  assert.match(h.stdout(), /usage/i);
});

test("missing command exits 1", async () => {
  const h = cliHarness();
  assert.equal(await runCli([], h.dependencies), 1);
});

test("routes backup", async () => {
  const h = cliHarness();
  const code = await runCli(["backup", "git@github.com:o/r.git"], h.dependencies);
  assert.equal(code, 0);
  assert.deepEqual(h.calls, [["backup", ["git@github.com:o/r.git"]]]);
});

test("help mentions backup", async () => {
  let stdout = "";
  const code = await runCli(["--help"], {
    cwd: "/repo",
    env: process.env,
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write: () => {} },
    runPushCommand: async () => 0,
    runFetchCommand: async () => 0,
    runBackupCommand: async () => 0,
    ui: createUi({ stdout: { write: (v) => { stdout += v; } }, stderr: { write: () => {} } }),
  });
  assert.equal(code, 0);
  assert.match(stdout, /backup \[-n\|--new\] <ssh-url>  Mirror repo to gitlab\.com\/anngth-dev\/backups/);
});

test("unknown command exits 1", async () => {
  const h = cliHarness();
  assert.equal(await runCli(["nope"], h.dependencies), 1);
});
