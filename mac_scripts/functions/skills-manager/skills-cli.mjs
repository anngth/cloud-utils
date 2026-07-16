import { accessSync, constants } from "node:fs";
import { delimiter, join } from "node:path";
import { spawn } from "node:child_process";

export function hasCommand(name, { env = process.env, access = accessSync } = {}) {
  for (const directory of (env.PATH ?? "").split(delimiter).filter(Boolean)) {
    try {
      access(join(directory, name), constants.X_OK);
      return true;
    } catch {}
  }
  return false;
}

export function runNpx(args, { spawnImpl = spawn } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (status) => {
      if (settled) return;
      settled = true;
      resolve(status);
    };
    let child;
    try {
      child = spawnImpl("npx", args, { stdio: "inherit" });
    } catch {
      finish(1);
      return;
    }
    child.once("error", () => finish(1));
    child.once("close", (status) => finish(Number.isInteger(status) ? status : 1));
  });
}
