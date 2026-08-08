import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { chmodSync, existsSync, readFileSync, writeFileSync } from "node:fs";
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

const FOREGROUND_JOB_EXPECT_SOURCE = String.raw`
set timeout 8
log_user 1

proc fail {phase} {
  puts stderr "__SKM_EXPECT_FAILURE__:$phase"
  catch {close}
  catch {wait}
  exit 1
}

proc expect_exact {pattern phase} {
  expect {
    -exact $pattern { return }
    timeout { fail "$phase:timeout" }
    eof { fail "$phase:eof" }
  }
}

proc require_flag {detail flag phase} {
  set pattern [format {(^|[[:space:]])%s([[:space:]]|$)} $flag]
  if {![regexp -- $pattern $detail]} {
    fail "$phase:missing-$flag"
  }
}

spawn -noecho /bin/zsh -f
send -- {unsetopt PROMPT_CR PROMPT_SP; PS1='__SKM_PROMPT__ '; print -r -- "__SKM_"SHELL_READY__}
send -- "\r"
expect_exact "__SKM_SHELL_READY__" "shell-ready"
expect_exact "__SKM_PROMPT__ " "initial-prompt"

send -- {stty -pendin; baseline=$(stty -g); print -r -- "__SKM_"BASELINE__:$baseline; "$SKM_TEST_NODE" "$SKM_TEST_CLI"}
send -- "\r"
expect {
  -re {__SKM_BASELINE__:([^\r\n]+)} { set baseline $expect_out(1,string) }
  timeout { fail "baseline:timeout" }
  eof { fail "baseline:eof" }
}
expect_exact "Select items" "initial-selector"

send -raw -- "\032"
expect {
  -nocase -re {suspended[^\r\n]*} { puts "__SKM_SHELL_SUSPENDED__:$expect_out(0,string)" }
  timeout { fail "suspend-report:timeout" }
  eof { fail "suspend-report:eof" }
}
expect_exact "__SKM_PROMPT__ " "suspended-prompt"

send -- {stopped=$(stty -g); print -r -- "__SKM_"STOPPED__:$stopped; print -r -- "__SKM_"STOPPED_DETAIL_BEGIN__; stty -a; print -r -- "__SKM_"STOPPED_DETAIL_END__}
send -- "\r"
expect {
  -re {__SKM_STOPPED__:([^\r\n]+)} { set stopped $expect_out(1,string) }
  timeout { fail "stopped-state:timeout" }
  eof { fail "stopped-state:eof" }
}
expect_exact "__SKM_STOPPED_DETAIL_BEGIN__" "stopped-detail-begin"
expect {
  -exact "__SKM_STOPPED_DETAIL_END__" { set stopped_detail $expect_out(buffer) }
  timeout { fail "stopped-detail:timeout" }
  eof { fail "stopped-detail:eof" }
}
require_flag $stopped_detail "icanon" "stopped"
require_flag $stopped_detail "echo" "stopped"
expect_exact "__SKM_PROMPT__ " "stopped-state-prompt"

send -- {fg; skm_status=$?; print -r -- "__SKM_"STATUS__:$skm_status}
send -- "\r"
expect_exact "Select items" "resumed-selector"
puts "__SKM_RESUMED_SELECTOR__"

# A bare q reaches the CLI without Enter only after raw mode is restored.
send -raw -- "q"
puts "__SKM_RESUMED_RAW_Q__"
expect {
  -re {__SKM_STATUS__:([0-9]+)} { set status $expect_out(1,string) }
  timeout { fail "status:timeout" }
  eof { fail "status:eof" }
}
if {$status ne "1"} { fail "status:$status" }
expect_exact "__SKM_PROMPT__ " "final-prompt"

send -- {stty -pendin; final=$(stty -g); print -r -- "__SKM_"FINAL__:$final}
send -- "\r"
expect {
  -re {__SKM_FINAL__:([^\r\n]+)} { set final $expect_out(1,string) }
  timeout { fail "final-state:timeout" }
  eof { fail "final-state:eof" }
}
if {$final ne $baseline} { fail "final:not-baseline" }
expect_exact "__SKM_PROMPT__ " "final-state-prompt"

send -- "exit\r"
expect {
  eof {}
  timeout { fail "shell-exit:timeout" }
}
catch wait result
exit [lindex $result 3]
`;

