import assert from "node:assert/strict";
import test from "node:test";
import {
  buildCatalogSelectorItems,
  createUi,
  groupRequirementsByCatalogSource,
  renderCatalogSelector,
  selectorNameColor,
  SELECTOR_DESCRIPTION_COLOR,
} from "../ui.mjs";
import { requirementKey } from "../planner.mjs";

function memoryStream() {
  let value = "";
  return { write(chunk) { value += chunk; }, read() { return value; } };
}

function makeUi() {
  const stdout = memoryStream();
  const stderr = memoryStream();
  return { stdout, stderr, ui: createUi({ stdout, stderr }) };
}

const paint = (code, text) => `\u001b[${code}m${text}\u001b[39m`;
const stripAnsi = (text) => text.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, "");
const occurrences = (text, fragment) => text.split(fragment).length - 1;

test("management UI exposes the complete renderer surface", () => {
  const { ui } = makeUi();
  for (const name of [
    "usage",
    "sourceChanged",
    "status",
    "installPlan",
    "uninstallPlan",
    "applyPreview",
    "executionSummary",
    "confirm",
    "selector",
    "cancelledSelector",
    "error",
    "warn",
    "info",
  ]) assert.equal(typeof ui[name], "function", `${name} renderer`);
});

test("source result renderers include selected-skill counts", () => {
  const source = makeUi();
  source.ui.sourceChanged({
    action: "added",
    profile: "default",
    source: "acme/skills",
    skills: ["a", "b"],
  });
  assert.match(source.stdout.read(), /2 selected skills/);
});

test("available skill rows highlight only names and separate adjacent records", () => {
  const { stdout, ui } = makeUi();
  ui.sourceChanged({
    action: "shown",
    profile: null,
    source: "obra/superpowers",
    skills: ["brainstorming", "test-driven-development"],
    available: [
      { name: "brainstorming", description: "Explore requirements" },
      { name: "test-driven-development", description: "Test first" },
    ],
  });

  const rendered = stdout.read();
  assert.ok(rendered.includes(`${paint("92", "brainstorming")} ${paint("90", "— Explore requirements")}`));
  assert.equal(occurrences(rendered, "\u001b[92mbrainstorming\u001b[39m"), 1);
  assert.match(
    stripAnsi(rendered),
    /│  ■ brainstorming — Explore requirements\n│\n│  ■ test-driven-development — Test first\n└/,
  );
});

test("a single available skill row has no skill-to-skill separator", () => {
  const { stdout, ui } = makeUi();
  ui.sourceChanged({
    action: "shown",
    profile: null,
    source: "obra/superpowers",
    skills: ["brainstorming"],
    available: [{ name: "brainstorming", description: "Explore requirements" }],
  });

  assert.match(
    stripAnsi(stdout.read()),
    /◆  Available skills\n│  ■ brainstorming — Explore requirements\n└/,
  );
});

test("source renderers never display URL queries, fragments, or credentials", () => {
  const { stdout, ui } = makeUi();
  const unsafe = "https://user:secret@git.example.com/acme/skills?ToKeN=query-secret#fragment-secret";
  ui.sourceChanged({ action: "shown", profile: null, source: unsafe, skills: [] });
  const rendered = stdout.read();
  assert.match(rendered, /https:\/\/git\.example\.com\/acme\/skills/);
  assert.doesNotMatch(rendered, /user|secret|token|fragment/i);
});

test("source renderers fail closed for unsafe GitHub SSH suffixes", () => {
  const { stdout, ui } = makeUi();
  ui.sourceChanged({
    action: "shown",
    profile: null,
    source: "git@github.com:owner/repo@ACCESS_TOKEN=query-secret",
    skills: [],
  });
  const rendered = stdout.read();
  assert.match(rendered, /unsafe source redacted/i);
  assert.doesNotMatch(rendered, /access_token|query-secret/i);
});

