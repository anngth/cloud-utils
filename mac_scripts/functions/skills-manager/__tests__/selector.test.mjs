import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { createKeyDecoder, createSelectorState, decodeKeys, reduceSelector, runSelector } from "../selector.mjs";

test("decodeKeys recognizes arrows, vim keys, space, enter, q, and Ctrl+C", () => {
  assert.deepEqual(
    decodeKeys(Buffer.from("\u001b[A\u001b[Bjk \rq\u0003")),
    ["up", "down", "down", "up", "toggle", "submit", "cancel", "cancel"],
  );
});

test("decodeKeys maps Ctrl+Z to suspend", () => {
  assert.deepEqual(decodeKeys(Buffer.from([0x1a])), ["suspend"]);
});

test("createKeyDecoder preserves a split arrow sequence", () => {
  const decoder = createKeyDecoder();
  assert.deepEqual(decoder.push(Buffer.from("\u001b[")), []);
  assert.deepEqual(decoder.push(Buffer.from("A")), ["up"]);
});

test("createKeyDecoder preserves one-byte escape fragments before mixed keys", () => {
  const decoder = createKeyDecoder();
  assert.deepEqual(decoder.push(Buffer.from("\u001b")), []);
  assert.deepEqual(decoder.push(Buffer.from("[")), []);
  assert.deepEqual(decoder.push(Buffer.from("Bj")), ["down", "down"]);
});

test("cursor movement clamps and multi-select returns display order", () => {
  let state = createSelectorState(["a", "b", "c"]);
  state = reduceSelector(state, "up", { multiple: true }).state;
  assert.equal(state.cursor, 0);
  state = reduceSelector(state, "toggle", { multiple: true }).state;
  state = reduceSelector(state, "down", { multiple: true }).state;
  state = reduceSelector(state, "down", { multiple: true }).state;
  state = reduceSelector(state, "toggle", { multiple: true }).state;
  const result = reduceSelector(state, "submit", { multiple: true });
  assert.deepEqual(result.selected, ["a", "c"]);
});

test("single-select ignores toggle and submits the cursor", () => {
  let state = createSelectorState(["a", "b"]);
  state = reduceSelector(state, "toggle", { multiple: false }).state;
  state = reduceSelector(state, "down", { multiple: false }).state;
  const result = reduceSelector(state, "submit", { multiple: false });
  assert.deepEqual(result.selected, ["b"]);
  assert.deepEqual([...result.state.selected], []);
});

class FakeInput extends EventEmitter {
  constructor() {
    super();
    this.isTTY = true;
    this.isRaw = false;
    this.rawCalls = [];
  }
  setRawMode(value) { this.isRaw = value; this.rawCalls.push(value); }
  resume() {}
  pause() {}
}

function assertSelectorListenersRemoved(input, processRef) {
  assert.equal(input.listenerCount("data"), 0);
  assert.equal(input.listenerCount("end"), 0);
  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP", "SIGTSTP", "SIGCONT"]) {
    assert.equal(processRef.listenerCount(signal), 0);
  }
}

function makeFakeTimers() {
  const intervals = [];
  const cleared = [];
  let unrefCalls = 0;
  return {
    intervals,
    cleared,
    get unrefCalls() { return unrefCalls; },
    setIntervalImpl(callback, delay) {
      const handle = {
        callback,
        delay,
        unref() { unrefCalls += 1; },
      };
      intervals.push(handle);
      return handle;
    },
    clearIntervalImpl(handle) { cleared.push(handle); },
  };
}

test("runSelector restores raw mode and listeners on submit", async () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  processRef.pid = 123;
  processRef.kill = () => assert.fail("submit must not signal the process");
  const promise = runSelector({
    sources: ["a", "b"],
    multiple: true,
    input,
    render() {},
    processRef,
  });
  input.emit("data", Buffer.from(" \r"));
  const result = await promise;
  assert.deepEqual(result.selected, ["a"]);
  assert.deepEqual(input.rawCalls, [true, false]);
  assertSelectorListenersRemoved(input, processRef);
});

