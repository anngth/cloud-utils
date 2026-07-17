import assert from "node:assert/strict";
import test from "node:test";
import { runDashboard } from "../dashboard.mjs";

function dashboardHarness({
  linkedProfiles = ["frontend"],
  selectedAction = "exit",
  selectedProfiles = [],
  actionCancelled = false,
  profilesCancelled = false,
  saveLinks = false,
  installStatus = 0,
  installOutcome = { type: "completed", ok: installStatus === 0 },
  npxAvailable = true,
} = {}) {
  const root = "/repo";
  const rendered = [];
  const calls = [];
  let writtenProjects;
  const context = {
    config: {
      profiles: {
        version: 1,
        profiles: [
          {
            name: "frontend",
            sources: [{ source: "a/repo", skills: ["frontend-design"] }],
          },
          { name: "review", sources: [] },
        ],
      },
      projects: linkedProfiles.length
        ? { version: 1, projects: [{ root, profiles: linkedProfiles }] }
        : { version: 1, projects: [] },
    },
    paths: { projectsFile: "/config/projects.json" },
    cwd: root,
    resolveProjectRoot: () => root,
    ui: {
      dashboard: (value) => { rendered.push(value); },
      error() {},
      warn() {},
    },
    selectAction: async () => actionCancelled
      ? { type: "cancel", selected: [] }
      : { type: "submit", selected: [selectedAction] },
    selectProfiles: async (items, options) => {
      calls.push(["selectProfiles", items, options]);
      return profilesCancelled
        ? { type: "cancel", selected: [] }
        : { type: "submit", selected: selectedProfiles };
    },
    confirmSaveLinks: async (value) => {
      calls.push(["confirmSaveLinks", value]);
      return saveLinks;
    },
    requireNpx: () => npxAvailable,
    runInstallCommand: async (args, _context, { onOutcome } = {}) => {
      calls.push(["install", args]);
      onOutcome?.(installOutcome);
      return installStatus;
    },
    runStatusCommand: async (args) => { calls.push(["status", args]); return 0; },
    runProjectCommand: async (args) => { calls.push(["project", args]); return 0; },
    runProfileCommand: async (args) => { calls.push(["profile", args]); return 0; },
    writeProjects: (_paths, _profiles, document) => { writtenProjects = document; },
  };
  return {
    context,
    rendered,
    calls,
    get writtenProjects() { return writtenProjects; },
  };
}

test("linked dashboard shows the current project, links, and complete action surface", async () => {
  const harness = dashboardHarness({ linkedProfiles: ["frontend"] });
  assert.equal(await runDashboard(harness.context), 0);
  assert.equal(harness.rendered[0].projectRoot, "/repo");
  assert.deepEqual(harness.rendered[0].linkedProfiles, ["frontend"]);
  assert.deepEqual(harness.rendered[0].actions.map((item) => item.value), [
    "install-linked", "edit-links", "install-once", "status", "manage", "exit",
  ]);
});

test("unlinked dashboard can save links only after successful install", async () => {
  const harness = dashboardHarness({
    linkedProfiles: [],
    selectedAction: "install-linked",
    selectedProfiles: ["frontend"],
    saveLinks: true,
    installStatus: 0,
  });
  assert.equal(await runDashboard(harness.context), 0);
  assert.deepEqual(harness.calls.filter(([name]) => name === "install"), [
    ["install", ["frontend"]],
  ]);
  assert.deepEqual(harness.writtenProjects.projects[0].profiles, ["frontend"]);
});

test("failed install never saves links or asks to save them", async () => {
  const harness = dashboardHarness({
    linkedProfiles: [],
    selectedAction: "install-linked",
    selectedProfiles: ["frontend"],
    saveLinks: true,
    installStatus: 1,
  });
  assert.equal(await runDashboard(harness.context), 1);
  assert.equal(harness.writtenProjects, undefined);
  assert.equal(harness.calls.some(([name]) => name === "confirmSaveLinks"), false);
});

