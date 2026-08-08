import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { resolveGtPaths } from "../config.mjs";
import {
  addBackupRepo,
  recordLastBackupAt,
  removeBackupRepo,
} from "../backup-list.mjs";

function tempPaths() {
  const configDir = mkdtempSync(join(tmpdir(), "gt-bl-"));
  return resolveGtPaths({ CLOUD_UTILS_CONFIG_DIR: configDir, HOME: "/Users/me" });
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
    { url: "git@github.com:Org/Foo.git", lastBackupAt: null },
  ]);
  assert.equal(first.document.version, 2);
});

test("addBackupRepo appends and rejects duplicates", () => {
  const paths = tempPaths();

  const first = addBackupRepo(paths, "git@GitHub.com:Org/Foo.git");
  assert.equal(first.ok, true);
  assert.equal(first.createdFile, true);
  assert.equal(first.index, 1);
  assert.deepEqual(first.document.repos, [
    { url: "git@github.com:Org/Foo.git", lastBackupAt: null },
  ]);

  const second = addBackupRepo(paths, "git@gitlab.com:acme/bar");
  assert.equal(second.ok, true);
  assert.equal(second.createdFile, false);
  assert.equal(second.index, 2);
  assert.deepEqual(second.document.repos, [
    { url: "git@github.com:Org/Foo.git", lastBackupAt: null },
    { url: "git@gitlab.com:acme/bar.git", lastBackupAt: null },
  ]);

  const dup = addBackupRepo(paths, "git@github.com:Org/Foo");
  assert.equal(dup.ok, false);
  assert.match(dup.error, /duplicate/i);

  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.deepEqual(onDisk.repos, [
    { url: "git@github.com:Org/Foo.git", lastBackupAt: null },
    { url: "git@gitlab.com:acme/bar.git", lastBackupAt: null },
  ]);
});

test("addBackupRepo rejects invalid ssh url", () => {
  const paths = tempPaths();
  const result = addBackupRepo(paths, "https://github.com/org/app.git");
  assert.equal(result.ok, false);
  assert.match(result.error, /HTTPS|Invalid/i);
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
    { url: "git@github.com:a/one.git", lastBackupAt: null },
    { url: "git@github.com:c/three.git", lastBackupAt: null },
  ]);
});

test("removeBackupRepo returns url string and preserves other timestamps", () => {
  const paths = tempPaths();
  mkdirSync(paths.gtDir, { recursive: true });
  writeFileSync(
    paths.backupsFile,
    JSON.stringify({
      version: 2,
      repos: [
        { url: "git@github.com:a/one.git", lastBackupAt: "2026-01-01T00:00:00.000Z" },
        { url: "git@github.com:b/two.git", lastBackupAt: null },
      ],
    }, null, 2) + "\n",
  );
  const byIndex = removeBackupRepo(paths, "2");
  assert.equal(byIndex.ok, true);
  assert.equal(byIndex.removed, "git@github.com:b/two.git");
  assert.deepEqual(byIndex.document.repos, [
    { url: "git@github.com:a/one.git", lastBackupAt: "2026-01-01T00:00:00.000Z" },
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
    { url: "git@github.com:a/one.git", lastBackupAt: null },
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
  assert.equal(onDisk.version, 2);
  assert.equal(onDisk.repos[0].lastBackupAt, "2026-08-08T09:30:00.000Z");
});
