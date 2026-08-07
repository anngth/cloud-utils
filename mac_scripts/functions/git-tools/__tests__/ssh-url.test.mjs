import assert from "node:assert/strict";
import test from "node:test";
import {
  parseSshGitUrl,
  buildProjectName,
  canonicalizeSshGitUrl,
} from "../ssh-url.mjs";

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

test("parses nested group path", () => {
  const r = parseSshGitUrl("git@gitlab.vinova.sg:vinova/project-sit/sit-be.git");
  assert.equal(r.ok, true);
  assert.equal(r.owner, "vinova/project-sit");
  assert.equal(r.repo, "sit-be");
  assert.equal(r.projectName, "vinova-project-sit-sit-be");
});

test("rejects https urls", () => {
  const r = parseSshGitUrl("https://github.com/org/my-app.git");
  assert.equal(r.ok, false);
});

test("slugifies odd characters", () => {
  assert.equal(buildProjectName("Org_Name", "My App"), "org-name-my-app");
});

test("canonicalizeSshGitUrl strips .git and lowercases host", () => {
  const r = canonicalizeSshGitUrl("git@GitHub.com:Org/My-App.git");
  assert.equal(r.ok, true);
  assert.equal(r.canonical, "git@github.com:Org/My-App");
  assert.equal(r.sshUrl, "git@github.com:Org/My-App.git");
});

test("parseSshGitUrl includes host", () => {
  const r = parseSshGitUrl("git@gitlab.com:a/b.git");
  assert.equal(r.ok, true);
  assert.equal(r.host, "gitlab.com");
});

test("canonicalizeSshGitUrl rejects invalid urls", () => {
  const r = canonicalizeSshGitUrl("https://github.com/org/app.git");
  assert.equal(r.ok, false);
  assert.match(r.error, /HTTPS|Invalid/i);
});
