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
        return { profilesFile: "/profiles", projectsFile: "/projects" };
      },
      readConfig: () => {
        read += 1;
        return {
          profiles: { version: 1, profiles: [{ name: "default", sources: [] }] },
          projects: { version: 1, projects: [] },
        };
      },
      runProfileCommand: handler("profile"),
      runSourceCommand: handler("source"),
      runSkillCommand: handler("skill"),
      runProjectCommand: handler("project"),
      runStatusCommand: handler("status"),
      runInstallCommand: handler("install"),
      runUninstallCommand: handler("uninstall"),
      runDashboard: async (context) => {
        calls.push(["dashboard", [], context]);
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
    [["profile", "list"], "profile"],
    [["source", "show", "a/repo"], "source"],
    [["skill", "remove", "a", "--source", "a/repo", "-p", "default"], "skill"],
    [["project", "show"], "project"],
    [["status"], "status"],
    [["install", "frontend"], "install"],
    [["uninstall", "frontend"], "uninstall"],
  ]) {
    const harness = cliHarness();
    assert.equal(await runCli(argv, harness.dependencies), 0);
    assert.deepEqual(harness.calls.map(([name]) => name), [handler]);
    assert.deepEqual(harness.calls[0][1], argv.slice(1));
  }
});

test("project remove without a path passes grammar validation", async () => {
  const harness = cliHarness();
  assert.equal(await runCli(["project", "remove"], harness.dependencies), 0);
  assert.deepEqual(harness.calls.map(([name]) => name), ["project"]);
  assert.deepEqual(harness.calls[0][1], ["remove"]);
});

test("project remove rejects two paths without dispatch", async () => {
  const harness = cliHarness();
  assert.equal(await runCli(["project", "remove", "/one", "/two"], harness.dependencies), 1);
  assert.deepEqual(harness.calls, []);
});

test("rejects removed legacy commands", async () => {
  for (const command of ["ls", "list", "add", "remove", "rm", "show"]) {
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

test("no arguments require a TTY and dispatch the dashboard", async () => {
  const interactive = cliHarness();
  assert.equal(await runCli([], interactive.dependencies), 0);
  assert.deepEqual(interactive.calls.map(([name]) => name), ["dashboard"]);

  for (const options of [{ stdinIsTTY: false }, { stdoutIsTTY: false }]) {
    const nonInteractive = cliHarness(options);
    assert.equal(await runCli([], nonInteractive.dependencies), 1);
    assert.match(nonInteractive.stderr(), /interactive terminal/i);
    assert.deepEqual(nonInteractive.calls, []);
  }
});

test("valid routes receive initialized paths and validated config", async () => {
  const harness = cliHarness();
  assert.equal(await runCli(["profile", "list"], harness.dependencies), 0);
  assert.equal(harness.initialized(), 1);
  assert.equal(harness.read(), 1);
  const context = harness.calls[0][2];
  assert.deepEqual(context.paths, { profilesFile: "/profiles", projectsFile: "/projects" });
  assert.equal(context.config.profiles.profiles[0].name, "default");
  assert.equal(context.cwd, "/repo");
});

test("routes that use upstream skills fail early when npx is missing", async () => {
  for (const argv of [
    ["source", "show", "a/repo"],
    ["skill", "add", "a", "--source", "a/repo", "-p", "default"],
    ["status"],
    ["install", "frontend"],
    ["uninstall", "frontend"],
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
    const argv = ["source", "add", "a/repo", "-p", "default", noSkills];
    assert.equal(await runCli(argv, harness.dependencies), 0, noSkills);
    assert.deepEqual(harness.calls.map(([name]) => name), ["source"], noSkills);
    assert.deepEqual(harness.calls[0][1], argv.slice(1), noSkills);
    assert.equal(harness.npxChecks(), 0, noSkills);
    assert.doesNotMatch(harness.stderr(), /npx is required/i, noSkills);
  }
});

test("route grammar errors take precedence over missing npx", async () => {
  for (const [argv, message] of [
    [["source", "show"], /Usage: skm source show/i],
    [["source", "add", "a\/repo", "--all", "--no-skills"], /mutually exclusive/i],
    [["install", "--unsupported"], /Unknown option: --unsupported/i],
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
  assert.equal(await runCli(["profile", "list"], harness.dependencies), 1);
  assert.match(harness.stderr(), /Could not create config directory/);
  assert.deepEqual(harness.calls, []);
});