const HELD_NPX_SOURCE = String.raw`
import { spawnSync } from "node:child_process";
import { existsSync, watch, writeFileSync } from "node:fs";
import { dirname } from "node:path";

function stty(args) {
  const result = spawnSync("/bin/stty", args, {
    encoding: "utf8",
    stdio: [0, "pipe", 2],
  });
  if (result.status !== 0) throw new Error("could not inspect inherited TTY state");
  return result.stdout;
}

function waitForRelease(filePath) {
  if (existsSync(filePath)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let watcher;
    const timer = setTimeout(() => finish(new Error("timed out waiting for npx release")), 8_000);
    const finish = (error) => {
      clearTimeout(timer);
      watcher?.close();
      if (error) reject(error);
      else resolve();
    };
    watcher = watch(dirname(filePath), () => {
      if (existsSync(filePath)) finish();
    });
    watcher.once("error", finish);
    if (existsSync(filePath)) finish();
  });
}

const args = process.argv.slice(2);
if (JSON.stringify(args) === JSON.stringify(["skills", "list", "--json"])) {
  process.stdout.write("[]\n");
} else {
  writeFileSync(process.env.SKM_NPX_TTY_STATE, stty(["-g"]).trim() + "\n", "utf8");
  writeFileSync(process.env.SKM_NPX_TTY_DETAIL, stty(["-a"]), "utf8");
  writeFileSync(process.env.SKM_ARGV_LOG, JSON.stringify(args) + "\n", "utf8");
  process.stdout.write("__SKM_NPX_READY__\n");
  await waitForRelease(process.env.SKM_NPX_RELEASE);
}
`;

const TEST_CATALOG = {
  version: 1,
  sources: [{ source: "a/one", skills: ["frontend-design"] }],
};
const TEST_PROFILES = {
  version: 1,
  profiles: [{
    name: "frontend",
    sources: [{ source: "a/one", skills: ["frontend-design"] }],
  }],
};
const TEST_PROJECTS = { version: 1, projects: [] };

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

