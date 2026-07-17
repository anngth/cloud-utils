import assert from "node:assert/strict";
import test from "node:test";
import { runInstallCommand, runStatusCommand } from "../lifecycle-commands.mjs";
import { executeInstallPlan } from "../operations.mjs";

const FRONTEND_PROFILES = {
  version: 1,
  profiles: [{
    name: "frontend",
    sources: [{ source: "a/repo", skills: ["frontend-design"] }],
  }],
};

const actualSkill = (name, source) => ({
  name,
  path: `/repo/.agents/skills/${name}`,
  agents: ["Codex"],
  source,
  provenance: source ? "tracked" : "untracked",
});

function lifecycleHarness({
  profiles = FRONTEND_PROFILES,
  linkedProfiles = ["frontend"],
  installed = new Map(),
  selectedProfiles = [],
  selectedSkills,
  saveLinks = false,
  confirmed = true,
  execution = { ok: true, succeeded: [], failed: [] },
  tty = true,
} = {}) {
  const root = "/repo";
  let stateCalls = 0;
  let writtenProjects;
  let stderr = "";
  let profileSelections = 0;
  let skillSelections = 0;
  let confirmations = 0;
  let executionCalls = 0;
  let capturedPlan;
  const uiCalls = [];
  const projects = linkedProfiles.length
    ? { version: 1, projects: [{ root, profiles: linkedProfiles }] }
    : { version: 1, projects: [] };
  const context = {
    paths: { projectsFile: "/config/projects.json" },
    config: { profiles, projects },
    stdin: { isTTY: tty },
    stdout: { isTTY: tty, write() {} },
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
    selectProfiles: async () => {
      profileSelections += 1;
      return { type: "submit", selected: selectedProfiles };
    },
    selectSkills: async (items) => {
      skillSelections += 1;
      return {
        type: "submit",
        selected: selectedSkills ?? items.map((item) => item.key),
      };
    },
    confirmSaveLinks: async () => saveLinks,
    confirm: async () => { confirmations += 1; return confirmed; },
    executeInstallPlan: async (value) => {
      executionCalls += 1;
      capturedPlan = value;
      return typeof execution === "function" ? execution(value) : execution;
    },
    writeProjects: (_paths, _profiles, document) => { writtenProjects = document; },
  };
  return {
    context,
    uiCalls,
    stderr: () => stderr,
    get stateCalls() { return stateCalls; },
    get writtenProjects() { return writtenProjects; },
    get profileSelections() { return profileSelections; },
    get skillSelections() { return skillSelections; },
    get confirmations() { return confirmations; },
    get executionCalls() { return executionCalls; },
    get capturedPlan() { return capturedPlan; },
  };
}

test("status exits one for missing and zero for extras alone", async () => {
  const missing = lifecycleHarness({ installed: new Map() });
  assert.equal(await runStatusCommand([], missing.context), 1);
  const extras = lifecycleHarness({ installed: new Map([
    ["frontend-design", actualSkill("frontend-design", "a/repo")],
    ["other", actualSkill("other", "x/repo")],
  ]) });
  assert.equal(await runStatusCommand(["frontend"], extras.context), 0);
  assert.equal(extras.uiCalls[0][1].status.extras[0].name, "other");
});

test("status reports mismatch and untracked requirements with contributing profiles", async () => {
  const profiles = {
    version: 1,
    profiles: [
      ...FRONTEND_PROFILES.profiles,
      { name: "quality", sources: [{ source: "a/repo", skills: ["frontend-design", "review"] }] },
    ],
  };
  const harness = lifecycleHarness({
    profiles,
    linkedProfiles: ["frontend", "quality"],
    installed: new Map([
      ["frontend-design", actualSkill("frontend-design", "wrong/repo")],
      ["review", actualSkill("review", null)],
    ]),
  });
  assert.equal(await runStatusCommand([], harness.context), 1);
  const status = harness.uiCalls[0][1].status;
  assert.deepEqual(status.mismatches[0].profiles, ["frontend", "quality"]);
  assert.deepEqual(status.untracked[0].profiles, ["quality"]);
});

