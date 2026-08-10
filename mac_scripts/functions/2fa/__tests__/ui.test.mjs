import assert from "node:assert/strict";
import test from "node:test";
import { createUi } from "../ui.mjs";

function captureUi() {
  let stdout = "";
  let stderr = "";
  const ui = createUi({
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write: (v) => { stderr += v; } },
  });
  const lines = () => stdout
    .replace(/\u001b\[[0-9;]*m/g, "")
    .split(/\r?\n/)
    .map((line) => line.trimEnd());
  return { ui, lines, get stdout() { return stdout; }, get stderr() { return stderr; } };
}

test("usage matches skm-style 2fa help", () => {
  const h = captureUi();
  h.ui.usage();
  const lines = h.lines();

  assert.ok(lines.some((line) => line.includes("2FA")));
  assert.equal(lines.find((line) => line.includes("Usage:")), "◇  Usage: 2fa [command]");
  assert.ok(lines.includes("│  2fa  Prompt for Base32 secret; copy 6-digit TOTP"));
  assert.ok(lines.includes("│  2fa (help | -h | --help)  Show this help"));
  assert.ok(lines.includes("◆  Notes"));
  assert.ok(lines.includes("│  Secret is read hidden from the terminal; code goes to the clipboard."));
});

test("error writes red cross to stderr", () => {
  const h = captureUi();
  h.ui.error("interactive terminal required");
  assert.match(h.stderr, /❌/);
  assert.match(h.stderr, /interactive terminal required/);
});

test("successCopied frames Code copied", () => {
  const h = captureUi();
  h.ui.successCopied("123456");
  const text = h.stdout.replace(/\u001b\[[0-9;]*m/g, "");
  assert.match(text, /2FA/);
  assert.match(text, /Code copied: 123456/);
  assert.match(text, /◇|└/);
});