test("source renderers preserve safe generic SCP provider identity", () => {
  const { stdout, ui } = makeUi();
  ui.sourceChanged({
    action: "shown",
    profile: null,
    source: "git@gitlab.com:owner/repo.git",
    skills: [],
  });
  const rendered = stdout.read();
  assert.match(rendered, /git@gitlab\.com:owner\/repo\.git/);
  assert.doesNotMatch(rendered, /unsafe source redacted/i);
});

const requirement = (source, skill, profiles = ["frontend"]) => ({
  key: JSON.stringify([source, skill]),
  source,
  skill,
  profiles,
});

test("status renders every classification and contributing profiles", () => {
  const { stdout, ui } = makeUi();
  const catalog = {
    version: 1,
    sources: [
      { source: "a/repo", skills: ["ready", "missing-a", "wrong-source", "unknown"] },
      { source: "b/repo", skills: ["missing-b"] },
      { source: "empty/repo", skills: [] },
    ],
  };
  const missingA = requirement("a/repo", "missing-a", ["quality"]);
  const missingB = requirement("b/repo", "missing-b", ["frontend"]);
  ui.status({
    projectRoot: "/repo/app",
    profileNames: ["frontend", "quality"],
    catalog,
    status: {
      installed: [requirement("a/repo", "ready")],
      missing: [missingA, missingB],
      mismatches: [requirement("a/repo", "wrong-source", ["frontend", "quality"])],
      untracked: [requirement("a/repo", "unknown", ["quality"])],
      extras: [{ name: "other", source: "x/repo", provenance: "tracked" }],
      desiredConflicts: [{
        skill: "ambiguous",
        sources: ["a/repo", "b/repo"],
        profiles: ["frontend", "quality"],
      }],
    },
  });
  const rendered = stdout.read();
  const plain = stripAnsi(rendered);
  assert.match(plain, /\/repo\/app/);
  assert.match(plain, /frontend, quality/);
  assert.match(plain, /1\s+a\/repo/);
  assert.match(plain, /ready/);
  assert.match(plain, /missing-a/);
  assert.match(plain, /wrong-source/);
  assert.match(plain, /unknown/);
  assert.match(plain, /2\s+b\/repo/);
  assert.match(plain, /missing-b/);
  assert.doesNotMatch(plain, /3/);
  assert.doesNotMatch(plain, /empty\/repo/);
  assert.match(rendered, /\u001b\[32m■\u001b\[39m/);
  assert.match(rendered, /\u001b\[90m□\u001b\[39m/);
  assert.match(rendered, /\u001b\[31m▲\u001b\[39m/);
  for (const text of [
    "Source mismatch",
    "Untracked",
    "Extra",
    "Desired-source conflict",
    "wrong-source",
    "a/repo",
    "required by frontend, quality",
    "other",
    "ambiguous",
  ]) assert.match(rendered, new RegExp(text.replace("/", "\\/"), "i"));
  assert.doesNotMatch(plain, /ready — a\/repo/);
  assert.doesNotMatch(plain, /other\n│\s+■ other/);
  for (const name of ["ready", "missing-a", "wrong-source", "unknown", "other", "ambiguous"]) {
    assert.ok(rendered.includes(paint("92", name)), `${name} is a highlighted skill`);
  }
});

test("status omits empty sections and empty Profiles", () => {
  const { stdout, ui } = makeUi();
  ui.status({
    projectRoot: "/repo/app",
    profileNames: [],
    catalog: { version: 1, sources: [{ source: "a/repo", skills: ["ready"] }] },
    status: {
      installed: [requirement("a/repo", "ready")],
      missing: [],
      mismatches: [],
      untracked: [],
      extras: [],
      desiredConflicts: [],
    },
  });
  const plain = stripAnsi(stdout.read());
  assert.match(plain, /Status: \/repo\/app/);
  assert.match(plain, /ready/);
  assert.doesNotMatch(plain, /Profiles:/);
  assert.doesNotMatch(plain, /Missing|Source mismatch|Untracked|Extra|Desired-source conflict|\bNone\b/);
});

test("install plan labels dry runs and all operation classes", () => {
  const { stdout, ui } = makeUi();
  ui.installPlan({
    projectRoot: "/repo/app",
    profileNames: ["frontend"],
    dryRun: true,
    plan: {
      install: [
        requirement("a/repo", "missing"),
        requirement("b/repo", "also-missing", ["quality"]),
      ],
      skip: [requirement("a/repo", "ready")],
      conflicts: [requirement("a/repo", "blocked")],
      extras: [{ name: "other", source: "x/repo" }],
      desiredConflicts: [],
    },
  });
  const rendered = stdout.read();
  for (const text of [
    "DRY RUN",
    "Install",
    "Already installed",
    "Conflict",
    "Extra",
    "missing",
    "ready",
    "blocked",
    "other",
  ]) assert.match(rendered, new RegExp(text, "i"));
  assert.doesNotMatch(rendered, /Replace|replace-me/i);
  assert.ok(rendered.includes(paint("92", "missing")));
  assert.ok(rendered.includes(paint("92", "also-missing")));
  assert.match(stripAnsi(rendered), /■ missing[^\n]*\n│\n│  ■ also-missing/);
});

test("install plan omits empty sections", () => {
  const { stdout, ui } = makeUi();
  ui.installPlan({
    projectRoot: "/repo/app",
    profileNames: [],
    plan: {
      install: [requirement("a/repo", "missing")],
      skip: [],
      conflicts: [],
      extras: [],
      desiredConflicts: [],
    },
  });
  const plain = stripAnsi(stdout.read());
  assert.match(plain, /Install/);
  assert.match(plain, /missing/);
  assert.doesNotMatch(plain, /Already installed|Conflict|Extra|Desired-source conflict|\bNone\b|Profiles:/);
});

test("uninstall plan renders removal, retention, absence, conflict, and unlink sections", () => {
  const { stdout, ui } = makeUi();
  ui.uninstallPlan({
    projectRoot: "/repo/app",
    profileNames: ["frontend"],
    dryRun: true,
    keepLink: false,
    plan: {
      remove: [
        requirement("a/repo", "remove-me"),
        requirement("b/repo", "also-remove", ["quality"]),
      ],
      retain: [requirement("a/repo", "shared", ["quality"])],
      absent: [requirement("a/repo", "gone")],
      conflicts: [requirement("a/repo", "blocked")],
      unlinkProfiles: ["frontend"],
      desiredConflicts: [],
    },
  });
  const rendered = stdout.read();
  for (const text of [
    "DRY RUN",
    "Remove",
    "Keep",
    "required by quality",
    "Already absent",
    "Conflict",
    "Unlink",
    "frontend",
  ]) assert.match(rendered, new RegExp(text, "i"));
  assert.doesNotMatch(rendered, /force/i);
  assert.ok(rendered.includes(paint("92", "remove-me")));
  assert.ok(rendered.includes(paint("92", "shared")));
  assert.ok(!rendered.includes(paint("92", "frontend")));
  assert.match(stripAnsi(rendered), /■ remove-me[^\n]*\n│\n│  ■ also-remove/);
});

test("uninstall plan omits empty sections", () => {
  const { stdout, ui } = makeUi();
  ui.uninstallPlan({
    projectRoot: "/repo/app",
    profileNames: [],
    plan: {
      remove: [requirement("a/repo", "remove-me")],
      retain: [],
      absent: [],
      conflicts: [],
      unlinkProfiles: [],
      desiredConflicts: [],
    },
  });
  const plain = stripAnsi(stdout.read());
  assert.match(plain, /Remove/);
  assert.match(plain, /remove-me/);
  assert.doesNotMatch(plain, /Keep|Already absent|Conflict|Unlink|\bNone\b|Profiles:/);
});

test("uninstall keep-link plan omits the unlink section", () => {
  const { stdout, ui } = makeUi();
  ui.uninstallPlan({
    projectRoot: "/repo/app",
    profileNames: ["frontend"],
    keepLink: true,
    plan: {
      remove: [requirement("a/repo", "remove-me")],
      retain: [], absent: [], conflicts: [],
      unlinkProfiles: ["frontend"], desiredConflicts: [],
    },
  });
  assert.doesNotMatch(stdout.read(), /Unlink/i);
});

test("execution summary renders aggregate results and exact retry batches", () => {
  const { stdout, ui } = makeUi();
  ui.executionSummary({
    ok: false,
    succeeded: [{ action: "install", source: "b/repo", skills: ["three"], status: 0 }],
    failed: [
      { action: "install", source: "a/repo", skills: ["one", "two"], status: 2 },
      { action: "install", source: "c/repo", skills: ["blocked"], status: 5 },
    ],
  });
  const rendered = stdout.read();
  assert.match(rendered, /1 succeeded/i);
  assert.match(rendered, /2 failed/i);
  for (const name of ["three", "one", "two", "blocked"]) {
    assert.ok(rendered.includes(paint("92", name)), `${name} is highlighted`);
  }
  assert.match(stripAnsi(rendered), /■ one — install failed \(status 2\)\n│\n│  ■ two — install failed \(status 2\)/);
  assert.match(rendered, /npx skills add a\/repo --skill one --skill two/);
  assert.match(rendered, /npx skills add c\/repo --skill blocked/);
  assert.equal(occurrences(rendered, "npx skills add a/repo --skill one --skill two"), 1);
  assert.equal(occurrences(rendered, "npx skills add c/repo --skill blocked"), 1);
});

test("execution retry guidance redacts unsafe persisted source text", () => {
  const { stdout, ui } = makeUi();
  ui.executionSummary({
    ok: false,
    succeeded: [],
    failed: [{
      action: "install",
      source: "https://user:secret@git.example.com/acme/skills?ToKeN=query-secret#fragment-secret",
      skills: ["review"],
      status: 7,
    }],
  });
  const rendered = stdout.read();
  assert.match(rendered, /npx skills add https:\/\/git\.example\.com\/acme\/skills --skill review/);
  assert.doesNotMatch(rendered, /user|secret|token|fragment/i);
});

test("uninstall execution summary renders the exact safe retry command", () => {
  const { stdout, ui } = makeUi();
  ui.executionSummary({
    ok: false,
    succeeded: [],
    failed: [{ action: "uninstall", source: null, skills: ["one", "two"], status: 4 }],
  }, { operation: "uninstall" });
  const rendered = stdout.read();
  assert.match(rendered, /Uninstall incomplete/i);
  assert.match(rendered, /npx skills remove one two/);
  assert.doesNotMatch(rendered, /skills add/);
});

test("confirm renders the injected prompt message without reading input", () => {
  const { stdout, ui } = makeUi();
  assert.equal(ui.confirm("Apply this plan?"), undefined);
  assert.match(stdout.read(), /Apply this plan\?/);
});

test("errors and warnings use stderr while info uses stdout", () => {
  const { stdout, stderr, ui } = makeUi();
  ui.error("broken");
  ui.warn("careful");
  ui.info("ready");
  assert.doesNotMatch(stdout.read(), /broken|careful/);
  assert.match(stdout.read(), /ready/);
  assert.match(stderr.read(), /broken/);
  assert.match(stderr.read(), /careful/);
});

test("usage documents every command signature and short flag", () => {
  const { stdout, ui } = makeUi();
  ui.usage();
  const lines = stdout.read()
    .replace(/\u001b\[[0-9;]*m/g, "")
    .split(/\r?\n/)
    .map((line) => line.trimEnd());

  assert.equal(lines.find((line) => line.includes("Usage:")), "◇  Usage: skm [command]");

  const sections = ["Lifecycle", "Catalog", "Notes"];
  const sectionIndexes = sections.map((section) => lines.indexOf(`◆  ${section}`));
  assert.ok(sectionIndexes.every((index) => index >= 0), "all help sections are present");
  assert.deepEqual(sectionIndexes, [...sectionIndexes].sort((left, right) => left - right));

  for (const [first, continuation] of [
    [
      "│  skm add <source|index...> [(-a | --all)]",
      "│      [(-y | --yes)] [(-d | --dry-run)]  Install catalog skills for selected sources",
    ],
    [
      "│  skm remove <source|index...> [(-a | --all)]",
      "│      [(-y | --yes)] [(-d | --dry-run)]  Uninstall catalog skills for selected sources",
    ],
    [
      "│  skm source add <source>",
      "│      [[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)] [(-y | --yes)]  Add a catalog source",
    ],
    [
      "│  skm source edit <source|index>",
      "│      [[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)] [(-y | --yes)]  Update skills for a catalog source",
    ],
  ]) {
    const index = lines.indexOf(first);
    assert.ok(index >= 0, `missing wrapped signature: ${first}`);
    assert.equal(lines[index + 1], continuation);
  }

  for (const line of [
    "│  skm  Open interactive catalog selector",
    "│  skm (help | -h | --help)  Show this help",
    "│  skm status  Compare catalog and installed skills",
    "│  skm source remove <source|index>  Remove a catalog source",
    "│  Source indexes are 1-based, matching the interactive selector and gt backup.",
    "│  source add, source edit, and source remove change the catalog only; use add/remove to change disk.",
  ]) assert.ok(lines.includes(line), `missing help line: ${line}`);
});

test("groupRequirementsByCatalogSource keeps catalog indexes and skips empty sources", () => {
  const catalog = {
    version: 1,
    sources: [
      { source: "a/one", skills: ["a"] },
      { source: "b/two", skills: ["b"] },
      { source: "c/three", skills: ["c"] },
    ],
  };
  const groups = groupRequirementsByCatalogSource([
    requirement("c/three", "c"),
    requirement("a/one", "a"),
  ], catalog);
  assert.deepEqual(
    groups.map((group) => [group.sourceIndex, group.source, group.skills]),
    [[1, "a/one", ["a"]], [3, "c/three", ["c"]]],
  );
});

test("applyPreview shows only Install and Remove grouped by catalog source", () => {
  const { stdout, ui } = makeUi();
  ui.applyPreview({
    heading: "Apply these changes?",
    catalog: {
      version: 1,
      sources: [
        { source: "a/one", skills: ["skill-a", "skill-b"] },
        { source: "b/two", skills: ["skill-c"] },
      ],
    },
    install: [requirement("b/two", "skill-c", [])],
    remove: [requirement("a/one", "skill-b", [])],
    confirmState: {
      items: [
        { value: true, label: "Yes" },
        { value: false, label: "No" },
      ],
      cursor: 0,
      selected: new Set(),
    },
  });
  const plain = stripAnsi(stdout.read());
  assert.match(plain, /Apply these changes\?/);
  assert.match(plain, /Install/);
  assert.match(plain, /2\s+b\/two/);
  assert.match(plain, /skill-c/);
  assert.match(plain, /Remove/);
  assert.match(plain, /1\s+a\/one/);
  assert.match(plain, /skill-b/);
  assert.doesNotMatch(plain, /skill-c — |Profiles:|\/repo|None|Replace|Extra/);
  assert.match(plain, /Yes/);
  assert.match(plain, /No/);
});

test("execution summary uses Changes complete for combined operations", () => {
  const { stdout, ui } = makeUi();
  ui.executionSummary({
    ok: true,
    succeeded: [
      { action: "install", source: "a/one", skills: ["skill-a"], status: 0 },
      { action: "uninstall", source: null, skills: ["skill-b"], status: 0 },
    ],
    failed: [],
  }, { operation: "changes" });
  const plain = stripAnsi(stdout.read());
  assert.match(plain, /Changes complete/);
  assert.doesNotMatch(plain, /Install complete|Uninstall complete/);
  assert.match(plain, /2 succeeded/);
});

test("selectorNameColor applies cursor, selected, and unselected ladder", () => {
  assert.equal(selectorNameColor({ isCursor: true, isSelected: true }), "\u001b[97m");
  assert.equal(selectorNameColor({ isCursor: true, isSelected: false }), "\u001b[97m");
  assert.equal(selectorNameColor({ isCursor: false, isSelected: true }), "\u001b[32m");
  assert.equal(selectorNameColor({ isCursor: false, isSelected: false }), "\u001b[90m");
  assert.match(SELECTOR_DESCRIPTION_COLOR, /\u001b\[2m/);
  assert.match(SELECTOR_DESCRIPTION_COLOR, /\u001b\[90m/);
});

test("renderSelector multi-select uses color ladder and a/c hint", () => {
  let stdout = "";
  const ui = createUi({ stdout: { write: (v) => { stdout += v; } }, stderr: { write() {} } });
  ui.selector("Select skills from demo", {
    items: [
      { kind: "skill", value: "a", label: "alpha", hint: "first" },
      { kind: "skill", value: "b", label: "beta", hint: "second" },
    ],
    cursor: 0,
    selected: new Set([1]),
  }, { mode: "install" });

  assert.match(stdout, /space toggle, a all, c clear, enter to continue, q to quit/);
  assert.match(stdout, new RegExp(`\\u001b\\[97malpha`));
  assert.match(stdout, new RegExp(`\\u001b\\[32mbeta`));
});

test("selector keeps ANSI output and renders selected items", () => {
  const { stdout, ui } = makeUi();
  ui.selector("Choose profiles", {
    items: [
      { value: "frontend", label: "Frontend" },
      { value: "review", label: "Review" },
    ],
    cursor: 1,
    selected: new Set([0]),
  }, { mode: "install" });
  assert.match(stdout.read(), /\u001b\[42m/);
  assert.doesNotMatch(stdout.read(), /\u001b\[46m/);
  assert.match(stdout.read(), /■.*Frontend/);
  assert.match(stdout.read(), /□.*Review/);
});

const actual = (skill, source) => ({
  name: skill,
  path: `/repo/.agents/skills/${skill}`,
  agents: ["Codex"],
  source,
  provenance: source ? "tracked" : "untracked",
});

function catalogSelectorState(catalog, installedState, selectedValues = null) {
  const { items, initial } = buildCatalogSelectorItems(catalog, { installedState });
  const values = selectedValues ?? initial;
  const selected = new Set(items.flatMap((item, index) => (
    values.includes(item.value) ? [index] : []
  )));
  for (const [index, item] of items.entries()) {
    if (item.kind !== "source" || item.childValues.length === 0) continue;
    const allChildren = item.childValues.every((value) => values.includes(value));
    if (allChildren) selected.add(index);
  }
  return { items, selected };
}

test("buildCatalogSelectorItems marks only correctly installed skills in initial", () => {
  const catalog = {
    version: 1,
    sources: [{ source: "a/repo", skills: ["one", "two"] }],
  };
  const installedState = new Map([
    ["one", actual("one", "a/repo")],
    ["two", actual("two", "b/repo")],
  ]);
  const { items, initial } = buildCatalogSelectorItems(catalog, { installedState });
  assert.equal(items[0].kind, "source");
  assert.deepEqual(items[0].childValues, [
    requirementKey("a/repo", "one"),
    requirementKey("a/repo", "two"),
  ]);
  assert.deepEqual(initial, [requirementKey("a/repo", "one")]);
});

test("catalog selector shows source unchecked for partial install and checked for full install", () => {
  const catalog = {
    version: 1,
    sources: [{ source: "a/repo", skills: ["one", "two"] }],
  };
  const partial = makeUi();
  partial.ui.catalogSelector("Choose skills", catalogSelectorState(
    catalog,
    new Map([["one", actual("one", "a/repo")]]),
  ));
  const partialOutput = stripAnsi(partial.stdout.read());
  assert.match(partialOutput, /1\s+□\s+a\/repo/);
  assert.match(partialOutput, /■ one/);
  assert.match(partialOutput, /□ two/);

  const full = makeUi();
  full.ui.catalogSelector("Choose skills", catalogSelectorState(
    catalog,
    new Map([
      ["one", actual("one", "a/repo")],
      ["two", actual("two", "a/repo")],
    ]),
  ));
  const fullOutput = stripAnsi(full.stdout.read());
  assert.match(fullOutput, /1\s+■\s+a\/repo/);
  assert.match(fullOutput, /■ one/);
  assert.match(fullOutput, /■ two/);
});

test("renderCatalogSelector uses layout C spacing and shared colors", () => {
  let stdout = "";
  const sink = { write: (chunk) => { stdout += chunk; } };
  const state = {
    items: [
      {
        kind: "source",
        value: "vercel-labs/agent-skills",
        label: "vercel-labs/agent-skills",
        sourceIndex: 1,
        childValues: ["vercel-labs/agent-skills:code-review", "vercel-labs/agent-skills:frontend-design"],
      },
      { kind: "skill", value: "vercel-labs/agent-skills:code-review", label: "code-review", sourceIndex: 1 },
      {
        kind: "skill",
        value: "vercel-labs/agent-skills:frontend-design",
        label: "frontend-design",
        sourceIndex: 1,
        hint: "design flows",
      },
      {
        kind: "source",
        value: "anthropics/skills",
        label: "anthropics/skills",
        sourceIndex: 2,
        childValues: ["anthropics/skills:brainstorming"],
      },
      { kind: "skill", value: "anthropics/skills:brainstorming", label: "brainstorming", sourceIndex: 2 },
    ],
    cursor: 1,
    selected: new Set([1, 2]),
  };
  renderCatalogSelector(sink, "Choose skills", state);
  const plain = stripAnsi(stdout);
  assert.match(stdout, /space toggle, a all, c clear/);
  assert.match(plain, /1\s+■\s+vercel-labs\/agent-skills/);
  assert.match(plain, /│\s+■ code-review/);
  assert.match(plain, /│\s+■ frontend-design/);
  assert.doesNotMatch(plain, /code-review\n│\n│\s+■ frontend-design/);
  assert.match(plain, /frontend-design design flows\n│\n│\s+2\s+□\s+anthropics\/skills/);
  assert.match(stdout, /\u001b\[92m/);
  assert.match(stdout, /\u001b\[42m/);
  assert.doesNotMatch(stdout, /\u001b\[46m/);
  assert.match(stdout, new RegExp(`${SELECTOR_DESCRIPTION_COLOR.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}design flows`));
});

test("catalog selector numbers sources and indents skills like gt backup", () => {
  const catalog = {
    version: 1,
    sources: [
      { source: "a/repo", skills: ["alpha"] },
      { source: "b/repo", skills: ["beta"] },
    ],
  };
  const { stdout, ui } = makeUi();
  ui.catalogSelector("Choose skills", catalogSelectorState(catalog, new Map()));
  const rendered = stripAnsi(stdout.read());
  assert.match(rendered, /1\s+□\s+a\/repo\n│\s+□ alpha/);
  assert.match(rendered, /alpha\n│\n│\s+2\s+□\s+b\/repo\n│\s+□ beta/);
});

test("skill selectors highlight names and separate rows while profile selectors stay compact", () => {
  const skills = makeUi();
  skills.ui.selector("Choose skills", {
    items: [
      { kind: "skill", value: "brainstorming", label: "brainstorming", hint: "Explore" },
      { kind: "skill", value: "testing", label: "testing", hint: "Verify" },
    ],
    cursor: 1,
    selected: new Set([0]),
  }, { mode: "install" });
  const skillOutput = skills.stdout.read();
  assert.ok(skillOutput.includes(paint("32", "brainstorming")));
  assert.ok(skillOutput.includes(paint("97", "testing")));
  assert.match(stripAnsi(skillOutput), /■ brainstorming Explore\n│\n│  □ testing Verify/);

  const profiles = makeUi();
  profiles.ui.selector("Choose profiles", {
    items: [
      { value: "frontend", label: "Frontend" },
      { value: "review", label: "Review" },
    ],
    cursor: 1,
    selected: new Set([0]),
  }, { mode: "install" });
  const profileOutput = profiles.stdout.read();
  assert.doesNotMatch(stripAnsi(profileOutput), /Frontend\n│\n│  □ Review/);
  assert.ok(!profileOutput.includes(paint("92", "Frontend")));
});
