import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { backupOneRepo, runBackupBatch, runBackupCommand } from "../backup.mjs";
import { resolveGtPaths } from "../config.mjs";
import { BACKUP_GROUP, projectSshUrl, projectWebUrl } from "../gitlab.mjs";
import { createUi } from "../ui.mjs";

const SOURCE = "git@github.com:org/app.git";
const SOURCE_B = "git@github.com:org/other.git";
const BASE_NAME = "org-app";
const BASE_NAME_B = "org-other";

function tempPaths() {
  const configDir = mkdtempSync(join(tmpdir(), "gt-backup-cmd-"));
  return resolveGtPaths({ CLOUD_UTILS_CONFIG_DIR: configDir, HOME: "/Users/me" });
}

function seedRepos(paths, repos) {
  mkdirSync(paths.gtDir, { recursive: true });
  const normalized = repos.map((r) => {
    if (typeof r === "string") {
      return {
        url: r,
        lastBackupAt: null,
        lastCheckedAt: null,
        selectedLast: false,
      };
    }
    return {
      lastBackupAt: null,
      lastCheckedAt: null,
      selectedLast: false,
      ...r,
    };
  });
  writeFileSync(
    paths.backupsFile,
    `${JSON.stringify({ version: 4, repos: normalized }, null, 2)}\n`,
    "utf8",
  );
}

function uiHarness() {
  const messages = {
    errors: [],
    lines: [],
    statuses: [],
    warnings: [],
    ends: [],
    items: [],
    titles: [],
    events: [],
    cancelledCalls: [],
  };
  return {
    messages,
    ui: {
      error(message) {
        messages.errors.push(message);
      },
      usage() {},
      usageLine(message) {
        messages.errors.push(message);
      },
      status(message, { tone = "success" } = {}) {
        messages.statuses.push(message);
        messages.events.push({ kind: "status", message, tone });
      },
      step(message) {
        messages.statuses.push(message);
        messages.events.push({ kind: "step", message });
      },
      success(message) {
        messages.statuses.push(message);
        messages.events.push({ kind: "status", message, tone: "success" });
      },
      title(label) {
        messages.titles.push(label);
        messages.events.push({ kind: "title", message: label });
      },
      active(message) {
        messages.events.push({ kind: "section", message });
      },
      section(message) {
        messages.events.push({ kind: "section", message });
      },
      item(message, { tone = "success", marker } = {}) {
        messages.items.push(message);
        messages.events.push({ kind: "item", message, tone, marker });
      },
      detail(message, { tone = "muted" } = {}) {
        messages.items.push(message);
        messages.events.push({ kind: "detail", message, tone });
      },
      warn(message) {
        messages.warnings.push(message);
        messages.events.push({ kind: "item", message, tone: "warning" });
      },
      listEnd(message = "") {
        messages.ends.push(message);
        messages.events.push({ kind: "listEnd", message });
      },
      line(message = "") {
        messages.lines.push(message);
      },
      renderBackupSelector() {},
      cancelledBackupSelector(heading, state, opts) {
        messages.cancelledCalls.push({ heading, state, opts });
      },
    },
  };
}

function baseContext(overrides = {}) {
  const h = uiHarness();
  const created = [];
  const removed = [];
  let tempCounter = 0;

  return {
    h,
    created,
    removed,
    context: {
      hasCommand: (name) => name === "git" || name === "glab",
      assertGlabReady: async () => ({ ok: true }),
      ensureBackupGroup: async () => ({ ok: true, created: false }),
      groupExists: async () => ({ ok: true, exists: true }),
      projectExists: async () => ({ ok: true, exists: false }),
      createPrivateProject: async (_group, name) => {
        created.push(name);
        return { ok: true };
      },
      pickPreferredDefaultBranch: async () => "main",
      setDefaultBranch: async () => ({ ok: true }),
      protectBranch: async () => ({ ok: true }),
      runGit: async () => ({ status: 0, stdout: "", stderr: "" }),
      mkdtempSync: (prefix) => {
        const dir = `/tmp/${prefix.replace(/-$/, "")}-${++tempCounter}`;
        return dir;
      },
      rmSync: (path, opts) => {
        removed.push({ path, opts });
      },
      ui: h.ui,
      stdin: { isTTY: true },
      stdout: { write() {} },
      ...overrides,
    },
  };
}

function assertBackupFrameBeforeRepo(events, { dryRun = false } = {}) {
  const titleIndex = events.findIndex((event) =>
    event.kind === "title" && event.message === "REPO BACKUP"
  );
  const stepIndex = events.findIndex((event) =>
    event.kind === "step"
      && event.message === (dryRun ? "Dry run: backup repositories" : "Backup repositories")
  );
  const listPathIndex = events.findIndex((event) =>
    event.kind === "detail" && /backups\.json$/.test(event.message)
  );
  const repoIndex = events.findIndex((event) =>
    event.kind === "section" && event.message.includes(` → ${BACKUP_GROUP}/`)
  );

  assert.ok(titleIndex >= 0);
  assert.ok(stepIndex > titleIndex);
  assert.ok(listPathIndex > stepIndex);
  assert.ok(repoIndex > listPathIndex);
}

test("backupOneRepo rejects bad URL", async () => {
  const { context } = baseContext();

  const result = await backupOneRepo("https://github.com/org/app.git", context);

  assert.equal(result.ok, false);
  assert.match(result.error, /https|ssh|invalid/i);
});

