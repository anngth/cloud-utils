import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { makeSandbox } from "./helpers.mjs";
import {
  ConfigFileError,
  EMPTY_PROFILES,
  EMPTY_PROJECTS,
  defaultConfigDir,
  initializeConfig,
  readConfig,
  recoverConfigTransaction,
  writeJsonAtomic,
  writeProfiles,
  writeProjects,
} from "../config.mjs";

test("defaultConfigDir uses HOME without os.homedir fallback", () => {
  assert.equal(
    defaultConfigDir({ HOME: "/Users/test" }),
    "/Users/test/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
  );
  assert.equal(
    defaultConfigDir({}),
    "/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
  );
});

test("creates one empty profile and an empty project registry when fresh", (t) => {
  const sandbox = makeSandbox(t, { createProfiles: false, createProjects: false });
  initializeConfig({ env: sandbox.env });
  assert.deepEqual(JSON.parse(readFileSync(sandbox.profilesFile, "utf8")), EMPTY_PROFILES);
  assert.deepEqual(JSON.parse(readFileSync(sandbox.projectsFile, "utf8")), EMPTY_PROJECTS);
});

test("migrates legacy sources without selecting skills or linking a project", (t) => {
  const sandbox = makeSandbox(t, {
    createProfiles: false,
    createProjects: false,
    legacyList: [{ source: "b/repo" }, { source: "a/repo" }],
  });
  initializeConfig({ env: sandbox.env });
  const data = JSON.parse(readFileSync(sandbox.profilesFile, "utf8"));
  assert.deepEqual(data.profiles[0].sources, [
    { source: "a/repo", skills: [] },
    { source: "b/repo", skills: [] },
  ]);
  assert.equal(existsSync(sandbox.legacyFile), true);
  assert.deepEqual(JSON.parse(readFileSync(sandbox.projectsFile, "utf8")), EMPTY_PROJECTS);
});

test("migrates legacy presets and canonicalizes duplicate source identities", (t) => {
  const sandbox = makeSandbox(t, {
    createProfiles: false,
    createProjects: false,
    legacyList: { presets: [
      { source: "https://github.com/acme/skills.git" },
      { source: "acme/skills" },
    ] },
  });
  initializeConfig({ env: sandbox.env });
  assert.deepEqual(readConfig(initializeConfig({ env: sandbox.env })).profiles.profiles[0].sources, [
    { source: "acme/skills", skills: [] },
  ]);
});

test("refuses projects.json without profiles.json", (t) => {
  const sandbox = makeSandbox(t, { createProfiles: false });
  assert.throws(() => initializeConfig({ env: sandbox.env }), /profiles\.json.*missing/i);
});

test("invalid JSON is byte preserving", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.profilesFile, "{broken", "utf8");
  assert.throws(() => readConfig(initializeConfig({ env: sandbox.env })), ConfigFileError);
  assert.equal(readFileSync(sandbox.profilesFile, "utf8"), "{broken");
});

test("invalid legacy JSON leaves both new documents absent", (t) => {
  const sandbox = makeSandbox(t, { createProfiles: false, createProjects: false });
  writeFileSync(sandbox.legacyFile, "{broken", "utf8");
  assert.throws(() => initializeConfig({ env: sandbox.env }), ConfigFileError);
  assert.equal(readFileSync(sandbox.legacyFile, "utf8"), "{broken");
  assert.equal(existsSync(sandbox.profilesFile), false);
  assert.equal(existsSync(sandbox.projectsFile), false);
});

test("returns the legacy file only as a compatibility alias", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  assert.equal(paths.skillsFile, sandbox.legacyFile);
  assert.equal(paths.transactionFile, sandbox.transactionFile);
});

test("refuses to proceed while a transaction file requires recovery", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.transactionFile, "{}\n", "utf8");
  assert.throws(
    () => recoverConfigTransaction(initializeConfig({ env: sandbox.env })),
    ConfigFileError,
  );
});

test("writes a JSON document atomically and cleans up its temporary file after a failure", (t) => {
  const sandbox = makeSandbox(t);
  const file = sandbox.profilesFile;
  const writes = [];
  const fs = {
    writeFileSync(path, value) { writes.push([path, value]); },
    renameSync() { throw new Error("rename failed"); },
    rmSync(path, options) { writes.push([path, options]); },
  };
  assert.throws(() => writeJsonAtomic(file, { version: 1 }, { fs, pid: 42 }), /rename failed/);
  assert.deepEqual(writes, [
    [`${file}.42.tmp`, '{\n  "version": 1\n}\n'],
    [`${file}.42.tmp`, { force: true }],
  ]);
});

test("validates documents before writing profiles and projects", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  writeProfiles(paths, {
    version: 1,
    profiles: [{ name: "z", sources: [] }, { name: "a", sources: [] }],
  });
  writeProjects(paths, readConfig(paths).profiles, {
    version: 1,
    projects: [{ root: "/repo", profiles: ["a"] }],
  });
  assert.deepEqual(readConfig(paths), {
    profiles: { version: 1, profiles: [{ name: "a", sources: [] }, { name: "z", sources: [] }] },
    projects: { version: 1, projects: [{ root: "/repo", profiles: ["a"] }] },
  });
});
