import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogRequirements,
  classifyStatus,
  createInstallPlan,
  createUninstallPlan,
} from "../planner.mjs";

const FRONTEND_CATALOG = {
  version: 1,
  sources: [{ source: "a/repo", skills: ["frontend-design", "code-review"] }],
};

const QUALITY_CATALOG = {
  version: 1,
  sources: [{ source: "a/repo", skills: ["code-review", "testing"] }],
};

const CONFLICTING_CATALOG = {
  version: 1,
  sources: [
    { source: "a/repo", skills: ["review"] },
    { source: "b/repo", skills: ["review"] },
  ],
};

const CONFLICTING_CATALOG_A_ONLY = {
  version: 1,
  sources: [{ source: "a/repo", skills: ["review"] }],
};

const CONFLICTING_CATALOG_B_ONLY = {
  version: 1,
  sources: [{ source: "b/repo", skills: ["review"] }],
};

const CONFLICTING_WITH_SAFE_CATALOG = {
  version: 1,
  sources: [
    { source: "a/repo", skills: ["review"] },
    { source: "b/repo", skills: ["review"] },
    { source: "c/repo", skills: ["testing"] },
  ],
};

const name = (item) => item.skill;
const actual = (skill, source) => ({
  name: skill,
  path: `/repo/.agents/skills/${skill}`,
  agents: ["Codex"],
  source,
  provenance: source ? "tracked" : "untracked",
});

const MERGED = {
  requirements: [
    { key: '["a/repo","ok"]', source: "a/repo", skill: "ok" },
    { key: '["a/repo","missing"]', source: "a/repo", skill: "missing" },
    { key: '["a/repo","wrong"]', source: "a/repo", skill: "wrong" },
    { key: '["a/repo","unknown"]', source: "a/repo", skill: "unknown" },
  ],
  desiredConflicts: [],
};

test("catalogRequirements unions all catalog skills", () => {
  const merged = catalogRequirements({
    version: 1,
    sources: [
      { source: "a/repo", skills: ["one"] },
      { source: "b/repo", skills: ["two"] },
    ],
  });
  assert.deepEqual(merged.requirements.map((r) => r.skill).sort(), ["one", "two"]);
  assert.equal(merged.desiredConflicts.length, 0);
});

test("catalogRequirements deduplicates the same source and skill pair", () => {
  const merged = catalogRequirements({
    version: 1,
    sources: [
      { source: "a/repo", skills: ["code-review"] },
      { source: "a/repo", skills: ["code-review", "testing"] },
    ],
  });
  const key = '["a/repo","code-review"]';
  const matches = merged.requirements.filter((item) => item.key === key);
  assert.equal(matches.length, 1);
  assert.equal(merged.requirements.length, 2);
  assert.deepEqual(merged.requirements.map((item) => item.skill).sort(), ["code-review", "testing"]);
});

test("catalogRequirements reports one name required from different sources", () => {
  const merged = catalogRequirements(CONFLICTING_CATALOG);
  assert.deepEqual(merged.desiredConflicts, [{
    skill: "review",
    sources: ["a/repo", "b/repo"],
    profiles: [],
  }]);
});

test("classifies installed missing mismatch untracked and extra", () => {
  const result = classifyStatus(MERGED, new Map([
    ["ok", actual("ok", "a/repo")],
    ["wrong", actual("wrong", "other/repo")],
    ["unknown", actual("unknown", null)],
    ["extra", actual("extra", "x/repo")],
  ]));
  assert.deepEqual(result.installed.map(name), ["ok"]);
  assert.deepEqual(result.missing.map(name), ["missing"]);
  assert.deepEqual(result.mismatches.map(name), ["wrong"]);
  assert.deepEqual(result.untracked.map(name), ["unknown"]);
  assert.deepEqual(result.extras.map((item) => item.name), ["extra"]);
});

test("status preserves desired conflicts and skips their ambiguous actual names", () => {
  const merged = catalogRequirements(CONFLICTING_CATALOG);
  const result = classifyStatus(merged, new Map([
    ["review", actual("review", "a/repo")],
  ]));

  assert.equal(result.desiredConflicts, merged.desiredConflicts);
  assert.deepEqual(result.installed, []);
  assert.deepEqual(result.missing, []);
  assert.deepEqual(result.mismatches, []);
  assert.deepEqual(result.untracked, []);
  assert.deepEqual(result.extras, []);
});

const STATUS = classifyStatus(MERGED, new Map([
  ["ok", actual("ok", "a/repo")],
  ["wrong", actual("wrong", "other/repo")],
  ["unknown", actual("unknown", null)],
]));

const INSTALLED = new Map([
  ["frontend-design", actual("frontend-design", "a/repo")],
  ["code-review", actual("code-review", "a/repo")],
]);


test("install selects missing and blocks conflicts without force", () => {
  const plan = createInstallPlan(STATUS, { force: false });
  assert.deepEqual(plan.install.map(name), ["missing"]);
  assert.deepEqual(plan.replace, []);
  assert.deepEqual(plan.conflicts.map(name), ["wrong", "unknown"]);
});

