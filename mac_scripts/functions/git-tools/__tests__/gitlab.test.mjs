import assert from "node:assert/strict";
import test from "node:test";
import {
  BACKUP_GROUP,
  GITLAB_HOST,
  assertGlabReady,
  createPrivateProject,
  nextAvailableName,
  nextSuffixedName,
  projectExists,
  projectSshUrl,
  projectWebUrl,
} from "../gitlab.mjs";

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
      return { status: 0, stdout: "{}", stderr: "" };
    },
  });

  assert.deepEqual(result, { ok: true, exists: true });
  assert.deepEqual(received, ["api", "projects/team%20name%2Fmy%20app"]);
});

test("projectExists maps a not-found API response to exists false", async () => {
  const result = await projectExists("anngth-backups", "missing", {
    runGlab: async () => ({ status: 1, stdout: "", stderr: "404 Not Found" }),
  });

  assert.deepEqual(result, { ok: true, exists: false });
});

test("projectExists returns an error for unrelated API failures", async () => {
  const result = await projectExists("anngth-backups", "broken", {
    runGlab: async () => ({ status: 1, stdout: "", stderr: "connection refused" }),
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /connection refused/i);
});

test("createPrivateProject uses a private empty-project glab command", async () => {
  let received;
  const result = await createPrivateProject("anngth-backups", "my-app", {
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
    "anngth-backups",
    "--private",
    "--skipGitInit",
  ]);
});

test("nextAvailableName returns the base name when it is free", async () => {
  const result = await nextAvailableName("anngth-backups", "my-app", {
    projectExists: async () => ({ ok: true, exists: false }),
  });

  assert.deepEqual(result, { ok: true, name: "my-app" });
});

test("nextSuffixedName starts at suffix two and skips taken names", async () => {
  const taken = new Set(["my-app", "my-app-2", "my-app-3"]);

  const result = await nextSuffixedName("anngth-backups", "my-app", {
    projectExists: async (_group, name) => ({ ok: true, exists: taken.has(name) }),
  });

  assert.deepEqual(result, { ok: true, name: "my-app-4" });
});

test("nextSuffixedName does not return the base name when it is free", async () => {
  const result = await nextSuffixedName("anngth-backups", "my-app", {
    projectExists: async () => ({ ok: true, exists: false }),
  });

  assert.deepEqual(result, { ok: true, name: "my-app-2" });
});

test("nextSuffixedName returns structured error on mid-walk API failure", async () => {
  const result = await nextSuffixedName("anngth-backups", "my-app", {
    projectExists: async (_group, name) => {
      if (name === "my-app-2") return { ok: true, exists: true };
      return { ok: false, error: "connection refused" };
    },
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /connection refused/i);
});

test("URL helpers use the fixed GitLab host", () => {
  assert.equal(BACKUP_GROUP, "anngth-backups");
  assert.equal(GITLAB_HOST, "gitlab.com");
  assert.equal(projectSshUrl("group", "my-app"), "git@gitlab.com:group/my-app.git");
  assert.equal(projectWebUrl("group", "my-app"), "https://gitlab.com/group/my-app");
});
