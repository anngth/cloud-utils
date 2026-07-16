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

test("runNpx maps spawn and signal-only failures to one", async () => {
  const spawnError = () => {
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("error", new Error("ENOENT")));
    return child;
  };
  const signalOnly = () => {
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("close", null, "SIGTERM"));
    return child;
  };
  assert.equal(await runNpx([], { spawnImpl: spawnError }), 1);
  assert.equal(await runNpx([], { spawnImpl: signalOnly }), 1);
});
