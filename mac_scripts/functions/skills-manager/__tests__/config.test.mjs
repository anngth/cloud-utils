import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import * as realFs from "node:fs";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { makeSandbox } from "./helpers.mjs";
import { createProfile } from "../profiles.mjs";
import { linkProjectProfiles } from "../projects.mjs";
import {
  ConfigFileError,
  EMPTY_PROFILES,
  EMPTY_PROJECTS,
  defaultConfigDir,
  initializeConfig,
  readConfig,
  recoverConfigTransaction,
  writeConfigTransaction,
  writeJsonAtomic,
  writeProfiles,
  writeProjects,
} from "../config.mjs";

test("recovers the old pair when the second target rename fails", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const before = readConfig(paths);
  const profiles = createProfile(before.profiles, "frontend");
  const projects = linkProjectProfiles(
    before.projects,
    sandbox.root,
    ["frontend"],
    new Set(["default", "frontend"]),
  );
  let targetRenames = 0;
  const fs = {
    ...realFs,
    renameSync(from, to) {
      if ([paths.profilesFile, paths.projectsFile].includes(to) && ++targetRenames === 2) {
        throw new Error("second rename failed");
      }
      realFs.renameSync(from, to);
    },
  };
  assert.throws(
    () => writeConfigTransaction(paths, { profiles, projects }, { fs, pid: 4242 }),
    /second rename failed/,
  );
  recoverConfigTransaction(paths);
  assert.deepEqual(readConfig(paths), before);
  assert.equal(existsSync(paths.transactionFile), false);
});

test("completes a verified next pair after both targets were renamed", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const beforeProfiles = readFileSync(paths.profilesFile, "utf8");
  const beforeProjects = readFileSync(paths.projectsFile, "utf8");
  const nextProfiles = `${JSON.stringify({
    version: 1,
    profiles: [
      { name: "default", sources: [] },
      { name: "frontend", sources: [] },
    ],
  }, null, 2)}\n`;
  const nextProjects = `${JSON.stringify({
    version: 1,
    projects: [{ root: sandbox.root, profiles: ["frontend"] }],
  }, null, 2)}\n`;
  const hash = (value) => createHash("sha256").update(value).digest("hex");
  const files = [
    {
      target: paths.profilesFile,
      backup: `${paths.profilesFile}.777.bak`,
      next: `${paths.profilesFile}.777.next`,
      beforeHash: hash(beforeProfiles),
      nextHash: hash(nextProfiles),
      before: beforeProfiles,
      after: nextProfiles,
    },
    {
      target: paths.projectsFile,
      backup: `${paths.projectsFile}.777.bak`,
      next: `${paths.projectsFile}.777.next`,
      beforeHash: hash(beforeProjects),
      nextHash: hash(nextProjects),
      before: beforeProjects,
      after: nextProjects,
    },
  ];
  for (const item of files) {
    writeFileSync(item.backup, item.before, "utf8");
    writeFileSync(item.next, item.after, "utf8");
    writeFileSync(item.target, item.after, "utf8");
  }
  writeFileSync(paths.transactionFile, `${JSON.stringify({
    version: 1,
    phase: "targets-written",
    files: files.map(({ target, backup, next, beforeHash, nextHash }) => ({
      target, backup, next, beforeHash, nextHash,
    })),
  }, null, 2)}\n`, "utf8");

  recoverConfigTransaction(paths);

  assert.deepEqual(readConfig(paths), {
    profiles: JSON.parse(nextProfiles),
    projects: JSON.parse(nextProjects),
  });
  assert.equal(existsSync(paths.transactionFile), false);
  for (const item of files) {
    assert.equal(existsSync(item.backup), false);
    assert.equal(existsSync(item.next), false);
  }
});

test("writes and cleans up a complete cross-file transaction", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const before = readConfig(paths);
  const profiles = createProfile(before.profiles, "frontend");
  const projects = linkProjectProfiles(
    before.projects,
    sandbox.root,
    ["frontend"],
    new Set(["default", "frontend"]),
  );

  writeConfigTransaction(paths, { profiles, projects }, { pid: 5150 });

  assert.deepEqual(readConfig(paths), { profiles, projects });
  assert.equal(existsSync(paths.transactionFile), false);
  for (const target of [paths.profilesFile, paths.projectsFile]) {
    assert.equal(existsSync(`${target}.5150.bak`), false);
    assert.equal(existsSync(`${target}.5150.next`), false);
  }
});

test("rolls back when saving the profiles-written phase fails", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const before = readConfig(paths);
  const profiles = createProfile(before.profiles, "frontend");
  const projects = linkProjectProfiles(
    before.projects,
    sandbox.root,
    ["frontend"],
    new Set(["default", "frontend"]),
  );
  let journalRenames = 0;
  const fs = {
    ...realFs,
    renameSync(from, to) {
      if (to === paths.transactionFile && ++journalRenames === 2) {
        throw new Error("phase journal failed");
      }
      realFs.renameSync(from, to);
    },
  };

  assert.throws(
    () => writeConfigTransaction(paths, { profiles, projects }, { fs, pid: 6161 }),
    /phase journal failed/,
  );
  assert.deepEqual(readConfig(paths), before);
  assert.equal(existsSync(paths.transactionFile), false);
});

