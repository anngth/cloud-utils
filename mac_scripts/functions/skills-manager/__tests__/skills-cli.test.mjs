import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { hasCommand, runNpx } from "../skills-cli.mjs";
import { makeSandbox } from "./helpers.mjs";

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
