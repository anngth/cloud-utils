import assert from "node:assert/strict";
import test from "node:test";
import {
  SourceIdentityError,
  canonicalizeSource,
  redactSource,
} from "../source-id.mjs";

test("canonicalizes equivalent GitHub repository roots", () => {
  for (const value of [
    "vercel-labs/agent-skills",
    "https://github.com/vercel-labs/agent-skills",
    "https://github.com/vercel-labs/agent-skills.git",
    "git@github.com:vercel-labs/agent-skills.git",
    "ssh://git@github.com/vercel-labs/agent-skills.git",
  ]) {
    assert.equal(canonicalizeSource(value), "vercel-labs/agent-skills");
  }
});

test("preserves safe GitHub shorthand component characters", () => {
  assert.equal(
    canonicalizeSource("owner-name/repo_name.v2.git"),
    "owner-name/repo_name.v2",
  );
});

test("rejects query tokens and fragments appended to GitHub shorthand", () => {
  for (const value of [
    "owner/repo?TOKEN=query-secret",
    "owner/repo?Access_Token=query-secret",
    "owner/repo#fragment-secret",
  ]) {
    assert.throws(() => canonicalizeSource(value), SourceIdentityError);
    assert.doesNotMatch(redactSource(value), /token|secret|fragment/i);
  }
});

test("rejects unsafe GitHub SSH query fragment and credential-like suffixes", () => {
  for (const value of [
    "git@github.com:owner/repo?ToKeN=query-secret",
    "ssh://git@github.com/owner/repo#fragment-secret",
    "git@github.com:owner/repo@ACCESS_TOKEN=query-secret",
    "ssh://git@github.com/owner/repo.git?KeY=query-secret",
  ]) {
    assert.throws(() => canonicalizeSource(value), SourceIdentityError);
    assert.doesNotMatch(redactSource(value), /token|secret|fragment|query/i);
  }
});

test("rejects delimiters and credential syntax embedded in shorthand components", () => {
  for (const value of [
    "owner/repo@secret",
    "owner/repo=secret",
    "owner:token/repo",
  ]) {
    assert.throws(() => canonicalizeSource(value), SourceIdentityError);
    assert.doesNotMatch(redactSource(value), /secret|token/i);
  }
});

test("preserves a GitHub ref and subpath", () => {
  assert.equal(
    canonicalizeSource("https://github.com/acme/skills/tree/v2/skills/review"),
    "https://github.com/acme/skills/tree/v2/skills/review",
  );
});

test("canonicalizes local paths through injected realpath", () => {
  assert.equal(
    canonicalizeSource("../skills", {
      cwd: "/repo/app",
      realpath: (value) => `/real${value}`,
    }),
    "/real/repo/skills",
  );
});

test("removes credentials from stored and displayed URLs", () => {
  const input = "https://user:secret@git.example.com/acme/skills.git?token=query-secret";
  assert.doesNotMatch(canonicalizeSource(input), /user|secret/);
  assert.equal(redactSource(input), "https://git.example.com/acme/skills.git");
});

test("strips every generic URL query and fragment case-insensitively", () => {
  const input = "https://git.example.com/acme/skills.git?ToKeN=query-secret&mode=private#fragment-secret";
  assert.equal(canonicalizeSource(input), "https://git.example.com/acme/skills");
  assert.equal(redactSource(input), "https://git.example.com/acme/skills.git");
});

test("rejects unsafe opaque sources while redacting them for display", () => {
  const input = "opaque source?ACCESS_TOKEN=query-secret#fragment-secret";
  assert.throws(() => canonicalizeSource(input), SourceIdentityError);
  assert.equal(redactSource(input), "opaque source");
});

test("redacts opaque credential assignments even when URL parsing succeeds", () => {
  const input = "opaque:ACCESS_TOKEN=query-secret";
  assert.throws(() => canonicalizeSource(input), SourceIdentityError);
  assert.equal(redactSource(input), "[unsafe source redacted]");
});

test("rejects empty source strings", () => {
  assert.throws(() => canonicalizeSource("   "), SourceIdentityError);
});
