import assert from "node:assert/strict";
import test from "node:test";
import {
  ProfileConfigError,
  addProfileSource,
  createProfile,
  removeProfile,
  validateProfilesDocument,
} from "../profiles.mjs";

const base = { version: 1, profiles: [{ name: "default", sources: [] }] };

test("requires at least one profile", () => {
  assert.throws(
    () => validateProfilesDocument({ version: 1, profiles: [] }),
    ProfileConfigError,
  );
  assert.throws(() => removeProfile(base, "default"), /final profile/i);
});

test("sorts profiles and sources without mutating input", () => {
  const created = createProfile(base, "alpha");
  const withSource = addProfileSource(created, "alpha", {
    source: "z/repo",
    skills: ["review"],
  });
  assert.deepEqual(withSource.profiles.map((item) => item.name), ["alpha", "default"]);
  assert.deepEqual(base, { version: 1, profiles: [{ name: "default", sources: [] }] });
});

test("rejects one skill name from two sources in one profile", () => {
  const first = addProfileSource(base, "default", {
    source: "a/repo",
    skills: ["review"],
  });
  assert.throws(
    () => addProfileSource(first, "default", {
      source: "b/repo",
      skills: ["review"],
    }),
    /review.*different source/i,
  );
});
