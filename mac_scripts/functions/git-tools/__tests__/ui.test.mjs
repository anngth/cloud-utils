import assert from "node:assert/strict";
import test from "node:test";
import { createUi } from "../ui.mjs";

test("usage matches skm-style sections and signatures", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });
  ui.usage();

  const lines = stdout
    .replace(/\u001b\[[0-9;]*m/g, "")
    .split(/\r?\n/)
    .map((line) => line.trimEnd());

  assert.equal(lines.find((line) => line.includes("Usage:")), "◇  Usage: gt <command>");

  const sections = ["Core", "Backup", "Notes"];
  const sectionIndexes = sections.map((section) => lines.indexOf(`◆  ${section}`));
  assert.ok(sectionIndexes.every((index) => index >= 0), "all help sections are present");
  assert.deepEqual(sectionIndexes, [...sectionIndexes].sort((a, b) => a - b));

  for (const [first, continuation] of [
    [
      "│  gt backup [(-f | --force) | --dry-run]",
      "│      Interactive select; force and dry-run are mutually exclusive",
    ],
    [
      "│  gt backup --all [(-f | --force) | --dry-run]",
      "│      Backup or preview every listed repo",
    ],
    [
      "│  gt backup stale [--days <n>] [--all] [(-f | --force) | --dry-run]",
      "│      Stale repos only (default 7 days)",
    ],
  ]) {
    const index = lines.indexOf(first);
    assert.ok(index >= 0, `missing wrapped signature: ${first}`);
    assert.equal(lines[index + 1], continuation);
  }

  for (const line of [
    "│  gt (help | -h | --help)  Show this help",
    "│  gt push  Force push (safe --force-with-lease)",
    "│  gt fetch [--sync-upstream]  Fetch with optional upstream sync",
    "│  gt backup add <ssh-url> [<ssh-url> ...]  Add SSH URL(s) to managed list",
    "│  gt backup remove <index|ssh-url>  Remove by 1-based index or URL",
    "│  Selector: space toggle, a all, c clear, enter start, q quit",
    "│  Remove indexes are 1-based (same as skm).",
    "│  --force skips fingerprint short-circuit; cannot combine with --dry-run.",
  ]) assert.ok(lines.includes(line), `missing help line: ${line}`);

  assert.doesNotMatch(stdout, /■/);
  assert.doesNotMatch(stdout, /-n|--new/);
  assert.doesNotMatch(stdout, /backup <ssh-url>/);
  assert.doesNotMatch(stdout, /Run 'gt --help'/);
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

test("renderBackupSelector shows checkbox lines without timestamp labels", () => {
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
        lastCheckedAt: "2026-08-08T11:00:00.000Z",
      },
      {
        value: "git@gitlab.com:acme/b.git",
        label: "git@gitlab.com:acme/b.git",
        lastBackupAt: null,
        lastCheckedAt: null,
      },
    ],
    cursor: 0,
    selected: new Set([0]),
  }, { now });

  assert.match(stdout, /1\s+.*■.*git@github\.com:org\/a\.git/);
  assert.match(stdout, /2\s+.*□.*git@gitlab\.com:acme\/b\.git/);
  assert.doesNotMatch(stdout, /Last backup/);
  assert.doesNotMatch(stdout, /Last checked/);
  const plain = stdout.replace(/\x1B\[[0-9;]*m/g, "");
  assert.match(plain, /org\/a\.git[\s\S]*│\n│\s+2\s+/);
});
