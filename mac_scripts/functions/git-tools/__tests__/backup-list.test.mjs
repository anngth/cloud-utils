import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { resolveGtPaths } from "../config.mjs";
import {
  addBackupRepo,
  recordLastBackupAt,
  recordLastCheckedAt,
  removeBackupRepo,
  setSelectedLast,
} from "../backup-list.mjs";

function tempPaths() {
  const configDir = mkdtempSync(join(tmpdir(), "gt-bl-"));
  return resolveGtPaths({ CLOUD_UTILS_CONFIG_DIR: configDir, HOME: "/Users/me" });
}

function repo(url, overrides = {}) {
  return {
    url,
    lastBackupAt: null,
    lastCheckedAt: null,
    selectedLast: false,
    ...overrides,
  };
}

function seedV1(paths, urls) {
  mkdirSync(paths.gtDir, { recursive: true });
  writeFileSync(
    paths.backupsFile,
    `${JSON.stringify({ version: 1, repos: urls }, null, 2)}\n`,
    "utf8",
  );
}

test("addBackupRepo appends objects and migrates v1 file", () => {
  const paths = tempPaths();
  const first = addBackupRepo(paths, "git@GitHub.com:Org/Foo.git");
  assert.equal(first.ok, true);
  assert.deepEqual(first.document.repos, [
    repo("git@github.com:Org/Foo.git"),
  ]);
  assert.equal(first.document.version, 4);
});

test("addBackupRepo sets selectedLast false", () => {
  const paths = tempPaths();
  const first = addBackupRepo(paths, "git@github.com:Org/Foo.git");
  assert.equal(first.document.repos[0].selectedLast, false);
});

test("addBackupRepo includes lastCheckedAt null", () => {
  const paths = tempPaths();
  const first = addBackupRepo(paths, "git@github.com:Org/Foo.git");
  assert.deepEqual(first.document.repos[0], repo("git@github.com:Org/Foo.git"));
});

test("addBackupRepo appends and rejects duplicates", () => {
  const paths = tempPaths();

  const first = addBackupRepo(paths, "git@GitHub.com:Org/Foo.git");
  assert.equal(first.ok, true);
  assert.equal(first.createdFile, true);
  assert.equal(first.index, 1);
  assert.deepEqual(first.document.repos, [
    repo("git@github.com:Org/Foo.git"),
  ]);

  const second = addBackupRepo(paths, "git@gitlab.com:acme/bar");
  assert.equal(second.ok, true);
  assert.equal(second.createdFile, false);
  assert.equal(second.index, 2);
  assert.deepEqual(second.document.repos, [
    repo("git@github.com:Org/Foo.git"),
    repo("git@gitlab.com:acme/bar.git"),
  ]);

  const dup = addBackupRepo(paths, "git@github.com:Org/Foo");
  assert.equal(dup.ok, false);
  assert.match(dup.error, /duplicate/i);

  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.deepEqual(onDisk.repos, [
    repo("git@github.com:Org/Foo.git"),
    repo("git@gitlab.com:acme/bar.git"),
  ]);
});

test("addBackupRepo rejects invalid ssh url", () => {
  const paths = tempPaths();
  const result = addBackupRepo(paths, "https://github.com/org/app.git");
  assert.equal(result.ok, false);
  assert.match(result.error, /HTTPS|Invalid/i);
});

test("setSelectedLast marks only submitted urls true", () => {
  const paths = tempPaths();
  mkdirSync(paths.gtDir, { recursive: true });
  writeFileSync(paths.backupsFile, JSON.stringify({
    version: 4,
    repos: [
      repo("git@github.com:a/one.git"),
      repo("git@github.com:b/two.git", { selectedLast: true }),
    ],
  }));
  const result = setSelectedLast(paths, ["git@github.com:a/one.git"]);
  assert.equal(result.ok, true);
  assert.equal(result.document.repos[0].selectedLast, true);
  assert.equal(result.document.repos[1].selectedLast, false);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].selectedLast, true);
  assert.equal(onDisk.repos[1].selectedLast, false);
});

test("removeBackupRepo removes by 1-based index 2", () => {
  const paths = tempPaths();
  seedV1(paths, [
    "git@github.com:a/one.git",
    "git@github.com:b/two.git",
    "git@github.com:c/three.git",
  ]);

  const byIndex = removeBackupRepo(paths, "2");
  assert.equal(byIndex.ok, true);
  assert.equal(byIndex.removed, "git@github.com:b/two.git");
  assert.deepEqual(byIndex.document.repos, [
    repo("git@github.com:a/one.git"),
    repo("git@github.com:c/three.git"),
  ]);
});

