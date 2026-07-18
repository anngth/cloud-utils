import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import {
  ProfileConfigError,
  addProfileSource,
  createProfile,
  removeProfile,
  validateProfilesDocument,
} from "../profiles.mjs";

const base = { version: 1, profiles: [{ name: "default", sources: [] }] };

test("ships a current profiles example instead of a legacy list example", () => {
  const profilesExample = new URL("../profiles.json.example", import.meta.url);
  const legacyExample = new URL("../list.json.example", import.meta.url);

  assert.equal(existsSync(profilesExample), true);
  assert.equal(existsSync(legacyExample), false);
  assert.deepEqual(
    validateProfilesDocument(JSON.parse(readFileSync(profilesExample, "utf8"))),
    {
      version: 1,
      profiles: [{
        name: "default",
        sources: [
          { source: "anthropics/skills", skills: [] },
          { source: "vercel-labs/agent-skills", skills: [] },
        ],
      }],
    },
  );
});

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
