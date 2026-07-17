import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  InstalledStateError,
  loadInstalledState,
  readProjectProvenance,
} from "../installed-state.mjs";

function makeRoot(t) {
  const root = mkdtempSync(join(tmpdir(), "skm-state-test-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

function writeLock(path, skills) {
  mkdirSync(join(path, ".."), { recursive: true });
  writeFileSync(path, `${JSON.stringify({ version: 1, skills }, null, 2)}\n`, "utf8");
}

function writeProjectLock(root, skills) {
  writeLock(join(root, "skills-lock.json"), skills);
}

function actual(root, name = "review") {
  return {
    name,
    path: join(root, ".agents/skills", name),
    scope: "project",
    agents: ["Codex"],
  };
}

test("combines actual presence with project-local provenance", async (t) => {
  const root = makeRoot(t);
  writeProjectLock(root, {
    review: { source: "https://github.com/acme/skills.git", sourceType: "github" },
  });

  const state = await loadInstalledState({
    projectRoot: root,
    listInstalled: async () => [actual(root)],
  });

  assert.deepEqual(state.get("review"), {
    name: "review",
    path: join(root, ".agents/skills/review"),
    agents: ["Codex"],
    source: "acme/skills",
    provenance: "tracked",
  });
});

test("uses known newer global locks when project-local provenance is absent", (t) => {
  const root = makeRoot(t);
  const stateHome = join(root, "state");
  const lock = join(stateHome, "skills/.skill-lock.json");
  writeLock(lock, { review: { source: "acme/skills", sourceType: "github" } });

  const provenance = readProjectProvenance({
    projectRoot: root,
    env: { XDG_STATE_HOME: stateHome, HOME: join(root, "home") },
  });

  assert.deepEqual(provenance.get("review"), {
    source: "acme/skills",
    sourceType: "github",
    origin: lock,
  });
});

test("project-local provenance wins over newer global lock data", (t) => {
  const root = makeRoot(t);
  const stateHome = join(root, "state");
  writeProjectLock(root, { review: { source: "acme/local", sourceType: "github" } });
  writeLock(join(stateHome, "skills/.skill-lock.json"), {
    review: { source: "acme/global", sourceType: "github" },
  });

  const provenance = readProjectProvenance({
    projectRoot: root,
    env: { XDG_STATE_HOME: stateHome, HOME: join(root, "home") },
  });

  assert.equal(provenance.get("review").source, "acme/local");
});

test("marks actual skills untracked when lock data is absent or invalid", async (t) => {
  const root = makeRoot(t);
  writeFileSync(join(root, "skills-lock.json"), "{broken", "utf8");

  const state = await loadInstalledState({
    projectRoot: root,
    env: {},
    listInstalled: async () => [actual(root)],
  });

  assert.equal(state.get("review").source, null);
  assert.equal(state.get("review").provenance, "untracked");
});

test("ignores entries without a usable explicit source", async (t) => {
  const root = makeRoot(t);
  writeProjectLock(root, {
    review: { source: "   ", sourceType: "github" },
    inferred: { sourceType: "github" },
  });

  const state = await loadInstalledState({
    projectRoot: root,
    env: {},
    listInstalled: async () => [actual(root)],
  });

  assert.deepEqual(state.get("review").source, null);
  assert.equal(state.get("review").provenance, "untracked");
});

test("does not turn a stale lock-only entry into actual presence", async (t) => {
  const root = makeRoot(t);
  writeProjectLock(root, { review: { source: "acme/skills", sourceType: "github" } });

  const state = await loadInstalledState({ projectRoot: root, listInstalled: async () => [] });

  assert.equal(state.has("review"), false);
});

test("marks provenance untracked when same-priority usable locks disagree", async (t) => {
  const root = makeRoot(t);
  const stateHome = join(root, "state");
  const home = join(root, "home");
  writeLock(join(stateHome, "skills/.skill-lock.json"), {
    review: { source: "acme/first", sourceType: "github" },
  });
  writeLock(join(home, ".agents/.skill-lock.json"), {
    review: { source: "acme/second", sourceType: "github" },
  });

  const state = await loadInstalledState({
    projectRoot: root,
    env: { XDG_STATE_HOME: stateHome, HOME: home },
    listInstalled: async () => [actual(root)],
  });

  assert.equal(state.get("review").source, null);
  assert.equal(state.get("review").provenance, "untracked");
});

test("marks provenance untracked when same-priority source metadata disagrees", async (t) => {
  const root = makeRoot(t);
  const stateHome = join(root, "state");
  const home = join(root, "home");
  writeLock(join(stateHome, "skills/.skill-lock.json"), {
    review: { source: "acme/skills", sourceType: "github" },
  });
  writeLock(join(home, ".agents/.skill-lock.json"), {
    review: { source: "acme/skills", sourceType: "local" },
  });

  const state = await loadInstalledState({
    projectRoot: root,
    env: { XDG_STATE_HOME: stateHome, HOME: home },
    listInstalled: async () => [actual(root)],
  });

  assert.equal(state.get("review").source, null);
  assert.equal(state.get("review").provenance, "untracked");
});

test("uses the project .agents lock and HOME fallback only at their documented locations", (t) => {
  const root = makeRoot(t);
  const home = join(root, "home");
  const projectLock = join(root, ".agents/.skill-lock.json");
  const homeLock = join(home, ".agents/.skill-lock.json");
  writeLock(projectLock, { project: { source: "acme/project", sourceType: "github" } });
  writeLock(homeLock, { home: { source: "acme/home", sourceType: "github" } });

  const provenance = readProjectProvenance({ projectRoot: root, env: { HOME: home } });

  assert.equal(provenance.get("project").origin, projectLock);
  assert.equal(provenance.has("home"), true);
  assert.equal(existsSync(join(root, ".skill-lock.json")), false);
});

test("wraps installed-list failures without treating them as untracked state", async (t) => {
  const root = makeRoot(t);
  const cause = new Error("npx failed");

  await assert.rejects(
    loadInstalledState({ projectRoot: root, listInstalled: async () => { throw cause; } }),
    (error) => error instanceof InstalledStateError
      && error.message === `Could not list installed skills in ${root}`
      && error.cause === cause,
  );
});