test("removeBackupRepo returns url string and preserves other timestamps", () => {
  const paths = tempPaths();
  mkdirSync(paths.gtDir, { recursive: true });
  writeFileSync(
    paths.backupsFile,
    JSON.stringify({
      version: 3,
      repos: [
        {
          url: "git@github.com:a/one.git",
          lastBackupAt: "2026-01-01T00:00:00.000Z",
          lastCheckedAt: null,
        },
        {
          url: "git@github.com:b/two.git",
          lastBackupAt: null,
          lastCheckedAt: null,
        },
      ],
    }, null, 2) + "\n",
  );
  const byIndex = removeBackupRepo(paths, "2");
  assert.equal(byIndex.ok, true);
  assert.equal(byIndex.removed, "git@github.com:b/two.git");
  assert.deepEqual(byIndex.document.repos, [
    repo("git@github.com:a/one.git", {
      lastBackupAt: "2026-01-01T00:00:00.000Z",
    }),
  ]);
});

test("removeBackupRepo removes by url (canonical match)", () => {
  const paths = tempPaths();
  seedV1(paths, [
    "git@github.com:a/one.git",
    "git@github.com:c/three.git",
  ]);

  const byUrl = removeBackupRepo(paths, "git@GitHub.com:c/three");
  assert.equal(byUrl.ok, true);
  assert.equal(byUrl.removed, "git@github.com:c/three.git");
  assert.deepEqual(byUrl.document.repos, [
    repo("git@github.com:a/one.git"),
  ]);
});

test("removeBackupRepo errors for out-of-range 0 and 99", () => {
  const paths = tempPaths();
  seedV1(paths, ["git@github.com:a/one.git"]);

  const zero = removeBackupRepo(paths, "0");
  assert.equal(zero.ok, false);
  assert.match(zero.error, /out of range|invalid index/i);

  const high = removeBackupRepo(paths, "99");
  assert.equal(high.ok, false);
  assert.match(high.error, /out of range|invalid index/i);
});

test("removeBackupRepo errors when file missing", () => {
  const paths = tempPaths();
  const result = removeBackupRepo(paths, "1");
  assert.equal(result.ok, false);
  assert.match(result.error, /add/i);
  assert.match(result.error, /gt backup add|backup add/i);
});

test("removeBackupRepo errors when list empty", () => {
  const paths = tempPaths();
  seedV1(paths, []);

  const result = removeBackupRepo(paths, "1");
  assert.equal(result.ok, false);
  assert.match(result.error, /add/i);
  assert.match(result.error, /gt backup add|backup add/i);
});

test("recordLastBackupAt sets ISO timestamp for matching url", () => {
  const paths = tempPaths();
  seedV1(paths, ["git@github.com:org/app.git"]);
  const fixed = new Date("2026-08-08T09:30:00.000Z");
  const result = recordLastBackupAt(paths, "git@github.com:org/app.git", { now: fixed });
  assert.equal(result.ok, true);
  assert.equal(result.document.repos[0].lastBackupAt, "2026-08-08T09:30:00.000Z");
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.version, 4);
  assert.equal(onDisk.repos[0].lastBackupAt, "2026-08-08T09:30:00.000Z");
});

test("recordLastBackupAt sets both timestamps", () => {
  const paths = tempPaths();
  seedV1(paths, ["git@github.com:org/app.git"]);
  const fixed = new Date("2026-08-08T09:30:00.000Z");
  const result = recordLastBackupAt(paths, "git@github.com:org/app.git", { now: fixed });
  assert.equal(result.document.repos[0].lastBackupAt, "2026-08-08T09:30:00.000Z");
  assert.equal(result.document.repos[0].lastCheckedAt, "2026-08-08T09:30:00.000Z");
});

test("recordLastCheckedAt updates checked only", () => {
  const paths = tempPaths();
  mkdirSync(paths.gtDir, { recursive: true });
  writeFileSync(paths.backupsFile, JSON.stringify({
    version: 3,
    repos: [{
      url: "git@github.com:org/app.git",
      lastBackupAt: "2020-01-01T00:00:00.000Z",
      lastCheckedAt: null,
    }],
  }));
  const fixed = new Date("2026-08-08T12:00:00.000Z");
  const result = recordLastCheckedAt(paths, "git@github.com:org/app.git", { now: fixed });
  assert.equal(result.ok, true);
  assert.equal(result.document.repos[0].lastBackupAt, "2020-01-01T00:00:00.000Z");
  assert.equal(result.document.repos[0].lastCheckedAt, "2026-08-08T12:00:00.000Z");
});
