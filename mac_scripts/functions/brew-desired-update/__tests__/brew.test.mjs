import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import {
  resolveBrewBinary,
  runBrew,
  loadBrewState,
  brewInfoToken,
  ensureTap,
  listBrewTaps,
} from "../brew.mjs";

function makeFs(executablePaths) {
  const set = new Set(executablePaths);
  return {
    existsSync: (p) => set.has(p),
    accessSync: (p) => {
      if (!set.has(p)) {
        throw new Error(`ENOENT: ${p}`);
      }
    },
    constants: { X_OK: 1 },
  };
}

test("resolveBrewBinary prefers PATH then opt homebrew", () => {
  assert.equal(
    resolveBrewBinary({
      pathEnv: "/custom/bin",
      fs: makeFs(["/custom/bin/brew"]),
    }),
    "/custom/bin/brew",
  );

  assert.equal(
    resolveBrewBinary({
      pathEnv: "/missing",
      fs: makeFs(["/opt/homebrew/bin/brew"]),
    }),
    "/opt/homebrew/bin/brew",
  );

  assert.equal(
    resolveBrewBinary({
      pathEnv: "/missing",
      fs: makeFs(["/usr/local/bin/brew"]),
    }),
    "/usr/local/bin/brew",
  );

  assert.equal(
    resolveBrewBinary({ pathEnv: "/missing", fs: makeFs([]) }),
    null,
  );
});

test("runBrew collects stdout stderr and exit code", async () => {
  const spawn = (cmd, args, options) => {
    assert.equal(cmd, "/brew");
    assert.deepEqual(args, ["--version"]);
    assert.deepEqual(options.stdio, ["ignore", "pipe", "pipe"]);
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => {
      child.stdout.emit("data", Buffer.from("Homebrew 4\n"));
      child.stderr.emit("data", Buffer.from("warn\n"));
      child.emit("close", 0);
    });
    return child;
  };

  const result = await runBrew(["--version"], { brewBin: "/brew", spawn });
  assert.equal(result.code, 0);
  assert.equal(result.stdout, "Homebrew 4\n");
  assert.equal(result.stderr, "warn\n");
});

test("loadBrewState strips tap prefixes", async () => {
  const runBrew = async (args) => {
    if (args.includes("--formula")) return { code: 0, stdout: "bat\nfoo/bar/jq\n", stderr: "" };
    if (args.includes("--cask")) return { code: 0, stdout: "homebrew/cask/cursor\n", stderr: "" };
    return { code: 0, stdout: "mongodb/brew\n", stderr: "" };
  };
  const state = await loadBrewState({ brewBin: "/brew", runBrew });
  assert.deepEqual(state.formulas, ["bat", "jq"]);
  assert.deepEqual(state.casks, ["cursor"]);
  assert.deepEqual(state.taps, ["mongodb/brew"]);
});

test("loadBrewState runs list commands in parallel", async () => {
  let pending = 0;
  let maxPending = 0;
  const runBrew = async (args) => {
    pending += 1;
    maxPending = Math.max(maxPending, pending);
    await new Promise((resolve) => setTimeout(resolve, 10));
    pending -= 1;
    if (args.includes("--formula")) return { code: 0, stdout: "", stderr: "" };
    if (args.includes("--cask")) return { code: 0, stdout: "", stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };
  await loadBrewState({ brewBin: "/brew", runBrew });
  assert.equal(maxPending, 3);
});

test("brewInfoToken parses ==> name:", async () => {
  const runBrew = async () => ({ code: 0, stdout: "==> bat: Clone of cat\n", stderr: "" });
  assert.equal(await brewInfoToken("formula", "bat", { brewBin: "/brew", runBrew }), "bat");
});

test("brewInfoToken returns null on failure", async () => {
  const runBrew = async () => ({ code: 1, stdout: "", stderr: "missing" });
  assert.equal(await brewInfoToken("formula", "nope", { brewBin: "/brew", runBrew }), null);
});

test("ensureTap runs trust when available then tap", async () => {
  const calls = [];
  const runBrew = async (args) => {
    calls.push(args);
    return { code: 0, stdout: "", stderr: "" };
  };
  assert.equal(await ensureTap("mongodb/brew", { brewBin: "/brew", runBrew }), true);
  assert.deepEqual(calls, [
    ["trust", "--help"],
    ["trust", "--tap", "mongodb/brew"],
    ["tap", "mongodb/brew"],
  ]);
});

test("ensureTap skips trust when unavailable", async () => {
  const calls = [];
  const runBrew = async (args) => {
    calls.push(args);
    if (args[0] === "trust" && args[1] === "--help") {
      return { code: 1, stdout: "", stderr: "" };
    }
    return { code: 0, stdout: "", stderr: "" };
  };
  assert.equal(await ensureTap("mongodb/brew", { brewBin: "/brew", runBrew }), true);
  assert.deepEqual(calls, [
    ["trust", "--help"],
    ["tap", "mongodb/brew"],
  ]);
});

test("listBrewTaps returns tap lines", async () => {
  const runBrew = async () => ({
    code: 0,
    stdout: "homebrew/core\nmongodb/brew\n",
    stderr: "",
  });
  assert.deepEqual(
    await listBrewTaps({ brewBin: "/brew", runBrew }),
    ["homebrew/core", "mongodb/brew"],
  );
});
