#!/usr/bin/env node
import fs from "node:fs";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { copyToClipboard } from "./clipboard.mjs";
import { generateTotp } from "./totp.mjs";
import { createUi } from "./ui.mjs";

function setTtyEcho(fd, on, deps = {}) {
  const spawnSyncImpl = deps.spawnSync ?? spawnSync;
  const result = spawnSyncImpl("stty", [on ? "echo" : "-echo"], {
    stdio: [fd, "inherit", "inherit"],
  });
  if (result.error || result.status !== 0) {
    throw new Error(on ? "failed to restore terminal echo" : "failed to disable terminal echo");
  }
}

export async function defaultReadSecret(prompt, deps = {}) {
  const openTty = deps.openTty ?? (() => fs.openSync("/dev/tty", fs.constants.O_RDWR));
  let fd;
  try {
    fd = openTty();
  } catch {
    throw new Error("interactive terminal required");
  }

  const restore = () => {
    try { setTtyEcho(fd, true, deps); } catch { /* ignore */ }
    try { fs.closeSync(fd); } catch { /* ignore */ }
  };

  try {
    setTtyEcho(fd, false, deps);
    fs.writeSync(fd, prompt);
    let line = "";
    const buf = Buffer.alloc(1);
    while (true) {
      const n = fs.readSync(fd, buf, 0, 1, null);
      if (n === 0) break;
      const c = buf[0];
      if (c === 0x0a || c === 0x0d) break;
      line += String.fromCharCode(c);
    }
    fs.writeSync(fd, "\n");
    setTtyEcho(fd, true, deps);
    fs.closeSync(fd);
    return line;
  } catch (err) {
    restore();
    throw err;
  }
}

export async function runCli(argv, dependencies = {}) {
  const {
    stdout = process.stdout,
    stderr = process.stderr,
    ui = createUi({ stdout, stderr }),
  } = dependencies;

  const action = argv[0];

  if (action !== undefined && ["help", "-h", "--help"].includes(action)) {
    ui.usage();
    return 0;
  }

  if (action !== undefined) {
    ui.error(`Unknown command: ${action}`);
    ui.usage();
    return 1;
  }

  try {
    const readSecret = dependencies.readSecret ?? defaultReadSecret;
    const copy = dependencies.copyToClipboard ?? copyToClipboard;
    const now = dependencies.now;
    const secret = await readSecret("Base32 secret: ", dependencies);
    const otp = generateTotp(secret, now !== undefined ? { now } : {});
    await copy(otp, dependencies);
    ui.successCopied(otp);
    return 0;
  } catch (err) {
    ui.error(err?.message ?? String(err));
    return 1;
  }
}

async function main() {
  process.exitCode = await runCli(process.argv.slice(2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
