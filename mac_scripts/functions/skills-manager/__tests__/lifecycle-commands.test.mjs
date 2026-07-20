import assert from "node:assert/strict";
import test from "node:test";
import {
  runInstallCommand,
  runStatusCommand,
  runUninstallCommand,
} from "../lifecycle-commands.mjs";
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

const UNINSTALL_PROFILES = {
  version: 1,
  profiles: [
    {
      name: "frontend",
      sources: [{ source: "a/repo", skills: ["frontend-design", "code-review"] }],
    },
    {
      name: "quality",
      sources: [{ source: "a/repo", skills: ["code-review"] }],
    },
    { name: "empty", sources: [{ source: "a/repo", skills: [] }] },
  ],
};

function makeUninstallHarness({
  profiles = UNINSTALL_PROFILES,
  linkedProfiles = ["frontend", "quality"],
  installed = new Map([
    ["frontend-design", actualSkill("frontend-design", "a/repo")],
    ["code-review", actualSkill("code-review", "a/repo")],
  ]),
  executionOk = true,
  confirmed = true,
} = {}) {
  const root = "/repo";
  const mutationCalls = [];
  let capturedPlan;
  let writtenProjects;
  let projectWrites = 0;
  let stateCalls = 0;
  let confirmations = 0;
  let output = "";
  let errors = "";
  const uiCalls = [];
  const context = {
    paths: { projectsFile: "/config/projects.json" },
    config: {
      profiles,
      projects: linkedProfiles.length
        ? { version: 1, projects: [{ root, profiles: linkedProfiles }] }
        : { version: 1, projects: [] },
    },
    stdin: { isTTY: true },
    stdout: { isTTY: true, write: (value) => { output += value; } },
    stderr: { write: (value) => { errors += value; } },
    ui: {
      error: (message) => { errors += `${message}\n`; },
      warn: (message) => { errors += `${message}\n`; },
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
    writeProjects: (_paths, _profiles, document) => {
      projectWrites += 1;
      writtenProjects = document;
    },
  };
  return {
    context,
    mutationCalls,
    stdout: () => output,
    stderr: () => errors,
    uiCalls,
    get capturedPlan() { return capturedPlan; },
    get writtenProjects() { return writtenProjects; },
    get projectWrites() { return projectWrites; },
    get stateCalls() { return stateCalls; },
    get confirmations() { return confirmations; },
    get removedNames() { return capturedPlan?.remove.map((item) => item.skill) ?? []; },
  };
}

function lifecycleHarness({
  profiles = FRONTEND_PROFILES,
  linkedProfiles = ["frontend"],
  installed = new Map(),
  selectedProfiles = [],
  selectedSkills,
  skillSelectionCancelled = false,
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
  let executionOptions;
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
      if (skillSelectionCancelled) return { type: "cancel", selected: [] };
      return {
        type: "submit",
        selected: selectedSkills ?? items.map((item) => item.key),
      };
    },
    confirmSaveLinks: async () => saveLinks,
    confirm: async () => { confirmations += 1; return confirmed; },
    executeInstallPlan: async (value, options) => {
      executionCalls += 1;
      capturedPlan = value;
      executionOptions = options;
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
    get executionOptions() { return executionOptions; },
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

test("install short flags match yes dry-run and force", async () => {
  const dryRun = lifecycleHarness({ installed: new Map() });
  assert.equal(await runInstallCommand(["-d"], dryRun.context), 0);
  assert.equal(dryRun.executionCalls, 0);
  assert.equal(dryRun.confirmations, 0);
  assert.equal(dryRun.uiCalls[0][1].dryRun, true);

  const yes = lifecycleHarness();
  assert.equal(await runInstallCommand(["-y"], yes.context), 0);
  assert.equal(yes.skillSelections, 0);
  assert.equal(yes.confirmations, 0);
  assert.equal(yes.executionCalls, 1);

  const force = lifecycleHarness({
    installed: new Map([
      ["frontend-design", actualSkill("frontend-design", null)],
    ]),
  });
  assert.equal(await runInstallCommand(["-y", "-f"], force.context), 0);
  assert.deepEqual(force.capturedPlan.replace.map((item) => item.skill), [
    "frontend-design",
  ]);
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

test("desired-source conflict errors redact unsafe persisted source text", async () => {
  const harness = lifecycleHarness({
    profiles: {
      version: 1,
      profiles: [
        { name: "a", sources: [{
          source: "https://user:secret@git.example.com/a/repo?ToKeN=query-secret#fragment-secret",
          skills: ["review"],
        }] },
        { name: "b", sources: [{ source: "b/repo", skills: ["review"] }] },
      ],
    },
    linkedProfiles: ["a", "b"],
  });
  assert.equal(await runInstallCommand(["--yes"], harness.context), 1);
  assert.doesNotMatch(harness.stderr(), /user|secret|token|fragment/i);
  assert.match(harness.stderr(), /https:\/\/git\.example\.com\/a\/repo/);
});

test("install selector hints redact unsafe persisted source text", async () => {
  const harness = lifecycleHarness({
    profiles: {
      version: 1,
      profiles: [{ name: "a", sources: [{
        source: "https://user:secret@git.example.com/a/repo?ToKeN=query-secret#fragment-secret",
        skills: ["review"],
      }] }],
    },
    linkedProfiles: ["a"],
  });
  let items;
  harness.context.selectSkills = async (value) => {
    items = value;
    return { type: "cancel", selected: [] };
  };
  assert.equal(await runInstallCommand([], harness.context), 0);
  assert.equal(items[0].kind, "skill");
  assert.equal(items[0].value, items[0].key);
  assert.equal(items[0].label, "review");
  assert.match(items[0].hint, /https:\/\/git\.example\.com\/a\/repo/);
  assert.doesNotMatch(items[0].hint, /user|secret|token|fragment/i);
});

test("interactive cancellation is a successful no-op", async () => {
  const harness = lifecycleHarness({ confirmed: false });
  let outcome;
  assert.equal(await runInstallCommand([], harness.context, {
    onOutcome: (value) => { outcome = value; },
  }), 0);
  assert.equal(harness.executionCalls, 0);
  assert.equal(harness.writtenProjects, undefined);
  assert.deepEqual(outcome, { type: "cancelled", stage: "confirmation" });
});

test("nested skill selector cancellation has a captured non-completion outcome", async () => {
  const harness = lifecycleHarness({ skillSelectionCancelled: true });
  let outcome;
  assert.equal(await runInstallCommand([], harness.context, {
    onOutcome: (value) => { outcome = value; },
  }), 0);
  assert.equal(harness.executionCalls, 0);
  assert.deepEqual(outcome, { type: "cancelled", stage: "skill-selection" });
});

test("completed install keeps numeric status and captures completion", async () => {
  const harness = lifecycleHarness();
  let outcome;
  assert.equal(await runInstallCommand(["--yes"], harness.context, {
    onOutcome: (value) => { outcome = value; },
  }), 0);
  assert.deepEqual(outcome, { type: "completed", ok: true });
});

test("install and uninstall execute mutations at the canonical project root", async () => {
  const install = lifecycleHarness();
  install.context.cwd = "/repo/subdirectory";
  assert.equal(await runInstallCommand(["--yes"], install.context), 0);
  assert.deepEqual(install.executionOptions, { yes: true, projectRoot: "/repo" });

  const uninstall = makeUninstallHarness({ linkedProfiles: ["frontend"] });
  let uninstallOptions;
  uninstall.context.cwd = "/repo/subdirectory";
  uninstall.context.executeUninstallPlan = async (_plan, options) => {
    uninstallOptions = options;
    return { ok: true, succeeded: [], failed: [] };
  };
  assert.equal(await runUninstallCommand(["frontend", "--yes"], uninstall.context), 0);
  assert.deepEqual(uninstallOptions, { yes: true, projectRoot: "/repo" });
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

test("retains a skill required by a remaining linked profile", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["frontend", "quality"] });
  await runUninstallCommand(["frontend", "--yes"], harness.context);
  assert.deepEqual(harness.removedNames, ["frontend-design"]);
  assert.deepEqual(harness.writtenProjects.projects[0].profiles, ["quality"]);
  assert.deepEqual(harness.capturedPlan.retain[0].profiles, ["quality"]);
});

test("empty linked profile unlinks without upstream removal", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["empty"] });
  assert.equal(await runUninstallCommand(["empty", "--yes"], harness.context), 0);
  assert.deepEqual(harness.mutationCalls, [[]]);
  assert.deepEqual(harness.writtenProjects.projects, []);
});

test("keep-link removes files but leaves the selected profile linked", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["frontend"] });
  assert.equal(await runUninstallCommand(["frontend", "--yes", "--keep-link"], harness.context), 0);
  assert.deepEqual(harness.removedNames, ["code-review", "frontend-design"]);
  assert.equal(harness.projectWrites, 0);
  assert.equal(harness.uiCalls[0][1].keepLink, true);
});