test("rolls back when saving the targets-written phase fails", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const before = readConfig(paths);
  const profiles = createProfile(before.profiles, "frontend");
  const projects = linkProjectProfiles(
    before.projects,
    sandbox.root,
    ["frontend"],
    new Set(["default", "frontend"]),
  );
  let journalRenames = 0;
  const fs = {
    ...realFs,
    renameSync(from, to) {
      if (to === paths.transactionFile && ++journalRenames === 3) {
        throw new Error("final phase journal failed");
      }
      realFs.renameSync(from, to);
    },
  };

  assert.throws(
    () => writeConfigTransaction(paths, { profiles, projects }, { fs, pid: 7171 }),
    /final phase journal failed/,
  );
  assert.deepEqual(readConfig(paths), before);
  assert.equal(existsSync(paths.transactionFile), false);
});

test("retries cleanup once and rethrows the original cleanup error", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const before = readConfig(paths);
  const profiles = createProfile(before.profiles, "frontend");
  const projects = linkProjectProfiles(
    before.projects,
    sandbox.root,
    ["frontend"],
    new Set(["default", "frontend"]),
  );
  let backupRemovals = 0;
  const fs = {
    ...realFs,
    rmSync(path, options) {
      if (path.endsWith(".bak") && ++backupRemovals === 1) {
        throw new Error("cleanup failed");
      }
      realFs.rmSync(path, options);
    },
  };

  assert.throws(
    () => writeConfigTransaction(paths, { profiles, projects }, { fs, pid: 8181 }),
    /cleanup failed/,
  );
  assert.deepEqual(readConfig(paths), { profiles, projects });
  assert.equal(existsSync(paths.transactionFile), false);
});

test("leaves a verified committed journal recoverable after persistent cleanup errors", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const before = readConfig(paths);
  const profiles = createProfile(before.profiles, "frontend");
  const projects = linkProjectProfiles(
    before.projects,
    sandbox.root,
    ["frontend"],
    new Set(["default", "frontend"]),
  );
  const fs = {
    ...realFs,
    rmSync(path, options) {
      if (path.endsWith(".bak")) throw new Error("persistent cleanup failure");
      realFs.rmSync(path, options);
    },
  };

  assert.throws(
    () => writeConfigTransaction(paths, { profiles, projects }, { fs, pid: 9191 }),
    /persistent cleanup failure/,
  );
  assert.deepEqual(readConfig(paths), { profiles, projects });
  assert.equal(existsSync(paths.transactionFile), true);

  recoverConfigTransaction(paths);

  assert.deepEqual(readConfig(paths), { profiles, projects });
  assert.equal(existsSync(paths.transactionFile), false);
});

test("restores both backups for an incomplete profiles-written transaction", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const beforeProfiles = readFileSync(paths.profilesFile, "utf8");
  const beforeProjects = readFileSync(paths.projectsFile, "utf8");
  const nextProfiles = `${JSON.stringify({
    version: 1,
    profiles: [
      { name: "default", sources: [] },
      { name: "frontend", sources: [] },
    ],
  }, null, 2)}\n`;
  const nextProjects = `${JSON.stringify({
    version: 1,
    projects: [{ root: sandbox.root, profiles: ["frontend"] }],
  }, null, 2)}\n`;
  const hash = (value) => createHash("sha256").update(value).digest("hex");
  const files = [
    [paths.profilesFile, beforeProfiles, nextProfiles],
    [paths.projectsFile, beforeProjects, nextProjects],
  ].map(([target, before, after]) => ({
    target,
    backup: `${target}.2020.bak`,
    next: `${target}.2020.next`,
    beforeHash: hash(before),
    nextHash: hash(after),
    before,
    after,
  }));
  for (const item of files) {
    writeFileSync(item.backup, item.before, "utf8");
    writeFileSync(item.next, item.after, "utf8");
  }
  writeFileSync(files[0].target, files[0].after, "utf8");
  writeFileSync(paths.transactionFile, `${JSON.stringify({
    version: 1,
    phase: "profiles-written",
    files: files.map(({ target, backup, next, beforeHash, nextHash }) => ({
      target, backup, next, beforeHash, nextHash,
    })),
  }, null, 2)}\n`, "utf8");

  recoverConfigTransaction(paths);

  assert.equal(readFileSync(paths.profilesFile, "utf8"), beforeProfiles);
  assert.equal(readFileSync(paths.projectsFile, "utf8"), beforeProjects);
  assert.equal(existsSync(paths.transactionFile), false);
});

