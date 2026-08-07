import assert from "node:assert/strict";
import test from "node:test";
import { runPushCommand } from "../push.mjs";

function uiHarness() {
  const messages = { errors: [], statuses: [] };
  return {
    messages,
    ui: {
      error(message) { messages.errors.push(message); },
      status(message) { messages.statuses.push(message); },
      usage() {},
      usageLine(message) { messages.errors.push(message); },
    },
  };
}

test("rejects when not a git repository", async () => {
  const calls = [];
  const runGit = async (args) => {
    calls.push(args);
    if (args[0] === "rev-parse" && args.includes("--git-dir")) {
      return { status: 128, stdout: "", stderr: "not a git repository" };
    }
    return { status: 0, stdout: "", stderr: "" };
  };
  const h = uiHarness();

  const code = await runPushCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /repository/i);
  assert.deepEqual(calls, [["rev-parse", "--git-dir"]]);
});

test("rejects a detached HEAD", async () => {
  const runGit = async (args) => {
    if (args.join(" ") === "rev-parse --abbrev-ref HEAD") {
      return { status: 0, stdout: "HEAD\n", stderr: "" };
    }
    return { status: 0, stdout: "", stderr: "" };
  };
  const h = uiHarness();

  const code = await runPushCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /detached/i);
});

test("prunes stale remote ref, force pushes, and fetches after success", async () => {
  const calls = [];
  const runGit = async (args) => {
    calls.push(args);
    switch (args.join(" ")) {
      case "rev-parse --abbrev-ref HEAD":
        return { status: 0, stdout: "feature\n", stderr: "" };
      case "rev-parse --abbrev-ref --symbolic-full-name @{u}":
        return { status: 0, stdout: "upstream/feature\n", stderr: "" };
      case "show-ref --verify --quiet refs/remotes/upstream/feature":
        return { status: 0, stdout: "", stderr: "" };
      case "ls-remote --exit-code --heads upstream feature":
        return { status: 2, stdout: "", stderr: "" };
      case "fetch upstream --prune":
        return { status: 0, stdout: "", stderr: "" };
      case "push --force-with-lease upstream HEAD:feature":
        return { status: 0, stdout: "", stderr: "To origin\n * [new branch] HEAD -> feature\n" };
      case "fetch":
        return { status: 0, stdout: "", stderr: "" };
      default:
        return { status: 0, stdout: "", stderr: "" };
    }
  };
  const h = uiHarness();

  const code = await runPushCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 0);
  assert.match(h.messages.statuses.join("\n"), /To origin\n \* \[new branch\] HEAD -> feature/);
  assert.deepEqual(calls, [
    ["rev-parse", "--git-dir"],
    ["rev-parse", "--abbrev-ref", "HEAD"],
    ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
    ["show-ref", "--verify", "--quiet", "refs/remotes/upstream/feature"],
    ["ls-remote", "--exit-code", "--heads", "upstream", "feature"],
    ["fetch", "upstream", "--prune"],
    ["push", "--force-with-lease", "upstream", "HEAD:feature"],
    ["fetch"],
  ]);
});

test("reports push failure with a remotes and permissions tip", async () => {
  const runGit = async (args) => {
    if (args.join(" ") === "rev-parse --abbrev-ref HEAD") {
      return { status: 0, stdout: "feature\n", stderr: "" };
    }
    if (args[0] === "push") {
      return { status: 1, stdout: "", stderr: "permission denied" };
    }
    return { status: 0, stdout: "", stderr: "" };
  };
  const h = uiHarness();

  const code = await runPushCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /remotes|permission/i);
});
