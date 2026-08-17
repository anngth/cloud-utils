import { spawn as defaultSpawn } from "node:child_process";
import { accessSync, constants, existsSync } from "node:fs";
import { join } from "node:path";

const OPT_BREW = "/opt/homebrew/bin/brew";
const INTEL_BREW = "/usr/local/bin/brew";

const defaultFs = {
  existsSync,
  accessSync,
  constants,
};

/**
 * @param {import("node:fs")} fs
 * @param {string} filePath
 */
function isExecutable(fs, filePath) {
  if (!fs.existsSync(filePath)) {
    return false;
  }
  try {
    fs.accessSync(filePath, fs.constants?.X_OK ?? constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * @param {{ env?: Record<string, string | undefined>, fs?: typeof defaultFs, pathEnv?: string }} [options]
 * @returns {string | null}
 */
export function resolveBrewBinary({ env = process.env, fs = defaultFs, pathEnv } = {}) {
  const pathValue = pathEnv ?? env.PATH ?? "";
  for (const dir of pathValue.split(":")) {
    if (!dir) {
      continue;
    }
    const candidate = join(dir, "brew");
    if (isExecutable(fs, candidate)) {
      return candidate;
    }
  }
  if (isExecutable(fs, OPT_BREW)) {
    return OPT_BREW;
  }
  if (isExecutable(fs, INTEL_BREW)) {
    return INTEL_BREW;
  }
  return null;
}

/**
 * @param {string[]} args
 * @returns {string}
 */
export function formatBrewCommand(args) {
  const quoted = args.map((arg) => {
    if (arg === "") return "''";
    if (/^[A-Za-z0-9_./:=+-]+$/.test(arg)) return arg;
    return `'${String(arg).replace(/'/g, `'\\''`)}'`;
  });
  return `$ brew ${quoted.join(" ")}`;
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
export function isBrewProbe(args) {
  const [cmd, ...rest] = args;
  if (cmd === "list" || cmd === "info") return true;
  if (cmd === "tap" && rest.length === 0) return true;
  if (cmd === "trust" && rest.includes("--help")) return true;
  if (cmd === "--help") return true;
  return false;
}

/**
 * @param {string[]} args
 * @param {{
 *   brewBin: string,
 *   spawn?: typeof defaultSpawn,
 *   cwd?: string,
 *   onCommand?: (line: string) => void,
 *   stdout?: { write: (chunk: string | Buffer) => void },
 *   stderr?: { write: (chunk: string | Buffer) => void },
 *   mode?: "probe" | "interactive",
 * }} options
 * @returns {Promise<{ code: number, stdout: string, stderr: string }>}
 */
export function runBrew(
  args,
  {
    brewBin,
    spawn = defaultSpawn,
    cwd,
    onCommand = (line) => process.stdout.write(`${line}\n`),
    mode,
  } = {},
) {
  const resolvedMode = mode ?? (isBrewProbe(args) ? "probe" : "interactive");
  return new Promise((resolve) => {
    onCommand(formatBrewCommand(args));
    if (resolvedMode === "interactive") {
      const child = spawn(brewBin, args, { cwd, stdio: "inherit" });
      const finish = (code) => resolve({ code: code ?? 1, stdout: "", stderr: "" });
      child.on("close", finish);
      child.on("error", () => finish(1));
      return;
    }
    const child = spawn(brewBin, args, {
      cwd,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr?.on("data", (chunk) => {
      stderr += chunk;
    });
    const finish = (code) => resolve({ code: code ?? 1, stdout, stderr });
    child.on("close", finish);
    child.on("error", () => finish(1));
  });
}

/**
 * @param {{
 *   brewBin: string,
 *   ui?: { command?: (line: string) => void },
 *   stdout?: { write: (chunk: string | Buffer) => void },
 *   spawn?: typeof defaultSpawn,
 * }} options
 * @returns {(args: string[]) => Promise<{ code: number, stdout: string, stderr: string }>}
 */
export function createBrewRunner({
  brewBin,
  ui,
  stdout = process.stdout,
  spawn,
}) {
  return (args) => runBrew(args, {
    brewBin,
    spawn,
    onCommand: (line) => {
      if (ui?.command) ui.command(line);
      else stdout.write(`${line}\n`);
    },
  });
}

/**
 * @param {string} text
 * @returns {string[]}
 */
function parseLines(text) {
  return text.split(/\r?\n/).filter((line) => line.length > 0);
}

/**
 * @param {string} name
 * @returns {string}
 */
function shortName(name) {
  const slash = name.lastIndexOf("/");
  return slash === -1 ? name : name.slice(slash + 1);
}

/**
 * @param {{ brewBin: string, runBrew?: (args: string[]) => Promise<{ code: number, stdout: string, stderr: string }> }} options
 */
export async function loadBrewState({ brewBin, runBrew }) {
  const runner = runBrew ?? ((args) => runBrewImpl(args, { brewBin }));

  const [formulasResult, casksResult, tapsResult] = await Promise.all([
    runner(["list", "--formula"]),
    runner(["list", "--cask", "--full-name"]),
    runner(["tap"]),
  ]);

  return {
    formulas: parseLines(formulasResult.stdout).map(shortName),
    casks: parseLines(casksResult.stdout).map(shortName),
    taps: parseLines(tapsResult.stdout),
  };
}

/**
 * @param {"formula" | "cask"} type
 * @param {string} name
 * @param {{ brewBin: string, runBrew?: (args: string[]) => Promise<{ code: number, stdout: string, stderr: string }> }} deps
 * @returns {Promise<string | null>}
 */
export async function brewInfoToken(type, name, { brewBin, runBrew } = {}) {
  const runner = runBrew ?? ((args) => runBrewImpl(args, { brewBin }));
  const flag = type === "cask" ? "--cask" : "--formula";
  const { code, stdout } = await runner(["info", flag, name]);
  if (code !== 0) {
    return null;
  }
  const firstLine = stdout.split(/\r?\n/)[0] ?? "";
  const match = firstLine.match(/^==>\s+([^: (]+)/);
  return match ? match[1] : null;
}

/**
 * @param {string} tap
 * @param {{ brewBin: string, runBrew?: (args: string[]) => Promise<{ code: number, stdout: string, stderr: string }> }} deps
 * @returns {Promise<boolean>}
 */
export async function ensureTap(tap, { brewBin, runBrew } = {}) {
  const runner = runBrew ?? ((args) => runBrewImpl(args, { brewBin }));

  const trustHelp = await runner(["trust", "--help"]);
  if (trustHelp.code === 0) {
    const trust = await runner(["trust", "--tap", tap]);
    if (trust.code !== 0) {
      return false;
    }
  }

  const tapResult = await runner(["tap", tap]);
  return tapResult.code === 0;
}

/**
 * @param {{ brewBin: string, runBrew?: (args: string[]) => Promise<{ code: number, stdout: string, stderr: string }> }} deps
 * @returns {Promise<string[]>}
 */
export async function listBrewTaps({ brewBin, runBrew } = {}) {
  const runner = runBrew ?? ((args) => runBrewImpl(args, { brewBin }));
  const { code, stdout } = await runner(["tap"]);
  if (code !== 0) {
    return [];
  }
  return parseLines(stdout);
}

/**
 * @param {string[]} args
 * @param {{
 *   brewBin: string,
 *   onCommand?: (line: string) => void,
 *   stdout?: { write: (chunk: string | Buffer) => void },
 *   stderr?: { write: (chunk: string | Buffer) => void },
 * }} options
 */
function runBrewImpl(args, { brewBin, onCommand, stdout, stderr }) {
  return runBrew(args, { brewBin, onCommand, stdout, stderr });
}
