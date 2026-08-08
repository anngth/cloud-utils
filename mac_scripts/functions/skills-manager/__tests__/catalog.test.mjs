import assert from "node:assert/strict";
import test from "node:test";
import {
  EMPTY_CATALOG,
  validateCatalogDocument,
  resolveSourceToken,
  upsertSource,
  removeSourceAt,
  migrateProfilesToCatalog,
} from "../catalog.mjs";

test("validateCatalogDocument accepts empty catalog", () => {
  assert.deepEqual(validateCatalogDocument({ version: 1, sources: [] }), EMPTY_CATALOG);
});

test("resolveSourceToken accepts 1-based index and source id", () => {
  const doc = validateCatalogDocument({
    version: 1,
    sources: [
      { source: "a/one", skills: [] },
      { source: "b/two", skills: ["x"] },
    ],
  });
  assert.equal(resolveSourceToken(doc, "2").entry.source, "b/two");
  assert.equal(resolveSourceToken(doc, "b/two").index, 1);
  assert.throws(() => resolveSourceToken(doc, "3"), /out of range/i);
});

test("upsertSource replaces skills and blocks cross-source skill clash", () => {
  let doc = EMPTY_CATALOG;
  doc = upsertSource(doc, "a/one", ["alpha"]);
  doc = upsertSource(doc, "b/two", ["beta"]);
  assert.throws(() => upsertSource(doc, "b/two", ["alpha"]), /alpha/i);
  doc = upsertSource(doc, "a/one", ["alpha", "gamma"]);
  assert.deepEqual(doc.sources[0].skills, ["alpha", "gamma"]);
});

test("migrateProfilesToCatalog preserves every source and unions skills", () => {
  const migrated = migrateProfilesToCatalog({
    version: 1,
    profiles: [
      {
        name: "frontend",
        sources: [
          { source: "vercel-labs/agent-skills", skills: ["code-review"] },
          { source: "anthropics/skills", skills: [] },
        ],
      },
      {
        name: "review",
        sources: [
          { source: "vercel-labs/agent-skills", skills: ["code-review", "frontend-design"] },
        ],
      },
    ],
  });
  assert.equal(migrated.sources.length, 2);
  assert.deepEqual(
    migrated.sources.find((s) => s.source === "vercel-labs/agent-skills").skills,
    ["code-review", "frontend-design"],
  );
  assert.deepEqual(
    migrated.sources.find((s) => s.source === "anthropics/skills").skills,
    [],
  );
});

test("migrateProfilesToCatalog fails on skill owned by two sources", () => {
  assert.throws(
    () => migrateProfilesToCatalog({
      version: 1,
      profiles: [
        { name: "a", sources: [{ source: "one/repo", skills: ["dup"] }] },
        { name: "b", sources: [{ source: "two/repo", skills: ["dup"] }] },
      ],
    }),
    /dup/,
  );
});