test("backupOneRepo fails when git missing", async () => {
  const { context } = baseContext({
    hasCommand: (name) => name === "glab",
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, false);
  assert.match(result.error, /git/i);
});

test("backupOneRepo fails when ensureBackupGroup fails before create", async () => {
  const created = [];
  const { context } = baseContext({
    ensureBackupGroup: async () => ({ ok: false, error: "failed to create GitLab subgroup" }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, false);
  assert.deepEqual(created, []);
  assert.match(result.error, /group|subgroup/i);
});

test("backupOneRepo ensures backup group before checking project", async () => {
  const steps = [];
  const { context } = baseContext({
    ensureBackupGroup: async (group) => {
      steps.push(["ensure", group]);
      return { ok: true, created: true };
    },
    projectExists: async (group, name) => {
      steps.push(["exists", group, name]);
      return { ok: true, exists: false };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.deepEqual(steps[0], ["ensure", BACKUP_GROUP]);
  assert.equal(steps[1][0], "exists");
});

test("backupOneRepo creates private project and mirrors when missing", async () => {
  const gitCalls = [];
  const created = [];
  const { removed, context } = baseContext({
    projectExists: async () => ({ ok: true, exists: false }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
    runGit: async (args) => {
      gitCalls.push(args);
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.equal(result.projectPath, `${BACKUP_GROUP}/${BASE_NAME}`);
  assert.equal(result.webUrl, projectWebUrl(BACKUP_GROUP, BASE_NAME));
  assert.deepEqual(created, [BASE_NAME]);
  assert.ok(gitCalls.some((a) => a[0] === "clone" && a.includes("--mirror")));
  const pushCall = gitCalls.find((a) => a[0] === "push");
  assert.ok(pushCall);
  assert.ok(!pushCall.includes("--mirror"));
  assert.ok(pushCall.includes("--prune"));
  assert.ok(pushCall.includes("+refs/heads/*:refs/heads/*"));
  assert.ok(pushCall.includes("+refs/tags/*:refs/tags/*"));
  assert.ok(pushCall.includes(projectSshUrl(BACKUP_GROUP, BASE_NAME)));
  assert.ok(removed.length >= 1);
});

test("backupOneRepo sets preferred default branch after push", async () => {
  const defaults = [];
  const { h, context } = baseContext({
    pickPreferredDefaultBranch: async () => "develop",
    setDefaultBranch: async (group, name, branch) => {
      defaults.push([group, name, branch]);
      return { ok: true };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.deepEqual(defaults, [[BACKUP_GROUP, BASE_NAME, "develop"]]);
  assert.ok(h.messages.statuses.some((m) => /default branch develop/i.test(m)));
});

test("backupOneRepo protects main when it is the default branch", async () => {
  const protectedCalls = [];
  const { h, context } = baseContext({
    pickPreferredDefaultBranch: async () => "main",
    setDefaultBranch: async () => ({ ok: true }),
    protectBranch: async (group, name, branch) => {
      protectedCalls.push([group, name, branch]);
      return { ok: true };
    },
    runGit: async (args) => {
      if (args[0] === "show-ref" && args.includes("refs/heads/main")) {
        return { status: 0, stdout: "", stderr: "" };
      }
      if (args[0] === "show-ref" && args.includes("refs/heads/develop")) {
        return { status: 1, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.deepEqual(protectedCalls, [[BACKUP_GROUP, BASE_NAME, "main"]]);
  assert.ok(h.messages.statuses.some((m) => /Protected main/i.test(m)));
});

test("backupOneRepo protects both main and develop when present", async () => {
  const protectedCalls = [];
  const { h, context } = baseContext({
    pickPreferredDefaultBranch: async () => "main",
    setDefaultBranch: async () => ({ ok: true }),
    protectBranch: async (group, name, branch) => {
      protectedCalls.push([group, name, branch]);
      return { ok: true };
    },
    runGit: async (args) => {
      if (args[0] === "show-ref") return { status: 0, stdout: "", stderr: "" };
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.deepEqual(protectedCalls, [
    [BACKUP_GROUP, BASE_NAME, "main"],
    [BACKUP_GROUP, BASE_NAME, "develop"],
  ]);
  assert.ok(h.messages.statuses.some((m) => /Protected main/i.test(m)));
  assert.ok(h.messages.statuses.some((m) => /Protected develop/i.test(m)));
});

test("backupOneRepo protects develop when only develop exists", async () => {
  const protectedCalls = [];
  const { h, context } = baseContext({
    pickPreferredDefaultBranch: async () => "develop",
    setDefaultBranch: async () => ({ ok: true }),
    protectBranch: async (group, name, branch) => {
      protectedCalls.push([group, name, branch]);
      return { ok: true };
    },
    runGit: async (args) => {
      if (args[0] === "show-ref" && args.includes("refs/heads/main")) {
        return { status: 1, stdout: "", stderr: "" };
      }
      if (args[0] === "show-ref" && args.includes("refs/heads/develop")) {
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.deepEqual(protectedCalls, [[BACKUP_GROUP, BASE_NAME, "develop"]]);
  assert.ok(h.messages.statuses.some((m) => /Protected develop/i.test(m)));
});

test("backupOneRepo prints concise progress including clone path", async () => {
  const { h, context } = baseContext({
    env: { HOME: "/Users/me", TMPDIR: "/tmp" },
    mkdtempSync: () => "/tmp/gt-backup-test",
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.ok(h.messages.events.some((event) =>
    event.kind === "section"
      && event.message === `${SOURCE} → ${BACKUP_GROUP}/${BASE_NAME}`
  ));
  const statuses = h.messages.statuses.join("\n");
  assert.match(statuses, /Created /);
  assert.match(statuses, /Cloning source to gt-backup-test\/mirror\.git/);
  assert.match(statuses, /Pushing all branches \+ tags → /);
  assert.doesNotMatch(statuses, /Checking backup group|Mirror clone complete|Cleaning up|Backup finished/);
});

test("backupOneRepo renders branch protection failures as warning progress", async () => {
  const { h, context } = baseContext({
    protectBranch: async () => ({ ok: false, error: "protected branch API denied" }),
    runGit: async (args) => {
      if (args[0] === "show-ref" && args.includes("refs/heads/main")) {
        return { status: 0, stdout: "", stderr: "" };
      }
      if (args[0] === "show-ref" && args.includes("refs/heads/develop")) {
        return { status: 1, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.ok(h.messages.events.some((event) =>
    event.kind === "status"
      && event.message === "Could not protect main: protected branch API denied"
      && event.tone === "warning"
  ));
});

test("backupOneRepo updates live existing project", async () => {
  const created = [];
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    createPrivateProject: async (_g, name) => {
      created.push(name);
      return { ok: true };
    },
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        const out = args[1] === SOURCE
          ? "aaa\trefs/heads/main\n"
          : "bbb\trefs/heads/main\n";
        return { status: 0, stdout: out, stderr: "" };
      }
      if (args[0] === "show-ref" && args.includes("refs/heads/main")) {
        return { status: 0, stdout: "", stderr: "" };
      }
      if (args[0] === "show-ref" && args.includes("refs/heads/develop")) {
        return { status: 1, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
    stdin: { isTTY: false },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.equal(result.skipped, false);
  assert.equal(result.projectPath, `${BACKUP_GROUP}/${BASE_NAME}`);
  assert.deepEqual(created, []);
});

test("backupOneRepo recreates when inactive", async () => {
  const created = [];
  const { h, context } = baseContext({
    projectExists: async () => ({ ok: true, exists: false, inactive: true }),
    createPrivateProject: async (_g, name) => {
      created.push(name);
      return { ok: true };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  assert.deepEqual(created, [BASE_NAME]);
  assert.match(h.messages.statuses.join("\n"), /pending deletion|inactive/i);
  assert.match(h.messages.statuses.join("\n"), /Created /);
});

test("backupOneRepo with force mirrors even when fingerprints match", async () => {
  let cloned = false;
  const { context } = baseContext({
    force: true,
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") assert.fail("ls-remote should not run when force");
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "ref: refs/heads/main\n", stderr: "" };
    },
  });
  const result = await backupOneRepo(SOURCE, context);
  assert.equal(result.ok, true);
  assert.equal(result.skipped, false);
  assert.equal(cloned, true);
});

test("backupOneRepo skips when live fingerprints match", async () => {
  let cloned = false;
  const { h, context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        const out = "abc\trefs/heads/main\n";
        return { status: 0, stdout: out, stderr: "" };
      }
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  const result = await backupOneRepo(SOURCE, context);
  assert.equal(result.ok, true);
  assert.equal(result.skipped, true);
  assert.equal(cloned, false);
  assert.match(h.messages.statuses.join("\n"), /unchanged|skip/i);
});

test("backupOneRepo does not skip when fingerprints differ", async () => {
  let cloned = false;
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        const out = args[1] === SOURCE
          ? "aaa\trefs/heads/main\n"
          : "bbb\trefs/heads/main\n";
        return { status: 0, stdout: out, stderr: "" };
      }
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "ref: refs/heads/main\n", stderr: "" };
    },
  });
  const result = await backupOneRepo(SOURCE, context);
  assert.equal(result.ok, true);
  assert.equal(result.skipped, false);
  assert.equal(cloned, true);
});

test("backupOneRepo never skips when project missing", async () => {
  let sawLsRemote = false;
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: false }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") sawLsRemote = true;
      if (args[0] === "clone") return { status: 0, stdout: "", stderr: "" };
      return { status: 0, stdout: "ref: refs/heads/main\n", stderr: "" };
    },
  });
  const result = await backupOneRepo(SOURCE, context);
  assert.equal(result.ok, true);
  assert.equal(result.skipped, false);
  assert.equal(sawLsRemote, false);
});

test("backupOneRepo fails when ls-remote fails on live project", async () => {
  let cloned = false;
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        return { status: 128, stdout: "", stderr: "remote error" };
      }
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  const result = await backupOneRepo(SOURCE, context);
  assert.equal(result.ok, false);
  assert.match(result.error, /remote error|ls-remote/i);
  assert.equal(cloned, false);
});

test("runBackupBatch skip updates lastCheckedAt only and exits 0", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{
    url: SOURCE,
    lastBackupAt: "2020-01-01T00:00:00.000Z",
    lastCheckedAt: null,
  }]);
  const fixed = new Date("2026-08-08T12:00:00.000Z");
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => fixed,
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        return { status: 0, stdout: "abc\trefs/heads/main\n", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  const code = await runBackupBatch([SOURCE], context);
  assert.equal(code, 0);
  assert.match(h.messages.items.join("\n"), /skip/);
  assert.ok(h.messages.events.some((event) =>
    event.kind === "status"
      && event.message === "Unchanged; skipping mirror"
      && event.tone === "muted"
  ));
  assert.ok(h.messages.events.some((event) =>
    event.kind === "item"
      && event.message === `skip  ${SOURCE}`
      && event.tone === "muted"
  ));
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].lastBackupAt, "2020-01-01T00:00:00.000Z");
  assert.equal(onDisk.repos[0].lastCheckedAt, "2026-08-08T12:00:00.000Z");
});

test("runBackupBatch skip metadata write failure counts as fail", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{
    url: SOURCE,
    lastBackupAt: "2020-01-01T00:00:00.000Z",
    lastCheckedAt: null,
  }]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        return { status: 0, stdout: "abc\trefs/heads/main\n", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
    recordLastCheckedAt: () => ({ ok: false, error: "disk full" }),
  });
  const code = await runBackupBatch([SOURCE], context);
  assert.equal(code, 1);
  const items = h.messages.items.join("\n");
  assert.match(items, /fail/);
  assert.match(items, /lastCheckedAt|saving/i);
  assert.doesNotMatch(items, new RegExp(`ok\\s+${SOURCE}`));
  assert.doesNotMatch(items, new RegExp(`skip\\s+${SOURCE}`));
});

test("backupOneRepo clone failure still removes temp dir", async () => {
  const removed = [];
  const tempDir = "/tmp/gt-backup-sandbox";
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: false }),
    createPrivateProject: async () => ({ ok: true }),
    mkdtempSync: () => tempDir,
    rmSync: (path, opts) => {
      removed.push({ path, opts });
    },
    runGit: async (args) => {
      if (args[0] === "clone") {
        return { status: 1, stdout: "", stderr: "clone failed" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, false);
  assert.match(result.error, /clone failed/i);
  assert.ok(removed.some((r) => r.path === tempDir && r.opts?.recursive === true));
});

test("runBackupBatch continues after a failure and exits 1", async () => {
  const created = [];
  const { h, context } = baseContext({
    recordLastBackupAt: () => ({ ok: true, document: { version: 3, repos: [] } }),
    projectExists: async (_group, name) => {
      if (name === BASE_NAME) {
        return { ok: false, error: "project lookup failed" };
      }
      return { ok: true, exists: false };
    },
    createPrivateProject: async (_g, name) => {
      created.push(name);
      return { ok: true };
    },
  });

  const code = await runBackupBatch([SOURCE, SOURCE_B], context);

  assert.equal(code, 1);
  assert.deepEqual(created, [BASE_NAME_B]);
  const items = h.messages.items.join("\n");
  assert.match(
    items,
    new RegExp(`ok\\s+${SOURCE_B}\\n→\\s+${projectWebUrl(BACKUP_GROUP, BASE_NAME_B)}`),
  );
  assert.match(items, new RegExp(`fail\\s+${SOURCE}\\n—\\s+project lookup failed`));
  assert.ok(h.messages.events.some((event) =>
    event.kind === "section" && event.message === "Backup summary"
  ));
  assert.ok(h.messages.events.some((event) =>
    event.kind === "item" && event.tone === "failure" && /fail/.test(event.message)
  ));
  assert.doesNotMatch(
    h.messages.events.map(({ message }) => message).join("\n"),
    /[✅⚠❌]/u,
  );
  assert.equal(h.messages.ends.length, 1);
});

test("runBackupBatch renders failed summary rows in red with the real renderer", async () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });
  const code = await runBackupBatch([SOURCE], {
    ui,
    hasCommand: () => true,
    assertGlabReady: async () => ({ ok: true }),
    ensureBackupGroup: async () => ({ ok: false, error: "access denied" }),
    resolveGtPaths: () => ({ backupsFile: "/tmp/backups.json" }),
  });

  assert.equal(code, 1);
  assert.match(stdout, /\u001b\[31m■\u001b\[39m fail  git@github\.com:org\/app\.git/);
  assert.match(stdout, /\u001b\[31m— access denied\u001b\[39m/);
});

test("runBackupBatch separates repository and summary sections with framed blank lines", async () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  const code = await runBackupBatch([SOURCE, SOURCE_B], {
    ui,
    hasCommand: () => true,
    assertGlabReady: async () => ({ ok: true }),
    ensureBackupGroup: async () => ({ ok: false, error: "access denied" }),
    resolveGtPaths: () => ({ backupsFile: "/tmp/backups.json" }),
  });

  assert.equal(code, 1);
  const plain = stdout.replace(/\u001b\[[0-9;]*m/g, "");
  assert.ok(plain.includes(
    `│\n◆  ${SOURCE} → ${BACKUP_GROUP}/${BASE_NAME}`
    + `\n│\n◆  ${SOURCE_B} → ${BACKUP_GROUP}/${BASE_NAME_B}`
    + "\n│\n◆  Backup summary",
  ));
});

test("runBackupBatch returns 0 when all succeed", async () => {
  const { h, context } = baseContext({
    recordLastBackupAt: () => ({ ok: true, document: { version: 3, repos: [] } }),
  });

  const code = await runBackupBatch([SOURCE], context);

  assert.equal(code, 0);
  assert.ok(h.messages.events.some((event) =>
    event.kind === "section" && event.message === "Backup summary"
  ));
  assert.match(
    h.messages.items.join("\n"),
    new RegExp(`ok\\s+${SOURCE}\\n→\\s+${projectWebUrl(BACKUP_GROUP, BASE_NAME)}`),
  );
});

test("runBackupBatch records lastBackupAt after success only", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastBackupAt: null },
    { url: SOURCE_B, lastBackupAt: "2020-01-01T00:00:00.000Z" },
  ]);
  const fixed = new Date("2026-08-08T09:30:00.000Z");
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => fixed,
    projectExists: async (_g, name) => {
      if (name === BASE_NAME) return { ok: false, error: "project lookup failed" };
      return { ok: true, exists: false };
    },
  });

  const code = await runBackupBatch([SOURCE, SOURCE_B], context);
  assert.equal(code, 1);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].lastBackupAt, null); // failed
  assert.equal(onDisk.repos[1].lastBackupAt, "2026-08-08T09:30:00.000Z");
  assert.match(h.messages.items.join("\n"), /fail/);
});

test("runBackupBatch metadata write failure counts as fail", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    recordLastBackupAt: () => ({ ok: false, error: "disk full" }),
  });
  const code = await runBackupBatch([SOURCE], context);
  assert.equal(code, 1);
  assert.match(h.messages.items.join("\n"), /fail/);
  assert.match(
    h.messages.items.join("\n") + h.messages.statuses.join("\n"),
    /lastBackupAt|saving/i,
  );
});

test("runBackupCommand interactive passes initial from selectedLast", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastBackupAt: null, lastCheckedAt: null, selectedLast: true },
    { url: SOURCE_B, lastBackupAt: null, lastCheckedAt: null, selectedLast: false },
  ]);
  let capturedInitial;
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async ({ items, initial }) => {
      capturedInitial = initial;
      return { type: "submit", selected: [items[0].value] };
    },
    setSelectedLast: () => ({ ok: true, document: { version: 4, repos: [] } }),
    recordLastBackupAt: () => ({ ok: true, document: { version: 4, repos: [] } }),
  });
  await runBackupCommand([], context);
  assert.deepEqual(capturedInitial, [SOURCE]);
});

test("runBackupCommand submit persists selectedLast before batch", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE, SOURCE_B]);
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async ({ items }) => ({
      type: "submit",
      selected: [items[1].value],
    }),
  });
  await runBackupCommand([], context);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].selectedLast, false);
  assert.equal(onDisk.repos[1].selectedLast, true);
});

