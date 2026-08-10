import { spawn } from "node:child_process";

export function copyToClipboard(text, { spawnImpl = spawn } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawnImpl("pbcopy", [], { stdio: ["pipe", "ignore", "pipe"] });
    let err = "";
    child.stderr.on("data", (chunk) => { err += chunk; });
    child.on("error", () => {
      reject(new Error("failed to copy code to clipboard"));
    });
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error("failed to copy code to clipboard"));
    });
    child.stdin.end(String(text));
  });
}