test("uninstall short keep-link preserves project links", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["frontend"] });
  assert.equal(await runUninstallCommand([
    "frontend", "-y", "-l",
  ], harness.context), 0);
  assert.deepEqual(harness.removedNames, ["code-review", "frontend-design"]);
  assert.equal(harness.projectWrites, 0);
  assert.equal(harness.uiCalls[0][1].keepLink, true);
});

test("uninstall failure prevents link removal", async () => {
  const harness = makeUninstallHarness({ executionOk: false, linkedProfiles: ["frontend"] });
  assert.equal(await runUninstallCommand(["frontend", "--yes"], harness.context), 1);
  assert.equal(harness.projectWrites, 0);
  assert.deepEqual(harness.uiCalls.at(-1)[2], { operation: "uninstall" });
});

test("uninstall with no explicit profiles selects and unlinks all linked profiles", async () => {
  const harness = makeUninstallHarness();
  assert.equal(await runUninstallCommand(["--yes"], harness.context), 0);
  assert.deepEqual(harness.removedNames, ["code-review", "frontend-design"]);
  assert.deepEqual(harness.capturedPlan.unlinkProfiles, ["frontend", "quality"]);
  assert.deepEqual(harness.writtenProjects.projects, []);
});

test("explicit unlinked profile uninstalls without changing links", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["quality"] });
  assert.equal(await runUninstallCommand(["frontend", "--yes"], harness.context), 0);
  assert.deepEqual(harness.removedNames, ["frontend-design"]);
  assert.deepEqual(harness.capturedPlan.unlinkProfiles, []);
  assert.equal(harness.projectWrites, 0);
});