test("runBackupCommand selection write failure skips batch", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const created = [];
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async ({ items }) => ({ type: "submit", selected: [items[0].value] }),
    setSelectedLast: () => ({ ok: false, error: "disk full" }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
  });
  const code = await runBackupCommand([], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /selection|save|selectedLast|disk full/i);
  assert.deepEqual(created, []);
  assert.doesNotMatch(h.messages.statuses.join("\n"), /Backup summary/);
});

test("runBackupCommand cancel redraws selector with Selection cancelled", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE, SOURCE_B]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async ({ items }) => ({
      type: "cancel",
      selected: [],
      state: { items, cursor: 0, selected: new Set() },
    }),
  });
  const code = await runBackupCommand([], context);
  assert.equal(code, 1);
  assert.equal(h.messages.cancelledCalls.length, 1);
  assert.equal(h.messages.cancelledCalls[0].heading, "Select repos to backup");
  assert.equal(h.messages.cancelledCalls[0].state.items.length, 2);
  assert.match(String(h.messages.cancelledCalls[0].opts?.listPath ?? ""), /backups\.json/);
});

test("runBackupCommand stale cancel redraws selector", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastCheckedAt: null },
    { url: SOURCE_B, lastCheckedAt: "2026-08-08T12:00:00.000Z" },
  ]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => new Date("2026-08-08T12:00:00.000Z"),
    stdin: { isTTY: true },
    runSelector: async ({ items }) => ({
      type: "cancel",
      selected: [],
      state: { items, cursor: 0, selected: new Set() },
    }),
  });
  const code = await runBackupCommand(["stale"], context);
  assert.equal(code, 1);
  assert.equal(h.messages.cancelledCalls.length, 1);
  assert.equal(h.messages.cancelledCalls[0].heading, "Select stale repos to backup");
  assert.equal(h.messages.cancelledCalls[0].state.items.length, 1);
});

