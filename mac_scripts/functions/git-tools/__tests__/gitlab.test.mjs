import assert from "node:assert/strict";
import test from "node:test";
import {
  BACKUP_GROUP,
  GITLAB_HOST,
  assertGlabReady,
  createPrivateProject,
  ensureBackupGroup,
  groupExists,
  nextAvailableName,
  nextSuffixedName,
  pickPreferredDefaultBranch,
  projectExists,
  projectSshUrl,
  projectWebUrl,
  setDefaultBranch,
} from "../gitlab.mjs";

const GROUP = "anngth-dev/backups";

test("assertGlabReady fails when glab is missing", async () => {
  const result = await assertGlabReady({ hasCommand: () => false });

  assert.equal(result.ok, false);
  assert.match(result.error, /glab/i);
});

test("assertGlabReady fails when not authenticated", async () => {
  const result = await assertGlabReady({
    hasCommand: () => true,
    runGlab: async (args) => {
      if (args[0] === "auth" && args[1] === "status") {
        return { status: 1, stdout: "", stderr: "not logged in" };
      }
      return { status: 1, stdout: "", stderr: "" };
    },
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /auth|login/i);
});

test("assertGlabReady succeeds when glab is available and authenticated", async () => {
  const result = await assertGlabReady({
    hasCommand: () => true,
    runGlab: async () => ({ status: 0, stdout: "Logged in", stderr: "" }),
  });

  assert.deepEqual(result, { ok: true });
});

test("projectExists returns true for a successful API response", async () => {
  let received;
  const result = await projectExists("team name", "my app", {
    runGlab: async (args) => {
      received = args;
      return {
        status: 0,
        stdout: JSON.stringify({ path: "my app", marked_for_deletion_on: null }),
        stderr: "",
      };
    },
  });

  assert.deepEqual(result, { ok: true, exists: true });
  assert.deepEqual(received, ["api", "projects/team%20name%2Fmy%20app"]);
});

test("projectExists maps a not-found API response to exists false", async () => {
  const result = await projectExists(GROUP, "missing", {
    runGlab: async () => ({ status: 1, stdout: "", stderr: "404 Not Found" }),
  });

  assert.deepEqual(result, { ok: true, exists: false });
});

test("projectExists treats marked_for_deletion_on as inactive (not live)", async () => {
  const result = await projectExists(GROUP, "vinova-project-sit-sit-be", {
    runGlab: async () => ({
      status: 0,
      stdout: JSON.stringify({
        path: "vinova-project-sit-sit-be-deletion_scheduled-85217875",
        marked_for_deletion_on: "2026-08-07",
      }),
      stderr: "",
    }),
  });

  assert.deepEqual(result, { ok: true, exists: false, inactive: true });
});

test("projectExists treats marked_for_deletion_at as inactive", async () => {
  const result = await projectExists(GROUP, "my-app", {
    runGlab: async () => ({
      status: 0,
      stdout: JSON.stringify({
        path: "my-app",
        marked_for_deletion_at: "2026-08-07",
        marked_for_deletion_on: null,
      }),
      stderr: "",
    }),
  });

  assert.deepEqual(result, { ok: true, exists: false, inactive: true });
});

test("projectExists treats deletion_scheduled path rename as inactive without marked field", async () => {
  const result = await projectExists(GROUP, "my-app", {
    runGlab: async () => ({
      status: 0,
      stdout: JSON.stringify({
        path: "my-app-deletion_scheduled-99",
      }),
      stderr: "",
    }),
  });

  assert.deepEqual(result, { ok: true, exists: false, inactive: true });
});

test("projectExists treats -deleted-<id> path rename as inactive", async () => {
  const result = await projectExists(GROUP, "my-app", {
    runGlab: async () => ({
      status: 0,
      stdout: JSON.stringify({
        path: "my-app-deleted-42",
      }),
      stderr: "",
    }),
  });

  assert.deepEqual(result, { ok: true, exists: false, inactive: true });
});

test("projectExists keeps scheduled path lookup inactive via marked even when path matches request", async () => {
  const result = await projectExists(GROUP, "my-app-deletion_scheduled-99", {
    runGlab: async () => ({
      status: 0,
      stdout: JSON.stringify({
        path: "my-app-deletion_scheduled-99",
        marked_for_deletion_on: "2026-08-07",
      }),
      stderr: "",
    }),
  });

  assert.deepEqual(result, { ok: true, exists: false, inactive: true });
});

test("projectExists returns an error for unrelated API failures", async () => {
  const result = await projectExists(GROUP, "broken", {
    runGlab: async () => ({ status: 1, stdout: "", stderr: "connection refused" }),
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /connection refused/i);
});

test("projectExists returns an error when project JSON is invalid", async () => {
  const result = await projectExists(GROUP, "broken-json", {
    runGlab: async () => ({ status: 0, stdout: "not-json", stderr: "" }),
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /parse/i);
});

test("groupExists uses encoded nested group path", async () => {
  let received;
  const result = await groupExists(GROUP, {
    runGlab: async (args) => {
      received = args;
      return { status: 0, stdout: "{}", stderr: "" };
    },
  });

  assert.deepEqual(result, { ok: true, exists: true });
  assert.deepEqual(received, ["api", "groups/anngth-dev%2Fbackups"]);
});

test("ensureBackupGroup creates nested backups subgroup when missing", async () => {
  const calls = [];
  const result = await ensureBackupGroup(GROUP, {
    runGlab: async (args) => {
      calls.push(args);
      if (args[0] === "api" && args[1] === "groups/anngth-dev%2Fbackups") {
        return { status: 1, stdout: "", stderr: "404 Group Not Found" };
      }
      if (args[0] === "api" && args[1] === "groups/anngth-dev") {
        return { status: 0, stdout: JSON.stringify({ id: 136327837 }), stderr: "" };
      }
      return { status: 0, stdout: "{}", stderr: "" };
    },
  });

  assert.deepEqual(result, { ok: true, created: true });
  assert.deepEqual(calls[0], ["api", "groups/anngth-dev%2Fbackups"]);
  assert.deepEqual(calls[1], ["api", "groups/anngth-dev"]);
  assert.deepEqual(calls[2], [
    "api",
    "--method",
    "POST",
    "groups",
    "-f",
    "name=backups",
    "-f",
    "path=backups",
    "-f",
    "parent_id=136327837",
    "-f",
    "visibility=private",
  ]);
});

test("ensureBackupGroup skips create when the group already exists", async () => {
  const calls = [];
  const result = await ensureBackupGroup(GROUP, {
    runGlab: async (args) => {
      calls.push(args);
      return { status: 0, stdout: "{}", stderr: "" };
    },
  });

  assert.deepEqual(result, { ok: true, created: false });
  assert.equal(calls.length, 1);
});

test("createPrivateProject uses a private empty-project glab command", async () => {
  let received;
  const result = await createPrivateProject(GROUP, "my-app", {
    runGlab: async (args) => {
      received = args;
      return { status: 0, stdout: "created", stderr: "" };
    },
  });

  assert.deepEqual(result, { ok: true, stdout: "created", stderr: "" });
  assert.deepEqual(received, [
    "repo",
    "create",
    "my-app",
    "--group",
    GROUP,
    "--private",
    "--skipGitInit",
  ]);
});

test("nextAvailableName returns the base name when it is free", async () => {
  const result = await nextAvailableName(GROUP, "my-app", {
    projectExists: async () => ({ ok: true, exists: false }),
  });

  assert.deepEqual(result, { ok: true, name: "my-app" });
});

test("nextSuffixedName starts at suffix two and skips taken names", async () => {
  const taken = new Set(["my-app", "my-app-2", "my-app-3"]);

  const result = await nextSuffixedName(GROUP, "my-app", {
    projectExists: async (_group, name) => ({ ok: true, exists: taken.has(name) }),
  });

  assert.deepEqual(result, { ok: true, name: "my-app-4" });
});

test("nextSuffixedName does not return the base name when it is free", async () => {
  const result = await nextSuffixedName(GROUP, "my-app", {
    projectExists: async () => ({ ok: true, exists: false }),
  });

  assert.deepEqual(result, { ok: true, name: "my-app-2" });
});

test("nextSuffixedName returns structured error on mid-walk API failure", async () => {
  const result = await nextSuffixedName(GROUP, "my-app", {
    projectExists: async (_group, name) => {
      if (name === "my-app-2") return { ok: true, exists: true };
      return { ok: false, error: "connection refused" };
    },
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /connection refused/i);
});

test("URL helpers use nested backup group and fixed GitLab host", () => {
  assert.equal(BACKUP_GROUP, "anngth-dev/backups");
  assert.equal(GITLAB_HOST, "gitlab.com");
  assert.equal(
    projectSshUrl(BACKUP_GROUP, "my-app"),
    "git@gitlab.com:anngth-dev/backups/my-app.git",
  );
  assert.equal(
    projectWebUrl(BACKUP_GROUP, "my-app"),
    "https://gitlab.com/anngth-dev/backups/my-app",
  );
});

test("setDefaultBranch updates the GitLab project default branch", async () => {
  let received;
  const result = await setDefaultBranch(GROUP, "my-app", "main", {
    runGlab: async (args) => {
      received = args;
      return { status: 0, stdout: "{}", stderr: "" };
    },
  });

  assert.deepEqual(result, { ok: true });
  assert.deepEqual(received, [
    "api",
    "--method",
    "PUT",
    `projects/${encodeURIComponent(`${GROUP}/my-app`)}`,
    "-f",
    "default_branch=main",
  ]);
});

test("pickPreferredDefaultBranch prefers main over develop", async () => {
  const checked = [];
  const branch = await pickPreferredDefaultBranch("/mirror.git", {
    runGit: async (args, { cwd }) => {
      checked.push([args.at(-1), cwd]);
      return { status: 0, stdout: "", stderr: "" };
    },
  });

  assert.equal(branch, "main");
  assert.deepEqual(checked[0], ["refs/heads/main", "/mirror.git"]);
});

test("pickPreferredDefaultBranch falls back to develop", async () => {
  const branch = await pickPreferredDefaultBranch("/mirror.git", {
    runGit: async (args) => {
      const ref = args.at(-1);
      return {
        status: ref === "refs/heads/develop" ? 0 : 1,
        stdout: "",
        stderr: "",
      };
    },
  });

  assert.equal(branch, "develop");
});

test("pickPreferredDefaultBranch returns null when neither exists", async () => {
  const branch = await pickPreferredDefaultBranch("/mirror.git", {
    runGit: async () => ({ status: 1, stdout: "", stderr: "" }),
  });

  assert.equal(branch, null);
});
