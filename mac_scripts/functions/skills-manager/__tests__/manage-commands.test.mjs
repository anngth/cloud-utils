import assert from "node:assert/strict";
import test from "node:test";
import { runSourceCommand } from "../manage-commands.mjs";

const catalog = (...entries) => ({
  version: 1,
  sources: entries.map(({ source, skills }) => ({ source, skills })),
});

function makeManagementHarness(t, {
  catalog: catalogDocument = { version: 1, sources: [] },
  discover = [],
  selected = [],
  installedState = new Map(),
  projectRoot = "/repo",
} = {}) {
  const upstreamCalls = [];
  const selectionCalls = [];
  const uiCalls = [];
  let writtenCatalog;
  let stdout = "";
  let stderr = "";
  const context = {
    paths: { sourcesFile: "/config/sources.json" },
    config: { catalog: catalogDocument },
    cwd: projectRoot,
    stdin: { isTTY: true },
    stdout: { isTTY: true, write: (value) => { stdout += value; } },
    stderr: { write: (value) => { stderr += value; } },
    ui: {
      error: (message) => { stderr += `${message}\n`; },
      warn: (message) => { stderr += `${message}\n`; },
      sourceChanged: (...args) => uiCalls.push(["sourceChanged", ...args]),
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
    loadInstalledState: async () => installedState,
    writeCatalog: (_paths, document) => { writtenCatalog = document; },
  };
  return {
    context,
    upstreamCalls,
    selectionCalls,
    uiCalls,
    stdout: () => stdout,
    stderr: () => stderr,
    get writtenCatalog() { return writtenCatalog; },
  };
}

test("source add creates catalog entry", async (t) => {
  const harness = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
    selected: ["b"],
  });

  assert.equal(await runSourceCommand(["add", "acme/skills"], harness.context), 0);
  assert.deepEqual(harness.writtenCatalog, catalog({
    source: "acme/skills",
    skills: ["b"],
  }));
  assert.deepEqual(harness.uiCalls.at(-1), [
    "sourceChanged",
    { action: "added", profile: null, source: "acme/skills", skills: ["b"] },
  ]);
});

test("source add rejects an existing catalog source", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog({ source: "acme/skills", skills: ["old"] }),
    discover: [{ name: "new", description: "New" }],
    selected: ["new"],
  });

  assert.equal(await runSourceCommand(["add", "acme/skills"], harness.context), 1);
  assert.match(harness.stderr(), /already exists/i);
  assert.match(harness.stderr(), /skm source edit/i);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.equal(harness.writtenCatalog, undefined);
  assert.deepEqual(harness.uiCalls, []);
});

test("source remove by index", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog(
      { source: "first/repo", skills: ["a"] },
      { source: "second/repo", skills: ["b"] },
    ),
  });

  assert.equal(await runSourceCommand(["remove", "2"], harness.context), 0);
  assert.deepEqual(harness.writtenCatalog, catalog({
    source: "first/repo",
    skills: ["a"],
  }));
  assert.deepEqual(harness.uiCalls.at(-1), [
    "sourceChanged",
    { action: "removed", profile: null, source: "second/repo", skills: [] },
  ]);
});

test("source remove blocked when skill installed from that source", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog({ source: "acme/skills", skills: ["a", "b"] }),
    installedState: new Map([
      ["a", { name: "a", source: "acme/skills", provenance: "tracked", path: "/p/a", agents: [] }],
    ]),
  });

  assert.equal(await runSourceCommand(["remove", "acme/skills"], harness.context), 1);
  assert.match(harness.stderr(), /installed skills/i);
  assert.match(harness.stderr(), /\ba\b/);
  assert.equal(harness.writtenCatalog, undefined);
});

test("source add --no-skills never contacts upstream", async (t) => {
  const harness = makeManagementHarness(t);
  assert.equal(await runSourceCommand(["add", "acme/skills", "--no-skills"], harness.context), 0);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.deepEqual(harness.writtenCatalog, catalog({ source: "acme/skills", skills: [] }));
});

test("source add rejects conflicting selection modes before discovery", async (t) => {
  const harness = makeManagementHarness(t);
  assert.equal(await runSourceCommand(["add", "acme/skills", "--all", "--no-skills"], harness.context), 1);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.equal(harness.writtenCatalog, undefined);
  assert.match(harness.stderr(), /mutually exclusive/i);
});

