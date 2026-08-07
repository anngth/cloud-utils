import assert from "node:assert/strict";
import test from "node:test";
import { parseSshGitUrl, buildProjectName } from "../ssh-url.mjs";

test("parses github ssh url", () => {
  const r = parseSshGitUrl("git@github.com:org/my-app.git");
  assert.equal(r.ok, true);
  assert.equal(r.owner, "org");
  assert.equal(r.repo, "my-app");
  assert.equal(r.projectName, "org-my-app");
});

test("parses gitlab ssh url without .git", () => {
  const r = parseSshGitUrl("git@gitlab.com:acme/api");
  assert.equal(r.ok, true);
  assert.equal(r.projectName, "acme-api");
});

test("rejects https urls", () => {
  const r = parseSshGitUrl("https://github.com/org/my-app.git");
  assert.equal(r.ok, false);
});

test("slugifies odd characters", () => {
  assert.equal(buildProjectName("Org_Name", "My App"), "org-name-my-app");
});