test("runBackupCommand cancel does not change selectedLast", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastBackupAt: null, lastCheckedAt: null, selectedLast: true },
  ]);
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async ({ items }) => ({
      type: "cancel",
      selected: [],
      state: { items, cursor: 0, selected: new Set() },
    }),
  });
  await runBackupCommand([], context);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].selectedLast, true);
});

test("runBackupCommand --all leaves selectedLast unchanged", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastBackupAt: null, lastCheckedAt: null, selectedLast: true },
  ]);
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });
  await runBackupCommand(["--all"], context);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].selectedLast, true);
});

test("runBackupCommand interactive passes lastBackupAt and lastCheckedAt into selector render", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    {
      url: SOURCE,
      lastBackupAt: "2026-08-01T00:00:00.000Z",
      lastCheckedAt: "2026-08-08T11:00:00.000Z",
    },
  ]);
  let captured;
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async ({ items, render }) => {
      captured = items;
      render({ items, cursor: 0, selected: new Set([0]) });
      return { type: "submit", selected: [items[0].value] };
    },
  });
  h.ui.renderBackupSelector = (_heading, state) => {
    captured = state.items;
  };
  await runBackupCommand([], context);
  assert.equal(captured[0].lastBackupAt, "2026-08-01T00:00:00.000Z");
  assert.equal(captured[0].lastCheckedAt, "2026-08-08T11:00:00.000Z");
});

