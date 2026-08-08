import assert from "node:assert/strict";
import * as realFs from "node:fs";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { makeSandbox } from "./helpers.mjs";
import { EMPTY_CATALOG } from "../catalog.mjs";
import { upsertSource } from "../catalog.mjs";
import {
  ConfigFileError,
  defaultConfigDir,
  initializeConfig,
  readConfig,
  writeCatalog,
  writeJsonAtomic,
} from "../config.mjs";

test("initializeConfig paths include sourcesFile", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  assert.equal(paths.sourcesFile, sandbox.sourcesFile);
  assert.equal(paths.legacyFile, sandbox.legacyFile);
  assert.equal("skillsFile" in paths, false);
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

test("creates empty catalog when no legacy files exist", (t) => {
  const sandbox = makeSandbox(t, { createCatalog: false, createProfiles: false });
  initializeConfig({ env: sandbox.env });
  assert.deepEqual(JSON.parse(readFileSync(sandbox.sourcesFile, "utf8")), EMPTY_CATALOG);
});

test("migrates profiles.json into sources.json and preserves profiles file", (t) => {
  const profiles = {
    version: 1,
    profiles: [
      {
        name: "frontend",
        sources: [
          { source: "vercel-labs/agent-skills", skills: ["code-review"] },
          { source: "anthropics/skills", skills: [] },
        ],
      },
      {
        name: "review",
        sources: [
          { source: "vercel-labs/agent-skills", skills: ["frontend-design"] },
        ],
      },
    ],
  };
  const sandbox = makeSandbox(t, { createCatalog: false, profiles });
  const profilesBytes = readFileSync(sandbox.profilesFile, "utf8");
  initializeConfig({ env: sandbox.env });
  const { catalog } = readConfig(initializeConfig({ env: sandbox.env }));
  assert.equal(catalog.sources.length, 2);
  assert.deepEqual(
    catalog.sources.find((entry) => entry.source === "vercel-labs/agent-skills").skills,
    ["code-review", "frontend-design"],
  );
  assert.deepEqual(
    catalog.sources.find((entry) => entry.source === "anthropics/skills").skills,
    [],
  );
  assert.equal(readFileSync(sandbox.profilesFile, "utf8"), profilesBytes);
});

test("migrates list.json-only legacy sources with empty skills", (t) => {
  const sandbox = makeSandbox(t, {
    createCatalog: false,
    createProfiles: false,
    legacyList: [{ source: "b/repo" }, { source: "a/repo" }],
  });
  initializeConfig({ env: sandbox.env });
  const { catalog } = readConfig(initializeConfig({ env: sandbox.env }));
  assert.deepEqual(catalog.sources, [
    { source: "a/repo", skills: [] },
    { source: "b/repo", skills: [] },
  ]);
  assert.equal(existsSync(sandbox.legacyFile), true);
});

test("migrates legacy presets and canonicalizes duplicate source identities", (t) => {
  const sandbox = makeSandbox(t, {
    createCatalog: false,
    createProfiles: false,
    legacyList: { presets: [
      { source: "https://github.com/acme/skills.git" },
      { source: "acme/skills" },
    ] },
  });
  initializeConfig({ env: sandbox.env });
  assert.deepEqual(readConfig(initializeConfig({ env: sandbox.env })).catalog.sources, [
    { source: "acme/skills", skills: [] },
  ]);
});

test("only migrates an existing config legacy file", (t) => {
  const sandbox = makeSandbox(t, { createCatalog: false, createProfiles: false });
  const managerDir = `${sandbox.root}/manager`;
  realFs.mkdirSync(managerDir);
  writeFileSync(`${managerDir}/list.json`, '[{"source":"outside/repo"}]\n', "utf8");
  initializeConfig({ env: sandbox.env, managerDir });
  assert.deepEqual(readConfig(initializeConfig({ env: sandbox.env })).catalog, EMPTY_CATALOG);
  assert.equal(existsSync(sandbox.legacyFile), false);
});

test("bootstrap never overwrites existing sources.json bytes", (t) => {
  const sandbox = makeSandbox(t, { createCatalog: false, createProfiles: false });
  const sources = '{\n  "sources": [],\n  "version": 1\n}\n';
  writeFileSync(sandbox.sourcesFile, sources, "utf8");
  initializeConfig({ env: sandbox.env });
  assert.equal(readFileSync(sandbox.sourcesFile, "utf8"), sources);
});

test("profile migration conflict surfaces error without writing sources.json", (t) => {
  const sandbox = makeSandbox(t, {
    createCatalog: false,
    profiles: {
      version: 1,
      profiles: [
        { name: "a", sources: [{ source: "one/repo", skills: ["dup"] }] },
        { name: "b", sources: [{ source: "two/repo", skills: ["dup"] }] },
      ],
    },
  });
  assert.throws(() => initializeConfig({ env: sandbox.env }), ConfigFileError);
  assert.equal(existsSync(sandbox.sourcesFile), false);
});

test("invalid profiles.json during migrate bootstrap throws ConfigFileError without creating sources.json", (t) => {
  const sandbox = makeSandbox(t, {
    createCatalog: false,
    profiles: { version: 1, profiles: [] },
  });
  assert.throws(
    () => initializeConfig({ env: sandbox.env }),
    (error) => error instanceof ConfigFileError && error.filePath === sandbox.profilesFile,
  );
  assert.equal(existsSync(sandbox.sourcesFile), false);
});

test("invalid JSON is byte preserving", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.sourcesFile, "{broken", "utf8");
  assert.throws(() => readConfig(initializeConfig({ env: sandbox.env })), ConfigFileError);
  assert.equal(readFileSync(sandbox.sourcesFile, "utf8"), "{broken");
});

test("invalid legacy JSON leaves sources.json absent", (t) => {
  const sandbox = makeSandbox(t, { createCatalog: false, createProfiles: false });
  writeFileSync(sandbox.legacyFile, "{broken", "utf8");
  assert.throws(() => initializeConfig({ env: sandbox.env }), ConfigFileError);
  assert.equal(readFileSync(sandbox.legacyFile, "utf8"), "{broken");
  assert.equal(existsSync(sandbox.sourcesFile), false);
});

test("writeCatalog round-trips through readConfig", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  let catalog = EMPTY_CATALOG;
  catalog = upsertSource(catalog, "z/repo", ["alpha"]);
  catalog = upsertSource(catalog, "a/repo", ["beta"]);
  writeCatalog(paths, catalog, { pid: 5150 });
  assert.deepEqual(readConfig(paths).catalog, catalog);
});

test("validates catalog before writing", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  assert.throws(
    () => writeCatalog(paths, { version: 1, sources: [{ source: "a", skills: ["dup"] }, { source: "b", skills: ["dup"] }] }),
    /dup/,
  );
  assert.deepEqual(readConfig(paths).catalog, EMPTY_CATALOG);
});

test("writes a JSON document atomically and cleans up its temporary file after a failure", (t) => {
  const sandbox = makeSandbox(t);
  const file = sandbox.sourcesFile;
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
