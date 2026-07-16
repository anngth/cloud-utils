import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import * as realFs from "node:fs";
import test from "node:test";
import { makeSandbox } from "./helpers.mjs";
import {
  SourceListError,
  addSources,
  hasSource,
  normalizeSourceFile,
  readSources,
  removeSources,
  writeSourcesAtomic,
} from "../sources.mjs";

test("normalizeSourceFile creates a canonical empty file", (t) => {
  const sandbox = makeSandbox(t);
  const missing = `${sandbox.root}/nested/list.json`;
  normalizeSourceFile(missing);
  assert.equal(readFileSync(missing, "utf8"), "[]\n");
});

test("readSources accepts array and legacy presets while preserving item properties", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.skillsFile, '{"extra":true,"presets":[{"source":"a/one","label":"A"}]}\n');
  assert.deepEqual(readSources(sandbox.skillsFile), [{ source: "a/one", label: "A" }]);
});

test("readSources rejects malformed and structurally invalid data", (t) => {
  const sandbox = makeSandbox(t);
  for (const value of ["{", "{}", "null", "[null]", '[{"source":1}]']) {
    writeFileSync(sandbox.skillsFile, value);
    assert.throws(() => readSources(sandbox.skillsFile), SourceListError);
    assert.equal(readFileSync(sandbox.skillsFile, "utf8"), value);
  }
});

test("addSources handles same-command duplicates and opaque values", () => {
  const original = [{ source: "z/last", keep: true }];
  const result = addSources(original, ["a/space value", "雪/repo|%", "a/space value"]);
  assert.deepEqual(result.results, [
    { source: "a/space value", status: "added" },
    { source: "雪/repo|%", status: "added" },
    { source: "a/space value", status: "exists" },
  ]);
  assert.equal(result.added, 2);
  assert.equal(result.skipped, 1);
  assert.equal(result.items.find((item) => item.source === "z/last").keep, true);
});

test("removeSources classifies repeats against the original list", () => {
  const result = removeSources(
    [{ source: "a/one", keep: true }, { source: "b/two" }],
    ["a/one", "a/one", "x/missing"],
  );
  assert.deepEqual(result.results, [
    { source: "a/one", status: "removed" },
    { source: "a/one", status: "removed" },
    { source: "x/missing", status: "missing" },
  ]);
  assert.deepEqual(result.items, [{ source: "b/two" }]);
  assert.equal(result.removed, 2);
  assert.equal(result.skipped, 1);
});

test("atomic write uses the pid temporary name and removes it after success", (t) => {
  const sandbox = makeSandbox(t);
  writeSourcesAtomic(sandbox.skillsFile, [{ source: "a/one" }], { pid: 4242 });
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), '[\n  {\n    "source": "a/one"\n  }\n]\n');
  assert.equal(existsSync(`${sandbox.skillsFile}.4242.tmp`), false);
  assert.equal(hasSource(readSources(sandbox.skillsFile), "a/one"), true);
});

test("a modifying write drops legacy top-level fields and keeps item fields", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.skillsFile, '{"label":"legacy","presets":[{"source":"a/one","keep":true}]}\n');
  const result = addSources(readSources(sandbox.skillsFile), ["b/two"]);
  writeSourcesAtomic(sandbox.skillsFile, result.items);
  assert.deepEqual(JSON.parse(readFileSync(sandbox.skillsFile, "utf8")), [
    { source: "a/one", keep: true },
    { source: "b/two" },
  ]);
});

test("failed atomic rename keeps original bytes and cleans its own temp file", (t) => {
  const sandbox = makeSandbox(t);
  const before = readFileSync(sandbox.skillsFile, "utf8");
  const fs = {
    ...realFs,
    renameSync() { throw new Error("rename failed"); },
  };

  assert.throws(
    () => writeSourcesAtomic(sandbox.skillsFile, [{ source: "new/repo" }], { fs, pid: 9876 }),
    /rename failed/,
  );
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), before);
  assert.equal(existsSync(`${sandbox.skillsFile}.9876.tmp`), false);
});

test("serialization failure leaves the original untouched", (t) => {
  const sandbox = makeSandbox(t);
  const before = readFileSync(sandbox.skillsFile, "utf8");
  const circular = { source: "a/one" };
  circular.self = circular;
  assert.throws(() => writeSourcesAtomic(sandbox.skillsFile, [circular], { pid: 2468 }), TypeError);
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), before);
  assert.equal(existsSync(`${sandbox.skillsFile}.2468.tmp`), false);
});

test("temporary-file write failure leaves the original untouched", (t) => {
  const sandbox = makeSandbox(t);
  const before = readFileSync(sandbox.skillsFile, "utf8");
  const fs = {
    ...realFs,
    writeFileSync() { throw new Error("write failed"); },
  };
  assert.throws(
    () => writeSourcesAtomic(sandbox.skillsFile, [{ source: "new/repo" }], { fs, pid: 1357 }),
    /write failed/,
  );
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), before);
  assert.equal(existsSync(`${sandbox.skillsFile}.1357.tmp`), false);
});
