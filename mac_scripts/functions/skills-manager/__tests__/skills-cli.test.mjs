import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  DiscoveryParseError,
  discoverAvailableSkills,
  hasCommand,
  listInstalledSkills,
  parseAvailableSkills,
  runNpx,
  runNpxCapture,
  runSkillsMutation,
} from "../skills-cli.mjs";
import { makeSandbox } from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const FLAT = join(TEST_DIR, "fixtures/discovery-flat.txt");
const GROUPED = join(TEST_DIR, "fixtures/discovery-grouped.txt");

test("hasCommand searches PATH without a shell", (t) => {
  const sandbox = makeSandbox(t);
  assert.equal(hasCommand("npx", { env: sandbox.env }), true);
  assert.equal(hasCommand("definitely-missing", { env: sandbox.env }), false);
});

test("runNpx preserves opaque arguments and inherited stdio", async () => {
  const calls = [];
  const spawnImpl = (command, args, options) => {
    calls.push({ command, args, options });
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("close", 7, null));
    return child;
  };
  const status = await runNpx(["skills", "add", "owner/repo with | %", "--list"], { spawnImpl });
  assert.equal(status, 7);
  assert.deepEqual(calls, [{
    command: "npx",
    args: ["skills", "add", "owner/repo with | %", "--list"],
    options: { stdio: "inherit" },
  }]);
});

test("runNpxCapture preserves opaque arguments and captures output", async () => {
  const calls = [];
  const spawnImpl = (command, args, options) => {
    calls.push({ command, args, options });
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdout.setEncoding = (encoding) => assert.equal(encoding, "utf8");
    child.stderr.setEncoding = (encoding) => assert.equal(encoding, "utf8");
    queueMicrotask(() => {
      child.stdout.emit("data", "installed");
      child.stderr.emit("data", "warning");
      child.emit("close", 0, null);
    });
    return child;
  };
  const result = await runNpxCapture(["skills", "add", "owner/repo with | %", "--list"], {
    cwd: "/repo",
    spawnImpl,
  });
  assert.deepEqual(result, { status: 0, stdout: "installed", stderr: "warning" });
  assert.deepEqual(calls, [{
    command: "npx",
    args: ["skills", "add", "owner/repo with | %", "--list"],
    options: { cwd: "/repo", stdio: ["ignore", "pipe", "pipe"] },
  }]);
});

async function assertSettlesOnceWithOne(promise) {
  const settlements = [];
  promise.then((status) => settlements.push(status));
  assert.equal(await promise, 1);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(settlements, [1]);
}

test("runNpx maps a synchronous spawn throw to one exactly once", async () => {
  let spawnCalls = 0;
  const spawnThrow = () => {
    spawnCalls += 1;
    throw new Error("ENOENT");
  };
  await assertSettlesOnceWithOne(runNpx([], { spawnImpl: spawnThrow }));
  assert.equal(spawnCalls, 1);
});

test("runNpx settles asynchronous error followed by close as one exactly once", async () => {
  const errorThenClose = () => {
    const child = new EventEmitter();
    queueMicrotask(() => {
      child.emit("error", new Error("ENOENT"));
      child.emit("close", 0, null);
    });
    return child;
  };
  await assertSettlesOnceWithOne(runNpx([], { spawnImpl: errorThenClose }));
});

test("runNpx maps signal-only failures to one", async () => {
  const signalOnly = () => {
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("close", null, "SIGTERM"));
    return child;
  };
  assert.equal(await runNpx([], { spawnImpl: signalOnly }), 1);
});

test("parses installed JSON and validates its shape", async () => {
  const calls = [];
  const result = await listInstalledSkills({
    cwd: "/repo",
    runner: async (...args) => {
      calls.push(args);
      return {
        status: 0,
        stdout: '[{"name":"review","path":"/repo/.agents/skills/review","scope":"project","agents":["Codex"]}]',
        stderr: "",
      };
    },
  });
  assert.deepEqual(result, [{
    name: "review",
    path: "/repo/.agents/skills/review",
    scope: "project",
    agents: ["Codex"],
  }]);
  assert.deepEqual(calls, [[["skills", "list", "--json"], { cwd: "/repo" }]]);
});

test("rejects installed JSON with a nonzero status or invalid skill record", async () => {
  await assert.rejects(
    listInstalledSkills({ runner: async () => ({ status: 1, stdout: "[]", stderr: "failed" }) }),
  );
  await assert.rejects(
    listInstalledSkills({ runner: async () => ({
      status: 0,
      stdout: '[{"name":"review","path":"/repo/.agents/skills/review","scope":"project"}]',
      stderr: "",
    }) }),
  );
});

test("parses flat and grouped available-skill output", () => {
  assert.deepEqual(parseAvailableSkills(readFileSync(FLAT, "utf8")), [
    { name: "frontend-design", description: "Build distinctive frontend interfaces." },
    { name: "react-best-practices", description: "Review React code for performance." },
  ]);
  assert.deepEqual(parseAvailableSkills(readFileSync(GROUPED, "utf8")), [
    { name: "react-best-practices", description: "Review React code for performance." },
    { name: "docs", description: "Create concise documentation." },
  ]);
});

test("rejects uncertain discovery output", () => {
  assert.throws(() => parseAvailableSkills("Available Skills\nmaybe"), DiscoveryParseError);
});

test("discovers available skills only from successful upstream output", async () => {
  const calls = [];
  const skills = await discoverAvailableSkills("owner/repo with | %", {
    cwd: "/repo",
    runner: async (...args) => {
      calls.push(args);
      return { status: 0, stdout: readFileSync(FLAT, "utf8"), stderr: "" };
    },
  });
  assert.deepEqual(skills, [
    { name: "frontend-design", description: "Build distinctive frontend interfaces." },
    { name: "react-best-practices", description: "Review React code for performance." },
  ]);
  assert.deepEqual(calls, [[["skills", "add", "owner/repo with | %", "--list"], { cwd: "/repo" }]]);
  await assert.rejects(discoverAvailableSkills("owner/repo", {
    runner: async () => ({ status: 1, stdout: readFileSync(FLAT, "utf8"), stderr: "failed" }),
  }));
});

test("runSkillsMutation delegates its opaque arguments to the runner", async () => {
  const calls = [];
  const status = await runSkillsMutation(["skills", "add", "owner/repo with | %"], {
    runner: async (...args) => {
      calls.push(args);
      return 9;
    },
  });
  assert.equal(status, 9);
  assert.deepEqual(calls, [[["skills", "add", "owner/repo with | %"]]]);
});
