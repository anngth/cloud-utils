import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { makeSandbox } from "./helpers.mjs";
import { bootstrapFile, defaultConfigDir, initializeConfig } from "../config.mjs";

test("defaultConfigDir uses HOME without os.homedir fallback", () => {
  assert.equal(
    defaultConfigDir({ HOME: "/Users/test" }),
    "/Users/test/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
  );
  assert.equal(
    defaultConfigDir({}),
    "/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
  );
});

test("bootstrapFile prefers legacy and never overwrites destination", (t) => {
  const sandbox = makeSandbox(t);
  const dest = join(sandbox.root, "dest/list.json");
  const example = join(sandbox.root, "example.json");
  const legacy = join(sandbox.root, "legacy.json");
  const writes = [];
  writeFileSync(example, "[]\n");
  writeFileSync(legacy, '[{"source":"legacy/repo"}]\n');

  assert.equal(bootstrapFile({ dest, example, legacy, stderr: { write: (s) => writes.push(s) } }), true);
  assert.equal(readFileSync(dest, "utf8"), '[{"source":"legacy/repo"}]\n');
  assert.match(writes.join(""), /Migrated list\.json/);

  writeFileSync(legacy, '[{"source":"changed/repo"}]\n');
  assert.equal(bootstrapFile({ dest, example, legacy }), true);
  assert.equal(readFileSync(dest, "utf8"), '[{"source":"legacy/repo"}]\n');
});

test("bootstrapFile silently falls back to the example", (t) => {
  const sandbox = makeSandbox(t);
  const dest = join(sandbox.root, "example-dest/list.json");
  const example = join(sandbox.root, "example-only.json");
  const writes = [];
  writeFileSync(example, '[{"source":"example/repo"}]\n');
  assert.equal(bootstrapFile({
    dest,
    example,
    legacy: join(sandbox.root, "absent.json"),
    stderr: { write: (value) => writes.push(value) },
  }), true);
  assert.equal(readFileSync(dest, "utf8"), '[{"source":"example/repo"}]\n');
  assert.deepEqual(writes, []);
});

test("initializeConfig performs the second legacy attempt when first pass creates nothing", (t) => {
  const sandbox = makeSandbox(t);
  const managerDir = join(sandbox.root, "manager");
  const configDir = join(sandbox.root, "config-two");
  mkdirSync(join(configDir, "skills"), { recursive: true });
  writeFileSync(join(configDir, "skills/list.json"), '[{"source":"old/repo"}]\n');

  const result = initializeConfig({
    env: { CLOUD_UTILS_CONFIG_DIR: configDir, HOME: sandbox.env.HOME },
    managerDir,
    stderr: { write() {} },
  });

  assert.equal(result.skillsFile, join(configDir, "skm/list.json"));
  assert.equal(readFileSync(result.skillsFile, "utf8"), '[{"source":"old/repo"}]\n');
  assert.equal(existsSync(join(configDir, "skills/list.json")), true);
});

test("initializeConfig propagates config directory creation failure", () => {
  const failure = new Error("mkdir denied");
  assert.throws(
    () => initializeConfig({
      env: { CLOUD_UTILS_CONFIG_DIR: "/denied" },
      managerDir: "/manager",
      fs: {
        copyFileSync() {},
        existsSync() { return false; },
        mkdirSync() { throw failure; },
      },
    }),
    failure,
  );
});
