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
    "profileList",
    "profileShow",
    "profileChanged",
    "sourceChanged",
    "skillChanged",
    "projectShow",
    "projectList",
    "projectChanged",
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

test("usage documents management families and the non-installing skill boundary", () => {
  const { stdout, ui } = makeUi();
  ui.usage();
  const rendered = stdout.read();
  for (const name of ["profile", "source", "skill", "project", "status", "install", "uninstall"]) {
    assert.match(rendered, new RegExp(name));
  }
  assert.match(rendered, /does not change installed project skills/);
});

test("selector keeps ANSI output and renders selected items", () => {
  const { stdout, ui } = makeUi();
  ui.selector("Choose profiles", {
    sources: ["frontend", "review"],
    cursor: 1,
    selected: new Set([0]),
  }, { mode: "install" });
  assert.match(stdout.read(), /\u001b\[46m/);
  assert.match(stdout.read(), /■.*frontend/);
  assert.match(stdout.read(), /□.*review/);
});
