import { spawn, spawnSync } from "node:child_process";

export const BACKUP_GROUP = "anngth-backups";
export const GITLAB_HOST = "gitlab.com";

function defaultHasCommand(name) {
  return spawnSync("which", [name], { stdio: "ignore" }).status === 0;
}

export function runGlab(
  args,
  { cwd = process.cwd(), env = process.env, runCommand } = {},
) {
  if (runCommand) return runCommand("glab", args, { cwd, env });

  return new Promise((resolve) => {
    const child = spawn("glab", args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => resolve({ status: 1, stdout, stderr: error.message }));
    child.on("close", (status) => resolve({ status: status ?? 1, stdout, stderr }));
  });
}

function resultError(result, fallback) {
  return result.stderr?.trim() || result.stdout?.trim() || fallback;
}

export async function assertGlabReady({
  hasCommand = defaultHasCommand,
  runGlab: runGlabDependency = runGlab,
  env = process.env,
} = {}) {
  if (!(await hasCommand("glab"))) {
    return { ok: false, error: "glab is not installed or not available on PATH" };
  }

  const result = await runGlabDependency(["auth", "status"], { env });
  if (result.status !== 0) {
    return {
      ok: false,
      error: `glab authentication is required: ${resultError(result, "not logged in")}`,
    };
  }

  return { ok: true };
}

export async function projectExists(
  group,
  name,
  { runGlab: runGlabDependency = runGlab } = {},
) {
  const path = `projects/${encodeURIComponent(`${group}/${name}`)}`;
  const result = await runGlabDependency(["api", path]);
  if (result.status === 0) return { ok: true, exists: true };

  const detail = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  if (/404|not found/i.test(detail)) return { ok: true, exists: false };

  return { ok: false, error: resultError(result, "glab API request failed") };
}

export async function createPrivateProject(
  group,
  name,
  { runGlab: runGlabDependency = runGlab } = {},
) {
  // Use --skipGitInit to keep the project empty for the first mirror push.
  const result = await runGlabDependency([
    "repo",
    "create",
    name,
    "--group",
    group,
    "--private",
    "--skipGitInit",
  ]);
  if (result.status !== 0) {
    return { ok: false, error: resultError(result, "failed to create GitLab project") };
  }

  return { ok: true, stdout: result.stdout, stderr: result.stderr };
}

async function findName(group, baseName, projectExistsDependency, start) {
  for (let suffix = start; ; suffix += 1) {
    const name = suffix === 0 ? baseName : `${baseName}-${suffix}`;
    const result = await projectExistsDependency(group, name);
    if (!result.ok) {
      return { ok: false, error: result.error || "could not check GitLab project" };
    }
    if (!result.exists) return { ok: true, name };
  }
}

export async function nextAvailableName(
  group,
  baseName,
  { projectExists: projectExistsDependency = projectExists } = {},
) {
  return findName(group, baseName, projectExistsDependency, 0);
}

export async function nextSuffixedName(
  group,
  baseName,
  { projectExists: projectExistsDependency = projectExists } = {},
) {
  return findName(group, baseName, projectExistsDependency, 2);
}

export function projectSshUrl(group, name) {
  return `git@${GITLAB_HOST}:${group}/${name}.git`;
}

export function projectWebUrl(group, name) {
  return `https://${GITLAB_HOST}/${group}/${name}`;
}
