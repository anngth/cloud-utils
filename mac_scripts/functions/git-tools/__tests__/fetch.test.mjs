import assert from "node:assert/strict";
import test from "node:test";
import { runFetchCommand } from "../fetch.mjs";
import { createUi } from "../ui.mjs";

const stripAnsi = (text) => text.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, "");

function uiHarness() {
  const messages = {
    errors: [],
    errorDetails: [],
    statuses: [],
    frames: [],
    events: [],
    lines: [],
  };
  return {
    messages,
    ui: {
      begin(message) { messages.frames.push({ kind: "begin", message }); },
      end() { messages.frames.push({ kind: "end" }); },
      error(message) { messages.errors.push(message); },
      errorDetail(message) { messages.errorDetails.push(message); },
      status(message, { tone = "success" } = {}) {
        messages.statuses.push(message);
        messages.events.push({ kind: "status", message, tone });
      },
      detail(message, { tone = "muted" } = {}) {
        messages.events.push({ kind: "detail", message, tone });
      },
      usage() {},
      usageLine(message) { messages.errors.push(message); },
      line(message = "") { messages.lines.push(message); },
    },
  };
}

function makeGit(overrides = {}) {
  const calls = [];
  const defaults = {
    "rev-parse --git-dir": { status: 0, stdout: ".git\n", stderr: "" },
    "ls-remote --heads origin main": { status: 0, stdout: "", stderr: "" },
    "ls-remote --heads origin develop": { status: 0, stdout: "", stderr: "" },
    "fetch --all --prune": { status: 0, stdout: "", stderr: "" },
    "rev-parse --abbrev-ref HEAD": { status: 0, stdout: "feature\n", stderr: "" },
    "show-ref --verify --quiet refs/remotes/origin/main": { status: 1, stdout: "", stderr: "" },
    "show-ref --verify --quiet refs/remotes/origin/develop": { status: 1, stdout: "", stderr: "" },
    "show-ref --verify --quiet refs/heads/main": { status: 1, stdout: "", stderr: "" },
    "show-ref --verify --quiet refs/heads/develop": { status: 1, stdout: "", stderr: "" },
    "branch -vv": { status: 0, stdout: "", stderr: "" },
    "for-each-ref --format=%(refname:short) refs/heads": { status: 0, stdout: "", stderr: "" },
  };
  const responses = { ...defaults, ...overrides };
  const runGit = async (args) => {
    calls.push(args);
    const key = args.join(" ");
    if (Object.hasOwn(responses, key)) return responses[key];
    return { status: 0, stdout: "", stderr: "" };
  };
  return { runGit, calls };
}

test("rejects an unknown flag with usage and exits 1", async () => {
  const { runGit, calls } = makeGit();
  const h = uiHarness();

  const code = await runFetchCommand(["--bogus"], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /Unknown option/i);
  assert.match(h.messages.errors.join("\n"), /gt fetch \[--sync-upstream\]/);
  assert.deepEqual(calls, []);
});

test("--help exits 0 with plain usage matching zsh (no --- prefix)", async () => {
  const { runGit, calls } = makeGit();
  const h = uiHarness();

  const code = await runFetchCommand(["--help"], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 0);
  assert.deepEqual(h.messages.lines, [
    "Usage: gt fetch [--sync-upstream]",
    "  --sync-upstream  Merge upstream/main into local main and push origin/main (main only)",
  ]);
  assert.ok(h.messages.lines.every((line) => !line.startsWith("---")));
  assert.equal(h.messages.statuses.length, 0);
  assert.deepEqual(calls, []);
});

test("-h exits 0 with plain usage (no --- prefix)", async () => {
  const { runGit } = makeGit();
  const h = uiHarness();

  const code = await runFetchCommand(["-h"], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 0);
  assert.match(h.messages.lines.join("\n"), /Usage: gt fetch \[--sync-upstream\]/);
  assert.ok(h.messages.lines.every((line) => !line.startsWith("---")));
});

