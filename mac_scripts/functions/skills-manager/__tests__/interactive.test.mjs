import assert from "node:assert/strict";
import test from "node:test";
import { requirementKey } from "../planner.mjs";
import { filterSkillRequirementKeys, runInteractive } from "../interactive.mjs";

const SAMPLE_CATALOG = {
  version: 1,
  sources: [
    { source: "a/one", skills: ["skill-a", "skill-b"] },
    { source: "b/two", skills: ["skill-c"] },
  ],
};

const actualSkill = (name, source) => ({
  name,
  path: `/repo/.agents/skills/${name}`,
  agents: ["Codex"],
  source,
  provenance: source ? "tracked" : "untracked",
});

function interactiveHarness({
  catalog = SAMPLE_CATALOG,
  installed = new Map(),
  selection = { type: "submit", selected: [] },
  confirmed = true,
  installResult = { ok: true, succeeded: [], failed: [] },
  uninstallResult = { ok: true, succeeded: [], failed: [] },
  npxAvailable = true,
} = {}) {
  const root = "/repo";
  let stderr = "";
  const uiCalls = [];
  const calls = [];
  const context = {
    cwd: root,
    config: { catalog },
    ui: {
      error: (message) => { stderr += `${message}\n`; },
      warn: (message) => { stderr += `${message}\n`; },
      catalogSelector: (title, state) => uiCalls.push(["catalogSelector", title, state]),
      cancelledCatalogSelector: (title, state) => uiCalls.push(["cancelledCatalogSelector", title, state]),
      installPlan: (value) => uiCalls.push(["installPlan", value]),
      uninstallPlan: (value) => uiCalls.push(["uninstallPlan", value]),
      executionSummary: (value, options) => uiCalls.push(["executionSummary", value, options]),
    },
    resolveProjectRoot: () => root,
    loadInstalledState: async () => installed,
    selectCatalogItems: async (items, options) => {
      calls.push(["selectCatalogItems", items, options]);
      return selection;
    },
    confirm: async (message) => {
      calls.push(["confirm", message]);
      return confirmed;
    },
    requireNpx: () => npxAvailable,
    executeInstallPlan: async (plan, options) => {
      calls.push(["executeInstallPlan", plan, options]);
      return installResult;
    },
    executeUninstallPlan: async (plan, options) => {
      calls.push(["executeUninstallPlan", plan, options]);
      return uninstallResult;
    },
  };
  return { context, uiCalls, calls, stderr: () => stderr };
}

test("filterSkillRequirementKeys drops source-row values from selector submit", () => {
  const items = [
    { kind: "source", value: "a/one", childValues: [requirementKey("a/one", "skill-a")] },
    { kind: "skill", value: requirementKey("a/one", "skill-a") },
  ];
  const selected = ["a/one", requirementKey("a/one", "skill-a")];
  assert.deepEqual(
    filterSkillRequirementKeys(selected, items),
    [requirementKey("a/one", "skill-a")],
  );
});

test("empty catalog errors with source add hint", async () => {
  const harness = interactiveHarness({ catalog: { version: 1, sources: [] } });
  assert.equal(await runInteractive(harness.context), 1);
  assert.match(harness.stderr(), /empty/i);
  assert.match(harness.stderr(), /skm source add/i);
  assert.equal(harness.calls.length, 0);
});

test("cancellation exits non-zero without mutations", async () => {
  const harness = interactiveHarness({
    selection: { type: "cancel", selected: [], state: { items: [], selected: new Set() } },
  });
  assert.equal(await runInteractive(harness.context), 1);
  assert.equal(harness.calls.some(([name]) => name === "executeInstallPlan"), false);
  assert.equal(harness.calls.some(([name]) => name === "executeUninstallPlan"), false);
});

test("selector receives disk-backed initial state from catalog helper", async () => {
  const installed = new Map([["skill-a", actualSkill("skill-a", "a/one")]]);
  const harness = interactiveHarness({
    installed,
    selection: {
      type: "submit",
      selected: [requirementKey("a/one", "skill-a")],
    },
  });
  await runInteractive(harness.context);
  const [, , options] = harness.calls.find(([name]) => name === "selectCatalogItems");
  assert.deepEqual(options.initial, [requirementKey("a/one", "skill-a")]);
});

test("submit installs newly selected missing skills", async () => {
  const harness = interactiveHarness({
    installed: new Map(),
    selection: {
      type: "submit",
      selected: [
        "a/one",
        requirementKey("a/one", "skill-a"),
        requirementKey("a/one", "skill-b"),
      ],
    },
  });
  assert.equal(await runInteractive(harness.context), 0);
  const installCall = harness.calls.find(([name]) => name === "executeInstallPlan");
  assert.ok(installCall);
  assert.deepEqual(
    installCall[1].install.map((item) => item.skill).sort(),
    ["skill-a", "skill-b"],
  );
  assert.equal(harness.calls.some(([name]) => name === "executeUninstallPlan"), false);
});

test("submit uninstalls deselected catalog skills", async () => {
  const harness = interactiveHarness({
    installed: new Map([
      ["skill-a", actualSkill("skill-a", "a/one")],
      ["skill-b", actualSkill("skill-b", "a/one")],
    ]),
    selection: {
      type: "submit",
      selected: [requirementKey("a/one", "skill-a")],
    },
  });
  assert.equal(await runInteractive(harness.context), 0);
  assert.equal(harness.calls.some(([name]) => name === "executeInstallPlan"), false);
  const uninstallCall = harness.calls.find(([name]) => name === "executeUninstallPlan");
  assert.ok(uninstallCall);
  assert.deepEqual(uninstallCall[1].remove.map((item) => item.skill), ["skill-b"]);
});

test("no-op when selection matches disk exits without confirmation", async () => {
  const harness = interactiveHarness({
    installed: new Map([["skill-a", actualSkill("skill-a", "a/one")]]),
    selection: {
      type: "submit",
      selected: [requirementKey("a/one", "skill-a")],
    },
  });
  assert.equal(await runInteractive(harness.context), 0);
  assert.equal(harness.calls.some(([name]) => name === "confirm"), false);
  assert.equal(harness.calls.some(([name]) => name === "executeInstallPlan"), false);
  assert.equal(harness.calls.some(([name]) => name === "executeUninstallPlan"), false);
});

test("mismatch conflicts block interactive submit without force", async () => {
  const harness = interactiveHarness({
    installed: new Map([["skill-a", actualSkill("skill-a", "wrong/repo")]]),
    selection: {
      type: "submit",
      selected: [requirementKey("a/one", "skill-a")],
    },
  });
  assert.equal(await runInteractive(harness.context), 1);
  assert.match(harness.stderr(), /conflict|mismatch|blocked/i);
  assert.equal(harness.calls.some(([name]) => name === "executeInstallPlan"), false);
});

test("confirmation cancellation exits non-zero", async () => {
  const harness = interactiveHarness({
    installed: new Map(),
    selection: {
      type: "submit",
      selected: [requirementKey("a/one", "skill-a")],
    },
    confirmed: false,
  });
  assert.equal(await runInteractive(harness.context), 1);
  assert.equal(harness.calls.some(([name]) => name === "executeInstallPlan"), false);
});
