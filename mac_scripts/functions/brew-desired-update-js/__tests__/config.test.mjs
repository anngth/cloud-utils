import assert from "node:assert/strict";
import test from "node:test";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import {
  defaultConfigDir,
  resolveBudPaths,
  isValidDesiredDocument,
  readDesiredDocument,
  writeDesiredDocument,
  loadDesiredDocument,
} from "../config.mjs";

const examplePath = join(dirname(fileURLToPath(import.meta.url)), "../desired.json.example");

const defaultFsFromNode = {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

test("defaultConfigDir matches gt iCloud root", () => {
  assert.equal(
    defaultConfigDir({ HOME: "/Users/me" }),
    "/Users/me/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
  );
});

test("resolveBudPaths uses CLOUD_UTILS_CONFIG_DIR", () => {
  const p = resolveBudPaths({ CLOUD_UTILS_CONFIG_DIR: "/tmp/cu", HOME: "/Users/me" });
  assert.equal(p.budDir, "/tmp/cu/bud");
  assert.equal(p.desiredFile, "/tmp/cu/bud/desired.json");
});

test("isValidDesiredDocument accepts version 1 shape", () => {
  assert.equal(isValidDesiredDocument({
    version: 1, formulas: ["a"], casks: [], taps: ["x/y"],
  }), true);
  assert.equal(isValidDesiredDocument({ version: 2, formulas: [], casks: [], taps: [] }), false);
  assert.equal(isValidDesiredDocument({ version: 1, formulas: [""], casks: [], taps: [] }), false);
});

test("writeDesiredDocument sorts unique and round-trips", () => {
  const dir = mkdtempSync(join(tmpdir(), "bud-cfg-"));
  const file = join(dir, "bud", "desired.json");
  const result = writeDesiredDocument(file, {
    version: 1,
    formulas: ["gh", "bat", "bat"],
    casks: ["cursor"],
    taps: ["mongodb/brew"],
  });
  assert.equal(result.ok, true);
  const read = readDesiredDocument(file);
  assert.equal(read.ok, true);
  assert.deepEqual(read.document.formulas, ["bat", "gh"]);
});

test("readDesiredDocument missing and invalid", () => {
  assert.equal(readDesiredDocument("/tmp/no-bud-desired.json").missing, true);
  const dir = mkdtempSync(join(tmpdir(), "bud-cfg-"));
  const bad = join(dir, "desired.json");
  writeFileSync(bad, "{", "utf8");
  assert.equal(readDesiredDocument(bad).ok, false);
});

test("bootstraps desired.json from example when missing", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-boot-"));
  mkdirSync(join(root, "bud"), { recursive: true });
  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/x" },
    examplePath,
  });
  assert.equal(result.ok, true);
  assert.equal(result.bootstrapped, true);
  assert.equal(result.migrated, undefined);
  const exampleDoc = JSON.parse(readFileSync(examplePath, "utf8"));
  assert.deepEqual(result.document, {
    version: 1,
    formulas: [...exampleDoc.formulas].sort(),
    casks: [...exampleDoc.casks].sort(),
    taps: [...exampleDoc.taps].sort(),
  });
  assert.equal(existsSync(join(root, "bud", "desired.json")), true);
});

test("bootstraps seeds empty taps from listBrewTaps", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-seed-"));
  mkdirSync(join(root, "bud"), { recursive: true });
  const emptyExample = join(root, "empty-example.json");
  writeFileSync(emptyExample, `${JSON.stringify({
    version: 1,
    formulas: ["bat"],
    casks: [],
    taps: [],
  }, null, 2)}\n`);

  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/x" },
    examplePath: emptyExample,
    listBrewTaps: async () => ["homebrew/core"],
  });
  assert.equal(result.ok, true);
  assert.equal(result.bootstrapped, true);
  assert.deepEqual(result.document.taps, ["homebrew/core"]);
  assert.deepEqual(result.document.formulas, ["bat"]);
});

test("ignores leftover txt files when desired.json is missing", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-ignore-txt-"));
  const budDir = join(root, "bud");
  mkdirSync(budDir, { recursive: true });
  writeFileSync(join(budDir, "casks.txt"), "should-be-ignored\n");

  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/x" },
    examplePath,
  });
  assert.equal(result.ok, true);
  assert.equal(result.bootstrapped, true);
  assert.equal(result.migrated, undefined);
  assert.ok(!result.document.casks.includes("should-be-ignored"));
  assert.equal(existsSync(join(budDir, "casks.txt")), true);
});

test("invalid desired.json does not overwrite", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-bad-"));
  const budDir = join(root, "bud");
  mkdirSync(budDir, { recursive: true });
  const raw = "{\"version\":1}\n";
  writeFileSync(join(budDir, "desired.json"), raw);
  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/x" },
    examplePath,
  });
  assert.equal(result.ok, false);
  assert.equal(readFileSync(join(budDir, "desired.json"), "utf8"), raw);
});

test("failed bootstrap write leaves no desired.json", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-fail-"));
  const budDir = join(root, "bud");
  mkdirSync(budDir, { recursive: true });
  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/x" },
    examplePath,
    fs: {
      ...defaultFsFromNode,
      renameSync() { throw new Error("disk full"); },
    },
  });
  assert.equal(result.ok, false);
  assert.equal(existsSync(join(budDir, "desired.json")), false);
});
