import assert from "node:assert/strict";
import test from "node:test";
import {
  runProfileCommand,
  runProjectCommand,
  runSkillCommand,
  runSourceCommand,
} from "../manage-commands.mjs";

const profiles = (...names) => ({
  version: 1,
  profiles: names.map((name) => ({ name, sources: [] })),
});

const projects = (...entries) => ({ version: 1, projects: entries });

const profileWithSource = (name, source, skills) => ({
  version: 1,
  profiles: [{ name, sources: [{ source, skills }] }],
});

function makeManagementHarness(t, {
  profiles: profilesDocument = profiles("default"),
  projects: projectsDocument = projects(),
  discover = [],
  selected = [],
  projectRoot = "/repo",
} = {}) {
  const transactions = [];
  const upstreamCalls = [];
  const selectionCalls = [];
  const uiCalls = [];
  let writtenProfiles;
  let writtenProjects;
  let stdout = "";
  let stderr = "";
  const context = {
    paths: { profilesFile: "/config/profiles.json", projectsFile: "/config/projects.json" },
    config: { profiles: profilesDocument, projects: projectsDocument },
    cwd: projectRoot,
    stdin: { isTTY: true },
    stdout: { isTTY: true, write: (value) => { stdout += value; } },
    stderr: { write: (value) => { stderr += value; } },
    ui: {
      error: (message) => { stderr += `${message}\n`; },
      warn: (message) => { stderr += `${message}\n`; },
      profileList: (...args) => uiCalls.push(["profileList", ...args]),
      profileShow: (...args) => uiCalls.push(["profileShow", ...args]),
      profileChanged: (...args) => uiCalls.push(["profileChanged", ...args]),
      sourceChanged: (...args) => uiCalls.push(["sourceChanged", ...args]),
      skillChanged: (...args) => uiCalls.push(["skillChanged", ...args]),
      projectShow: (...args) => uiCalls.push(["projectShow", ...args]),
      projectList: (...args) => uiCalls.push(["projectList", ...args]),
      projectChanged: (...args) => uiCalls.push(["projectChanged", ...args]),
    },
    discoverAvailableSkills: async (source) => {
      upstreamCalls.push(["discover", source]);
      return discover;
    },
    selectItems: async (...args) => {
      selectionCalls.push(args);
      return { type: "submit", selected };
    },
    resolveProjectRoot: () => projectRoot,
    pathExists: () => true,
    writeProfiles: (_paths, document) => { writtenProfiles = document; },
    writeProjects: (_paths, _profiles, document) => { writtenProjects = document; },
    writeConfigTransaction: (_paths, documents) => { transactions.push(documents); },
  };
  return {
    context,
    transactions,
    upstreamCalls,
    selectionCalls,
    uiCalls,
    stdout: () => stdout,
    stderr: () => stderr,
    get writtenProfiles() { return writtenProfiles; },
    get writtenProjects() { return writtenProjects; },
  };
}

test("profile rename updates linked projects through one transaction", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profiles("frontend"),
    projects: projects({ root: "/repo", profiles: ["frontend"] }),
  });
  assert.equal(await runProfileCommand(["rename", "frontend", "web"], harness.context), 0);
  assert.equal(harness.transactions.length, 1);
  assert.deepEqual(harness.transactions[0].profiles.profiles.map((item) => item.name), ["web"]);
  assert.deepEqual(harness.transactions[0].projects.projects[0].profiles, ["web"]);
});

test("profile add creates and persists an empty profile", async (t) => {
  const harness = makeManagementHarness(t);

  assert.equal(await runProfileCommand(["add", "frontend"], harness.context), 0);
  assert.deepEqual(
    harness.writtenProfiles.profiles.map(({ name, sources }) => ({ name, sources })),
    [
      { name: "default", sources: [] },
      { name: "frontend", sources: [] },
    ],
  );
  assert.deepEqual(harness.uiCalls.at(-1), [
    "profileChanged",
    { action: "created", name: "frontend" },
  ]);
});

test("profile create is not retained as an alias", async (t) => {
  const harness = makeManagementHarness(t);

  assert.equal(await runProfileCommand(["create", "frontend"], harness.context), 1);
  assert.match(harness.stderr(), /Unknown profile command: create/);
  assert.equal(harness.writtenProfiles, undefined);
});

