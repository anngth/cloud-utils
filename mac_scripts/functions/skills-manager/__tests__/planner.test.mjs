import assert from "node:assert/strict";
import test from "node:test";
import {
  classifyStatus,
  createInstallPlan,
  createUninstallPlan,
  mergeProfileRequirements,
} from "../planner.mjs";

const PROFILES = {
  version: 1,
  profiles: [
    {
      name: "frontend",
      sources: [{ source: "a/repo", skills: ["frontend-design", "code-review"] }],
    },
    {
      name: "quality",
      sources: [{ source: "a/repo", skills: ["code-review", "testing"] }],
    },
    {
      name: "review",
      sources: [{ source: "a/repo", skills: ["code-review"] }],
    },
  ],
};

const CONFLICTING_PROFILES = {
  version: 1,
  profiles: [
    { name: "a", sources: [{ source: "a/repo", skills: ["review"] }] },
    { name: "b", sources: [{ source: "b/repo", skills: ["review"] }] },
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
    { key: '["a/repo","ok"]', source: "a/repo", skill: "ok", profiles: ["p"] },
    { key: '["a/repo","missing"]', source: "a/repo", skill: "missing", profiles: ["p"] },
    { key: '["a/repo","wrong"]', source: "a/repo", skill: "wrong", profiles: ["p"] },
    { key: '["a/repo","unknown"]', source: "a/repo", skill: "unknown", profiles: ["p"] },
  ],
  desiredConflicts: [],
};

test("deduplicates the same pair and records contributing profiles", () => {
  const merged = mergeProfileRequirements(PROFILES, ["frontend", "review"]);
  const shared = merged.requirements.find((item) => item.skill === "code-review");
  assert.deepEqual(shared.profiles, ["frontend", "review"]);
});

test("reports one name required from different sources", () => {
  const merged = mergeProfileRequirements(CONFLICTING_PROFILES, ["a", "b"]);
  assert.deepEqual(merged.desiredConflicts, [{
    skill: "review",
    sources: ["a/repo", "b/repo"],
    profiles: ["a", "b"],
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
  const merged = mergeProfileRequirements(CONFLICTING_PROFILES, ["a", "b"]);
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

test("temporary selected keys filter install candidates without changing status", () => {
  const missing = STATUS.missing[0];
  const plan = createInstallPlan(STATUS, {
    force: false,
    selectedKeys: new Set([missing.key]),
  });
  assert.deepEqual(plan.install.map(name), ["missing"]);
  assert.equal(STATUS.missing.length, 1);
});

test("desired-source conflicts block their mutation while safe installs continue", () => {
  const document = {
    version: 1,
    profiles: [
      ...CONFLICTING_PROFILES.profiles,
      { name: "safe", sources: [{ source: "c/repo", skills: ["testing"] }] },
    ],
  };
  const status = classifyStatus(
    mergeProfileRequirements(document, ["a", "b", "safe"]),
    new Map(),
  );
  const plan = createInstallPlan(status, { force: true });

  assert.deepEqual(plan.install.map(name), ["testing"]);
  assert.deepEqual(plan.replace, []);
  assert.deepEqual(plan.desiredConflicts.map((item) => item.skill), ["review"]);
});

test("uninstall retains requirements from remaining linked profiles", () => {
  const plan = createUninstallPlan({
    selected: mergeProfileRequirements(PROFILES, ["frontend"]),
    remaining: mergeProfileRequirements(PROFILES, ["quality"]),
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
    selected: mergeProfileRequirements(CONFLICTING_PROFILES, ["a"]),
    remaining: mergeProfileRequirements(CONFLICTING_PROFILES, ["b"]),
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
      { key: '["a/repo","wrong"]', source: "a/repo", skill: "wrong", profiles: ["p"] },
      { key: '["a/repo","unknown"]', source: "a/repo", skill: "unknown", profiles: ["p"] },
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
  const selected = mergeProfileRequirements(CONFLICTING_PROFILES, ["a", "b"]);
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
      ...mergeProfileRequirements(CONFLICTING_PROFILES, ["a", "b"]).requirements,
      { key: '["c/repo","safe"]', source: "c/repo", skill: "safe", profiles: ["a"] },
    ],
    desiredConflicts: mergeProfileRequirements(CONFLICTING_PROFILES, ["a", "b"]).desiredConflicts,
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
