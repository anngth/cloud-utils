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
  isIsoUtcTimestamp,
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
    version: 4,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: null,
      lastCheckedAt: null,
      selectedLast: false,
    }],
  };
  assert.deepEqual(writeBackupsDocument(file, doc), { ok: true });
  assert.deepEqual(readBackupsDocument(file), { ok: true, document: doc });
});

test("EMPTY_BACKUPS is version 4", () => {
  assert.deepEqual(EMPTY_BACKUPS, { version: 4, repos: [] });
});

test("readBackupsDocument accepts v4 with selectedLast", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "backups.json");
  const doc = {
    version: 4,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: null,
      lastCheckedAt: null,
      selectedLast: true,
    }],
  };
  writeFileSync(file, `${JSON.stringify(doc)}\n`);
  assert.deepEqual(readBackupsDocument(file), { ok: true, document: doc });
});

test("readBackupsDocument rejects non-boolean selectedLast", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "backups.json");
  writeFileSync(file, JSON.stringify({
    version: 4,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: null,
      lastCheckedAt: null,
      selectedLast: "yes",
    }],
  }));
  assert.equal(readBackupsDocument(file).ok, false);
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

test("readBackupsDocument accepts v3", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "backups.json");
  const doc = {
    version: 3,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: null,
      lastCheckedAt: "2026-08-08T00:00:00.000Z",
    }],
  };
  writeFileSync(file, `${JSON.stringify(doc)}\n`);
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

test("writeBackupsDocument rejects v2", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "gt", "backups.json");
  assert.equal(
    writeBackupsDocument(file, {
      version: 2,
      repos: [{ url: "git@github.com:a/b.git", lastBackupAt: null }],
    }).ok,
    false,
  );
});

test("writeBackupsDocument rejects v3", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "gt", "backups.json");
  assert.equal(writeBackupsDocument(file, {
    version: 3,
    repos: [{ url: "git@github.com:a/b.git", lastBackupAt: null, lastCheckedAt: null }],
  }).ok, false);
});

test("migrateBackupsDocument maps v1 strings to v4 objects", () => {
  const result = migrateBackupsDocument({
    version: 1,
    repos: ["git@github.com:a/b.git"],
  });
  assert.equal(result.ok, true);
  assert.equal(result.migrated, true);
  assert.deepEqual(result.document, {
    version: 4,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: null,
      lastCheckedAt: null,
      selectedLast: false,
    }],
  });
});

test("migrateBackupsDocument upgrades v2 to v4 with selectedLast false", () => {
  const result = migrateBackupsDocument({
    version: 2,
    repos: [{ url: "git@github.com:a/b.git", lastBackupAt: "2026-01-01T00:00:00.000Z" }],
  });
  assert.equal(result.ok, true);
  assert.equal(result.migrated, true);
  assert.deepEqual(result.document, {
    version: 4,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: "2026-01-01T00:00:00.000Z",
      lastCheckedAt: null,
      selectedLast: false,
    }],
  });
});

test("migrateBackupsDocument upgrades v3 with selectedLast false", () => {
  const result = migrateBackupsDocument({
    version: 3,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: "2026-01-01T00:00:00.000Z",
      lastCheckedAt: null,
    }],
  });
  assert.equal(result.ok, true);
  assert.equal(result.migrated, true);
  assert.deepEqual(result.document, {
    version: 4,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: "2026-01-01T00:00:00.000Z",
      lastCheckedAt: null,
      selectedLast: false,
    }],
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
  assert.equal(loaded.document.version, 4);
  const onDisk = JSON.parse(readFileSync(file, "utf8"));
  assert.equal(onDisk.version, 4);
  assert.deepEqual(onDisk.repos[0], {
    url: "git@github.com:a/b.git",
    lastBackupAt: null,
    lastCheckedAt: null,
    selectedLast: false,
  });
});

test("loadBackupsDocument migrates v1 to v4 on disk", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "backups.json");
  writeFileSync(file, JSON.stringify({ version: 1, repos: ["git@github.com:a/b.git"] }));
  const loaded = loadBackupsDocument(file);
  assert.equal(loaded.ok, true);
  assert.equal(loaded.document.version, 4);
  assert.deepEqual(loaded.document.repos[0], {
    url: "git@github.com:a/b.git",
    lastBackupAt: null,
    lastCheckedAt: null,
    selectedLast: false,
  });
});

test("isIsoUtcTimestamp accepts Z and millis", () => {
  assert.equal(isIsoUtcTimestamp("2026-08-08T09:30:00.000Z"), true);
  assert.equal(isIsoUtcTimestamp("2026-08-08T09:30:00Z"), true);
});

test("isIsoUtcTimestamp rejects garbage", () => {
  assert.equal(isIsoUtcTimestamp("yesterday"), false);
  assert.equal(isIsoUtcTimestamp(""), false);
  assert.equal(isIsoUtcTimestamp("2026-08-08T09:30:00+07:00"), false);
});

test("readBackupsDocument rejects bad lastBackupAt ISO with field and URL", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "b.json");
  writeFileSync(file, JSON.stringify({
    version: 4,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: "not-iso",
      lastCheckedAt: null,
      selectedLast: false,
    }],
  }));
  const result = readBackupsDocument(file);
  assert.equal(result.ok, false);
  assert.match(
    result.error,
    /Invalid lastBackupAt for git@github\.com:a\/b\.git: "not-iso"/,
  );
});

test("readBackupsDocument rejects bad lastCheckedAt ISO with field and URL", () => {
  const dir = mkdtempSync(join(tmpdir(), "gt-cfg-"));
  const file = join(dir, "b.json");
  writeFileSync(file, JSON.stringify({
    version: 4,
    repos: [{
      url: "git@github.com:a/b.git",
      lastBackupAt: null,
      lastCheckedAt: "not-iso",
      selectedLast: false,
    }],
  }));
  const result = readBackupsDocument(file);
  assert.equal(result.ok, false);
  assert.match(
    result.error,
    /Invalid lastCheckedAt for git@github\.com:a\/b\.git: "not-iso"/,
  );
});

test("migrateBackupsDocument rejects bad lastBackupAt ISO with field and URL", () => {
  const result = migrateBackupsDocument({
    version: 2,
    repos: [{ url: "git@github.com:a/b.git", lastBackupAt: "not-iso" }],
  });
  assert.equal(result.ok, false);
  assert.match(
    result.error,
    /Invalid lastBackupAt for git@github\.com:a\/b\.git: "not-iso"/,
  );
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
