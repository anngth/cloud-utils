import assert from "node:assert/strict";
import test from "node:test";
import { executeInstallPlan, executeUninstallPlan } from "../operations.mjs";

const req = (source, skill) => ({
  key: JSON.stringify([source, skill]),
  source,
  skill,
  profiles: ["frontend"],
});

const plan = (overrides = {}) => ({
  install: [],
  replace: [],
  conflicts: [],
  skip: [],
  extras: [],
  desiredConflicts: [],
  ...overrides,
});

test("batches missing skills by source and continues after failure", async () => {
  const calls = [];
  const result = await executeInstallPlan(plan({
    install: [req("a/repo", "one"), req("a/repo", "two"), req("b/repo", "three")],
  }), {
    yes: true,
    runMutation: async (args) => {
      calls.push(args);
      return args.includes("a/repo") ? 2 : 0;
    },
    onEvent() {},
  });
  assert.deepEqual(calls, [
    ["skills", "add", "a/repo", "--skill", "one", "--skill", "two", "--yes"],
    ["skills", "add", "b/repo", "--skill", "three", "--yes"],
  ]);
  assert.equal(result.ok, false);
  assert.deepEqual(result.failed, [{
    action: "install", source: "a/repo", skills: ["one", "two"], status: 2,
  }]);
  assert.deepEqual(result.succeeded, [{
    action: "install", source: "b/repo", skills: ["three"], status: 0,
  }]);
});

test("installs independent missing skills while leaving actual conflicts unresolved", async () => {
  const calls = [];
  const result = await executeInstallPlan(plan({
    install: [req("a/repo", "missing")],
    conflicts: [req("a/repo", "mismatch")],
  }), {
    yes: true,
    runMutation: async (args) => { calls.push(args); return 0; },
    onEvent() {},
  });
  assert.equal(calls.length, 1);
  assert.equal(result.succeeded.length, 1);
  assert.equal(result.ok, false);
});

test("desired-source conflicts block every mutation", async () => {
  let calls = 0;
  const result = await executeInstallPlan(plan({
    install: [req("a/repo", "safe")],
    desiredConflicts: [{
      skill: "review",
      sources: ["a/repo", "b/repo"],
      profiles: ["frontend", "quality"],
    }],
  }), {
    runMutation: async () => { calls += 1; return 0; },
  });
  assert.equal(calls, 0);
  assert.deepEqual(result, { ok: false, succeeded: [], failed: [] });
});

test("removes replacements sequentially and installs only successful removals", async () => {
  const calls = [];
  const events = [];
  const result = await executeInstallPlan(plan({
    install: [req("c/repo", "missing")],
    replace: [req("a/repo", "blocked"), req("b/repo", "ready")],
  }), {
    runMutation: async (args) => {
      calls.push(args);
      return args[1] === "remove" && args.includes("blocked") ? 5 : 0;
    },
    onEvent: (event) => events.push(event),
  });
  assert.deepEqual(calls, [
    ["skills", "remove", "blocked", "--yes"],
    ["skills", "remove", "ready", "--yes"],
    ["skills", "add", "c/repo", "--skill", "missing"],
    ["skills", "add", "b/repo", "--skill", "ready"],
  ]);
  assert.equal(result.ok, false);
  assert.deepEqual(result.failed[0], {
    action: "replace", source: "a/repo", skills: ["blocked"], status: 5,
  });
  assert.equal(events[0].action, "remove-for-replace");
  assert.equal(events.length, 4);
});

test("records a removed-old-skill phase when replacement installation fails", async () => {
  const result = await executeInstallPlan(plan({
    replace: [req("a/repo", "review")],
  }), {
    runMutation: async (args) => args[1] === "remove" ? 0 : 7,
  });
  assert.deepEqual(result.replacements, [{
    source: "a/repo",
    skill: "review",
    removeStatus: 0,
    installStatus: 7,
  }]);
});

