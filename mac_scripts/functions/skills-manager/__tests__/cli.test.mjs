import assert from "node:assert/strict";
import test from "node:test";
import { runCli } from "../cli.mjs";

function cliHarness({ stdinIsTTY = true, stdoutIsTTY = true, hasNpx = true } = {}) {
  const calls = [];
  let stdout = "";
  let stderr = "";
  let initialized = 0;
  let read = 0;
  let npxChecks = 0;
  const handler = (name) => async (args, context) => {
    calls.push([name, args, context]);
    return 0;
  };
  return {
    calls,
    stdout: () => stdout,
    stderr: () => stderr,
    initialized: () => initialized,
    read: () => read,
    npxChecks: () => npxChecks,
    dependencies: {
      cwd: "/repo",
      env: { PATH: "/bin" },
      stdin: { isTTY: stdinIsTTY },
      stdout: { isTTY: stdoutIsTTY, write: (value) => { stdout += value; } },
      stderr: { write: (value) => { stderr += value; } },
      hasCommand: () => { npxChecks += 1; return hasNpx; },
      initializeConfig: () => {
        initialized += 1;
        return { sourcesFile: "/sources" };
      },
      readConfig: () => {
        read += 1;
        return {
          catalog: { version: 1, sources: [] },
        };
      },
      runSourceCommand: handler("source"),
      runStatusCommand: handler("status"),
      runAddCommand: handler("add"),
      runRemoveCommand: handler("remove"),
      runInteractive: async (context) => {
        calls.push(["interactive", [], context]);
        return 0;
      },
      ui: {
        usage() { stdout += "usage\n"; },
        error: (message) => { stderr += `${message}\n`; },
        usageLine: (message) => { stderr += `${message}\n`; },
      },
    },
  };
}

test("dispatches only the new top-level commands", async () => {
  for (const [argv, handler] of [
    [["source", "add", "a/repo", "-n"], "source"],
    [["source", "remove", "1"], "source"],
    [["status"], "status"],
    [["add", "1"], "add"],
    [["remove", "--all"], "remove"],
  ]) {
    const harness = cliHarness();
    assert.equal(await runCli(argv, harness.dependencies), 0);
    assert.deepEqual(harness.calls.map(([name]) => name), [handler]);
    assert.deepEqual(harness.calls[0][1], argv.slice(1));
  }
});

test("rejects removed legacy commands", async () => {
  for (const command of ["ls", "list", "rm", "show", "install", "uninstall", "profile", "skill", "project"]) {
    const harness = cliHarness({ hasNpx: false });
    assert.equal(await runCli([command], harness.dependencies), 1);
    assert.match(harness.stderr(), new RegExp(`Unknown command: ${command}`));
    assert.deepEqual(harness.calls, []);
    assert.equal(harness.initialized(), 0);
    assert.equal(harness.read(), 0);
  }
});

for (const command of ["constructor", "toString", "__proto__"]) {
  test(`rejects inherited route name ${command} without npx checks or dispatch`, async () => {
    const harness = cliHarness({ hasNpx: false });
    assert.equal(await runCli([command], harness.dependencies), 1);
    assert.match(harness.stderr(), new RegExp(`Unknown command: ${command}`));
    assert.match(harness.stderr(), /skm --help/);
    assert.deepEqual(harness.calls, []);
    assert.equal(harness.npxChecks(), 0);
    assert.equal(harness.initialized(), 0);
    assert.equal(harness.read(), 0);
  });
}

test("help aliases render without bootstrapping or checking npx", async () => {
  for (const alias of ["help", "-h", "--help"]) {
    const harness = cliHarness({ hasNpx: false });
    assert.equal(await runCli([alias], harness.dependencies), 0);
    assert.match(harness.stdout(), /usage/);
    assert.equal(harness.initialized(), 0);
    assert.equal(harness.read(), 0);
  }
});

