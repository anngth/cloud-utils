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

test("2fa badge uses green background", () => {
  const h = captureUi();
  h.ui.usage();
  assert.match(h.stdout, /\u001b\[42m/);
  assert.doesNotMatch(h.stdout, /\u001b\[46m/);
});

test("interactive flow exposes a framed hidden-input prompt", () => {
  const h = captureUi();
  h.ui.beginTotp();
  const prompt = h.ui.secretPrompt();
  h.ui.successCopied("123456");

  const plain = h.stdout.replace(/\u001b\[[0-9;]*m/g, "");
  assert.equal((plain.match(/2FA/g) ?? []).length, 1);
  assert.match(prompt.replace(/\u001b\[[0-9;]*m/g, ""), /^◆  Base32 secret: $/);
  assert.match(plain, /◇  Generate TOTP/);
  assert.match(plain, /◇  Code copied: 123456/);
  assert.match(plain, /└/);
});