test("runBackupCommand --all migrates v1 list on load", async () => {
  const paths = tempPaths();
  mkdirSync(paths.gtDir, { recursive: true });
  writeFileSync(
    paths.backupsFile,
    `${JSON.stringify({ version: 1, repos: [SOURCE] }, null, 2)}\n`,
    "utf8",
  );
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });

  const code = await runBackupCommand(["--all"], context);
  assert.equal(code, 0);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.version, 4);
  assert.equal(onDisk.repos[0].url, SOURCE);
  assert.ok("lastBackupAt" in onDisk.repos[0]);
  assert.ok("lastCheckedAt" in onDisk.repos[0]);
  assert.ok("selectedLast" in onDisk.repos[0]);
});

test("runBackupCommand add then --all backs up listed repos", async () => {
  const paths = tempPaths();
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });

  const addCode = await runBackupCommand(["add", SOURCE], context);
  assert.equal(addCode, 0);
  assert.match(h.messages.statuses.join("\n") + h.messages.lines.join("\n"), /1/);
  // Config dir is under tmp in tests → relative-to-tmpdir short form; real HOME uses ~/…
  assert.match(h.messages.items.join("\n"), /gt\/backups\.json/);
  assert.doesNotMatch(h.messages.items.join("\n"), /\/Users\/me\//);

  const beforeAllEvents = h.messages.events.length;
  const allCode = await runBackupCommand(["--all"], context);
  assert.equal(allCode, 0);
  assert.ok(h.messages.titles.includes("REPO BACKUP"));
  assertBackupFrameBeforeRepo(h.messages.events.slice(beforeAllEvents));
  assert.match(
    h.messages.items.join("\n"),
    new RegExp(`ok\\s+${SOURCE}\\n→\\s+${projectWebUrl(BACKUP_GROUP, BASE_NAME)}`),
  );
});

test("runBackupCommand add/remove log backups path with tilde under HOME", async () => {
  const home = mkdtempSync(join(tmpdir(), "gt-home-"));
  const { h, context } = baseContext({
    env: { HOME: home },
  });
  const expected = `~/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils/gt/backups.json`;

  assert.equal(await runBackupCommand(["add", SOURCE], context), 0);
  assert.equal(h.messages.items.at(-1), expected);

  assert.equal(await runBackupCommand(["remove", "1"], context), 0);
  assert.equal(h.messages.items.at(-1), expected);
});