test("no arguments require a TTY and dispatch interactive mode", async () => {
  const interactive = cliHarness();
  assert.equal(await runCli([], interactive.dependencies), 0);
  assert.deepEqual(interactive.calls.map(([name]) => name), ["interactive"]);

  for (const options of [{ stdinIsTTY: false }, { stdoutIsTTY: false }]) {
    const nonInteractive = cliHarness(options);
    assert.equal(await runCli([], nonInteractive.dependencies), 1);
    assert.match(nonInteractive.stderr(), /interactive terminal/i);
    assert.match(nonInteractive.stderr(), /skm add/);
    assert.match(nonInteractive.stderr(), /skm remove/);
    assert.match(nonInteractive.stderr(), /--all/);
    assert.deepEqual(nonInteractive.calls, []);
  }
});

test("valid routes receive initialized paths and validated config", async () => {
  const harness = cliHarness();
  assert.equal(await runCli(["source", "add", "a/repo", "-n"], harness.dependencies), 0);
  assert.equal(harness.initialized(), 1);
  assert.equal(harness.read(), 1);
  const context = harness.calls[0][2];
  assert.deepEqual(context.paths, { sourcesFile: "/sources" });
  assert.deepEqual(context.config.catalog, { version: 1, sources: [] });
  assert.equal(context.cwd, "/repo");
});

test("routes that use upstream skills fail early when npx is missing", async () => {
  for (const argv of [
    ["source", "add", "a/repo"],
    ["status"],
    ["add", "1"],
    ["remove", "1"],
  ]) {
    const harness = cliHarness({ hasNpx: false });
    assert.equal(await runCli(argv, harness.dependencies), 1);
    assert.match(harness.stderr(), /npx is required/i);
    assert.deepEqual(harness.calls, []);
  }
});

test("source add no-skills aliases bypass npx preflight and dispatch equivalently", async () => {
  for (const noSkills of ["-n", "--no-skills"]) {
    const harness = cliHarness({ hasNpx: false });
    const argv = ["source", "add", "a/repo", noSkills];
    assert.equal(await runCli(argv, harness.dependencies), 0, noSkills);
    assert.deepEqual(harness.calls.map(([name]) => name), ["source"], noSkills);
    assert.deepEqual(harness.calls[0][1], argv.slice(1), noSkills);
    assert.equal(harness.npxChecks(), 0, noSkills);
    assert.doesNotMatch(harness.stderr(), /npx is required/i, noSkills);
  }
});

test("source edit no-skills aliases bypass npx preflight and dispatch equivalently", async () => {
  for (const noSkills of ["-n", "--no-skills"]) {
    const harness = cliHarness({ hasNpx: false });
    const argv = ["source", "edit", "1", noSkills];
    assert.equal(await runCli(argv, harness.dependencies), 0, noSkills);
    assert.deepEqual(harness.calls.map(([name]) => name), ["source"], noSkills);
    assert.deepEqual(harness.calls[0][1], argv.slice(1), noSkills);
    assert.equal(harness.npxChecks(), 0, noSkills);
  }
});

test("route grammar errors take precedence over missing npx", async () => {
  for (const [argv, message] of [
    [["source", "add"], /Usage: skm source add/i],
    [["source", "edit"], /Usage: skm source edit/i],
    [["source", "add", "a/repo", "--all", "--no-skills"], /mutually exclusive/i],
    [["add", "--unsupported"], /Unknown option: --unsupported/i],
  ]) {
    const harness = cliHarness({ hasNpx: false });
    assert.equal(await runCli(argv, harness.dependencies), 1);
    assert.match(harness.stderr(), message);
    assert.doesNotMatch(harness.stderr(), /npx is required/i);
    assert.equal(harness.npxChecks(), 0);
    assert.equal(harness.initialized(), 0);
  }
});

test("configuration failures are rendered without dispatch", async () => {
  const harness = cliHarness();
  harness.dependencies.initializeConfig = () => { throw new Error("disk unavailable"); };
  assert.equal(await runCli(["status"], harness.dependencies), 1);
  assert.match(harness.stderr(), /Could not create config directory/);
  assert.deepEqual(harness.calls, []);
});
