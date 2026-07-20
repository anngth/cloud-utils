import assert from "node:assert/strict";
import test from "node:test";
import { createUi } from "../ui.mjs";

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
    "dashboard",
    "profileList",
    "profileShow",
    "profileChanged",
    "sourceChanged",
    "skillChanged",
    "projectShow",
    "projectList",
    "projectChanged",
    "status",
    "installPlan",
    "uninstallPlan",
    "executionSummary",
    "confirm",
    "selector",
    "cancelledSelector",
    "error",
    "warn",
    "info",
  ]) assert.equal(typeof ui[name], "function", `${name} renderer`);
});

test("profile list renders source, skill, and linked-project counts", () => {
  const { stdout, ui } = makeUi();
  ui.profileList({
    profiles: [{
      name: "frontend",
      sources: [
        { source: "a/skills", skills: ["lint", "review"] },
        { source: "b/skills", skills: [] },
      ],
    }],
    projects: [
      { root: "/repo/one", profiles: ["frontend"] },
      { root: "/repo/two", profiles: ["frontend"] },
    ],
  });
  const rendered = stdout.read();
  assert.match(rendered, /frontend/);
  assert.match(rendered, /2 sources/);
  assert.match(rendered, /2 skills/);
  assert.match(rendered, /2 linked projects/);
});

test("profile show renders sources, selected skills, and linked projects", () => {
  const { stdout, ui } = makeUi();
  ui.profileShow({
    profile: {
      name: "review",
      sources: [{ source: "acme/skills", skills: ["review-pr", "lint"] }],
    },
    projects: [{ root: "/repo/app", profiles: ["review"] }],
  });
  const rendered = stdout.read();
  for (const text of ["review", "acme/skills", "review-pr", "lint", "/repo/app"]) {
    assert.match(rendered, new RegExp(text.replace("/", "\\/")));
  }
});

