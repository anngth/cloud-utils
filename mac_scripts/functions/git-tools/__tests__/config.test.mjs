import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  defaultConfigDir,
  formatDisplayPath,
  resolveGtPaths,
  readBackupsDocument,
  writeBackupsDocument,
  migrateBackupsDocument,
  loadBackupsDocument,
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
  const doc = {
    version: 2,
    repos: [{ url: "git@github.com:a/b.git", lastBackupAt: null }],
  };
  assert.deepEqual(writeBackupsDocument(file, doc), { ok: true });
  assert.deepEqual(readBackupsDocument(file), { ok: true, document: doc });
});

test("EMPTY_BACKUPS is version 2 with empty repos", () => {
  assert.deepEqual(EMPTY_BACKUPS, { version: 2, repos: [] });
});

test("readBackupsDocument accepts v2 objects", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "backups.json");
  const doc = {
    version: 2,
    repos: [{ url: "git@github.com:a/b.git", lastBackupAt: null }],
  };
  writeFileSync(file, `${JSON.stringify(doc)}\n`, "utf8");
  assert.deepEqual(readBackupsDocument(file), { ok: true, document: doc });
});

test("readBackupsDocument still accepts v1 string repos", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "backups.json");
  const doc = { version: 1, repos: ["git@github.com:a/b.git"] };
  writeFileSync(file, `${JSON.stringify(doc)}\n`, "utf8");
  assert.deepEqual(readBackupsDocument(file), { ok: true, document: doc });
});

test("readBackupsDocument rejects v2 with bad lastBackupAt", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "backups.json");
  writeFileSync(
    file,
    JSON.stringify({
      version: 2,
      repos: [{ url: "git@github.com:a/b.git", lastBackupAt: 123 }],
    }),
    "utf8",
  );
  assert.equal(readBackupsDocument(file).ok, false);
});

test("writeBackupsDocument rejects v1 documents", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "gt", "backups.json");
  const result = writeBackupsDocument(file, {
    version: 1,
    repos: ["git@github.com:a/b.git"],
  });
  assert.equal(result.ok, false);
});

test("migrateBackupsDocument maps v1 strings to v2 objects", () => {
  const result = migrateBackupsDocument({
    version: 1,
    repos: ["git@github.com:a/b.git"],
  });
  assert.equal(result.ok, true);
  assert.equal(result.migrated, true);
  assert.deepEqual(result.document, {
    version: 2,
    repos: [{ url: "git@github.com:a/b.git", lastBackupAt: null }],
  });
});

test("loadBackupsDocument migrates v1 and rewrites file", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "backups.json");
  writeFileSync(
    file,
    `${JSON.stringify({ version: 1, repos: ["git@github.com:a/b.git"] }, null, 2)}\n`,
    "utf8",
  );
  const loaded = loadBackupsDocument(file);
  assert.equal(loaded.ok, true);
  assert.equal(loaded.migrated, true);
  assert.equal(loaded.document.version, 2);
  const onDisk = JSON.parse(readFileSync(file, "utf8"));
  assert.equal(onDisk.version, 2);
  assert.deepEqual(onDisk.repos[0], {
    url: "git@github.com:a/b.git",
    lastBackupAt: null,
  });
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
