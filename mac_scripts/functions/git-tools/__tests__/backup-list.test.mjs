import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { resolveGtPaths } from "../config.mjs";
import { addBackupRepo, removeBackupRepo } from "../backup-list.mjs";

function tempPaths() {
  const configDir = mkdtempSync(join(tmpdir(), "gt-bl-"));
  return resolveGtPaths({ CLOUD_UTILS_CONFIG_DIR: configDir, HOME: "/Users/me" });
}

function seedRepos(paths, repos) {
  mkdirSync(paths.gtDir, { recursive: true });
  writeFileSync(
    paths.backupsFile,
    `${JSON.stringify({ version: 1, repos }, null, 2)}\n`,
    "utf8",
  );
}

test("addBackupRepo appends and rejects duplicates", () => {
  const paths = tempPaths();

  const first = addBackupRepo(paths, "git@GitHub.com:Org/Foo.git");
  assert.equal(first.ok, true);
  assert.equal(first.createdFile, true);
  assert.equal(first.index, 1);
  assert.deepEqual(first.document.repos, ["git@github.com:Org/Foo.git"]);

  const second = addBackupRepo(paths, "git@gitlab.com:acme/bar");
  assert.equal(second.ok, true);
  assert.equal(second.createdFile, false);
  assert.equal(second.index, 2);
  assert.deepEqual(second.document.repos, [
    "git@github.com:Org/Foo.git",
    "git@gitlab.com:acme/bar.git",
  ]);

  // Duplicate after foo.git vs foo (canonical form)
  const dup = addBackupRepo(paths, "git@github.com:Org/Foo");
  assert.equal(dup.ok, false);
  assert.match(dup.error, /duplicate/i);

  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.deepEqual(onDisk.repos, [
    "git@github.com:Org/Foo.git",
    "git@gitlab.com:acme/bar.git",
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
  seedRepos(paths, [
    "git@github.com:a/one.git",
    "git@github.com:b/two.git",
    "git@github.com:c/three.git",
  ]);

  const byIndex = removeBackupRepo(paths, "2");
  assert.equal(byIndex.ok, true);
  assert.equal(byIndex.removed, "git@github.com:b/two.git");
  assert.deepEqual(byIndex.document.repos, [
    "git@github.com:a/one.git",
    "git@github.com:c/three.git",
  ]);
});

test("removeBackupRepo removes by url (canonical match)", () => {
  const paths = tempPaths();
  seedRepos(paths, [
    "git@github.com:a/one.git",
    "git@github.com:c/three.git",
  ]);

  const byUrl = removeBackupRepo(paths, "git@GitHub.com:c/three");
  assert.equal(byUrl.ok, true);
  assert.equal(byUrl.removed, "git@github.com:c/three.git");
  assert.deepEqual(byUrl.document.repos, ["git@github.com:a/one.git"]);
});

test("removeBackupRepo errors for out-of-range 0 and 99", () => {
  const paths = tempPaths();
  seedRepos(paths, ["git@github.com:a/one.git"]);

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
  seedRepos(paths, []);

  const result = removeBackupRepo(paths, "1");
  assert.equal(result.ok, false);
  assert.match(result.error, /add/i);
  assert.match(result.error, /gt backup add|backup add/i);
});