test("profile add reports its exact usage for missing or extra names", async (t) => {
  for (const args of [["add"], ["add", "frontend", "extra"]]) {
    const harness = makeManagementHarness(t);

    assert.equal(await runProfileCommand(args, harness.context), 1);
    assert.match(harness.stderr(), /^Usage: skm profile add <profile>$/m);
    assert.equal(harness.writtenProfiles, undefined);
  }
});

test("project link changes config without invoking upstream", async (t) => {
  const harness = makeManagementHarness(t, { profiles: profiles("frontend") });
  assert.equal(await runProjectCommand(["link", "frontend"], harness.context), 0);
  assert.deepEqual(harness.writtenProjects.projects[0].profiles, ["frontend"]);
  assert.equal(harness.upstreamCalls.length, 0);
});

test("project remove without a path removes the resolved current project", async (t) => {
  const harness = makeManagementHarness(t, {
    projects: projects({ root: "/repo", profiles: ["default"] }),
  });
  harness.context.cwd = "/repo/packages/app";
  harness.context.resolveProjectRoot = () => "/repo";

  assert.equal(await runProjectCommand(["remove"], harness.context), 0);
  assert.deepEqual(harness.writtenProjects, projects());
  assert.deepEqual(harness.uiCalls.at(-1), [
    "projectChanged",
    { action: "removed", root: "/repo", profiles: [] },
  ]);
  assert.equal(harness.upstreamCalls.length, 0);
});

test("project remove resolves explicit absolute and relative paths", async (t) => {
  for (const { args, cwd, root } of [
    { args: ["remove", "/repo/absolute"], cwd: "/repo/nested", root: "/repo/absolute" },
    { args: ["remove", "../relative"], cwd: "/repo/nested", root: "/repo/relative" },
  ]) {
    const harness = makeManagementHarness(t, {
      projects: projects({ root, profiles: ["default"] }),
    });
    harness.context.cwd = cwd;

    assert.equal(await runProjectCommand(args, harness.context), 0);
    assert.deepEqual(harness.writtenProjects, projects());
    assert.deepEqual(harness.uiCalls.at(-1), [
      "projectChanged",
      { action: "removed", root, profiles: [] },
    ]);
    assert.equal(harness.upstreamCalls.length, 0);
  }
});

test("project remove rejects two paths without writing", async (t) => {
  const harness = makeManagementHarness(t, {
    projects: projects({ root: "/one", profiles: ["default"] }),
  });

  assert.equal(await runProjectCommand(["remove", "/one", "/two"], harness.context), 1);
  assert.match(harness.stderr(), /Usage: skm project remove \[project-path\]/);
  assert.equal(harness.writtenProjects, undefined);
  assert.equal(harness.upstreamCalls.length, 0);
});

test("profile remove blocks linked use and force unlinks in one transaction", async (t) => {
  const blocked = makeManagementHarness(t, {
    profiles: profiles("default", "frontend"),
    projects: projects({ root: "/repo", profiles: ["frontend"] }),
  });
  assert.equal(await runProfileCommand(["remove", "frontend"], blocked.context), 1);
  assert.equal(blocked.transactions.length, 0);

  const forced = makeManagementHarness(t, {
    profiles: profiles("default", "frontend"),
    projects: projects({ root: "/repo", profiles: ["frontend"] }),
  });
  assert.equal(await runProfileCommand(["remove", "frontend", "--force"], forced.context), 0);
  assert.deepEqual(forced.transactions[0], {
    profiles: profiles("default"),
    projects: projects(),
  });
});

test("profile remove never deletes the final profile", async (t) => {
  const harness = makeManagementHarness(t, { profiles: profiles("only") });
  assert.equal(await runProfileCommand(["remove", "only", "--force"], harness.context), 1);
  assert.equal(harness.transactions.length, 0);
});

test("final-profile protection takes precedence over linked-use handling", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profiles("only"),
    projects: projects({ root: "/repo", profiles: ["only"] }),
  });
  assert.equal(await runProfileCommand(["remove", "only"], harness.context), 1);
  assert.match(harness.stderr(), /final profile/i);
  assert.equal(harness.transactions.length, 0);
});

test("source add snapshots selected discovery records", async (t) => {
  const harness = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
    selected: ["b"],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "--profile", "default",
  ], harness.context), 0);
  assert.ok(harness.selectionCalls[0][0].items.every((item) => item.kind === "skill"));
  assert.deepEqual(
    harness.selectionCalls[0][0].items.map(({ value, label, hint }) => ({ value, label, hint })),
    [
      { value: "a", label: "a", hint: "A" },
      { value: "b", label: "b", hint: "B" },
    ],
  );
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources, [{
    source: "acme/skills",
    skills: ["b"],
  }]);
});

