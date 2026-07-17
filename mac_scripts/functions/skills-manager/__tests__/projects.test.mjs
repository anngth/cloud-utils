import assert from "node:assert/strict";
import test from "node:test";
import {
  linkProjectProfiles,
  resolveProjectRoot,
  unlinkProjectProfiles,
  validateProjectsDocument,
} from "../projects.mjs";

const profiles = new Set(["frontend", "review"]);
const empty = { version: 1, projects: [] };

test("links profiles once and removes an entry after the last unlink", () => {
  const linked = linkProjectProfiles(empty, "/repo", ["frontend", "review"], profiles);
  assert.deepEqual(linked.projects[0], {
    root: "/repo",
    profiles: ["frontend", "review"],
  });
  assert.deepEqual(
    unlinkProjectProfiles(linked, "/repo", ["frontend", "review"]).projects,
    [],
  );
});

test("uses canonical Git root and falls back to cwd", () => {
  assert.equal(resolveProjectRoot({
    cwd: "/repo/subdir",
    execFile: () => "/repo\n",
    realpath: (value) => `/real${value}`,
  }), "/real/repo");
  assert.equal(resolveProjectRoot({
    cwd: "/plain",
    execFile: () => { throw new Error("not git"); },
    realpath: (value) => `/real${value}`,
  }), "/real/plain");
});

test("rejects references to missing profiles", () => {
  assert.throws(
    () => validateProjectsDocument({
      version: 1,
      projects: [{ root: "/repo", profiles: ["missing"] }],
    }, profiles),
    /missing profile/i,
  );
});
