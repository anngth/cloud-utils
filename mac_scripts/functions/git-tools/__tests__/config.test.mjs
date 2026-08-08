import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  defaultConfigDir,
  formatDisplayPath,
  resolveGtPaths,
  readBackupsDocument,
  writeBackupsDocument,
  EMPTY_BACKUPS,
} from "../config.mjs";

test("defaultConfigDir matches skm iCloud backups root", () => {
  assert.equal(
    defaultConfigDir({ HOME: "/Users/me" }),
    "/Users/me/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
  );
});

test("resolveGtPaths uses CLOUD_UTILS_CONFIG_DIR when set", () => {
  const paths = resolveGtPaths({ CLOUD_UTILS_CONFIG_DIR: "/tmp/cu", HOME: "/Users/me" });
  assert.equal(paths.configDir, "/tmp/cu");
  assert.equal(paths.gtDir, "/tmp/cu/gt");
  assert.equal(paths.backupsFile, "/tmp/cu/gt/backups.json");
});

test("readBackupsDocument reports missing file", () => {
  const result = readBackupsDocument("/tmp/does-not-exist-gt-backups.json");
  assert.equal(result.ok, false);
  assert.equal(result.missing, true);
});

test("readBackupsDocument rejects invalid JSON and wrong shape", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const bad = join(dir, "bad.json");
  writeFileSync(bad, "{", "utf8");
  assert.equal(readBackupsDocument(bad).ok, false);

  const wrong = join(dir, "wrong.json");
  writeFileSync(wrong, JSON.stringify({ version: 1, repos: "nope" }), "utf8");
  assert.equal(readBackupsDocument(wrong).ok, false);
});

test("writeBackupsDocument creates parent dirs and round-trips", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "gt", "backups.json");
  const doc = { version: 1, repos: ["git@github.com:a/b.git"] };
  assert.deepEqual(writeBackupsDocument(file, doc), { ok: true });
  assert.deepEqual(readBackupsDocument(file), { ok: true, document: doc });
  assert.deepEqual(EMPTY_BACKUPS, { version: 1, repos: [] });
});

test("formatDisplayPath shortens HOME and tmpdir paths", () => {
  assert.equal(
    formatDisplayPath(
      "/Users/me/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils/gt/backups.json",
      { home: "/Users/me", tempDir: "/tmp" },
    ),
    "~/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils/gt/backups.json",
  );
  assert.equal(
    formatDisplayPath("/var/folders/xx/T/gt-backup-abc/mirror.git", {
      home: "/Users/me",
      tempDir: "/var/folders/xx/T",
    }),
    "gt-backup-abc/mirror.git",
  );
  assert.equal(
    formatDisplayPath("/elsewhere/file.json", { home: "/Users/me", tempDir: "/tmp" }),
    "/elsewhere/file.json",
  );
});
