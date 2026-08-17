import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import {
  createBrewRunner,
  createLineFramer,
  formatBrewCommand,
  isBrewProbe,
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

test("formatBrewCommand quotes args with spaces", () => {
  assert.equal(formatBrewCommand(["update"]), "$ brew update");
  assert.equal(
    formatBrewCommand(["upgrade", "--cask", "-y", "my cask"]),
    "$ brew upgrade --cask -y 'my cask'",
  );
});

test("runBrew probe pipes, logs command, buffers, does not write", async () => {
  const commands = [];
  const outChunks = [];
  const spawn = (cmd, args, options) => {
    assert.equal(cmd, "/brew");
    assert.deepEqual(args, ["list", "--formula"]);
    assert.deepEqual(options.stdio, ["ignore", "pipe", "pipe"]);
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => {
      child.stdout.emit("data", Buffer.from("bat\n"));
      child.stderr.emit("data", Buffer.from("warn\n"));
      child.emit("close", 0);
    });
    return child;
  };

  const result = await runBrew(["list", "--formula"], {
    brewBin: "/brew",
    spawn,
    onCommand: (line) => commands.push(line),
    stdout: { write: (c) => outChunks.push(String(c)) },
    stderr: { write: (c) => outChunks.push(String(c)) },
  });

  assert.deepEqual(commands, ["$ brew list --formula"]);
  assert.deepEqual(outChunks, []);
  assert.equal(result.code, 0);
  assert.equal(result.stdout, "bat\n");
  assert.equal(result.stderr, "warn\n");
});

test("runBrew interactive inherits stdio and returns empty buffers", async () => {
  const commands = [];
  const outChunks = [];
  const spawn = (cmd, args, options) => {
    assert.equal(cmd, "/brew");
    assert.deepEqual(args, ["upgrade", "--formula", "-y"]);
    assert.equal(options.stdio, "inherit");
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("close", 0));
    return child;
  };

  const result = await runBrew(["upgrade", "--formula", "-y"], {
    brewBin: "/brew",
    spawn,
    onCommand: (line) => commands.push(line),
    stdout: { write: (c) => outChunks.push(String(c)) },
    stderr: { write: (c) => outChunks.push(String(c)) },
  });

  assert.deepEqual(commands, ["$ brew upgrade --formula -y"]);
  assert.deepEqual(outChunks, []);
  assert.equal(result.code, 0);
  assert.equal(result.stdout, "");
  assert.equal(result.stderr, "");
});

test("runBrew --version is interactive (not a probe)", async () => {
  const spawn = (cmd, args, options) => {
    assert.deepEqual(args, ["--version"]);
    assert.equal(options.stdio, "inherit");
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("close", 0));
    return child;
  };
  const result = await runBrew(["--version"], {
    brewBin: "/brew",
    spawn,
    onCommand() {},
  });
  assert.equal(result.code, 0);
  assert.equal(result.stdout, "");
});

test("runBrew mode probe overrides interactive args", async () => {
  const spawn = (cmd, args, options) => {
    assert.deepEqual(args, ["update"]);
    assert.deepEqual(options.stdio, ["ignore", "pipe", "pipe"]);
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => {
      child.stdout.emit("data", Buffer.from("updated\n"));
      child.emit("close", 0);
    });
    return child;
  };
  const result = await runBrew(["update"], {
    brewBin: "/brew",
    spawn,
    mode: "probe",
    onCommand() {},
  });
  assert.equal(result.stdout, "updated\n");
  assert.equal(result.code, 0);
});

test("runBrew mode interactive overrides probe args", async () => {
  const spawn = (cmd, args, options) => {
    assert.deepEqual(args, ["list", "--formula"]);
    assert.equal(options.stdio, "inherit");
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("close", 3));
    return child;
  };
  const result = await runBrew(["list", "--formula"], {
    brewBin: "/brew",
    spawn,
    mode: "interactive",
    onCommand() {},
  });
  assert.equal(result.code, 3);
  assert.equal(result.stdout, "");
  assert.equal(result.stderr, "");
});

test("isBrewProbe classifies list info bare tap trust help", () => {
  assert.equal(isBrewProbe(["list", "--formula"]), true);
  assert.equal(isBrewProbe(["info", "--cask", "x"]), true);
  assert.equal(isBrewProbe(["tap"]), true);
  assert.equal(isBrewProbe(["trust", "--help"]), true);
  assert.equal(isBrewProbe(["--help"]), true);
  assert.equal(isBrewProbe(["tap", "mongodb/brew"]), false);
  assert.equal(isBrewProbe(["tap", "--repair"]), false);
  assert.equal(isBrewProbe(["trust", "--tap", "mongodb/brew"]), false);
  assert.equal(isBrewProbe(["upgrade", "--cask", "-y", "x"]), false);
  assert.equal(isBrewProbe(["update"]), false);
});

test("createLineFramer prefixes lines across partial chunks", () => {
  const chunks = [];
  const sink = { write: (c) => chunks.push(String(c)) };
  const f = createLineFramer("│  ", sink);
  f.write("ab\nc");
  f.write("d\n");
  f.flush();
  assert.deepEqual(chunks, ["│  ab\n", "│  cd\n"]);
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

test("createBrewRunner invokes ui.command before spawn", async () => {
  const commands = [];
  const ui = { command: (line) => commands.push(line) };
  const runner = createBrewRunner({
    brewBin: "/nonexistent-brew-bin-for-test",
    ui,
    stdout: { write() {} },
    stderr: { write() {} },
  });

  const result = await runner(["update"]);

  assert.deepEqual(commands, ["$ brew update"]);
  assert.equal(result.code, 1);
});

test("createBrewRunner does not stream probe list output", async () => {
  const outChunks = [];
  const commands = [];
  const spawn = () => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => {
      child.stdout.emit("data", Buffer.from("bat\n"));
      child.emit("close", 0);
    });
    return child;
  };
  const runner = createBrewRunner({
    brewBin: "/brew",
    spawn,
    ui: { command: (l) => commands.push(l), streamPrefix: "│  " },
    stdout: { write: (c) => outChunks.push(String(c)) },
    stderr: { write() {} },
  });
  const result = await runner(["list", "--formula"]);
  assert.deepEqual(commands, ["$ brew list --formula"]);
  assert.deepEqual(outChunks, []);
  assert.equal(result.stdout, "bat\n");
});
