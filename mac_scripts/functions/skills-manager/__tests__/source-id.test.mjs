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

test("rejects empty source strings", () => {
  assert.throws(() => canonicalizeSource("   "), SourceIdentityError);
});
