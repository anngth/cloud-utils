import assert from "node:assert/strict";
import test from "node:test";
import {
  fingerprintsEqual,
  parseLsRemoteFingerprint,
} from "../refs-fingerprint.mjs";

test("parseLsRemoteFingerprint keeps heads and tags only, sorted", () => {
  const raw = [
    "aaa\tHEAD",
    "bbb\trefs/heads/main",
    "ccc\trefs/tags/v1",
    "ddd\trefs/pull/1/head",
    "eee\trefs/heads/develop",
  ].join("\n");
  const fp = parseLsRemoteFingerprint(raw);
  const expected = [
    "bbb\trefs/heads/main",
    "eee\trefs/heads/develop",
    "ccc\trefs/tags/v1",
  ]
    .sort()
    .join("\n");
  assert.equal(fp, expected);
});

test("fingerprintsEqual detects match and mismatch", () => {
  const a = parseLsRemoteFingerprint("x\trefs/heads/main\n");
  const b = parseLsRemoteFingerprint("x\trefs/heads/main\n");
  const c = parseLsRemoteFingerprint("y\trefs/heads/main\n");
  assert.equal(fingerprintsEqual(a, b), true);
  assert.equal(fingerprintsEqual(a, c), false);
});
