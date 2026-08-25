import assert from "node:assert/strict";
import test from "node:test";
import {
  runAddCommand,
  runRemoveCommand,
  runStatusCommand,
} from "../lifecycle-commands.mjs";
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

function catalogHarness({
  catalog = SAMPLE_CATALOG,
  installed = new Map(),
  selectedSkills,
  skillSelectionCancelled = false,
  confirmed = true,
  execution = { ok: true, succeeded: [], failed: [] },
} = {}) {
  const root = "/repo";
  let stateCalls = 0;
  let stderr = "";
  let skillSelections = 0;
  let confirmations = 0;
  let executionCalls = 0;
  let capturedPlan;
  let executionOptions;
  const uiCalls = [];
  const context = {
    cwd: root,
    paths: { sourcesFile: "/config/sources.json" },
    config: { catalog },
    stdin: { isTTY: true },
    stdout: { isTTY: true, write() {} },
    stderr: { write: (value) => { stderr += value; } },
    ui: {
      error: (message) => { stderr += `${message}\n`; },
      warn: (message) => { stderr += `${message}\n`; },
      status: (value) => uiCalls.push(["status", value]),
      installPlan: (value) => uiCalls.push(["installPlan", value]),
      executionSummary: (value) => uiCalls.push(["executionSummary", value]),
    },
    resolveProjectRoot: () => root,
    loadInstalledState: async () => { stateCalls += 1; return installed; },
    selectSkills: async (items) => {
      skillSelections += 1;
      if (skillSelectionCancelled) return { type: "cancel", selected: [] };
      return {
        type: "submit",
        selected: selectedSkills ?? items.map((item) => item.key),
      };
    },
    confirm: async () => { confirmations += 1; return confirmed; },
    executeInstallPlan: async (value, options) => {
      executionCalls += 1;
      capturedPlan = value;
      executionOptions = options;
      return typeof execution === "function" ? execution(value) : execution;
    },
  };
  return {
    context,
    uiCalls,
    stderr: () => stderr,
    get stateCalls() { return stateCalls; },
    get skillSelections() { return skillSelections; },
    get confirmations() { return confirmations; },
    get executionCalls() { return executionCalls; },
    get capturedPlan() { return capturedPlan; },
    get executionOptions() { return executionOptions; },
  };
}

function removeHarness({
  catalog = SAMPLE_CATALOG,
  installed = new Map([
    ["skill-a", actualSkill("skill-a", "a/one")],
    ["skill-b", actualSkill("skill-b", "a/one")],
    ["skill-c", actualSkill("skill-c", "b/two")],
  ]),
  executionOk = true,
  confirmed = true,
} = {}) {
  const root = "/repo";
  let capturedPlan;
  let confirmations = 0;
  let stateCalls = 0;
  let stderr = "";
  const uiCalls = [];
  const mutationCalls = [];
  const context = {
    cwd: root,
    paths: { sourcesFile: "/config/sources.json" },
    config: { catalog },
    stdin: { isTTY: true },
    stdout: { isTTY: true, write() {} },
    stderr: { write: (value) => { stderr += value; } },
    ui: {
      error: (message) => { stderr += `${message}\n`; },
      warn: (message) => { stderr += `${message}\n`; },
      uninstallPlan: (value) => { capturedPlan = value.plan; uiCalls.push(["uninstallPlan", value]); },
      executionSummary: (value, options) => uiCalls.push(["executionSummary", value, options]),
    },
    resolveProjectRoot: () => root,
    loadInstalledState: async () => { stateCalls += 1; return installed; },
    confirm: async () => { confirmations += 1; return confirmed; },
    executeUninstallPlan: async (plan) => {
      capturedPlan = plan;
      mutationCalls.push(plan.remove.map((item) => item.skill));
      return executionOk
        ? { ok: true, succeeded: [], failed: [] }
        : { ok: false, succeeded: [], failed: [{ action: "uninstall", status: 4 }] };
    },
  };
  return {
    context,
    uiCalls,
    stderr: () => stderr,
    mutationCalls,
    get capturedPlan() { return capturedPlan; },
    get stateCalls() { return stateCalls; },
    get confirmations() { return confirmations; },
    get removedNames() { return capturedPlan?.remove.map((item) => item.skill) ?? []; },
  };
}

