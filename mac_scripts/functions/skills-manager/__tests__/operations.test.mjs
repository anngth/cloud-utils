import assert from "node:assert/strict";
import test from "node:test";
import { executeInstallPlan } from "../operations.mjs";

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

test("an empty conflict-free plan succeeds without mutation", async () => {
  let calls = 0;
  const result = await executeInstallPlan(plan(), {
    runMutation: async () => { calls += 1; return 0; },
  });
  assert.equal(calls, 0);
  assert.deepEqual(result, { ok: true, succeeded: [], failed: [] });
});