function installHeldNpx(sandbox) {
  const stub = join(sandbox.root, "held-npx.mjs");
  const readyState = join(sandbox.root, "npx-stty-g.txt");
  const readyDetail = join(sandbox.root, "npx-stty-a.txt");
  const release = join(sandbox.root, "npx-release");
  writeFileSync(stub, HELD_NPX_SOURCE, "utf8");
  writeFileSync(
    join(sandbox.binDir, "npx"),
    `#!/bin/zsh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(stub)} "$@"\n`,
    "utf8",
  );
  chmodSync(join(sandbox.binDir, "npx"), 0o755);
  Object.assign(sandbox.env, {
    SKM_NPX_TTY_STATE: readyState,
    SKM_NPX_TTY_DETAIL: readyDetail,
    SKM_NPX_RELEASE: release,
  });
  return { readyState, readyDetail, release };
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

test("foreground shell Ctrl+Z, fg, and q preserve terminal job control", { skip: !hasMacOsPtyTools }, (t) => {
  const sandbox = makeSandbox(t, { catalog: TEST_CATALOG, profiles: TEST_PROFILES, projects: TEST_PROJECTS });
  const expectScript = join(sandbox.root, "foreground-job.exp");
  writeFileSync(expectScript, FOREGROUND_JOB_EXPECT_SOURCE, "utf8");
  const result = spawnSync(EXPECT, [expectScript], {
    env: {
      ...sandbox.env,
      SKM_TEST_NODE: process.execPath,
      SKM_TEST_CLI: JS_ENTRY,
    },
    encoding: "utf8",
    timeout: 45_000,
    killSignal: "SIGKILL",
    maxBuffer: 1024 * 1024,
  });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.signal, null, result.stderr);
  assert.equal(result.status, 0, `foreground job-control harness failed\n${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /__SKM_BASELINE__:[^\r\n]+/);
  assert.match(result.stdout, /__SKM_SHELL_SUSPENDED__:[^\r\n]*suspended/i);
  assert.match(result.stdout, /__SKM_STOPPED__:[^\r\n]+/);
  assert.match(result.stdout, /__SKM_STOPPED_DETAIL_BEGIN__[\s\S]*\bicanon\b[\s\S]*__SKM_STOPPED_DETAIL_END__/);
  assert.match(result.stdout, /__SKM_RESUMED_SELECTOR__/);
  assert.match(result.stdout, /__SKM_RESUMED_RAW_Q__/);
  assert.match(result.stdout, /__SKM_STATUS__:1/);
  assert.match(result.stdout, /__SKM_FINAL__:[^\r\n]+/);
  assert.doesNotMatch(`${result.stdout}\n${result.stderr}`, /unsettled top-level await/i);
});

test("q cancels interactive selector and restores terminal state", { skip: !hasMacOsPtyTools }, async (t) => {
  const sandbox = makeSandbox(t, { catalog: TEST_CATALOG, profiles: TEST_PROFILES, projects: TEST_PROJECTS });
  const { session, before } = await readySession(t, [], sandbox, "Select items");
  session.input("q");
  const result = await finishAndAssertRestored(session, before);
  assert.deepEqual(result.childExit, { status: 1, signal: null });
  assert.equal(result.status, 1);
  assert.match(result.stdout, /Selection cancelled/);
});

test("raw Ctrl+C cancels a skill selector and restores terminal state", { skip: !hasMacOsPtyTools }, async (t) => {
  const sandbox = makeSandbox(t, { catalog: TEST_CATALOG, profiles: TEST_PROFILES, projects: TEST_PROJECTS });
  const { session, before } = await readySession(
    t,
    ["add", "1"],
    sandbox,
    "Select items",
  );
  session.input("\u0003");
  const result = await finishAndAssertRestored(session, before);
  assert.deepEqual(result.childExit, { status: 0, signal: null });
  assert.equal(result.status, 0);
  assert.match(result.stdout, /Selection cancelled/);
});

test("Enter restores terminal input before the child inherits stdio", { skip: !hasMacOsPtyTools }, async (t) => {
  const sandbox = makeSandbox(t, { catalog: TEST_CATALOG, profiles: TEST_PROFILES, projects: TEST_PROJECTS });
  const heldNpx = installHeldNpx(sandbox);
  const { session, before } = await readySession(t, ["add", "1"], sandbox, "Select items");
  const beforeEnter = session.stdout.length;
  try {
    session.input(" \r");
    await session.waitForText("Apply this install plan?", { from: beforeEnter });
    session.input("\r");
    await session.waitForText("__SKM_NPX_READY__", { from: beforeEnter });
    const inherited = {
      state: readFileSync(heldNpx.readyState, "utf8").trim(),
      detail: readFileSync(heldNpx.readyDetail, "utf8"),
    };
    assert.equal(
      inherited.state,
      before.state,
      "inherited-stdio child must start with the exact pre-selector TTY state",
    );
    assertEcho(inherited, true);
    assert.match(inherited.detail, /(?:^|\s)icanon(?:\s|$)/m);
  } finally {
    writeFileSync(heldNpx.release, "release\n", "utf8");
  }
  const result = await finishAndAssertRestored(session, before);
  assert.deepEqual(result.childExit, { status: 0, signal: null });
  assert.equal(result.status, 0);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","a/one","--skill","frontend-design"]\n',
  );
});

for (const signal of ["SIGTERM", "SIGHUP"]) {
  test(`${signal} restores terminal state before signal termination`, { skip: !hasMacOsPtyTools }, async (t) => {
    const sandbox = makeSandbox(t, { catalog: TEST_CATALOG, profiles: TEST_PROFILES, projects: TEST_PROJECTS });
    const { session, before } = await readySession(t, [], sandbox, "Select items");
    session.signalCli(signal);
    const result = await finishAndAssertRestored(session, before);
    assert.deepEqual(result.childExit, { status: null, signal });
  });
}