test("source edit by index updates skills in place", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog(
      { source: "first/repo", skills: ["a"] },
      { source: "second/repo", skills: ["old"] },
    ),
    discover: [
      { name: "old", description: "Old" },
      { name: "new", description: "New" },
    ],
    selected: ["new"],
  });

  assert.equal(await runSourceCommand(["edit", "2"], harness.context), 0);
  assert.deepEqual(harness.selectionCalls[0][0].initial, ["old"]);
  assert.deepEqual(harness.writtenCatalog, catalog(
    { source: "first/repo", skills: ["a"] },
    { source: "second/repo", skills: ["new"] },
  ));
  assert.deepEqual(harness.uiCalls.at(-1), [
    "sourceChanged",
    { action: "edited", profile: null, source: "second/repo", skills: ["new"] },
  ]);
});

test("source edit by source id uses resolveSourceToken", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog({ source: "acme/skills", skills: ["keep"] }),
    discover: [
      { name: "keep", description: "Keep" },
      { name: "extra", description: "Extra" },
    ],
    selected: ["keep", "extra"],
  });

  assert.equal(await runSourceCommand(["edit", "acme/skills"], harness.context), 0);
  assert.deepEqual(harness.writtenCatalog, catalog({
    source: "acme/skills",
    skills: ["keep", "extra"],
  }));
});

test("source edit missing token fails without write", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog({ source: "acme/skills", skills: ["a"] }),
  });
  assert.equal(await runSourceCommand(["edit", "9"], harness.context), 1);
  assert.match(harness.stderr(), /out of range/i);
  assert.equal(harness.writtenCatalog, undefined);
});

test("source edit --no-skills clears skills without discovery", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog({ source: "acme/skills", skills: ["a"] }),
  });
  assert.equal(await runSourceCommand(["edit", "1", "--no-skills"], harness.context), 0);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.deepEqual(harness.writtenCatalog, catalog({ source: "acme/skills", skills: [] }));
  assert.equal(harness.uiCalls.at(-1)[1].action, "edited");
});

test("source add --all rejects when any discovered skill is owned elsewhere", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog(
      { source: "a/one", skills: ["alpha"] },
      { source: "c/three", skills: ["beta"] },
    ),
    discover: [
      { name: "alpha", description: "A" },
      { name: "fresh", description: "F" },
      { name: "beta", description: "B" },
    ],
  });
  assert.equal(await runSourceCommand(["add", "b/two", "--all"], harness.context), 1);
  assert.match(
    harness.stderr(),
    /Skill already in another source: alpha \(a\/one\); beta \(c\/three\)/,
  );
  assert.equal(harness.writtenCatalog, undefined);
  assert.deepEqual(harness.uiCalls, []);
});

test("source edit rejects skills owned by another source and keeps this source's skills", async (t) => {
  const clash = makeManagementHarness(t, {
    catalog: catalog(
      { source: "a/one", skills: ["alpha"] },
      { source: "b/two", skills: ["beta"] },
    ),
    discover: [
      { name: "alpha", description: "A" },
      { name: "beta", description: "B" },
    ],
    selected: ["alpha", "beta"],
  });
  assert.equal(await runSourceCommand(["edit", "2"], clash.context), 1);
  assert.match(clash.stderr(), /Skill already in another source: alpha \(a\/one\)/);
  assert.equal(clash.writtenCatalog, undefined);

  const keep = makeManagementHarness(t, {
    catalog: catalog({ source: "a/one", skills: ["alpha"] }),
    discover: [
      { name: "alpha", description: "A" },
      { name: "gamma", description: "G" },
    ],
    selected: ["alpha", "gamma"],
  });
  assert.equal(await runSourceCommand(["edit", "1"], keep.context), 0);
  assert.deepEqual(keep.writtenCatalog, catalog({
    source: "a/one",
    skills: ["alpha", "gamma"],
  }));
});

test("source add --no-skills does not uniqueness-fail", async (t) => {
  const harness = makeManagementHarness(t, {
    catalog: catalog({ source: "a/one", skills: ["alpha"] }),
  });
  assert.equal(await runSourceCommand(["add", "b/two", "--no-skills"], harness.context), 0);
  assert.deepEqual(harness.writtenCatalog, catalog(
    { source: "a/one", skills: ["alpha"] },
    { source: "b/two", skills: [] },
  ));
});
