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
  ui.status({
    projectRoot: "/repo/app",
    profileNames: ["frontend", "quality"],
    status: {
      installed: [requirement("a/repo", "ready")],
      missing: [requirement("a/repo", "missing", ["quality"])],
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
});

test("install plan labels dry runs and all operation classes", () => {
  const { stdout, ui } = makeUi();
  ui.installPlan({
    projectRoot: "/repo/app",
    profileNames: ["frontend"],
    dryRun: true,
    plan: {
      install: [requirement("a/repo", "missing")],
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
      remove: [requirement("a/repo", "remove-me")],
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
  assert.match(rendered, /npx skills add a\/repo --skill one --skill two/);
  assert.match(rendered, /npx skills remove blocked --yes/);
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
  assert.match(rendered, /old skill review was removed/i);
  assert.match(rendered, /replacement.*failed/i);
  assert.match(rendered, /npx skills add a\/repo --skill review/);
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
    "│  skm profile create <profile>  Create a profile",
    "│  skm profile rename <old> <new>  Rename a profile",
    "│  skm profile remove <profile> [(-f | --force)]  Remove a profile",
    "│  skm source show <source>  Show available source skills",
    "│  skm project link <profile...>  Link profiles to the current project",
    "│  skm project unlink [profile...]  Unlink profiles from the current project",
    "│  skm project show  Show the current project",
    "│  skm project list  List registered projects",
    "│  skm project remove <project-path>  Remove a project registration",
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
