import assert from "node:assert/strict";
import test from "node:test";
import { chooseCollisionAction, runBackupCommand } from "../backup.mjs";
import { BACKUP_GROUP, projectSshUrl, projectWebUrl } from "../gitlab.mjs";

const SOURCE = "git@github.com:org/app.git";
const BASE_NAME = "org-app";

function uiHarness() {
  const messages = { errors: [], lines: [], statuses: [] };
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
      chooseCollisionAction: async () => "cancel",
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
  assert.ok(
    h.messages.lines.concat(h.messages.statuses).some((m) =>
      String(m).includes(projectWebUrl(BACKUP_GROUP, BASE_NAME))
    ),
  );
  assert.ok(removed.length >= 1);
});

test("collision cancel does not create or push", async () => {
  const gitCalls = [];
  const created = [];
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
    chooseCollisionAction: async () => "cancel",
    runGit: async (args) => {
      gitCalls.push(args);
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 1);
  assert.deepEqual(created, []);
  assert.deepEqual(gitCalls, []);
});

test("collision update mirror-pushes existing project without create", async () => {
  const gitCalls = [];
  const created = [];
  const { context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
    chooseCollisionAction: async () => "update",
    runGit: async (args) => {
      gitCalls.push(args);
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 0);
  assert.deepEqual(created, []);
  assert.ok(gitCalls.some((a) => a[0] === "clone" && a.includes("--mirror")));
  const pushCall = gitCalls.find((a) => a[0] === "push");
  assert.ok(pushCall);
  assert.ok(pushCall.includes(projectSshUrl(BACKUP_GROUP, BASE_NAME)));
});

test("collision new uses nextSuffixedName then creates and pushes", async () => {
  const gitCalls = [];
  const created = [];
  let nextArgs;
  const newName = `${BASE_NAME}-2`;
  const { h, context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    nextSuffixedName: async (group, baseName) => {
      nextArgs = { group, baseName };
      return { ok: true, name: newName };
    },
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
    chooseCollisionAction: async () => "new",
    runGit: async (args) => {
      gitCalls.push(args);
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 0);
  assert.deepEqual(nextArgs, { group: BACKUP_GROUP, baseName: BASE_NAME });
  assert.deepEqual(created, [newName]);
  const pushCall = gitCalls.find((a) => a[0] === "push");
  assert.ok(pushCall.includes(projectSshUrl(BACKUP_GROUP, newName)));
  assert.ok(
    h.messages.lines.concat(h.messages.statuses).some((m) =>
      String(m).includes(projectWebUrl(BACKUP_GROUP, newName))
    ),
  );
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
    chooseCollisionAction: async () => "new",
  });

  const code = await runBackupCommand([SOURCE], context);

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

test("collision required but non-TTY exits 1 with TTY message", async () => {
  const created = [];
  const gitCalls = [];
  const { h, context } = baseContext({
    projectExists: async () => ({ ok: true, exists: true }),
    createPrivateProject: async (_group, name) => {
      created.push(name);
      return { ok: true };
    },
    chooseCollisionAction: async () => "update",
    runGit: async (args) => {
      gitCalls.push(args);
      return { status: 0, stdout: "", stderr: "" };
    },
    stdin: { isTTY: false },
  });

  const code = await runBackupCommand([SOURCE], context);

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /tty|interactive|terminal/i);
  assert.deepEqual(created, []);
  assert.deepEqual(gitCalls, []);
});

test("chooseCollisionAction accepts numbered and named choices", async () => {
  const cases = [
    ["1\n", "update"],
    ["update\n", "update"],
    ["2\n", "new"],
    ["new\n", "new"],
    ["3\n", "cancel"],
    ["cancel\n", "cancel"],
  ];

  for (const [input, expected] of cases) {
    const chunks = [input];
    const stdin = {
      isTTY: true,
      [Symbol.asyncIterator]: async function* () {
        yield* chunks;
      },
    };
    const written = [];
    const stdout = {
      write(chunk) {
        written.push(String(chunk));
      },
    };

    const action = await chooseCollisionAction({
      projectPath: `${BACKUP_GROUP}/${BASE_NAME}`,
      baseName: BASE_NAME,
      stdin,
      stdout,
      isTTY: true,
    });

    assert.equal(action, expected);
    assert.match(written.join(""), /1\)\s*update/);
    assert.match(written.join(""), /2\)\s*new/);
    assert.match(written.join(""), /3\)\s*cancel/);
    assert.match(written.join(""), /Choose \[1\/2\/3\]:/);
  }
});
