import assert from "node:assert/strict";
import test from "node:test";
import { parseBackupArgs, runBackupCommand } from "../backup.mjs";
import { BACKUP_GROUP, projectSshUrl, projectWebUrl } from "../gitlab.mjs";

const SOURCE = "git@github.com:org/app.git";
const BASE_NAME = "org-app";

function uiHarness() {
  const messages = { errors: [], lines: [], statuses: [], warnings: [], ends: [] };
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
      item() {},
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
      nextSuffixedName: async () => ({ ok: true, name: `${BASE_NAME}-2` }),
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

test("parseBackupArgs accepts url only (createNew false)", () => {
  const r = parseBackupArgs(["git@github.com:org/app.git"]);
  assert.deepEqual(r, {
    ok: true,
    sshUrl: "git@github.com:org/app.git",
    createNew: false,
  });
});

test("parseBackupArgs accepts -n and --new before or after url", () => {
  for (const args of [
    ["-n", "git@github.com:org/app.git"],
    ["--new", "git@github.com:org/app.git"],
    ["git@github.com:org/app.git", "-n"],
  ]) {
    const r = parseBackupArgs(args);
    assert.equal(r.ok, true);
    assert.equal(r.createNew, true);
    assert.equal(r.sshUrl, "git@github.com:org/app.git");
  }
});

test("parseBackupArgs rejects unknown flags and bad arity", () => {
  assert.equal(parseBackupArgs(["--update", "git@x:o/r.git"]).ok, false);
  assert.equal(parseBackupArgs([]).ok, false);
  assert.equal(parseBackupArgs(["git@x:o/r.git", "git@x:o/r2.git"]).ok, false);
});

test("missing arg exits 1 with usage hint", async () => {
  const { h, context } = baseContext();

  const code = await runBackupCommand([], context);

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /usage|backup/i);
});

test("bad URL exits 1", async () => {
  const { h, context } = baseContext();

  const code = await runBackupCommand(["https://github.com/org/app.git"], context);

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /https|ssh|invalid/i);
});

test("git missing exits 1", async () => {
  const { h, context } = baseContext({
    hasCommand: (name) => name === "glab",
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /git/i);
});

test("ensureBackupGroup failure exits 1 before project create", async () => {
  const created = [];
  const { h, context } = baseContext({
    ensureBackupGroup: async () => ({ ok: false, error: "failed to create GitLab subgroup" }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 1);
  assert.deepEqual(created, []);
  assert.match(h.messages.errors.join("\n"), /group|subgroup/i);
});

test("ensures backup group before checking project", async () => {
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

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 0);
  assert.deepEqual(steps[0], ["ensure", BACKUP_GROUP]);
  assert.equal(steps[1][0], "exists");
});

test("creates private project and mirrors when missing", async () => {
  const gitCalls = [];
  const created = [];
  const { h, removed, context } = baseContext({
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

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 0);
  assert.deepEqual(created, [BASE_NAME]);
  assert.ok(gitCalls.some((a) => a[0] === "clone" && a.includes("--mirror")));
  const pushCall = gitCalls.find((a) => a[0] === "push");
  assert.ok(pushCall);
  assert.ok(!pushCall.includes("--mirror"));
  assert.ok(pushCall.includes("--prune"));
  assert.ok(pushCall.includes("+refs/heads/*:refs/heads/*"));
  assert.ok(pushCall.includes("+refs/tags/*:refs/tags/*"));
  assert.ok(pushCall.includes(projectSshUrl(BACKUP_GROUP, BASE_NAME)));
  assert.ok(h.messages.ends.some((m) => String(m).includes(projectWebUrl(BACKUP_GROUP, BASE_NAME))));
  assert.ok(removed.length >= 1);
});

test("sets preferred default branch after push", async () => {
  const defaults = [];
  const { h, context } = baseContext({
    pickPreferredDefaultBranch: async () => "develop",
    setDefaultBranch: async (group, name, branch) => {
      defaults.push([group, name, branch]);
      return { ok: true };
    },
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 0);
  assert.deepEqual(defaults, [[BACKUP_GROUP, BASE_NAME, "develop"]]);
  assert.ok(h.messages.statuses.some((m) => /default branch develop/i.test(m)));
});

test("prints concise progress including clone path", async () => {
  const { h, context } = baseContext({
    mkdtempSync: () => "/tmp/gt-backup-test",
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 0);
  const statuses = h.messages.statuses.join("\n");
  assert.match(statuses, new RegExp(`${SOURCE} → ${BACKUP_GROUP}/${BASE_NAME}`));
  assert.match(statuses, /Created /);
  assert.match(statuses, /Cloning source to \/tmp\/gt-backup-test\/mirror\.git/);
  assert.match(statuses, /Pushing all branches \+ tags → /);
  assert.doesNotMatch(statuses, /Checking backup group|Mirror clone complete|Cleaning up|Backup finished/);
});

test("existing project without --new updates in place", async () => {
  const created = [];
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    createPrivateProject: async (_g, name) => {
      created.push(name);
      return { ok: true };
    },
    stdin: { isTTY: false }, // must still succeed — no prompt
  });
  const code = await runBackupCommand([SOURCE], context);
  assert.equal(code, 0);
  assert.deepEqual(created, []); // no new project
});

test("inactive pending-deletion project creates a new backup at the base name", async () => {
  const created = [];
  const { h, context } = baseContext({
    projectExists: async () => ({ ok: true, exists: false, inactive: true }),
    createPrivateProject: async (_g, name) => {
      created.push(name);
      return { ok: true };
    },
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 0);
  assert.deepEqual(created, [BASE_NAME]);
  assert.match(
    h.messages.statuses.join("\n"),
    /pending deletion|inactive/i,
  );
  assert.match(h.messages.statuses.join("\n"), /Created /);
});

test("existing project with --new creates suffixed project", async () => {
  const created = [];
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    nextSuffixedName: async () => ({ ok: true, name: `${BASE_NAME}-2` }),
    createPrivateProject: async (_g, name) => {
      created.push(name);
      return { ok: true };
    },
  });
  const code = await runBackupCommand([SOURCE, "--new"], context);
  assert.equal(code, 0);
  assert.deepEqual(created, [`${BASE_NAME}-2`]);
});

test("collision new exits 1 when nextSuffixedName fails mid-walk", async () => {
  const created = [];
  const { h, context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    nextSuffixedName: async () => ({ ok: false, error: "connection refused" }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
  });

  const code = await runBackupCommand([SOURCE, "--new"], context);

  assert.equal(code, 1);
  assert.deepEqual(created, []);
  assert.match(h.messages.errors.join("\n"), /connection refused/i);
});

test("clone failure still removes temp dir", async () => {
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

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 1);
  assert.ok(removed.some((r) => r.path === tempDir && r.opts?.recursive === true));
});