test("rejects when not a git repository", async () => {
  const { runGit } = makeGit({
    "rev-parse --git-dir": { status: 128, stdout: "", stderr: "not a git repository" },
  });
  const h = uiHarness();

  const code = await runFetchCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 1);
  assert.match(h.messages.errors.join("\n"), /not in a git repository/i);
});

test("on a feature branch: no auto-sync message, still runs cleanup hooks", async () => {
  const { runGit, calls } = makeGit({
    "rev-parse --abbrev-ref HEAD": { status: 0, stdout: "feature-x\n", stderr: "" },
    "branch -vv": {
      status: 0,
      stdout:
        "* feature-x   5678abc [origin/feature-x] latest\n" +
        "  old-feature 1234abc [origin/old-feature: gone] stale\n",
      stderr: "",
    },
    "branch -d old-feature": { status: 0, stdout: "Deleted branch old-feature\n", stderr: "" },
    "show-ref --verify --quiet refs/heads/main": { status: 0, stdout: "", stderr: "" },
    "for-each-ref --format=%(refname:short) refs/heads": {
      status: 0,
      stdout: "main\nfeature-x\nmerged-thing\n",
      stderr: "",
    },
    "merge-base --is-ancestor merged-thing main": { status: 0, stdout: "", stderr: "" },
    "branch -d merged-thing": { status: 0, stdout: "Deleted branch merged-thing\n", stderr: "" },
  });
  const h = uiHarness();

  const code = await runFetchCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 0);
  assert.match(h.messages.statuses.join("\n"), /no auto-sync performed/i);
  assert.ok(calls.some((c) => c.join(" ") === "branch -vv"));
  assert.ok(calls.some((c) => c.join(" ") === "branch -d old-feature"));
  assert.ok(calls.some((c) => c.join(" ") === "for-each-ref --format=%(refname:short) refs/heads"));
  assert.ok(calls.some((c) => c.join(" ") === "merge-base --is-ancestor merged-thing main"));
  assert.ok(calls.some((c) => c.join(" ") === "branch -d merged-thing"));
  assert.match(h.messages.statuses.join("\n"), /Deleted: old-feature/);
  assert.match(h.messages.statuses.join("\n"), /Deleted merged branch: merged-thing/);
  assert.deepEqual(h.messages.frames.map(({ kind }) => kind), ["begin", "end"]);
  assert.doesNotMatch(
    h.messages.events.map(({ message }) => message).join("\n"),
    /[⬇✅⚠🔄🌱💡ℹ🧹🎯🌿❌]/u,
  );
});

test("multiple gone branches remain inside the rendered frame", async () => {
  const { runGit } = makeGit({
    "branch -vv": {
      status: 0,
      stdout:
        "* feature-x 5678abc [origin/feature-x] latest\n"
        + "  old-feature 1234abc [origin/old-feature: gone] stale\n"
        + "  older-feature 9876def [origin/older-feature: gone] older\n",
      stderr: "",
    },
  });
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  assert.equal(await runFetchCommand([], { cwd: "/repo", runGit, ui }), 0);
  assert.match(
    stripAnsi(stdout),
    /│  ■ Cleaning gone branches: old-feature\n│      older-feature/,
  );
});

