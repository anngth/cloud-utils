import { chmodSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { EMPTY_CATALOG } from "../catalog.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
export const MANAGER_DIR = resolve(TEST_DIR, "..");
export const REPO_ROOT = resolve(MANAGER_DIR, "../../..");
export const JS_ENTRY = join(MANAGER_DIR, "cli.mjs");
export const WRAPPER = join(REPO_ROOT, "mac_scripts/skm");

function executable(path, body) {
  writeFileSync(path, body, "utf8");
  chmodSync(path, 0o755);
}

export function makeSandbox(t, {
  catalog = EMPTY_CATALOG,
  profiles,
  projects,
  legacyList,
  createCatalog = true,
  createProfiles = profiles !== undefined,
  createProjects = projects !== undefined,
} = {}) {
  const root = mkdtempSync(join(tmpdir(), "skm-test-"));
  const configDir = join(root, "config");
  const binDir = join(root, "bin");
  const skmDir = join(configDir, "skm");
  const sourcesFile = join(skmDir, "sources.json");
  const profilesFile = join(skmDir, "profiles.json");
  const projectsFile = join(skmDir, "projects.json");
  const legacyFile = join(skmDir, "list.json");
  const argvLog = join(root, "npx-argv.jsonl");
  mkdirSync(skmDir, { recursive: true });
  mkdirSync(binDir, { recursive: true });
  if (createCatalog) writeFileSync(sourcesFile, `${JSON.stringify(catalog, null, 2)}\n`);
  if (createProfiles) {
    writeFileSync(profilesFile, `${JSON.stringify(profiles ?? {
      version: 1,
      profiles: [{ name: "default", sources: [] }],
    }, null, 2)}\n`);
  }
  if (createProjects) {
    writeFileSync(projectsFile, `${JSON.stringify(projects ?? { version: 1, projects: [] }, null, 2)}\n`);
  }
  if (legacyList !== undefined) {
    writeFileSync(legacyFile, `${JSON.stringify(legacyList, null, 2)}\n`);
  }

  executable(
    join(binDir, "node"),
    `#!/bin/zsh\nexec ${JSON.stringify(process.execPath)} "$@"\n`,
  );
  executable(
    join(binDir, "npx"),
    `#!/bin/zsh\nprintf '%s\\n' "$@" | ${JSON.stringify(process.execPath)} -e '
const fs = require("fs");
const values = fs.readFileSync(0, "utf8").split("\\n").slice(0, -1);
fs.appendFileSync(process.env.SKM_ARGV_LOG, JSON.stringify(values) + "\\n");
if (JSON.stringify(values) === JSON.stringify(["skills", "list", "--json"])) {
  process.stdout.write("[]\\n");
  process.exit(0);
}
process.exit(Number(process.env.SKM_NPX_STATUS || 0));
'\n`,
  );

  const sandbox = {
    root,
    configDir,
    skmDir,
    sourcesFile,
    profilesFile,
    projectsFile,
    legacyFile,
    binDir,
    argvLog,
    env: {
      ...process.env,
      CLOUD_UTILS_CONFIG_DIR: configDir,
      HOME: join(root, "home"),
      PATH: `${binDir}${delimiter}${dirname(process.execPath)}:/usr/bin:/bin`,
      SKM_ARGV_LOG: argvLog,
      TERM: "xterm-256color",
    },
  };
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return sandbox;
}

function capture(command, args, sandbox, options = {}) {
  const result = spawnSync(command, args, {
    env: { ...sandbox.env, ...options.env },
    encoding: "utf8",
    input: options.input,
  });
  return {
    status: result.status,
    signal: result.signal,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
  };
}

export function runJavaScript(args, sandbox, options = {}) {
  return capture(process.execPath, [JS_ENTRY, ...args], sandbox, options);
}

export function normalizeCapture(captureResult, sandbox) {
  const replaceRoot = (value) => value.split(sandbox.root).join("<sandbox>");
  return {
    ...captureResult,
    stdout: replaceRoot(captureResult.stdout),
    stderr: replaceRoot(captureResult.stderr),
  };
}