test("passes the canonical project root to every install and uninstall mutation", async () => {
  const calls = [];
  await executeInstallPlan(plan({
    install: [req("a/repo", "new")],
    replace: [req("b/repo", "old")],
  }), {
    projectRoot: "/repo",
    runMutation: async (args, options) => { calls.push([args, options]); return 0; },
  });
  await executeUninstallPlan({
    remove: [req("a/repo", "new")], retain: [], absent: [], conflicts: [],
    unlinkProfiles: ["frontend"], desiredConflicts: [],
  }, {
    projectRoot: "/repo",
    runMutation: async (args, options) => { calls.push([args, options]); return 0; },
  });
  assert.equal(calls.length, 4);
  assert.deepEqual(calls.map(([, options]) => options), [
    { cwd: "/repo" }, { cwd: "/repo" }, { cwd: "/repo" }, { cwd: "/repo" },
  ]);
});

test("an empty conflict-free plan succeeds without mutation", async () => {
  let calls = 0;
  const result = await executeInstallPlan(plan(), {
    runMutation: async () => { calls += 1; return 0; },
  });
  assert.equal(calls, 0);
  assert.deepEqual(result, { ok: true, succeeded: [], failed: [] });
});

test("uninstall removes only eligible names in one upstream call", async () => {
  const calls = [];
  const result = await executeUninstallPlan({
    remove: [req("a/repo", "one"), req("b/repo", "two")],
    retain: [req("a/repo", "shared")],
    absent: [], conflicts: [], unlinkProfiles: ["frontend"], desiredConflicts: [],
  }, {
    yes: true,
    runMutation: async (args) => { calls.push(args); return 0; },
    onEvent() {},
  });
  assert.deepEqual(calls, [["skills", "remove", "one", "two", "--yes"]]);
  assert.equal(result.ok, true);
});

test("uninstall removes eligible names but remains unsuccessful with skipped conflicts", async () => {
  const calls = [];
  const result = await executeUninstallPlan({
    remove: [req("a/repo", "one")],
    retain: [], absent: [],
    conflicts: [req("a/repo", "unknown")],
    unlinkProfiles: ["frontend"], desiredConflicts: [],
  }, {
    yes: true,
    runMutation: async (args) => { calls.push(args); return 0; },
    onEvent() {},
  });
  assert.equal(calls.length, 1);
  assert.equal(result.ok, false);
});

test("uninstall mutation failure reports the exact failed batch", async () => {
  const events = [];
  const result = await executeUninstallPlan({
    remove: [req("a/repo", "one"), req("b/repo", "two")],
    retain: [], absent: [], conflicts: [], unlinkProfiles: ["frontend"], desiredConflicts: [],
  }, {
    runMutation: async () => 4,
    onEvent: (event) => events.push(event),
  });
  assert.equal(result.ok, false);
  assert.deepEqual(result.succeeded, []);
  assert.deepEqual(result.failed, [{
    action: "uninstall", source: null, skills: ["one", "two"], status: 4,
  }]);
  assert.deepEqual(events, result.failed);
});

test("uninstall desired-source conflicts block every mutation", async () => {
  let calls = 0;
  const result = await executeUninstallPlan({
    remove: [req("c/repo", "safe")], retain: [], absent: [], conflicts: [],
    unlinkProfiles: ["frontend"],
    desiredConflicts: [{
      skill: "review", sources: ["a/repo", "b/repo"], profiles: ["frontend", "quality"],
    }],
  }, {
    runMutation: async () => { calls += 1; return 0; },
  });
  assert.equal(calls, 0);
  assert.deepEqual(result, { ok: false, succeeded: [], failed: [] });
});

test("empty uninstall plans succeed without an upstream call", async () => {
  let calls = 0;
  const result = await executeUninstallPlan({
    remove: [], retain: [], absent: [], conflicts: [],
    unlinkProfiles: ["empty"], desiredConflicts: [],
  }, {
    runMutation: async () => { calls += 1; return 0; },
  });
  assert.equal(calls, 0);
  assert.deepEqual(result, { ok: true, succeeded: [], failed: [] });
});
