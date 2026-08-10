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
  normalizeDesiredDocument,
  loadDesiredDocument,
  EMPTY_DESIRED,
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

test("migrate merges bud txt files, writes JSON, deletes bud txt only", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-mig-"));
  const budDir = join(root, "bud");
  mkdirSync(budDir);
  writeFileSync(join(budDir, "formulas.txt"), "# hi\nbat\n\ngh\n");
  writeFileSync(join(budDir, "casks.txt"), "cursor\n");
  writeFileSync(join(budDir, "taps.txt"), "mongodb/brew\n");
  const brewDir = join(root, "brew");
  mkdirSync(brewDir);
  writeFileSync(join(brewDir, "casks.txt"), "should-not-win\n");

  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/Users/me" },
    examplePath,
  });
  assert.equal(result.ok, true);
  assert.equal(result.migrated, true);
  assert.deepEqual(result.document.casks, ["cursor"]);
  assert.equal(existsSync(join(budDir, "desired.json")), true);
  assert.equal(existsSync(join(budDir, "casks.txt")), false);
  assert.equal(existsSync(join(brewDir, "casks.txt")), true);
});

test("legacy brew txt used when bud txt missing", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-leg-"));
  mkdirSync(join(root, "brew"), { recursive: true });
  mkdirSync(join(root, "bud"), { recursive: true });
  writeFileSync(join(root, "brew", "formulas.txt"), "jq\n");
  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/x" },
    examplePath,
    listBrewTaps: async () => ["homebrew/core"],
  });
  assert.equal(result.ok, true);
  assert.ok(result.document.formulas.includes("jq"));
});

test("invalid desired.json does not migrate or overwrite", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-bad-"));
  const budDir = join(root, "bud");
  mkdirSync(budDir, { recursive: true });
  writeFileSync(join(budDir, "desired.json"), "{\"version\":1}\n");
  writeFileSync(join(budDir, "casks.txt"), "cursor\n");
  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/x" },
    examplePath,
  });
  assert.equal(result.ok, false);
  assert.equal(existsSync(join(budDir, "casks.txt")), true);
});

test("failed write does not delete txt", async () => {
  const root = mkdtempSync(join(tmpdir(), "bud-fail-"));
  const budDir = join(root, "bud");
  mkdirSync(budDir, { recursive: true });
  writeFileSync(join(budDir, "casks.txt"), "cursor\n");
  const result = await loadDesiredDocument({
    env: { CLOUD_UTILS_CONFIG_DIR: root, HOME: "/x" },
    examplePath,
    fs: {
      ...defaultFsFromNode,
      renameSync() { throw new Error("disk full"); },
    },
  });
  assert.equal(result.ok, false);
  assert.equal(existsSync(join(budDir, "casks.txt")), true);
});