test("runBackupCommand remove by index", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE, SOURCE_B]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });

  const code = await runBackupCommand(["remove", "1"], context);
  assert.equal(code, 0);
  assert.deepEqual(h.messages.titles, ["REPO BACKUP"]);
  assert.ok(h.messages.events.some((event) =>
    event.kind === "step" && event.message === "Remove repository"
  ));
  assert.equal(h.messages.ends.length, 1);
  assert.match(h.messages.statuses.join("\n") + h.messages.lines.join("\n"), new RegExp(SOURCE));
  assert.match(h.messages.items.join("\n"), /gt\/backups\.json/);
  assert.doesNotMatch(h.messages.items.join("\n"), /\/Users\/me\//);

  const leftover = await runBackupCommand(["--all"], context);
  assert.equal(leftover, 0);
  assert.match(h.messages.items.join("\n"), new RegExp(SOURCE_B));
  assert.doesNotMatch(h.messages.items.join("\n"), new RegExp(`ok\\s+${SOURCE}\\s+→`));
});

test("runBackupCommand interactive mock submit backs up selected", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE, SOURCE_B]);
  let capturedListPath;
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async ({ items, render }) => {
      render({ items, cursor: 0, selected: new Set([1]) });
      return {
        type: "submit",
        selected: [items[1].value],
      };
    },
  });
  h.ui.renderBackupSelector = (_heading, _state, opts = {}) => {
    capturedListPath = opts.listPath;
  };

  const code = await runBackupCommand([], context);
  assert.equal(code, 0);
  assertBackupFrameBeforeRepo(h.messages.events);
  assert.match(String(capturedListPath), /gt\/backups\.json/);
  assert.match(h.messages.items.join("\n"), new RegExp(SOURCE_B));
  assert.doesNotMatch(h.messages.items.join("\n"), new RegExp(`ok\\s+${SOURCE}\\s+→`));
});

test("runBackupCommand empty select errors No repos selected", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async () => ({ type: "submit", selected: [] }),
  });

  const code = await runBackupCommand([], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /No repos selected/i);
  assert.equal(h.messages.titles.length, 0);
});

test("runBackupCommand cancel exits 1 without backing up", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE, SOURCE_B]);
  const created = [];
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    runSelector: async ({ items }) => ({
      type: "cancel",
      selected: [],
      state: { items, cursor: 0, selected: new Set() },
    }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
  });

  const code = await runBackupCommand([], context);
  assert.equal(code, 1);
  assert.deepEqual(created, []);
  assert.equal(h.messages.titles.length, 0);
  assert.doesNotMatch(h.messages.statuses.join("\n"), /Backup summary/);
  assert.equal(h.messages.items.length, 0);
});

test("runBackupCommand non-TTY without --all mentions terminal and --all", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: false },
  });

  const code = await runBackupCommand([], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /terminal/i);
  assert.match(h.messages.errors.join("\n"), /--all/);
});

test("runBackupCommand add with no URLs shows usage", async () => {
  const { h, context } = baseContext();
  const code = await runBackupCommand(["add"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /Usage:.*gt backup add/i);
});

test("runBackupCommand add multiple valid URLs exits 0", async () => {
  const paths = tempPaths();
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });

  const code = await runBackupCommand(
    ["add", SOURCE, SOURCE_B],
    context,
  );

  assert.equal(code, 0);
  assert.deepEqual(h.messages.titles, ["REPO BACKUP"]);
  assert.ok(h.messages.events.some((event) =>
    event.kind === "step" && event.message === "Add repositories"
  ));
  assert.equal(h.messages.ends.length, 1);
  const statuses = h.messages.statuses.join("\n");
  assert.match(statuses, new RegExp(`Added ${SOURCE} at index 1`));
  assert.match(statuses, new RegExp(`Added ${SOURCE_B} at index 2`));
  assert.match(h.messages.items.join("\n"), /gt\/backups\.json/);

  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos.length, 2);
});

test("runBackupCommand add valid plus duplicate exits 1 and persists valid", async () => {
  const paths = tempPaths();
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });

  const code = await runBackupCommand(
    ["add", SOURCE, SOURCE],
    context,
  );

  assert.equal(code, 1);
  assert.match(h.messages.statuses.join("\n"), /Added.*index 1/);
  assert.match(h.messages.errors.join("\n"), /duplicate/i);

  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos.length, 1);
  assert.equal(onDisk.repos[0].url, SOURCE);
});

test("runBackupCommand add all invalid exits 1 and leaves file missing", async () => {
  const paths = tempPaths();
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });

  const code = await runBackupCommand(
    ["add", "https://github.com/a.git", "not-a-url"],
    context,
  );

  assert.equal(code, 1);
  assert.equal(h.messages.statuses.filter((m) => /Added/.test(m)).length, 0);
  assert.equal(h.messages.errors.length, 2);
  assert.throws(() => readFileSync(paths.backupsFile, "utf8"), /ENOENT/);
});

test("runBackupCommand rejects --force on add", async () => {
  const { h, context } = baseContext();
  const code = await runBackupCommand(["add", SOURCE, "--force"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /force|Usage|Unknown/i);
});

test("runBackupCommand rejects --force on remove", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });
  const code = await runBackupCommand(["remove", "1", "--force"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /force|Usage|Unknown/i);
});

test("runBackupCommand --all --force passes force through batch", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{
    url: SOURCE,
    lastBackupAt: "2020-01-01T00:00:00.000Z",
    lastCheckedAt: null,
  }]);
  let cloned = false;
  const fixed = new Date("2026-08-08T12:00:00.000Z");
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => fixed,
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") assert.fail("ls-remote should not run when force");
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  const code = await runBackupCommand(["--all", "--force"], context);
  assert.equal(code, 0);
  assert.equal(cloned, true);
  assert.match(h.messages.items.join("\n"), /ok/);
  assert.doesNotMatch(h.messages.items.join("\n"), /skip/);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].lastBackupAt, "2026-08-08T12:00:00.000Z");
  assert.equal(onDisk.repos[0].lastCheckedAt, "2026-08-08T12:00:00.000Z");
});

