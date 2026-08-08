import assert from "node:assert/strict";
import test from "node:test";
import { createUi } from "../ui.mjs";

test("usage documents managed backup list without -n/--new or one-shot URL", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  ui.usage();

  assert.match(stdout, /\bbackup\b/);
  assert.match(stdout, /backup \[--dry-run\] \[-f\|--force\]/);
  assert.match(stdout, /backup --all \[--dry-run\] \[-f\|--force\]/);
  assert.match(stdout, /backup add <ssh-url> \[<ssh-url> \.\.\.\]/);
  assert.match(stdout, /backup remove <index\|ssh-url>/);
  assert.doesNotMatch(stdout, /-n|--new/);
  assert.doesNotMatch(stdout, /backup <ssh-url>/);
});

test("status uses skm-style step marker instead of --- prefix", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  ui.status("Checking backup group");

  assert.match(stdout, /◇/);
  assert.match(stdout, /Checking backup group/);
  assert.doesNotMatch(stdout, /^--- /m);
});

test("error uses skm-style red cross prefix", () => {
  let stderr = "";
  const ui = createUi({
    stdout: { write() {} },
    stderr: { write: (value) => { stderr += value; } },
  });

  ui.error("Backup cancelled.");

  assert.match(stderr, /❌/);
  assert.match(stderr, /Backup cancelled/);
});

test("title and listEnd frame output like skm", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  ui.title("REPO BACKUP");
  ui.step("Done");
  ui.listEnd("https://gitlab.com/example");

  assert.match(stdout, /REPO BACKUP/);
  assert.match(stdout, /◇/);
  assert.match(stdout, /└/);
  assert.match(stdout, /https:\/\/gitlab\.com\/example/);
});

test("detail indents continuation under an item without a box", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  ui.item("ok  git@github.com:org/a.git");
  ui.detail("→ https://gitlab.com/example/a");

  assert.match(stdout, /■.*ok {2}git@github\.com:org\/a\.git/);
  assert.match(stdout, /\n.* {6}.*→ https:\/\/gitlab\.com\/example\/a/);
  assert.doesNotMatch(stdout, /■ →/);
});

test("renderBackupSelector shows 1-based numbers with □/■ and hint", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  ui.renderBackupSelector("Select repos to backup", {
    items: [
      { value: "git@github.com:org/a.git", label: "git@github.com:org/a.git" },
      { value: "git@gitlab.com:acme/b.git", label: "git@gitlab.com:acme/b.git" },
    ],
    cursor: 1,
    selected: new Set([0]),
  }, {
    listPath: "~/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils/gt/backups.json",
  });

  assert.match(stdout, /\u001b\[2J\u001b\[H/);
  assert.match(stdout, /REPO BACKUP/);
  assert.match(stdout, /~\/Library\/Mobile Documents\/.*\/gt\/backups\.json/);
  assert.match(stdout, /Select repos to backup/);
  assert.match(stdout, /space toggle, a all, c clear, enter to start, q to quit/);
  assert.match(stdout, /1\s+.*■.*git@github\.com:org\/a\.git/);
  assert.match(stdout, /2\s+.*□.*git@gitlab\.com:acme\/b\.git/);
  assert.match(stdout, /└/);
});

test("renderBackupSelector shows last backup on line 2 and blank between repos", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write() {} },
  });
  const now = new Date("2026-08-08T12:00:00.000Z");
  ui.renderBackupSelector("Select repos to backup", {
    items: [
      {
        value: "git@github.com:org/a.git",
        label: "git@github.com:org/a.git",
        lastBackupAt: "2026-08-08T10:00:00.000Z",
      },
      {
        value: "git@gitlab.com:acme/b.git",
        label: "git@gitlab.com:acme/b.git",
        lastBackupAt: null,
      },
    ],
    cursor: 0,
    selected: new Set([0]),
  }, { now });

  assert.match(stdout, /Last backup: 2 hours ago/);
  assert.match(stdout, /\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}\)/);
  assert.doesNotMatch(
    stdout,
    /git@gitlab\.com:acme\/b\.git[\s\S]*Last backup/,
  );
  const plain = stdout.replace(/\x1B\[[0-9;]*m/g, "");
  assert.match(plain, /org\/a\.git[\s\S]*│\n│\s+2\s+/);
});

test("renderBackupSelector shows last checked line when set", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write() {} },
  });
  const now = new Date("2026-08-08T12:00:00.000Z");
  ui.renderBackupSelector("Select repos to backup", {
    items: [{
      value: "git@github.com:org/a.git",
      label: "git@github.com:org/a.git",
      lastBackupAt: "2026-08-05T09:00:00.000Z",
      lastCheckedAt: "2026-08-08T11:00:00.000Z",
    }],
    cursor: 0,
    selected: new Set([0]),
  }, { now });
  assert.match(stdout, /Last backup:/);
  assert.match(stdout, /Last checked: 1 hour ago/);
});