test("status without links or explicit profiles fails before state discovery", async () => {
  const harness = lifecycleHarness({ linkedProfiles: [] });
  assert.equal(await runStatusCommand([], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /project link|name profiles/i);
});

test("install --yes without links or explicit profiles fails before state discovery", async () => {
  const harness = lifecycleHarness({ linkedProfiles: [] });
  assert.equal(await runInstallCommand(["--yes"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /project link|name profiles/i);
});

test("noninteractive install without links fails before state discovery", async () => {
  const harness = lifecycleHarness({ linkedProfiles: [], tty: false });
  assert.equal(await runInstallCommand([], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
});

test("install rejects the unsupported all-profiles flag before state discovery", async () => {
  const harness = lifecycleHarness();
  assert.equal(await runInstallCommand(["--all-profiles"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
});

test("interactive one-time install writes links only after complete success", async () => {
  const harness = lifecycleHarness({
    linkedProfiles: [], selectedProfiles: ["frontend"], saveLinks: true,
    execution: { ok: true, succeeded: [], failed: [] },
  });
  assert.equal(await runInstallCommand([], harness.context), 0);
  assert.deepEqual(harness.writtenProjects.projects[0].profiles, ["frontend"]);
});

test("interactive one-time install can remain unlinked", async () => {
  const harness = lifecycleHarness({
    linkedProfiles: [], selectedProfiles: ["frontend"], saveLinks: false,
  });
  assert.equal(await runInstallCommand([], harness.context), 0);
  assert.equal(harness.writtenProjects, undefined);
});

test("partial install failure never writes newly selected links", async () => {
  const harness = lifecycleHarness({
    linkedProfiles: [], selectedProfiles: ["frontend"], saveLinks: true,
    execution: { ok: false, succeeded: [], failed: [{ action: "install", status: 2 }] },
  });
  assert.equal(await runInstallCommand([], harness.context), 1);
  assert.equal(harness.writtenProjects, undefined);
});

test("temporary selection keeps unresolved conflicts and prevents a new link", async () => {
  const upstreamCalls = [];
  const safeKey = JSON.stringify(["a/repo", "safe"]);
  const harness = lifecycleHarness({
    profiles: {
      version: 1,
      profiles: [{
        name: "frontend",
        sources: [{ source: "a/repo", skills: ["safe", "blocked"] }],
      }],
    },
    linkedProfiles: [],
    selectedProfiles: ["frontend"],
    selectedSkills: [safeKey],
    saveLinks: true,
    installed: new Map([["blocked", actualSkill("blocked", "wrong/repo")]]),
    execution: (plan) => executeInstallPlan(plan, {
      runMutation: async (args) => { upstreamCalls.push(args); return 0; },
    }),
  });

  assert.equal(await runInstallCommand([], harness.context), 1);
  assert.deepEqual(upstreamCalls, [["skills", "add", "a/repo", "--skill", "safe"]]);
  assert.deepEqual(harness.capturedPlan.conflicts.map((item) => item.skill), ["blocked"]);
  assert.equal(harness.uiCalls.at(-1)[1].ok, false);
  assert.equal(harness.writtenProjects, undefined);
});

test("dry-run renders a plan without execution or link writes", async () => {
  const harness = lifecycleHarness({ linkedProfiles: ["frontend"], installed: new Map() });
  assert.equal(await runInstallCommand(["--dry-run"], harness.context), 0);
  assert.equal(harness.executionCalls, 0);
  assert.equal(harness.confirmations, 0);
  assert.equal(harness.writtenProjects, undefined);
  assert.equal(harness.uiCalls[0][0], "installPlan");
  assert.equal(harness.uiCalls[0][1].dryRun, true);
});

test("yes skips temporary selection and confirmation and reaches execution", async () => {
  const harness = lifecycleHarness();
  assert.equal(await runInstallCommand(["--yes"], harness.context), 0);
  assert.equal(harness.skillSelections, 0);
  assert.equal(harness.confirmations, 0);
  assert.equal(harness.executionCalls, 1);
});

test("temporary deselection changes only the operation plan", async () => {
  const profiles = {
    version: 1,
    profiles: [{
      name: "frontend",
      sources: [{ source: "a/repo", skills: ["one", "two"] }],
    }],
  };
  const before = structuredClone(profiles);
  const harness = lifecycleHarness({
    profiles,
    selectedSkills: [JSON.stringify(["a/repo", "two"])],
  });
  assert.equal(await runInstallCommand([], harness.context), 0);
  assert.deepEqual(harness.capturedPlan.install.map((item) => item.skill), ["two"]);
  assert.deepEqual(profiles, before);
});

test("already-installed requirements are skipped", async () => {
  const harness = lifecycleHarness({
    installed: new Map([["frontend-design", actualSkill("frontend-design", "a/repo")]]),
  });
  assert.equal(await runInstallCommand(["--yes"], harness.context), 0);
  assert.deepEqual(harness.capturedPlan.install, []);
  assert.deepEqual(harness.capturedPlan.skip.map((item) => item.skill), ["frontend-design"]);
});

test("mismatch and untracked skills remain blocked unless force is explicit", async () => {
  const mismatch = lifecycleHarness({
    installed: new Map([["frontend-design", actualSkill("frontend-design", "wrong/repo")]]),
    execution: { ok: false, succeeded: [], failed: [] },
  });
  assert.equal(await runInstallCommand(["--yes"], mismatch.context), 1);
  assert.deepEqual(mismatch.capturedPlan.conflicts.map((item) => item.skill), ["frontend-design"]);

  const untracked = lifecycleHarness({
    installed: new Map([["frontend-design", actualSkill("frontend-design", null)]]),
  });
  assert.equal(await runInstallCommand(["--yes", "--force"], untracked.context), 0);
  assert.deepEqual(untracked.capturedPlan.replace.map((item) => item.skill), ["frontend-design"]);
});

test("desired-source conflicts block independent installs before state discovery", async () => {
  const harness = lifecycleHarness({
    profiles: {
      version: 1,
      profiles: [
        { name: "a", sources: [{ source: "a/repo", skills: ["review", "safe"] }] },
        { name: "b", sources: [{ source: "b/repo", skills: ["review"] }] },
      ],
    },
    linkedProfiles: ["a", "b"],
  });
  assert.equal(await runInstallCommand(["--yes"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.equal(harness.executionCalls, 0);
});

test("interactive cancellation is a successful no-op", async () => {
  const harness = lifecycleHarness({ confirmed: false });
  assert.equal(await runInstallCommand([], harness.context), 0);
  assert.equal(harness.executionCalls, 0);
  assert.equal(harness.writtenProjects, undefined);
});

test("explicit profiles do not alter links", async () => {
  const harness = lifecycleHarness({ linkedProfiles: [] });
  assert.equal(await runInstallCommand(["frontend", "--yes"], harness.context), 0);
  assert.equal(harness.profileSelections, 0);
  assert.equal(harness.writtenProjects, undefined);
});

test("empty profile install fails before state discovery", async () => {
  const harness = lifecycleHarness({
    profiles: { version: 1, profiles: [{ name: "empty", sources: [] }] },
    linkedProfiles: ["empty"],
  });
  assert.equal(await runInstallCommand([], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /no selected skills|empty/i);
});