test("cleanup restores an input that was already raw", async () => {
  const input = new FakeInput();
  input.isRaw = true;
  const processRef = new EventEmitter();
  processRef.pid = 123;
  processRef.kill = () => {};
  const promise = runSelector({ sources: ["a"], multiple: false, input, render() {}, processRef });
  input.emit("end");
  assert.equal((await promise).type, "cancel");
  assert.equal(input.isRaw, true);
  assert.deepEqual(input.rawCalls, [true, true]);
});

test("EOF and SIGINT use cancellation cleanup", async () => {
  for (const finish of [
    (input) => input.emit("end"),
    (_input, processRef) => processRef.emit("SIGINT"),
  ]) {
    const input = new FakeInput();
    const processRef = new EventEmitter();
    processRef.pid = 123;
    processRef.kill = () => {};
    const promise = runSelector({ sources: ["a"], multiple: false, input, render() {}, processRef });
    finish(input, processRef);
    assert.equal((await promise).type, "cancel");
    assert.equal(input.isRaw, false);
  }
});

test("SIGTERM and SIGHUP restore raw mode before re-signalling", async () => {
  for (const signal of ["SIGTERM", "SIGHUP"]) {
    const input = new FakeInput();
    const processRef = new EventEmitter();
    processRef.pid = 123;
    let observed;
    processRef.kill = (pid, sentSignal) => {
      observed = { pid, sentSignal, raw: input.isRaw };
    };
    runSelector({ sources: ["a"], multiple: false, input, render() {}, processRef });
    processRef.emit(signal);
    assert.deepEqual(observed, { pid: 123, sentSignal: signal, raw: false });
  }
});

test("SIGTSTP restores mode and SIGCONT resumes and redraws", () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  processRef.pid = 123;
  const signals = [];
  let renders = 0;
  processRef.kill = (_pid, signal) => signals.push({ signal, raw: input.isRaw });
  runSelector({
    sources: ["a"],
    multiple: false,
    input,
    render() { renders += 1; },
    processRef,
  });
  processRef.emit("SIGTSTP");
  assert.deepEqual(signals, [{ signal: "SIGTSTP", raw: false }]);
  processRef.emit("SIGCONT");
  assert.equal(input.isRaw, true);
  assert.equal(renders, 2);
  processRef.emit("SIGINT");
});

test("raw Ctrl+Z suspends and resumes an active cancellable selector", async () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  const timers = makeFakeTimers();
  processRef.pid = 123;
  const signals = [];
  let renders = 0;
  let keepaliveSeenAtKill = false;
  let clearedDuringResumeRender;
  processRef.kill = (_pid, signal) => {
    keepaliveSeenAtKill = timers.intervals.length === 1 && timers.cleared.length === 0;
    signals.push({ signal, raw: input.isRaw });
  };
  const promise = runSelector({
    sources: ["a"],
    multiple: false,
    input,
    render() {
      renders += 1;
      if (renders === 2) clearedDuringResumeRender = timers.cleared.length;
    },
    processRef,
    setIntervalImpl: timers.setIntervalImpl,
    clearIntervalImpl: timers.clearIntervalImpl,
  });

  input.emit("data", Buffer.from([0x1a]));
  const suspendedDataListeners = input.listenerCount("data");
  processRef.emit("SIGTSTP");

  processRef.emit("SIGCONT");
  const rawAfterResume = input.isRaw;
  const resumedDataListeners = input.listenerCount("data");

  input.emit("data", Buffer.from("q"));
  assert.equal((await promise).type, "cancel");
  assert.deepEqual(signals, [{ signal: "SIGTSTP", raw: false }]);
  assert.equal(keepaliveSeenAtKill, true);
  assert.equal(suspendedDataListeners, 0);
  assert.equal(timers.intervals.length, 1);
  assert.equal(timers.intervals[0].delay, 2_147_483_647);
  assert.equal(timers.unrefCalls, 0);
  assert.equal(rawAfterResume, true);
  assert.equal(renders, 2);
  assert.equal(clearedDuringResumeRender, 0);
  assert.equal(resumedDataListeners, 1);
  assert.deepEqual(timers.cleared, [timers.intervals[0]]);
  assert.equal(input.isRaw, false);
  assert.equal(timers.cleared.length, 1);
  assertSelectorListenersRemoved(input, processRef);
});