test("source and skill result renderers include selected-skill counts", () => {
  const source = makeUi();
  source.ui.sourceChanged({
    action: "added",
    profile: "default",
    source: "acme/skills",
    skills: ["a", "b"],
  });
  assert.match(source.stdout.read(), /2 selected skills/);

  const skill = makeUi();
  skill.ui.skillChanged({
    action: "removed",
    profile: "default",
    source: "acme/skills",
    skills: ["a"],
    missing: [],
  });
  assert.match(skill.stdout.read(), /1 skill/);
  assert.match(skill.stdout.read(), /does not change installed project skills/);
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

test("profile and skill-change lists highlight and separate name-only skills", () => {
  const profile = makeUi();
  profile.ui.profileShow({
    profile: {
      name: "quality",
      sources: [{ source: "obra/superpowers", skills: ["brainstorming", "testing"] }],
    },
    projects: [],
  });
  assert.match(
    stripAnsi(profile.stdout.read()),
    /│      • brainstorming\n│\n│      • testing/,
  );
  assert.ok(profile.stdout.read().includes(paint("92", "brainstorming")));

  const changed = makeUi();
  changed.ui.skillChanged({
    action: "added",
    profile: "quality",
    source: "obra/superpowers",
    skills: ["brainstorming", "testing"],
    missing: [],
  });
  assert.match(stripAnsi(changed.stdout.read()), /■ brainstorming\n│\n│  ■ testing/);
  assert.ok(changed.stdout.read().includes(paint("92", "testing")));
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

test("project renderers show linked profiles and mark stale roots", () => {
  const current = makeUi();
  current.ui.projectShow({ root: "/repo/current", profiles: ["frontend", "review"] });
  assert.match(current.stdout.read(), /\/repo\/current/);
  assert.match(current.stdout.read(), /frontend/);
  assert.match(current.stdout.read(), /review/);

  const list = makeUi();
  list.ui.projectList({
    projects: [
      { root: "/repo/current", profiles: ["frontend"], stale: false },
      { root: "/repo/gone", profiles: ["review"], stale: true },
    ],
  });
  const rendered = list.stdout.read();
  assert.match(rendered, /\/repo\/current/);
  assert.match(rendered, /\/repo\/gone.*stale/i);
});

const requirement = (source, skill, profiles = ["frontend"]) => ({
  key: JSON.stringify([source, skill]),
  source,
  skill,
  profiles,
});

test("status renders every classification and contributing profiles", () => {
  const { stdout, ui } = makeUi();
  const missingA = requirement("a/repo", "missing-a", ["quality"]);
  const missingB = requirement("b/repo", "missing-b", ["frontend"]);
  ui.status({
    projectRoot: "/repo/app",
    profileNames: ["frontend", "quality"],
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
  for (const text of [
    "/repo/app",
    "frontend, quality",
    "Installed",
    "Missing",
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
  assert.ok(rendered.includes(`\u001b[33m■\u001b[39m ${paint("92", "missing-a")}`));
  assert.ok(rendered.includes(`${paint("92", "missing-a")} ${paint("33", "— a/repo — required by quality")}`));
  assert.match(
    stripAnsi(rendered),
    /│  ■ missing-a — a\/repo — required by quality\n│\n│  ■ missing-b — b\/repo — required by frontend/,
  );
  for (const name of ["ready", "missing-a", "wrong-source", "unknown", "other", "ambiguous"]) {
    assert.ok(rendered.includes(paint("92", name)), `${name} is a highlighted skill`);
  }
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
      replace: [requirement("a/repo", "replace-me")],
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
    "Replace",
    "Already installed",
    "Conflict",
    "Extra",
    "missing",
    "replace-me",
    "ready",
    "blocked",
    "other",
  ]) assert.match(rendered, new RegExp(text, "i"));
  assert.ok(rendered.includes(paint("92", "missing")));
  assert.ok(rendered.includes(paint("92", "also-missing")));
  assert.match(stripAnsi(rendered), /■ missing[^\n]*\n│\n│  ■ also-missing/);
});

test("uninstall plan renders removal, retention, absence, conflict, and unlink sections", () => {
  const { stdout, ui } = makeUi();
  ui.uninstallPlan({
    projectRoot: "/repo/app",
    profileNames: ["frontend"],
    dryRun: true,
    force: true,
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
  assert.match(rendered, /force/i);
  assert.ok(rendered.includes(paint("92", "remove-me")));
  assert.ok(rendered.includes(paint("92", "shared")));
  assert.ok(!rendered.includes(paint("92", "frontend")));
  assert.match(stripAnsi(rendered), /■ remove-me[^\n]*\n│\n│  ■ also-remove/);
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
      { action: "replace", source: "c/repo", skills: ["blocked"], status: 5 },
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
  assert.match(rendered, /npx skills remove blocked --yes/);
  assert.equal(occurrences(rendered, "npx skills add a/repo --skill one --skill two"), 1);
  assert.equal(occurrences(rendered, "npx skills remove blocked --yes"), 1);
});

test("execution summary warns when removal succeeded but replacement failed", () => {
  const { stdout, ui } = makeUi();
  ui.executionSummary({
    ok: false,
    succeeded: [],
    failed: [{ action: "install", source: "a/repo", skills: ["review"], status: 7 }],
    replacements: [{
      source: "a/repo", skill: "review", removeStatus: 0, installStatus: 7,
    }],
  });
  const rendered = stdout.read();
  assert.ok(rendered.includes(paint("92", "review")));
  assert.match(stripAnsi(rendered), /review — old version removed; replacement from a\/repo failed \(status 7\)/i);
  assert.match(rendered, /npx skills add a\/repo --skill review/);
  assert.equal(occurrences(rendered, "npx skills add a/repo --skill review"), 1);
});

test("execution summary preserves a failed replacement install batch as one retry", () => {
  const { stdout, ui } = makeUi();
  ui.executionSummary({
    ok: false,
    succeeded: [],
    failed: [{
      action: "install", source: "a/repo", skills: ["review", "testing"], status: 7,
    }],
    replacements: [
      { source: "a/repo", skill: "review", removeStatus: 0, installStatus: 7 },
      { source: "a/repo", skill: "testing", removeStatus: 0, installStatus: 7 },
    ],
  });
  const rendered = stripAnsi(stdout.read());
  assert.equal(occurrences(rendered, "npx skills add a/repo --skill review --skill testing"), 1);
  assert.doesNotMatch(rendered, /npx skills add a\/repo --skill review(?:\r?\n|$)/);
  assert.doesNotMatch(rendered, /npx skills add a\/repo --skill testing(?:\r?\n|$)/);
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

  const sections = ["Lifecycle", "Profiles", "Sources", "Skills", "Projects", "Notes"];
  const sectionIndexes = sections.map((section) => lines.indexOf(`◆  ${section}`));
  assert.ok(sectionIndexes.every((index) => index >= 0), "all help sections are present");
  assert.deepEqual(sectionIndexes, [...sectionIndexes].sort((left, right) => left - right));

  for (const [first, continuation] of [
    [
      "│  skm uninstall [profile...] [(-y | --yes)] [(-f | --force)]",
      "│      [(-d | --dry-run)] [(-l | --keep-link)]  Uninstall selected profile skills",
    ],
    [
      "│  skm source add <source> [(-p | --profile) <profile>]",
      "│      [[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)]  Add a source and select skills",
    ],
    [
      "│  skm skill add <skill...> (-s | --source) <source>",
      "│      (-p | --profile) <profile>  Add skills to a profile source",
    ],
    [
      "│  skm skill remove <skill...> (-s | --source) <source>",
      "│      (-p | --profile) <profile>  Remove skills from a profile source",
    ],
  ]) {
    const index = lines.indexOf(first);
    assert.ok(index >= 0, `missing wrapped signature: ${first}`);
    assert.equal(lines[index + 1], continuation);
  }

  for (const line of [
    "│  skm source edit <source> [(-p | --profile) <profile>]  Edit selected source skills",
    "│  skm source remove <source> [(-p | --profile) <profile>]  Remove a source from a profile",
  ]) assert.ok(lines.includes(line), `missing source signature: ${line}`);

  for (const line of [
    "│  skm  Open interactive dashboard",
    "│  skm (help | -h | --help)  Show this help",
    "│  skm status [profile...]  Compare desired and installed skills",
    "│  skm install [profile...] [(-y | --yes)] [(-f | --force)] [(-d | --dry-run)]  Install selected profile skills",
    "│  skm profile list  List profiles",
    "│  skm profile show <profile>  Show one profile",
    "│  skm profile add <profile>  Create a profile",
    "│  skm profile rename <old> <new>  Rename a profile",
    "│  skm profile remove <profile> [(-f | --force)]  Remove a profile",
    "│  skm source show <source>  Show available source skills",
    "│  skm project link <profile...>  Link profiles to the current project",
    "│  skm project unlink [profile...]  Unlink profiles from the current project",
    "│  skm project show  Show the current project",
    "│  skm project list  List registered projects",
    "│  skm project remove [project-path]  Remove a project registration",
    "│  Profile names omitted from lifecycle commands use current project links.",
    "│  Profile, source, skill, and project commands change configuration only.",
    "│  --force permits linked-profile removal or mismatch/untracked skill changes.",
  ]) assert.ok(lines.includes(line), `missing help line: ${line}`);
});

test("dashboard renders the current project, linked profiles, and actions", () => {
  const { stdout, ui } = makeUi();
  ui.dashboard({
    projectRoot: "/repo/app",
    linkedProfiles: ["frontend", "quality"],
    actions: [
      { value: "install-linked", label: "Install linked profiles" },
      { value: "exit", label: "Exit" },
    ],
  });
  const rendered = stdout.read();
  for (const text of ["/repo/app", "frontend, quality", "Install linked profiles", "Exit"]) {
    assert.match(rendered, new RegExp(text.replace("/", "\\/")));
  }
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
  assert.match(stdout.read(), /\u001b\[46m/);
  assert.match(stdout.read(), /■.*Frontend/);
  assert.match(stdout.read(), /□.*Review/);
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
  assert.ok(skillOutput.includes(paint("92", "brainstorming")));
  assert.ok(skillOutput.includes(paint("92", "testing")));
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
