import assert from "node:assert/strict";
import test from "node:test";
import { createUi } from "../ui.mjs";

test("status uses skm-style step marker instead of --- prefix", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  ui.status("Checking backup group");

  assert.match(stdout, /◇/);
  assert.match(stdout, /Checking backup group/);
  assert.doesNotMatch(stdout, /^--- /m);
});

test("error uses skm-style red cross prefix", () => {
  let stderr = "";
  const ui = createUi({
    stdout: { write() {} },
    stderr: { write: (value) => { stderr += value; } },
  });

  ui.error("Backup cancelled.");

  assert.match(stderr, /❌/);
  assert.match(stderr, /Backup cancelled/);
});

test("title and listEnd frame output like skm", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  ui.title("GT BACKUP");
  ui.step("Done");
  ui.listEnd("https://gitlab.com/example");

  assert.match(stdout, /GT BACKUP/);
  assert.match(stdout, /◇/);
  assert.match(stdout, /└/);
  assert.match(stdout, /https:\/\/gitlab\.com\/example/);
});

test("success renders text in green", () => {
  let stdout = "";
  const ui = createUi({
    stdout: { write: (value) => { stdout += value; } },
    stderr: { write() {} },
  });

  ui.success("Push complete");

  assert.match(stdout, /\u001b\[92mPush complete/);
  assert.match(stdout, /◇/);
});