test("cancelling while suspended clears the keepalive", async () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  const timers = makeFakeTimers();
  processRef.pid = 123;
  processRef.kill = () => {};
  const promise = runSelector({
    sources: ["a"],
    multiple: false,
    input,
    render() {},
    processRef,
    setIntervalImpl: timers.setIntervalImpl,
    clearIntervalImpl: timers.clearIntervalImpl,
  });

  input.emit("data", Buffer.from([0x1a]));
  processRef.emit("SIGINT");

  assert.equal((await promise).type, "cancel");
  assert.equal(timers.intervals.length, 1);
  assert.deepEqual(timers.cleared, [timers.intervals[0]]);
});

test("selector accepts input after SIGCONT", async () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  processRef.pid = 123;
  processRef.kill = () => {};
  const promise = runSelector({
    sources: ["a", "b"],
    multiple: false,
    input,
    render() {},
    processRef,
  });
  processRef.emit("SIGTSTP");
  processRef.emit("SIGCONT");
  input.emit("data", Buffer.from("j\r"));
  const result = await promise;
  assert.equal(result.type, "submit");
  assert.deepEqual(result.selected, ["b"]);
});

test("SIGCONT transition failures clean up and reject", async () => {
  for (const failurePoint of ["setRawMode", "render"]) {
    const input = new FakeInput();
    const processRef = new EventEmitter();
    const timers = makeFakeTimers();
    processRef.pid = 123;
    processRef.kill = () => {};
    const expected = new Error(`${failurePoint} failed`);
    let renders = 0;
    const promise = runSelector({
      sources: ["a"],
      multiple: false,
      input,
      render() {
        renders += 1;
        if (failurePoint === "render" && renders === 2) {
          assert.equal(input.listenerCount("data"), 1);
          throw expected;
        }
      },
      processRef,
      setIntervalImpl: timers.setIntervalImpl,
      clearIntervalImpl: timers.clearIntervalImpl,
    });
    if (failurePoint === "setRawMode") {
      const setRawMode = input.setRawMode.bind(input);
      input.setRawMode = (value) => {
        setRawMode(value);
        if (value) {
          assert.equal(input.listenerCount("data"), 1);
          throw expected;
        }
      };
    }

    processRef.emit("SIGTSTP");
    processRef.emit("SIGCONT");

    await assert.rejects(promise, expected);
    assert.equal(input.isRaw, false);
    assertSelectorListenersRemoved(input, processRef);
    assert.equal(timers.intervals.length, 1);
    assert.deepEqual(timers.cleared, [timers.intervals[0]]);
    const rawCalls = [...input.rawCalls];
    processRef.emit("SIGINT");
    input.emit("end");
    assert.deepEqual(input.rawCalls, rawCalls);
  }
});

test("SIGTSTP kill failures clean up and reject", async () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  const timers = makeFakeTimers();
  processRef.pid = 123;
  const expected = new Error("kill failed");
  processRef.kill = () => {
    assert.equal(input.listenerCount("data"), 0);
    assert.equal(processRef.listenerCount("SIGTSTP"), 0);
    assert.equal(input.isRaw, false);
    throw expected;
  };
  const promise = runSelector({
    sources: ["a"],
    multiple: false,
    input,
    render() {},
    processRef,
    setIntervalImpl: timers.setIntervalImpl,
    clearIntervalImpl: timers.clearIntervalImpl,
  });

  processRef.emit("SIGTSTP");

  await assert.rejects(promise, expected);
  assert.equal(input.isRaw, false);
  assertSelectorListenersRemoved(input, processRef);
  assert.equal(timers.intervals.length, 1);
  assert.deepEqual(timers.cleared, [timers.intervals[0]]);
});