test("runBackupCommand rejects force and dryRun together", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    dryRun: true,
  });
  const code = await runBackupCommand(["--force"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /force.*dry-run|dry-run.*force/i);
});

test("runBackupCommand unknown flag errors", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });

  const code = await runBackupCommand(["--new"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /unknown|invalid|flag/i);
});

test("runBackupCommand empty list errors with add hint", async () => {
  const paths = tempPaths();
  seedRepos(paths, []);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });

  const code = await runBackupCommand(["--all"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /empty|no repos|not found|missing/i);
  assert.match(h.messages.errors.join("\n"), /gt backup add|backup add/i);
});

test("backupOneRepo dry-run equal fingerprints skips without clone", async () => {
  let cloned = false;
  const { h, context } = baseContext({
    dryRun: true,
    projectExists: async () => ({ ok: true, exists: true }),
    createPrivateProject: async () => assert.fail("createPrivateProject should not run"),
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        return { status: 0, stdout: "abc\trefs/heads/main\n", stderr: "" };
      }
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  const result = await backupOneRepo(SOURCE, context);
  assert.equal(result.ok, true);
  assert.equal(result.skipped, true);
  assert.equal(result.dryRun, true);
  assert.equal(cloned, false);
  assert.match(h.messages.statuses.join("\n"), /would skip|unchanged/i);
});

test("backupOneRepo dry-run missing project plans mirror without create", async () => {
  const created = [];
  let cloned = false;
  const { h, context } = baseContext({
    dryRun: true,
    projectExists: async () => ({ ok: true, exists: false }),
    createPrivateProject: async (_g, name) => {
      created.push(name);
      return { ok: true };
    },
    runGit: async (args) => {
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  const result = await backupOneRepo(SOURCE, context);
  assert.equal(result.ok, true);
  assert.equal(result.skipped, false);
  assert.equal(result.dryRun, true);
  assert.deepEqual(created, []);
  assert.equal(cloned, false);
  assert.match(h.messages.statuses.join("\n"), /would create|would mirror/i);
});

test("runBackupBatch dry-run skip leaves timestamps unchanged", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{
    url: SOURCE,
    lastBackupAt: "2020-01-01T00:00:00.000Z",
    lastCheckedAt: null,
  }]);
  let cloned = false;
  const { h, context } = baseContext({
    dryRun: true,
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        return { status: 0, stdout: "abc\trefs/heads/main\n", stderr: "" };
      }
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  const code = await runBackupBatch([SOURCE], context);
  assert.equal(code, 0);
  assert.equal(cloned, false);
  assert.match(h.messages.statuses.join("\n"), /Dry run \(no changes\)/);
  const items = h.messages.items.join("\n");
  assert.match(items, /skip/);
  assert.match(items, /would skip \(unchanged\)/);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].lastBackupAt, "2020-01-01T00:00:00.000Z");
  assert.equal(onDisk.repos[0].lastCheckedAt, null);
});

test("runBackupCommand --all --dry-run --force exits 1", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });
  const code = await runBackupCommand(["--all", "--dry-run", "--force"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /force.*dry-run|dry-run.*force/i);
});

test("runBackupCommand interactive dry-run does not persist selectedLast", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastBackupAt: null, lastCheckedAt: null, selectedLast: false },
    { url: SOURCE_B, lastBackupAt: null, lastCheckedAt: null, selectedLast: false },
  ]);
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    stdin: { isTTY: true },
    dryRun: true,
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") {
        return { status: 0, stdout: "abc\trefs/heads/main\n", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
    runSelector: async ({ items }) => ({
      type: "submit",
      selected: [items[1].value],
    }),
  });
  await runBackupCommand([], context);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].selectedLast, false);
  assert.equal(onDisk.repos[1].selectedLast, false);
});

test("runBackupCommand rejects --dry-run on add", async () => {
  const { h, context } = baseContext();
  const code = await runBackupCommand(["add", SOURCE, "--dry-run"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /only valid for interactive backup/i);
});

test("runBackupCommand --all --dry-run plans mirror with ok summary", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{ url: SOURCE, lastBackupAt: null, lastCheckedAt: null }]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    projectExists: async () => ({ ok: true, exists: false }),
    runGit: async () => ({ status: 0, stdout: "", stderr: "" }),
  });
  const code = await runBackupCommand(["--all", "--dry-run"], context);
  assert.equal(code, 0);
  assertBackupFrameBeforeRepo(h.messages.events, { dryRun: true });
  const items = h.messages.items.join("\n");
  assert.match(items, /ok/);
  assert.match(items, /would mirror/);
  assert.doesNotMatch(items, new RegExp(`→\\s+${projectWebUrl(BACKUP_GROUP, BASE_NAME)}`));
});

const FIXED_NOW = new Date("2026-08-08T12:00:00.000Z");
const RECENT_CHECKED = "2026-08-07T12:00:00.000Z";
const OLD_CHECKED = "2026-08-01T00:00:00.000Z";

test("runBackupCommand stale with no stale repos prints message and exits 0", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastCheckedAt: RECENT_CHECKED },
    { url: SOURCE_B, lastCheckedAt: RECENT_CHECKED },
  ]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
  });
  const code = await runBackupCommand(["stale"], context);
  assert.equal(code, 0);
  assert.match(h.messages.statuses.join("\n"), /No stale repos/);
  assert.ok(h.messages.events.some((event) =>
    event.kind === "status" && event.message === "No stale repos" && event.tone === "muted"
  ));
  assert.deepEqual(h.messages.titles, ["REPO BACKUP"]);
  assert.ok(h.messages.events.some((event) =>
    event.kind === "step" && event.message === "Backup repositories"
  ));
  assert.ok(h.messages.events.some((event) =>
    event.kind === "detail" && /backups\.json$/.test(event.message)
  ));
  assert.equal(h.messages.ends.length, 1);
});