test("install force moves mismatch and untracked entries into replacement", () => {
  const plan = createInstallPlan(STATUS, { force: true });
  assert.deepEqual(plan.replace.map(name), ["wrong", "unknown"]);
  assert.deepEqual(plan.conflicts, []);
});

test("temporary selected keys filter installs without removing non-force conflicts", () => {
  const missing = STATUS.missing[0];
  const desiredConflicts = [{
    skill: "ambiguous",
    sources: ["a/repo", "b/repo"],
    profiles: [],
  }];
  const plan = createInstallPlan({ ...STATUS, desiredConflicts }, {
    force: false,
    selectedKeys: new Set([missing.key]),
  });
  assert.deepEqual(plan.install.map(name), ["missing"]);
  assert.deepEqual(plan.conflicts.map(name), ["wrong", "unknown"]);
  assert.equal(plan.desiredConflicts, desiredConflicts);
  assert.equal(STATUS.missing.length, 1);
});

test("temporary selected keys filter forced replacements", () => {
  const wrong = STATUS.mismatches[0];
  const plan = createInstallPlan(STATUS, {
    force: true,
    selectedKeys: new Set([wrong.key]),
  });
  assert.deepEqual(plan.replace.map(name), ["wrong"]);
  assert.deepEqual(plan.conflicts, []);
});

test("desired-source conflicts block their mutation while safe installs continue", () => {
  const status = classifyStatus(
    catalogRequirements(CONFLICTING_WITH_SAFE_CATALOG),
    new Map(),
  );
  const plan = createInstallPlan(status, { force: true });

  assert.deepEqual(plan.install.map(name), ["testing"]);
  assert.deepEqual(plan.replace, []);
  assert.deepEqual(plan.desiredConflicts.map((item) => item.skill), ["review"]);
});

test("uninstall retains requirements from remaining catalog skills", () => {
  const plan = createUninstallPlan({
    selected: catalogRequirements(FRONTEND_CATALOG),
    remaining: catalogRequirements(QUALITY_CATALOG),
    installedState: INSTALLED,
    force: false,
    linkedSelected: ["frontend"],
  });
  assert.deepEqual(plan.remove.map(name), ["frontend-design"]);
  assert.deepEqual(plan.retain.map(name), ["code-review"]);
  assert.deepEqual(plan.unlinkProfiles, ["frontend"]);
});

test("uninstall retention compares source and skill pair keys", () => {
  const plan = createUninstallPlan({
    selected: catalogRequirements(CONFLICTING_CATALOG_A_ONLY),
    remaining: catalogRequirements(CONFLICTING_CATALOG_B_ONLY),
    installedState: new Map([["review", actual("review", "a/repo")]]),
    force: false,
    linkedSelected: ["a"],
  });

  assert.deepEqual(plan.remove.map(name), ["review"]);
  assert.deepEqual(plan.retain, []);
});

test("uninstall skips mismatched and untracked actual entries unless forced", () => {
  const selected = {
    requirements: [
      { key: '["a/repo","wrong"]', source: "a/repo", skill: "wrong" },
      { key: '["a/repo","unknown"]', source: "a/repo", skill: "unknown" },
    ],
    desiredConflicts: [],
  };
  const installedState = new Map([
    ["wrong", actual("wrong", "other/repo")],
    ["unknown", actual("unknown", null)],
  ]);

  const blocked = createUninstallPlan({
    selected,
    remaining: { requirements: [], desiredConflicts: [] },
    installedState,
    force: false,
    linkedSelected: ["p"],
  });
  const forced = createUninstallPlan({
    selected,
    remaining: { requirements: [], desiredConflicts: [] },
    installedState,
    force: true,
    linkedSelected: ["p"],
  });

  assert.deepEqual(blocked.conflicts.map(name), ["wrong", "unknown"]);
  assert.deepEqual(blocked.remove, []);
  assert.deepEqual(forced.remove.map(name), ["wrong", "unknown"]);
  assert.deepEqual(forced.conflicts, []);
});

test("force never resolves two desired sources for one skill name", () => {
  const selected = catalogRequirements(CONFLICTING_CATALOG);
  const plan = createUninstallPlan({
    selected,
    remaining: { requirements: [], desiredConflicts: [] },
    installedState: new Map(),
    force: true,
    linkedSelected: ["a", "b"],
  });
  assert.equal(plan.desiredConflicts.length, 1);
  assert.deepEqual(plan.remove, []);
});

test("desired-source conflicts do not block independent safe uninstall items", () => {
  const selected = {
    requirements: [
      ...catalogRequirements(CONFLICTING_CATALOG).requirements,
      { key: '["c/repo","safe"]', source: "c/repo", skill: "safe" },
    ],
    desiredConflicts: catalogRequirements(CONFLICTING_CATALOG).desiredConflicts,
  };
  const plan = createUninstallPlan({
    selected,
    remaining: { requirements: [], desiredConflicts: [] },
    installedState: new Map([
      ["review", actual("review", "a/repo")],
      ["safe", actual("safe", "c/repo")],
    ]),
    force: true,
    linkedSelected: ["a", "b"],
  });

  assert.deepEqual(plan.remove.map(name), ["safe"]);
  assert.deepEqual(plan.desiredConflicts.map((item) => item.skill), ["review"]);
});
