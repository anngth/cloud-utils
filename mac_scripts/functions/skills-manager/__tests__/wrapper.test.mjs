import assert from "node:assert/strict";
import { mkdirSync, readFileSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { WRAPPER, makeSandbox } from "./helpers.mjs";

test("wrapper forwards opaque argument boundaries and child status", (t) => {
  const sandbox = makeSandbox(t);
  const result = spawnSync(WRAPPER, ["show", "owner/repo with space"], {
    env: { ...sandbox.env, SKM_NPX_STATUS: "7" },
    encoding: "utf8",
  });
  assert.equal(result.status, 7);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","owner/repo with space","--list"]\n',
  );
});

test("wrapper reports missing node after creating the config directory", (t) => {
  const sandbox = makeSandbox(t);
  const noNodeBin = join(sandbox.root, "no-node-bin");
  mkdirSync(noNodeBin);
  symlinkSync("/usr/bin/dirname", join(noNodeBin, "dirname"));
  symlinkSync("/bin/mkdir", join(noNodeBin, "mkdir"));
  const result = spawnSync("/bin/zsh", [WRAPPER, "--help"], {
    env: { ...sandbox.env, PATH: noNodeBin },
    encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /node is required to read and update/);
});