test("--sync-upstream on main merges upstream/main and pushes origin", async () => {
  const { runGit, calls } = makeGit({
    "rev-parse --abbrev-ref HEAD": { status: 0, stdout: "main\n", stderr: "" },
    "show-ref --verify --quiet refs/remotes/origin/main": { status: 0, stdout: "", stderr: "" },
    "pull --ff-only origin main": { status: 0, stdout: "Already up to date.\n", stderr: "" },
    "remote get-url upstream": { status: 0, stdout: "git@github.com:upstream/repo.git\n", stderr: "" },
    "ls-remote --heads upstream main": { status: 0, stdout: "sha refs/heads/main\n", stderr: "" },
    "diff --quiet": { status: 0, stdout: "", stderr: "" },
    "diff --cached --quiet": { status: 0, stdout: "", stderr: "" },
    "merge upstream/main --no-edit": { status: 0, stdout: "", stderr: "" },
    "push origin main": { status: 0, stdout: "", stderr: "" },
  });
  const h = uiHarness();

  const code = await runFetchCommand(["--sync-upstream"], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 0);
  assert.ok(calls.some((c) => c.join(" ") === "remote get-url upstream"));
  assert.ok(calls.some((c) => c.join(" ") === "merge upstream/main --no-edit"));
  assert.ok(calls.some((c) => c.join(" ") === "push origin main"));
  assert.match(h.messages.statuses.join("\n"), /Merged upstream changes successfully/);
  assert.match(h.messages.statuses.join("\n"), /Pushed updated main to origin/);
});

test("on main without --sync-upstream, skips upstream sync with an informational message", async () => {
  const { runGit, calls } = makeGit({
    "rev-parse --abbrev-ref HEAD": { status: 0, stdout: "main\n", stderr: "" },
    "show-ref --verify --quiet refs/remotes/origin/main": { status: 0, stdout: "", stderr: "" },
    "pull --ff-only origin main": { status: 0, stdout: "", stderr: "" },
  });
  const h = uiHarness();

  const code = await runFetchCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 0);
  assert.match(h.messages.statuses.join("\n"), /Skipping upstream sync/);
  assert.ok(!calls.some((c) => c[0] === "remote" && c[1] === "get-url"));
});

test("skips upstream merge when the worktree is dirty", async () => {
  const { runGit, calls } = makeGit({
    "rev-parse --abbrev-ref HEAD": { status: 0, stdout: "main\n", stderr: "" },
    "show-ref --verify --quiet refs/remotes/origin/main": { status: 0, stdout: "", stderr: "" },
    "pull --ff-only origin main": { status: 0, stdout: "", stderr: "" },
    "remote get-url upstream": { status: 0, stdout: "git@github.com:upstream/repo.git\n", stderr: "" },
    "ls-remote --heads upstream main": { status: 0, stdout: "sha refs/heads/main\n", stderr: "" },
    "diff --quiet": { status: 1, stdout: "", stderr: "" },
  });
  const h = uiHarness();

  const code = await runFetchCommand(["--sync-upstream"], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 0);
  assert.match(h.messages.statuses.join("\n"), /uncommitted changes/i);
  assert.ok(!calls.some((c) => c[0] === "merge"));
});

test("fetch failure on both --all and origin returns 1 and stops before cleanup", async () => {
  const { runGit, calls } = makeGit({
    "fetch --all --prune": { status: 1, stdout: "", stderr: "network down" },
    "fetch origin --prune": { status: 1, stdout: "", stderr: "network down" },
  });
  const h = uiHarness();

  const code = await runFetchCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 1);
  assert.ok(h.messages.errors.includes("Failed to fetch"));
  assert.doesNotMatch(h.messages.errors.join("\n"), /❌/u);
  assert.ok(!calls.some((c) => c.join(" ") === "branch -vv"));
});

test("refreshes primary remote refs for main/develop before fetch --all", async () => {
  const { runGit, calls } = makeGit({
    "ls-remote --heads origin main": { status: 0, stdout: "sha refs/heads/main\n", stderr: "" },
    "ls-remote --heads origin develop": { status: 0, stdout: "sha refs/heads/develop\n", stderr: "" },
    "fetch origin main": { status: 0, stdout: "", stderr: "" },
    "fetch origin develop": { status: 0, stdout: "", stderr: "" },
  });
  const h = uiHarness();

  const code = await runFetchCommand([], { cwd: "/repo", runGit, ui: h.ui });

  assert.equal(code, 0);
  assert.ok(calls.some((c) => c.join(" ") === "fetch origin main"));
  assert.ok(calls.some((c) => c.join(" ") === "fetch origin develop"));
});