test("uninstall mismatches and untracked skills require force", async () => {
  const installed = new Map([
    ["frontend-design", actualSkill("frontend-design", "wrong/repo")],
    ["code-review", actualSkill("code-review", null)],
  ]);
  const blocked = makeUninstallHarness({ linkedProfiles: ["frontend"], installed, executionOk: false });
  assert.equal(await runUninstallCommand(["frontend", "--yes"], blocked.context), 1);
  assert.deepEqual(blocked.capturedPlan.remove, []);
  assert.deepEqual(blocked.capturedPlan.conflicts.map((item) => item.skill), ["code-review", "frontend-design"]);
  assert.equal(blocked.projectWrites, 0);

  const forced = makeUninstallHarness({ linkedProfiles: ["frontend"], installed });
  assert.equal(await runUninstallCommand(["frontend", "--yes", "--force"], forced.context), 0);
  assert.deepEqual(forced.removedNames, ["code-review", "frontend-design"]);
  assert.match(forced.stderr(), /force|mismatch|untracked/i);
});

test("uninstall desired-source conflict blocks discovery and every mutation", async () => {
  const harness = makeUninstallHarness({
    profiles: {
      version: 1,
      profiles: [
        { name: "a", sources: [{ source: "a/repo", skills: ["review", "safe"] }] },
        { name: "b", sources: [{ source: "b/repo", skills: ["review"] }] },
      ],
    },
    linkedProfiles: ["a", "b"],
  });
  assert.equal(await runUninstallCommand(["--yes", "--force"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.deepEqual(harness.mutationCalls, []);
  assert.equal(harness.projectWrites, 0);
});

test("uninstall blocks desired-source conflicts across selected and remaining links", async () => {
  const harness = makeUninstallHarness({
    profiles: {
      version: 1,
      profiles: [
        { name: "a", sources: [{ source: "a/repo", skills: ["review"] }] },
        { name: "b", sources: [{ source: "b/repo", skills: ["review"] }] },
      ],
    },
    linkedProfiles: ["a", "b"],
  });
  assert.equal(await runUninstallCommand(["a", "--yes", "--force"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.deepEqual(harness.mutationCalls, []);
  assert.equal(harness.projectWrites, 0);
  assert.match(harness.stderr(), /conflicting desired skill sources|review|a\/repo|b\/repo/i);
});

test("uninstall dry-run renders without confirmation, execution, or link changes", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["frontend"] });
  assert.equal(await runUninstallCommand(["frontend", "--dry-run"], harness.context), 0);
  assert.deepEqual(harness.mutationCalls, []);
  assert.equal(harness.confirmations, 0);
  assert.equal(harness.projectWrites, 0);
  assert.equal(harness.uiCalls[0][1].dryRun, true);
});

test("uninstall confirmation cancellation is a successful no-op", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["frontend"], confirmed: false });
  assert.equal(await runUninstallCommand(["frontend"], harness.context), 0);
  assert.equal(harness.confirmations, 1);
  assert.deepEqual(harness.mutationCalls, []);
  assert.equal(harness.projectWrites, 0);
});

test("uninstall requires linked or explicitly named profiles", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: [] });
  assert.equal(await runUninstallCommand(["--yes"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /project link|name profiles/i);
});

test("unsupported lifecycle short flags still fail before discovery", async () => {
  const harness = lifecycleHarness();
  assert.equal(await runInstallCommand(["-z"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /unknown option: -z/i);
});
