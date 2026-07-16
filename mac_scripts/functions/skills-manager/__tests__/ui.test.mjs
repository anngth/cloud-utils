import assert from "node:assert/strict";
import test from "node:test";
import { createUi } from "../ui.mjs";

function memoryStream() {
  let value = "";
  return { write(chunk) { value += chunk; }, read() { return value; } };
}

test("errors and warnings go only to stderr", () => {
  const stdout = memoryStream();
  const stderr = memoryStream();
  const ui = createUi({ stdout, stderr });
  ui.error("broken");
  ui.warn("careful");
  assert.equal(stdout.read(), "");
  assert.match(stderr.read(), /❌ broken/);
  assert.match(stderr.read(), /⚠️  careful/);
});

test("list output keeps ANSI when captured", () => {
  const stdout = memoryStream();
  const ui = createUi({ stdout, stderr: memoryStream() });
  ui.list("/tmp/list.json", [{ source: "a/one" }]);
  assert.match(stdout.read(), /\u001b\[46m/);
  assert.match(stdout.read(), /Found .*1.* sources/);
  assert.match(stdout.read(), /■.* a\/one/);
});

test("result renderers preserve opaque percent and pipe characters", () => {
  const stdout = memoryStream();
  const ui = createUi({ stdout, stderr: memoryStream() });
  ui.addResult("/tmp/list.json", 1, {
    results: [{ source: "雪/repo|%F{red}", status: "added" }],
    added: 1,
    skipped: 0,
  });
  assert.match(stdout.read(), /Added: 雪\/repo\|%F\{red\}/);
});

test("help contains every documented command and omits list-available", () => {
  const stdout = memoryStream();
  const ui = createUi({ stdout, stderr: memoryStream() });
  ui.usage();
  const rendered = stdout.read();
  for (const text of ["ls", "list", "show", "add", "remove"]) assert.match(rendered, new RegExp(text));
  assert.doesNotMatch(rendered, /list-available/);
});

test("install selector renders selected and cursor state", () => {
  const stdout = memoryStream();
  const ui = createUi({ stdout, stderr: memoryStream() });
  ui.selector("/tmp/list.json", {
    sources: ["a/one", "b/two"],
    cursor: 1,
    selected: new Set([0]),
  }, { mode: "install" });
  assert.match(stdout.read(), /space to toggle/);
  assert.match(stdout.read(), /■.*a\/one/);
  assert.match(stdout.read(), /□.*b\/two/);
});
