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

export function runNpx(args, { spawnImpl = spawn, cwd } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (status) => {
      if (settled) return;
      settled = true;
      resolve(status);
    };
    let child;
    try {
      child = spawnImpl("npx", args, { ...(cwd ? { cwd } : {}), stdio: "inherit" });
    } catch {
      finish(1);
      return;
    }
    child.once("error", () => finish(1));
    child.once("close", (status) => finish(Number.isInteger(status) ? status : 1));
  });
}

export function runNpxCapture(args, {
  spawnImpl = spawn,
  cwd = process.cwd(),
} = {}) {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (status) => {
      if (settled) return;
      settled = true;
      resolve({ status, stdout, stderr });
    };
    let child;
    try {
      child = spawnImpl("npx", args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    } catch {
      finish(1);
      return;
    }
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", () => finish(1));
    child.once("close", (status) => finish(Number.isInteger(status) ? status : 1));
  });
}

export class DiscoveryParseError extends Error {}

const ANSI = /\u001b\[[0-?]*[ -/]*[@-~]/g;

export function parseAvailableSkills(output) {
  const lines = output.replace(ANSI, "").split(/\r?\n/);
  const start = lines.findIndex((line) => line.includes("Available Skills"));
  const end = lines.findIndex((line, index) => index > start && line.includes("Use --skill"));
  if (start < 0 || end < 0) throw new DiscoveryParseError("Could not parse available skills");

  const result = [];
  for (let index = start + 1; index < end; index += 1) {
    const name = lines[index].match(/^│\s{4}(\S.*)$/)?.[1]?.trim();
    if (!name) continue;
    let descriptionIndex = index + 1;
    while (descriptionIndex < end && /^│\s*$/.test(lines[descriptionIndex])) {
      descriptionIndex += 1;
    }
    const description = lines[descriptionIndex]?.match(/^│\s{6}(\S.*)$/)?.[1]?.trim();
    if (!description) throw new DiscoveryParseError(`Missing description for skill: ${name}`);
    result.push({ name, description });
    index = descriptionIndex;
  }
  if (result.length === 0) throw new DiscoveryParseError("No skill records found");
  if (new Set(result.map((item) => item.name)).size !== result.length) {
    throw new DiscoveryParseError("Duplicate skill names in discovery output");
  }
  return result;
}

function isInstalledSkill(value) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && typeof value.name === "string"
    && typeof value.path === "string"
    && typeof value.scope === "string"
    && Array.isArray(value.agents);
}

export async function listInstalledSkills({ cwd, runner = runNpxCapture } = {}) {
  const { status, stdout } = await runner(["skills", "list", "--json"], { cwd });
  if (status !== 0) throw new Error("Could not list installed skills");

  let skills;
  try {
    skills = JSON.parse(stdout);
  } catch {
    throw new Error("Could not parse installed skills");
  }
  if (!Array.isArray(skills) || !skills.every(isInstalledSkill)) {
    throw new Error("Invalid installed skills output");
  }
  return skills;
}

export async function discoverAvailableSkills(source, { cwd, runner = runNpxCapture } = {}) {
  const { status, stdout } = await runner(["skills", "add", source, "--list"], { cwd });
  if (status !== 0) throw new Error("Could not discover available skills");
  return parseAvailableSkills(stdout);
}

export function runSkillsMutation(args, { runner = runNpx, cwd } = {}) {
  return runner(args, { ...(cwd ? { cwd } : {}) });
}
