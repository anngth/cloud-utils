import assert from "node:assert/strict";
import test from "node:test";
import { isStaleRepo } from "../stale.mjs";

const NOW = new Date("2026-08-08T12:00:00.000Z");

test("isStaleRepo treats null lastCheckedAt as stale", () => {
  assert.equal(isStaleRepo({ lastCheckedAt: null }, { now: NOW }), true);
});

test("isStaleRepo treats missing lastCheckedAt as stale", () => {
  assert.equal(isStaleRepo({}, { now: NOW }), true);
});

test("isStaleRepo treats recent lastCheckedAt as not stale", () => {
  const entry = { lastCheckedAt: "2026-08-07T12:00:00.000Z" };
  assert.equal(isStaleRepo(entry, { now: NOW, days: 7 }), false);
});

test("isStaleRepo treats lastCheckedAt older than default 7 days as stale", () => {
  const entry = { lastCheckedAt: "2026-08-01T11:59:59.999Z" };
  assert.equal(isStaleRepo(entry, { now: NOW }), true);
});

test("isStaleRepo boundary: exactly 7 days old is not stale", () => {
  const entry = { lastCheckedAt: "2026-08-01T12:00:00.000Z" };
  assert.equal(isStaleRepo(entry, { now: NOW, days: 7 }), false);
});

test("isStaleRepo boundary: one ms past threshold is stale", () => {
  const entry = { lastCheckedAt: "2026-08-01T11:59:59.999Z" };
  assert.equal(isStaleRepo(entry, { now: NOW, days: 7 }), true);
});

test("isStaleRepo honors custom days threshold", () => {
  const entry = { lastCheckedAt: "2026-08-07T11:00:00.000Z" };
  assert.equal(isStaleRepo(entry, { now: NOW, days: 1 }), true);
  assert.equal(isStaleRepo(entry, { now: NOW, days: 2 }), false);
});

test("isStaleRepo treats invalid lastCheckedAt as stale", () => {
  assert.equal(isStaleRepo({ lastCheckedAt: "not-a-date" }, { now: NOW }), true);
});