test("source add --no-skills never contacts upstream", async (t) => {
  const harness = makeManagementHarness(t);
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "--no-skills",
  ], harness.context), 0);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.deepEqual(harness.writtenProfiles, profileWithSource("default", "acme/skills", []));
});

test("source add rejects unsafe GitHub shorthand before persistence", async (t) => {
  for (const source of [
    "owner/repo?ToKeN=query-secret",
    "owner/repo#fragment-secret",
    "git@github.com:owner/repo@ACCESS_TOKEN=query-secret",
  ]) {
    const harness = makeManagementHarness(t);
    assert.equal(await runSourceCommand([
      "add", source, "-p", "default", "--no-skills",
    ], harness.context), 1);
    assert.equal(harness.writtenProfiles, undefined);
    assert.doesNotMatch(harness.stderr(), /token|secret|fragment|query/i);
  }
});

test("source add persists generic SCP identity without GitHub reclassification", async (t) => {
  for (const [source, canonical] of [
    ["git@gitlab.com:owner/repo.git", "git@gitlab.com:owner/repo"],
    ["git@bitbucket.org:workspace/repo.git", "git@bitbucket.org:workspace/repo"],
  ]) {
    const harness = makeManagementHarness(t);
    assert.equal(await runSourceCommand([
      "add", source, "-p", "default", "--no-skills",
    ], harness.context), 0);
    assert.deepEqual(harness.writtenProfiles.profiles[0].sources, [{
      source: canonical,
      skills: [],
    }]);
  }
});

test("source add --all snapshots every discovered name without a wildcard", async (t) => {
  const harness = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "--all",
  ], harness.context), 0);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources[0].skills, ["a", "b"]);
  assert.doesNotMatch(JSON.stringify(harness.writtenProfiles), /"\*"/);
});

test("skill add verifies every requested name before writing once", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profileWithSource("default", "acme/skills", []),
    discover: [{ name: "a", description: "A" }],
  });
  assert.equal(await runSkillCommand([
    "add", "a", "--source", "acme/skills", "--profile", "default",
  ], harness.context), 0);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources[0].skills, ["a"]);
});

test("source edit retains an unavailable saved skill unless deselected", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profileWithSource("default", "acme/skills", ["old", "current"]),
    discover: [{ name: "current", description: "Current" }],
    selected: ["old", "current"],
  });
  assert.equal(await runSourceCommand([
    "edit", "acme/skills", "-p", "default",
  ], harness.context), 0);
  const editItems = harness.selectionCalls[0][0].items;
  assert.deepEqual(editItems.map(({ kind, value, hint }) => ({ kind, value, hint })), [
    { kind: "skill", value: "old", hint: "saved; unavailable upstream" },
    { kind: "skill", value: "current", hint: "Current" },
  ]);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources[0].skills, ["old", "current"]);
});

test("saved absolute local sources remain removable after the path disappears", async (t) => {
  const source = "/definitely/missing/skm-source";
  const harness = makeManagementHarness(t, {
    profiles: profileWithSource("default", source, ["old"]),
  });
  assert.equal(await runSourceCommand([
    "remove", source, "-p", "default",
  ], harness.context), 0);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources, []);
});

test("saved absolute local sources remain editable without re-canonicalizing the path", async (t) => {
  const source = "/definitely/missing/skm-source";
  const harness = makeManagementHarness(t, {
    profiles: profileWithSource("default", source, ["old"]),
    discover: [{ name: "current", description: "Current" }],
    selected: ["old", "current"],
  });
  assert.equal(await runSourceCommand([
    "edit", source, "-p", "default",
  ], harness.context), 0);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources[0], {
    source,
    skills: ["old", "current"],
  });
});

test("discovery failure leaves the complete profile document unwritten", async (t) => {
  const harness = makeManagementHarness(t);
  harness.context.discoverAvailableSkills = async () => { throw new Error("unparseable output"); };
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default",
  ], harness.context), 1);
  assert.equal(harness.writtenProfiles, undefined);
});

test("source add rejects conflicting selection modes before discovery", async (t) => {
  const harness = makeManagementHarness(t);
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "--all", "--no-skills",
  ], harness.context), 1);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.equal(harness.writtenProfiles, undefined);
});

