import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  defaultConfigDir,
  resolveBudPaths,
  isValidDesiredDocument,
  readDesiredDocument,
  writeDesiredDocument,
  normalizeDesiredDocument,
  EMPTY_DESIRED,
} from "../config.mjs";

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