test("restores backups when targets-written hashes do not verify", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const beforeProfiles = readFileSync(paths.profilesFile, "utf8");
  const beforeProjects = readFileSync(paths.projectsFile, "utf8");
  const hash = (value) => createHash("sha256").update(value).digest("hex");
  const files = [
    [paths.profilesFile, beforeProfiles],
    [paths.projectsFile, beforeProjects],
  ].map(([target, before]) => ({
    target,
    backup: `${target}.3030.bak`,
    next: `${target}.3030.next`,
    beforeHash: hash(before),
    nextHash: hash("expected next bytes"),
  }));
  for (const item of files) {
    writeFileSync(item.backup, readFileSync(item.target));
    writeFileSync(item.next, "expected next bytes");
  }
  writeFileSync(paths.profilesFile, "unexpected target bytes");
  writeFileSync(paths.projectsFile, "unexpected target bytes");
  writeFileSync(paths.transactionFile, `${JSON.stringify({
    version: 1,
    phase: "targets-written",
    files,
  }, null, 2)}\n`, "utf8");

  recoverConfigTransaction(paths);

  assert.equal(readFileSync(paths.profilesFile, "utf8"), beforeProfiles);
  assert.equal(readFileSync(paths.projectsFile, "utf8"), beforeProjects);
  assert.equal(existsSync(paths.transactionFile), false);
});

test("refuses recovery when a backup checksum does not match", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const hash = (value) => createHash("sha256").update(value).digest("hex");
  const files = [paths.profilesFile, paths.projectsFile].map((target) => ({
    target,
    backup: `${target}.4040.bak`,
    next: `${target}.4040.next`,
    beforeHash: hash(readFileSync(target)),
    nextHash: hash("next"),
  }));
  for (const item of files) {
    writeFileSync(item.backup, "corrupted backup");
    writeFileSync(item.next, "next");
  }
  writeFileSync(paths.transactionFile, `${JSON.stringify({
    version: 1,
    phase: "prepared",
    files,
  }, null, 2)}\n`, "utf8");

  assert.throws(
    () => recoverConfigTransaction(paths),
    new RegExp(`Cannot recover transaction: ${paths.profilesFile}`),
  );
  assert.equal(existsSync(paths.transactionFile), true);
  for (const item of files) {
    assert.equal(existsSync(item.backup), true);
    assert.equal(existsSync(item.next), true);
  }
});

test("validates transaction journal schema and fields before cleanup", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const sentinel = `${sandbox.root}/keep-me`;
  writeFileSync(sentinel, "keep", "utf8");
  const invalidJournals = [
    "{broken",
    JSON.stringify({ version: 2, phase: "prepared", files: [] }),
    JSON.stringify({
      version: 1,
      phase: "prepared",
      files: [
        { target: sentinel, backup: sentinel, next: sentinel, beforeHash: "", nextHash: "hash" },
        { target: sentinel, backup: sentinel, next: sentinel, beforeHash: "hash", nextHash: "hash" },
      ],
    }),
  ];

  for (const journal of invalidJournals) {
    writeFileSync(paths.transactionFile, `${journal}\n`, "utf8");
    assert.throws(() => recoverConfigTransaction(paths), ConfigFileError);
    assert.equal(readFileSync(sentinel, "utf8"), "keep");
  }
});

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

test("only migrates an existing config legacy file", (t) => {
  const sandbox = makeSandbox(t, { createProfiles: false, createProjects: false });
  const managerDir = `${sandbox.root}/manager`;
  mkdirSync(managerDir);
  writeFileSync(`${managerDir}/list.json`, '[{"source":"outside/repo"}]\n', "utf8");
  initializeConfig({ env: sandbox.env, managerDir });
  assert.deepEqual(readConfig(initializeConfig({ env: sandbox.env })).profiles, EMPTY_PROFILES);
  assert.equal(existsSync(sandbox.legacyFile), false);
});

test("bootstrap never overwrites existing profile and project document bytes", (t) => {
  const sandbox = makeSandbox(t);
  const profiles = '{\n  "profiles": [{"sources": [], "name": "default"}],\n  "version": 1\n}\n';
  const projects = '{\n  "projects": [],\n  "version": 1\n}\n';
  writeFileSync(sandbox.profilesFile, profiles, "utf8");
  writeFileSync(sandbox.projectsFile, projects, "utf8");
  initializeConfig({ env: sandbox.env });
  assert.equal(readFileSync(sandbox.profilesFile, "utf8"), profiles);
  assert.equal(readFileSync(sandbox.projectsFile, "utf8"), projects);
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

test("recoverConfigTransaction rejects a pending transaction directly", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.transactionFile, "{}\n", "utf8");
  const paths = {
    transactionFile: sandbox.transactionFile,
  };
  assert.throws(
    () => recoverConfigTransaction(paths),
    ConfigFileError,
  );
});

test("initialization rejects a pending transaction", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.transactionFile, "{}\n", "utf8");
  assert.throws(() => initializeConfig({ env: sandbox.env }), ConfigFileError);
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
