import assert from "node:assert/strict";
import test from "node:test";
import { backupOneRepo, runBackupBatch } from "../backup.mjs";
import { BACKUP_GROUP, projectSshUrl, projectWebUrl } from "../gitlab.mjs";

const SOURCE = "git@github.com:org/app.git";
const SOURCE_B = "git@github.com:org/other.git";
const BASE_NAME = "org-app";
const BASE_NAME_B = "org-other";

function uiHarness() {
  const messages = { errors: [], lines: [], statuses: [], warnings: [], ends: [], items: [] };
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
      status(message) {
        messages.statuses.push(message);
      },
      step(message) {
        messages.statuses.push(message);
      },
      success(message) {
        messages.statuses.push(message);
      },
      title() {},
      active() {},
      item(message) {
        messages.items.push(message);
      },
      warn(message) {
        messages.warnings.push(message);
      },
      listEnd(message = "") {
        messages.ends.push(message);
      },
      line(message = "") {
        messages.lines.push(message);
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
      projectExists: async () => ({ ok: true, exists: false }),
      createPrivateProject: async (_group, name) => {
        created.push(name);
        return { ok: true };
      },
      pickPreferredDefaultBranch: async () => "main",
      setDefaultBranch: async () => ({ ok: true }),
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

test("backupOneRepo prints concise progress including clone path", async () => {
  const { h, context } = baseContext({
    mkdtempSync: () => "/tmp/gt-backup-test",
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
  const statuses = h.messages.statuses.join("\n");
  assert.match(statuses, new RegExp(`${SOURCE} → ${BACKUP_GROUP}/${BASE_NAME}`));
  assert.match(statuses, /Created /);
  assert.match(statuses, /Cloning source to \/tmp\/gt-backup-test\/mirror\.git/);
  assert.match(statuses, /Pushing all branches \+ tags → /);
  assert.doesNotMatch(statuses, /Checking backup group|Mirror clone complete|Cleaning up|Backup finished/);
});

test("backupOneRepo updates live existing project", async () => {
  const created = [];
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    createPrivateProject: async (_g, name) => {
      created.push(name);
      return { ok: true };
    },
    stdin: { isTTY: false },
  });

  const result = await backupOneRepo(SOURCE, context);

  assert.equal(result.ok, true);
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
  const statuses = h.messages.statuses.join("\n");
  assert.match(statuses, /Backup summary/);
  const items = h.messages.items.join("\n");
  assert.match(items, new RegExp(`ok\\s+${SOURCE_B}\\s+→\\s+${projectWebUrl(BACKUP_GROUP, BASE_NAME_B)}`));
  assert.match(items, new RegExp(`fail\\s+${SOURCE}\\s+—\\s+project lookup failed`));
  assert.equal(h.messages.ends.length >= 1, true);
});

test("runBackupBatch returns 0 when all succeed", async () => {
  const { h, context } = baseContext();

  const code = await runBackupBatch([SOURCE], context);

  assert.equal(code, 0);
  assert.match(h.messages.statuses.join("\n"), /Backup summary/);
  assert.match(
    h.messages.items.join("\n"),
    new RegExp(`ok\\s+${SOURCE}\\s+→\\s+${projectWebUrl(BACKUP_GROUP, BASE_NAME)}`),
  );
});
