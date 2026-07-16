import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(TEST_DIR, "fixtures/golden.json"), "utf8"),
);

test("legacy golden fixture retains the required named cases", () => {
  assert.deepEqual(
    fixture.map((entry) => entry.name),
    [
      "help",
      "unknown",
      "list-empty",
      "list-values",
      "add-new-and-duplicate",
      "remove-repeated-and-missing",
      "show-too-many",
      "add-missing-argument",
    ],
  );
  for (const entry of fixture) {
    assert.equal(typeof entry.capture.stdout, "string");
    assert.equal(typeof entry.capture.stderr, "string");
    assert.equal(Number.isInteger(entry.capture.status), true);
  }
});
