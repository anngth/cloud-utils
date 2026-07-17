import assert from "node:assert/strict";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { JS_ENTRY, WRAPPER, makeSandbox } from "./helpers.mjs";

test("wrapper forwards opaque source-show arguments and child status", (t) => {
  const sandbox = makeSandbox(t);
  const node = join(sandbox.binDir, "node");
  writeFileSync(
    node,
    `#!/bin/zsh
exec ${JSON.stringify(process.execPath)} -e '
const fs = require("fs");
fs.writeFileSync(process.env.SKM_ARGV_LOG, JSON.stringify(process.argv.slice(1)) + "\\n");
process.exit(7);
' "$@"
`,
    "utf8",
  );
  chmodSync(node, 0o755);
  const result = spawnSync(WRAPPER, ["source", "show", "owner/repo with space"], {
    env: sandbox.env,
    encoding: "utf8",
  });
  assert.equal(result.status, 7);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    `${JSON.stringify([JS_ENTRY, "source", "show", "owner/repo with space"])}\n`,
  );
});

test("missing-node error names the SKM config directory", (t) => {
  const sandbox = makeSandbox(t);
  rmSync(sandbox.configDir, { recursive: true });
  const noNodeBin = join(sandbox.root, "no-node-bin");
  mkdirSync(noNodeBin);
  symlinkSync("/usr/bin/dirname", join(noNodeBin, "dirname"));
  symlinkSync("/bin/mkdir", join(noNodeBin, "mkdir"));
  const result = spawnSync("/bin/zsh", [WRAPPER, "--help"], {
    env: { ...sandbox.env, PATH: noNodeBin },
    encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /node is required to manage .*\/skm/);
  assert.doesNotMatch(result.stderr, /list\.json/);
  assert.equal(existsSync(sandbox.configDir), true);
});