test("status lists missing against full catalog", async () => {
  const harness = catalogHarness({ installed: new Map() });
  assert.equal(await runStatusCommand([], harness.context), 1);
  const { status } = harness.uiCalls[0][1];
  assert.deepEqual(status.missing.map((item) => item.skill).sort(), ["skill-a", "skill-b", "skill-c"]);
});

test("status exits zero when all catalog skills are installed", async () => {
  const harness = catalogHarness({
    installed: new Map([
      ["skill-a", actualSkill("skill-a", "a/one")],
      ["skill-b", actualSkill("skill-b", "a/one")],
      ["skill-c", actualSkill("skill-c", "b/two")],
    ]),
  });
  assert.equal(await runStatusCommand([], harness.context), 0);
});

test("status exits zero when only extras are present", async () => {
  const harness = catalogHarness({
    installed: new Map([
      ["skill-a", actualSkill("skill-a", "a/one")],
      ["skill-b", actualSkill("skill-b", "a/one")],
      ["skill-c", actualSkill("skill-c", "b/two")],
      ["extra", actualSkill("extra", "x/repo")],
    ]),
  });
  assert.equal(await runStatusCommand([], harness.context), 0);
  assert.equal(harness.uiCalls[0][1].status.extras[0].name, "extra");
});

test("add --all installs missing catalog skills", async () => {
  const harness = catalogHarness({ installed: new Map() });
  assert.equal(await runAddCommand(["--all", "--yes"], harness.context), 0);
  assert.deepEqual(
    harness.capturedPlan.install.map((item) => item.skill).sort(),
    ["skill-a", "skill-b", "skill-c"],
  );
});