test("runBackupCommand renders no stale repos as a muted absent item", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{ url: SOURCE, lastCheckedAt: RECENT_CHECKED }]);
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
    ui,
  });

  assert.equal(await runBackupCommand(["stale"], context), 0);
  assert.match(stdout, /\u001b\[90m□\u001b\[39m No stale repos/);
});

test("runBackupCommand stale --all backs up only stale URLs in list order", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastCheckedAt: null },
    { url: SOURCE_B, lastCheckedAt: RECENT_CHECKED },
  ]);
  const backedUp = [];
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
    recordLastBackupAt: (p, url) => {
      backedUp.push(url);
      return { ok: true, document: { version: 4, repos: [] } };
    },
  });
  const code = await runBackupCommand(["stale", "--all"], context);
  assert.equal(code, 0);
  assertBackupFrameBeforeRepo(h.messages.events);
  assert.deepEqual(backedUp, [SOURCE]);
  assert.match(h.messages.items.join("\n"), new RegExp(`ok\\s+${SOURCE}`));
  assert.doesNotMatch(h.messages.items.join("\n"), new RegExp(`ok\\s+${SOURCE_B}`));
});

test("runBackupCommand stale interactive shows only stale repos", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastCheckedAt: null },
    { url: SOURCE_B, lastCheckedAt: RECENT_CHECKED },
  ]);
  let capturedItems;
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
    stdin: { isTTY: true },
    runSelector: async ({ items }) => {
      capturedItems = items;
      return { type: "submit", selected: [items[0].value] };
    },
    setSelectedLast: () => ({ ok: true, document: { version: 4, repos: [] } }),
    recordLastBackupAt: () => ({ ok: true, document: { version: 4, repos: [] } }),
  });
  await runBackupCommand(["stale"], context);
  assertBackupFrameBeforeRepo(h.messages.events);
  assert.equal(capturedItems.length, 1);
  assert.equal(capturedItems[0].value, SOURCE);
});

test("runBackupCommand stale submit rewrites selectedLast on full list", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastCheckedAt: null, selectedLast: false },
    { url: SOURCE_B, lastCheckedAt: RECENT_CHECKED, selectedLast: true },
  ]);
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
    stdin: { isTTY: true },
    runSelector: async ({ items }) => ({
      type: "submit",
      selected: [items[0].value],
    }),
    recordLastBackupAt: () => ({ ok: true, document: { version: 4, repos: [] } }),
  });
  await runBackupCommand(["stale"], context);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].selectedLast, true);
  assert.equal(onDisk.repos[1].selectedLast, false);
});

test("runBackupCommand stale --days 1 changes stale set", async () => {
  const paths = tempPaths();
  seedRepos(paths, [
    { url: SOURCE, lastCheckedAt: "2026-08-07T11:00:00.000Z" },
    { url: SOURCE_B, lastCheckedAt: RECENT_CHECKED },
  ]);
  let capturedItems;
  const { context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
    stdin: { isTTY: true },
    runSelector: async ({ items }) => {
      capturedItems = items;
      return {
        type: "cancel",
        selected: [],
        state: { items, cursor: 0, selected: new Set() },
      };
    },
  });
  await runBackupCommand(["stale", "--days", "1"], context);
  assert.equal(capturedItems.length, 1);
  assert.equal(capturedItems[0].value, SOURCE);
});

test("runBackupCommand stale --days invalid errors", async () => {
  const paths = tempPaths();
  seedRepos(paths, [SOURCE]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
  });
  const code = await runBackupCommand(["stale", "--days", "abc"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /Invalid --days|positive integer/i);
});

test("runBackupCommand stale --all --force passes force through batch", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{
    url: SOURCE,
    lastBackupAt: "2020-01-01T00:00:00.000Z",
    lastCheckedAt: null,
  }]);
  let cloned = false;
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
    projectExists: async () => ({ ok: true, exists: true }),
    runGit: async (args) => {
      if (args[0] === "ls-remote") assert.fail("ls-remote should not run when force");
      if (args[0] === "clone") {
        cloned = true;
        return { status: 0, stdout: "", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  const code = await runBackupCommand(["stale", "--all", "--force"], context);
  assert.equal(code, 0);
  assert.equal(cloned, true);
  assert.match(h.messages.items.join("\n"), /ok/);
});

test("runBackupCommand stale --all --dry-run plans without writes", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{ url: SOURCE, lastCheckedAt: null }]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
    projectExists: async () => ({ ok: true, exists: false }),
    runGit: async () => ({ status: 0, stdout: "", stderr: "" }),
  });
  const code = await runBackupCommand(["stale", "--all", "--dry-run"], context);
  assert.equal(code, 0);
  assertBackupFrameBeforeRepo(h.messages.events, { dryRun: true });
  assert.match(h.messages.statuses.join("\n"), /Dry run/);
  const onDisk = JSON.parse(readFileSync(paths.backupsFile, "utf8"));
  assert.equal(onDisk.repos[0].lastBackupAt, null);
});

test("runBackupCommand stale non-TTY mentions stale --all", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{ url: SOURCE, lastCheckedAt: null }]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
    stdin: { isTTY: false },
  });
  const code = await runBackupCommand(["stale"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /terminal/i);
  assert.match(h.messages.errors.join("\n"), /stale --all/);
});

test("runBackupCommand stale rejects --force and --dry-run together", async () => {
  const paths = tempPaths();
  seedRepos(paths, [{ url: SOURCE, lastCheckedAt: null }]);
  const { h, context } = baseContext({
    env: { CLOUD_UTILS_CONFIG_DIR: paths.configDir, HOME: "/Users/me" },
    now: () => FIXED_NOW,
  });
  const code = await runBackupCommand(["stale", "--all", "--force", "--dry-run"], context);
  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /force.*dry-run|dry-run.*force/i);
});
