import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { JS_ENTRY, makeSandbox } from "./helpers.mjs";

const SCRIPT = "/usr/bin/script";
const EXPECT = "/usr/bin/expect";
const STTY = "/bin/stty";
const TIMEOUT_MS = 8_000;
const hasMacOsPtyTools = process.platform === "darwin"
  && existsSync(SCRIPT)
  && existsSync(EXPECT)
  && existsSync(STTY);

const HARNESS_SOURCE = String.raw`
import { spawn, spawnSync } from "node:child_process";

const [cli, ...args] = process.argv.slice(2);

function snapshot() {
  const state = spawnSync("/bin/stty", ["-g"], {
    encoding: "utf8",
    stdio: [0, "pipe", 2],
  });
  const detail = spawnSync("/bin/stty", ["-a"], {
    encoding: "utf8",
    stdio: [0, "pipe", 2],
  });
  if (state.status !== 0 || detail.status !== 0) {
    throw new Error("could not inspect PTY state");
  }
  return Buffer.from(JSON.stringify({
    state: state.stdout.trim(),
    detail: detail.stdout,
  })).toString("base64");
}

process.stdout.write("__SKM_WRAPPER_PID__:" + process.pid + "\n");
process.stdout.write("__SKM_TTY_BEFORE__:" + snapshot() + "\n");

const child = spawn(process.execPath, [cli, ...args], {
  env: process.env,
  stdio: "inherit",
});
process.stdout.write("__SKM_CHILD_PID__:" + child.pid + "\n");

process.on("SIGUSR1", () => {
  process.stdout.write("__SKM_TTY_SNAPSHOT__:" + snapshot() + "\n");
});

child.once("error", (error) => {
  process.stderr.write("__SKM_CHILD_ERROR__:" + error.message + "\n");
  process.exitCode = 1;
});

child.once("exit", (status, signal) => {
  process.stdout.write("__SKM_TTY_AFTER__:" + snapshot() + "\n");
  process.stdout.write("__SKM_CHILD_EXIT__:" + JSON.stringify({ status, signal }) + "\n");
  process.exitCode = status ?? 0;
});
`;

const EXPECT_BRIDGE_SOURCE = String.raw`
set timeout -1
log_user 1
spawn -noecho {*}$argv
interact
catch wait result
exit [lindex $result 3]
`;

function decodeSnapshot(encoded) {
  return JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
}

function markerValue(output, name) {
  const match = output.match(new RegExp(`${name}:([^\\r\\n]+)`));
  assert.ok(match, `missing ${name} marker in PTY output`);
  return match[1];
}

function assertEcho(snapshot, enabled) {
  const flag = enabled ? "echo" : "-echo";
  assert.match(snapshot.detail, new RegExp(`(?:^|\\s)${flag}(?:\\s|$)`, "m"));
}

function startInPty(t, args, sandbox) {
  const harness = join(sandbox.root, "pty-harness.mjs");
  const expectBridge = join(sandbox.root, "pty-bridge.exp");
  writeFileSync(harness, HARNESS_SOURCE, "utf8");
  writeFileSync(expectBridge, EXPECT_BRIDGE_SOURCE, "utf8");

  const child = spawn(EXPECT, [
    expectBridge,
    SCRIPT,
    "-q",
    "/dev/null",
    process.execPath,
    harness,
    JS_ENTRY,
    ...args,
  ], {
    env: sandbox.env,
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  let closed = false;
  let wrapperPid;
  let cliPid;

  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    stdout += chunk;
    const wrapperMatch = stdout.match(/__SKM_WRAPPER_PID__:(\d+)/);
    const cliMatch = stdout.match(/__SKM_CHILD_PID__:(\d+)/);
    if (wrapperPid === undefined && wrapperMatch) wrapperPid = Number(wrapperMatch[1]);
    if (cliPid === undefined && cliMatch) cliPid = Number(cliMatch[1]);
  });
  child.stderr.on("data", (chunk) => { stderr += chunk; });

  const close = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (status, signal) => {
      closed = true;
      resolve({ status, signal });
    });
  });

  function diagnostics() {
    return `\nstdout:\n${stdout}\nstderr:\n${stderr}`;
  }

  function waitForText(text, { from = 0, timeout = TIMEOUT_MS } = {}) {
    return new Promise((resolve, reject) => {
      const find = () => {
        const index = stdout.indexOf(text, from);
        if (index !== -1) {
          cleanup();
          resolve(index);
          return true;
        }
        return false;
      };
      const onData = () => { find(); };
      const onClose = () => {
        cleanup();
        reject(new Error(`PTY closed before ${JSON.stringify(text)}${diagnostics()}`));
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error(`timed out waiting for ${JSON.stringify(text)}${diagnostics()}`));
      }, timeout);
      const cleanup = () => {
        clearTimeout(timer);
        child.stdout.off("data", onData);
        child.off("close", onClose);
      };
      child.stdout.on("data", onData);
      child.on("close", onClose);
      find();
    });
  }

  async function waitForClose(timeout = TIMEOUT_MS) {
    let timer;
    try {
      const result = await Promise.race([
        close,
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error(`timed out waiting for PTY exit${diagnostics()}`)), timeout);
        }),
      ]);
      return { ...result, stdout, stderr };
    } finally {
      clearTimeout(timer);
    }
  }

  function signal(pid, name) {
    assert.ok(Number.isInteger(pid) && pid > 0, `missing PID before ${name}${diagnostics()}`);
    process.kill(pid, name);
  }

  const session = {
    get stdout() { return stdout; },
    input(value) { child.stdin.write(value); },
    signalCli(name) { signal(cliPid, name); },
    signalWrapper(name) { signal(wrapperPid, name); },
    waitForText,
    waitForClose,
  };

  t.after(async () => {
    if (closed) return;
    for (const pid of [cliPid, wrapperPid]) {
      try { if (pid) process.kill(pid, "SIGKILL"); } catch {}
    }
    try { child.kill("SIGKILL"); } catch {}
    try { await session.waitForClose(1_000); } catch {}
  });
  return session;
}

