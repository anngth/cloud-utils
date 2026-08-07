import { spawn } from "node:child_process";

export function runGit(args, { cwd = process.cwd(), env = process.env, runCommand } = {}) {
  if (runCommand) return runCommand("git", args, { cwd, env });
  return new Promise((resolve) => {
    const child = spawn("git", args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (c) => { stdout += c; });
    child.stderr.on("data", (c) => { stderr += c; });
    child.on("close", (status) => resolve({ status: status ?? 1, stdout, stderr }));
  });
}