test("management commands reject unknown flags before writes", async (t) => {
  const harness = makeManagementHarness(t);
  assert.equal(await runProfileCommand(["add", "web", "--wat"], harness.context), 1);
  assert.match(harness.stderr(), /Unknown option: --wat/);
  assert.equal(harness.writtenProfiles, undefined);
});

test("source command usage shows profile selection as optional", async (t) => {
  for (const action of ["add", "edit", "remove"]) {
    const harness = makeManagementHarness(t);
    assert.equal(await runSourceCommand([action], harness.context), 1);
    assert.ok(harness.stderr().includes(
      `Usage: skm source ${action} <source> [(-p | --profile) <profile>]`,
    ));
  }
});

test("profile and project read commands route complete display state", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profileWithSource("default", "acme/skills", ["a"]),
    projects: projects({ root: "/repo", profiles: ["default"] }),
  });
  assert.equal(await runProfileCommand(["list"], harness.context), 0);
  assert.equal(await runProfileCommand(["show", "default"], harness.context), 0);
  assert.equal(await runProjectCommand(["show"], harness.context), 0);
  assert.equal(await runProjectCommand(["list"], harness.context), 0);
  assert.deepEqual(harness.uiCalls.map(([name]) => name), [
    "profileList", "profileShow", "projectShow", "projectList",
  ]);
});

test("project unlink without names requires a TTY", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profiles("default"),
    projects: projects({ root: "/repo", profiles: ["default"] }),
  });
  harness.context.stdin.isTTY = false;
  assert.equal(await runProjectCommand(["unlink"], harness.context), 1);
  assert.equal(harness.writtenProjects, undefined);
});

test("skill verification failure does not partially write", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profileWithSource("default", "acme/skills", []),
    discover: [{ name: "a", description: "A" }],
  });
  assert.equal(await runSkillCommand([
    "add", "a", "missing", "--source", "acme/skills", "--profile", "default",
  ], harness.context), 1);
  assert.equal(harness.writtenProfiles, undefined);
});

test("invalid explicit source add --skill verification preserves input bytes and never writes", async (t) => {
  const document = profiles("default");
  const before = JSON.stringify(document);
  const harness = makeManagementHarness(t, {
    profiles: document,
    discover: [{ name: "available", description: "Available" }],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "--profile", "default",
    "--skill", "available", "--skill", "missing",
  ], harness.context), 1);
  assert.equal(JSON.stringify(document), before);
  assert.equal(harness.writtenProfiles, undefined);
});

test("skill add rejects an unsaved source before discovery", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profiles("default"),
    discover: [{ name: "a", description: "A" }],
  });
  assert.equal(await runSkillCommand([
    "add", "a", "--source", "acme/skills", "--profile", "default",
  ], harness.context), 1);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.equal(harness.writtenProfiles, undefined);
});

test("management short value flags match their long forms", async (t) => {
  const source = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "-k", "a", "-k", "b",
  ], source.context), 0);
  assert.deepEqual(source.writtenProfiles, profileWithSource(
    "default", "acme/skills", ["a", "b"],
  ));

  const skill = makeManagementHarness(t, {
    profiles: profileWithSource("default", "acme/skills", ["a"]),
  });
  assert.equal(await runSkillCommand([
    "remove", "a", "-s", "acme/skills", "-p", "default",
  ], skill.context), 0);
  assert.deepEqual(skill.writtenProfiles, profileWithSource("default", "acme/skills", []));
});

test("management short boolean flags match their long forms", async (t) => {
  const all = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "-a",
  ], all.context), 0);
  assert.deepEqual(all.writtenProfiles, profileWithSource(
    "default", "acme/skills", ["a", "b"],
  ));

  const none = makeManagementHarness(t);
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "-n",
  ], none.context), 0);
  assert.equal(none.upstreamCalls.length, 0);
  assert.deepEqual(none.writtenProfiles, profileWithSource("default", "acme/skills", []));

  const forced = makeManagementHarness(t, {
    profiles: profiles("default", "frontend"),
    projects: projects({ root: "/repo", profiles: ["frontend"] }),
  });
  assert.equal(await runProfileCommand(["remove", "frontend", "-f"], forced.context), 0);
  assert.deepEqual(forced.transactions[0], {
    profiles: profiles("default"),
    projects: projects(),
  });
});

test("mixed short and long source selection modes remain mutually exclusive", async (t) => {
  const harness = makeManagementHarness(t);
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "-k", "a", "--all",
  ], harness.context), 1);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.equal(harness.writtenProfiles, undefined);
  assert.match(harness.stderr(), /mutually exclusive/i);
});
