import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import {
  createKeyDecoder,
  createSelectorState,
  decodeKeys,
  reduceSelector,
  runSelector,
} from "../selector.mjs";

test("decodeKeys recognizes arrows, vim keys, space, enter, q, and Ctrl+C", () => {
  assert.deepEqual(
    decodeKeys(Buffer.from("\u001b[A\u001b[Bjk \rq\u0003")),
    ["up", "down", "down", "up", "toggle", "submit", "cancel", "cancel"],
  );
});

test("createKeyDecoder preserves a split arrow sequence", () => {
  const decoder = createKeyDecoder();
  assert.deepEqual(decoder.push(Buffer.from("\u001b[")), []);
  assert.deepEqual(decoder.push(Buffer.from("A")), ["up"]);
});

test("toggle and submit return selected item values in display order", () => {
  let state = createSelectorState([
    { value: "git@github.com:org/a.git", label: "git@github.com:org/a.git" },
    { value: "git@gitlab.com:acme/b.git", label: "git@gitlab.com:acme/b.git" },
    { value: "git@github.com:org/c.git", label: "git@github.com:org/c.git" },
  ]);
  state = reduceSelector(state, "toggle", { multiple: true }).state;
  state = reduceSelector(state, "down", { multiple: true }).state;
  state = reduceSelector(state, "down", { multiple: true }).state;
  state = reduceSelector(state, "toggle", { multiple: true }).state;
  const result = reduceSelector(state, "submit", { multiple: true });
  assert.equal(result.type, "submit");
  assert.deepEqual(result.selected, [
    "git@github.com:org/a.git",
    "git@github.com:org/c.git",
  ]);
});

test("cancel returns empty selection", () => {
  const state = createSelectorState([
    { value: "a", label: "A" },
  ]);
  const result = reduceSelector(state, "cancel", { multiple: true });
  assert.equal(result.type, "cancel");
  assert.deepEqual(result.selected, []);
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

test("runSelector restores raw mode and listeners on submit", async () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  processRef.pid = 123;
  processRef.kill = () => assert.fail("submit must not signal the process");
  const promise = runSelector({
    items: [
      { value: "a", label: "A" },
      { value: "b", label: "B" },
    ],
    multiple: true,
    input,
    render() {},
    processRef,
  });
  input.emit("data", Buffer.from(" \r"));
  const result = await promise;
  assert.deepEqual(result.selected, ["a"]);
  assert.deepEqual(input.rawCalls, [true, false]);
  assert.equal(input.listenerCount("data"), 0);
  assert.equal(input.listenerCount("end"), 0);
});

test("runSelector cancel via q restores raw mode", async () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  processRef.pid = 123;
  processRef.kill = () => {};
  const promise = runSelector({
    items: [{ value: "a", label: "A" }],
    multiple: true,
    input,
    render() {},
    processRef,
  });
  input.emit("data", Buffer.from("q"));
  const result = await promise;
  assert.equal(result.type, "cancel");
  assert.deepEqual(result.selected, []);
  assert.equal(input.isRaw, false);
});