test("nested install selector cancellation cannot write dashboard links", async () => {
  const harness = dashboardHarness({
    linkedProfiles: [],
    selectedAction: "install-linked",
    selectedProfiles: ["frontend"],
    saveLinks: true,
    installStatus: 0,
    installOutcome: { type: "cancelled", stage: "skill-selection" },
  });
  assert.equal(await runDashboard(harness.context), 0);
  assert.equal(harness.writtenProjects, undefined);
  assert.equal(harness.calls.some(([name]) => name === "confirmSaveLinks"), false);
});

test("nested install confirmation cancellation cannot write dashboard links", async () => {
  const harness = dashboardHarness({
    linkedProfiles: [],
    selectedAction: "install-linked",
    selectedProfiles: ["frontend"],
    saveLinks: true,
    installStatus: 0,
    installOutcome: { type: "cancelled", stage: "confirmation" },
  });
  assert.equal(await runDashboard(harness.context), 0);
  assert.equal(harness.writtenProjects, undefined);
  assert.equal(harness.calls.some(([name]) => name === "confirmSaveLinks"), false);
});

test("cancellation at the action or profile selector is a successful no-op", async () => {
  for (const options of [
    { actionCancelled: true },
    { linkedProfiles: [], selectedAction: "install-linked", profilesCancelled: true },
  ]) {
    const harness = dashboardHarness(options);
    assert.equal(await runDashboard(harness.context), 0);
    assert.equal(harness.writtenProjects, undefined);
    assert.equal(harness.calls.some(([name]) => name === "install"), false);
  }
});

test("linked install and status use current links", async () => {
  const install = dashboardHarness({ selectedAction: "install-linked" });
  assert.equal(await runDashboard(install.context), 0);
  assert.deepEqual(install.calls.filter(([name]) => name === "install"), [["install", []]]);

  const status = dashboardHarness({ selectedAction: "status" });
  assert.equal(await runDashboard(status.context), 0);
  assert.deepEqual(status.calls.filter(([name]) => name === "status"), [["status", []]]);
});

test("one-off install selects profiles without writing links", async () => {
  const harness = dashboardHarness({
    selectedAction: "install-once",
    selectedProfiles: ["review"],
  });
  assert.equal(await runDashboard(harness.context), 0);
  assert.deepEqual(harness.calls.filter(([name]) => name === "install"), [
    ["install", ["review"]],
  ]);
  assert.equal(harness.writtenProjects, undefined);
});

test("editing links writes the exact selected profile set and supports unlinking all", async () => {
  const relink = dashboardHarness({
    selectedAction: "edit-links",
    selectedProfiles: ["review"],
  });
  assert.equal(await runDashboard(relink.context), 0);
  assert.deepEqual(relink.writtenProjects.projects, [{ root: "/repo", profiles: ["review"] }]);

  const unlink = dashboardHarness({
    selectedAction: "edit-links",
    selectedProfiles: [],
  });
  assert.equal(await runDashboard(unlink.context), 0);
  assert.deepEqual(unlink.writtenProjects.projects, []);
});

test("manage action opens profile management", async () => {
  const harness = dashboardHarness({ selectedAction: "manage" });
  assert.equal(await runDashboard(harness.context), 0);
  assert.deepEqual(harness.calls.filter(([name]) => name === "profile"), [["profile", ["list"]]]);
});

test("upstream dashboard actions stop before dispatch when npx is unavailable", async () => {
  for (const selectedAction of ["install-linked", "install-once", "status"]) {
    const harness = dashboardHarness({
      selectedAction,
      selectedProfiles: ["frontend"],
      npxAvailable: false,
    });
    assert.equal(await runDashboard(harness.context), 1);
    assert.equal(
      harness.calls.some(([name]) => ["install", "status"].includes(name)),
      false,
    );
  }
});