test("add rejects --all with explicit tokens", async () => {
  const harness = catalogHarness();
  assert.equal(await runAddCommand(["--all", "1"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /cannot combine --all/i);
});

test("add 1 resolves first catalog source", async () => {
  const harness = catalogHarness({ installed: new Map() });
  assert.equal(await runAddCommand(["1", "--yes"], harness.context), 0);
  assert.deepEqual(
    harness.capturedPlan.install.map((item) => item.skill).sort(),
    ["skill-a", "skill-b"],
  );
});

test("add without targets or --all fails before state discovery", async () => {
  const harness = catalogHarness();
  assert.equal(await runAddCommand([], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /usage:/i);
});

test("remove uninstalls only that source skills", async () => {
  const harness = removeHarness();
  assert.equal(await runRemoveCommand(["1", "--yes"], harness.context), 0);
  assert.deepEqual(harness.removedNames.sort(), ["skill-a", "skill-b"]);
  assert.ok(!harness.removedNames.includes("skill-c"));
  assert.deepEqual(harness.capturedPlan.retain, []);
});

test("remove without targets or --all fails before state discovery", async () => {
  const harness = removeHarness();
  assert.equal(await runRemoveCommand([], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /usage:/i);
});

test("dry-run renders a plan without execution", async () => {
  const harness = catalogHarness({ installed: new Map() });
  assert.equal(await runAddCommand(["--all", "--dry-run"], harness.context), 0);
  assert.equal(harness.executionCalls, 0);
  assert.equal(harness.confirmations, 0);
  assert.equal(harness.uiCalls[0][1].dryRun, true);
});

test("add short flags match yes and dry-run", async () => {
  const dryRun = catalogHarness({ installed: new Map() });
  assert.equal(await runAddCommand(["--all", "-d"], dryRun.context), 0);
  assert.equal(dryRun.executionCalls, 0);
  assert.equal(dryRun.uiCalls[0][1].dryRun, true);

  const yes = catalogHarness();
  assert.equal(await runAddCommand(["1", "-y"], yes.context), 0);
  assert.equal(yes.skillSelections, 0);
  assert.equal(yes.confirmations, 0);
  assert.equal(yes.executionCalls, 1);
});

test("add rejects --force as an unknown option", async () => {
  const harness = catalogHarness();
  assert.equal(await runAddCommand(["1", "-y", "-f"], harness.context), 1);
  assert.match(harness.stderr(), /unknown option: -f/i);
  assert.equal(harness.executionCalls, 0);
  assert.equal(harness.stateCalls, 0);
});

test("add mismatch and untracked abort without a plan card or mutation", async () => {
  const mismatch = catalogHarness({
    installed: new Map([["skill-a", actualSkill("skill-a", "wrong/repo")]]),
  });
  assert.equal(await runAddCommand(["1", "--yes"], mismatch.context), 1);
  assert.match(mismatch.stderr(), /Blocked by installed skill conflicts: skill-a/);
  assert.equal(mismatch.uiCalls.some(([name]) => name === "installPlan"), false);
  assert.equal(mismatch.executionCalls, 0);

  const untracked = catalogHarness({
    installed: new Map([["skill-a", actualSkill("skill-a", null)]]),
  });
  assert.equal(await runAddCommand(["1", "--yes"], untracked.context), 1);
  assert.match(untracked.stderr(), /Blocked by installed skill conflicts: skill-a/);
  assert.equal(untracked.executionCalls, 0);
});

test("temporary selection changes only the operation plan", async () => {
  const harness = catalogHarness({
    selectedSkills: [JSON.stringify(["a/one", "skill-b"])],
    installed: new Map(),
  });
  assert.equal(await runAddCommand(["1"], harness.context), 0);
  assert.deepEqual(harness.capturedPlan.install.map((item) => item.skill), ["skill-b"]);
});

test("interactive cancellation is a successful no-op", async () => {
  const harness = catalogHarness({ confirmed: false });
  let outcome;
  assert.equal(await runAddCommand(["1"], harness.context, {
    onOutcome: (value) => { outcome = value; },
  }), 0);
  assert.equal(harness.executionCalls, 0);
  assert.deepEqual(outcome, { type: "cancelled", stage: "confirmation" });
});

test("add and remove execute mutations at the canonical project root", async () => {
  const install = catalogHarness();
  install.context.cwd = "/repo/subdirectory";
  assert.equal(await runAddCommand(["1", "--yes"], install.context), 0);
  assert.deepEqual(install.executionOptions, { yes: true, projectRoot: "/repo" });

  const uninstall = removeHarness();
  let uninstallOptions;
  uninstall.context.cwd = "/repo/subdirectory";
  uninstall.context.executeUninstallPlan = async (_plan, options) => {
    uninstallOptions = options;
    return { ok: true, succeeded: [], failed: [] };
  };
  assert.equal(await runRemoveCommand(["1", "--yes"], uninstall.context), 0);
  assert.deepEqual(uninstallOptions, { yes: true, projectRoot: "/repo" });
});

test("add aborts when the source still has mismatch conflicts even if the picker drops them", async () => {
  const harness = catalogHarness({
    catalog: {
      version: 1,
      sources: [{ source: "a/one", skills: ["skill-a", "blocked"] }],
    },
    selectedSkills: [JSON.stringify(["a/one", "skill-a"])],
    installed: new Map([["blocked", actualSkill("blocked", "wrong/repo")]]),
  });
  assert.equal(await runAddCommand(["1"], harness.context), 1);
  assert.match(harness.stderr(), /Blocked by installed skill conflicts: blocked/);
  assert.equal(harness.uiCalls.some(([name]) => name === "installPlan"), false);
  assert.equal(harness.executionCalls, 0);
});

test("remove dry-run renders without confirmation or execution", async () => {
  const harness = removeHarness();
  assert.equal(await runRemoveCommand(["1", "--dry-run"], harness.context), 0);
  assert.deepEqual(harness.mutationCalls, []);
  assert.equal(harness.confirmations, 0);
  assert.equal(harness.uiCalls[0][1].dryRun, true);
});

test("remove uninstalls mismatched and untracked catalog skills", async () => {
  const installed = new Map([
    ["skill-a", actualSkill("skill-a", "wrong/repo")],
    ["skill-b", actualSkill("skill-b", null)],
  ]);
  const harness = removeHarness({ installed });
  assert.equal(await runRemoveCommand(["1", "--yes"], harness.context), 0);
  assert.deepEqual(harness.removedNames.sort(), ["skill-a", "skill-b"]);
  assert.deepEqual(harness.capturedPlan.conflicts, []);
});

test("unsupported lifecycle short flags fail before discovery", async () => {
  const harness = catalogHarness();
  assert.equal(await runAddCommand(["-z"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /unknown option: -z/i);
});