async function takeSnapshot(session) {
  const from = session.stdout.length;
  session.signalWrapper("SIGUSR1");
  const marker = await session.waitForText("__SKM_TTY_SNAPSHOT__:", { from });
  await session.waitForText("\n", { from: marker });
  return decodeSnapshot(markerValue(session.stdout.slice(from), "__SKM_TTY_SNAPSHOT__"));
}

async function readySession(t, args, sandbox, readyMarker) {
  const session = startInPty(t, args, sandbox);
  await session.waitForText("__SKM_CHILD_PID__:");
  await session.waitForText(readyMarker);
  const before = decodeSnapshot(markerValue(session.stdout, "__SKM_TTY_BEFORE__"));
  const raw = await takeSnapshot(session);
  assert.notEqual(raw.state, before.state, "selector must put the PTY in raw mode");
  assertEcho(before, true);
  assertEcho(raw, false);
  return { session, before };
}

async function finishAndAssertRestored(session, before) {
  const result = await session.waitForClose();
  const after = decodeSnapshot(markerValue(result.stdout, "__SKM_TTY_AFTER__"));
  assert.equal(after.state, before.state, "PTY state must match its pre-selector state after exit");
  assertEcho(after, true);
  assert.equal(result.stderr, "");
  return {
    ...result,
    childExit: JSON.parse(markerValue(result.stdout, "__SKM_CHILD_EXIT__")),
  };
}

test("q cancels install selector and restores terminal state", { skip: !hasMacOsPtyTools }, async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const { session, before } = await readySession(t, [], sandbox, "Select sources to install");
  session.input("q");
  const result = await finishAndAssertRestored(session, before);
  assert.deepEqual(result.childExit, { status: 0, signal: null });
  assert.equal(result.status, 0);
  assert.match(result.stdout, /Select sources to install cancelled/);
});

test("raw Ctrl+C cancels show selector and restores terminal state", { skip: !hasMacOsPtyTools }, async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const { session, before } = await readySession(t, ["show"], sandbox, "Select source to inspect");
  session.input("\u0003");
  const result = await finishAndAssertRestored(session, before);
  assert.deepEqual(result.childExit, { status: 0, signal: null });
  assert.equal(result.status, 0);
  assert.match(result.stdout, /Select source to inspect cancelled/);
});

test("Enter restores terminal input before the child inherits stdio", { skip: !hasMacOsPtyTools }, async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const { session, before } = await readySession(t, ["show"], sandbox, "Select source to inspect");
  session.input("\r");
  const result = await finishAndAssertRestored(session, before);
  assert.deepEqual(result.childExit, { status: 0, signal: null });
  assert.equal(result.status, 0);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","a/one","--list"]\n',
  );
});

for (const signal of ["SIGTERM", "SIGHUP"]) {
  test(`${signal} restores terminal state before signal termination`, { skip: !hasMacOsPtyTools }, async (t) => {
    const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
    const { session, before } = await readySession(t, ["show"], sandbox, "Select source to inspect");
    session.signalCli(signal);
    const result = await finishAndAssertRestored(session, before);
    assert.deepEqual(result.childExit, { status: null, signal });
  });
}
