import { spawn, spawnSync } from "node:child_process";

export const BACKUP_GROUP = "anngth-dev/backups";
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

const DELETION_PATH_RE = /-(deletion_scheduled|deleted)-\d+$/;

/**
 * Soft-deleted / pending-deletion projects still resolve via the original path
 * on GitLab.com even after rename. Treat them as not live for backup collision.
 */
export function isInactiveGitLabProject(project, requestedName) {
  if (!project || typeof project !== "object") return false;
  if (project.marked_for_deletion_on || project.marked_for_deletion_at) {
    return true;
  }
  const path = project.path;
  return (
    typeof path === "string"
    && path !== requestedName
    && DELETION_PATH_RE.test(path)
  );
}

export async function projectExists(
  group,
  name,
  { runGlab: runGlabDependency = runGlab } = {},
) {
  const path = `projects/${encodeURIComponent(`${group}/${name}`)}`;
  const result = await runGlabDependency(["api", path]);
  if (result.status === 0) {
    let project;
    try {
      project = JSON.parse(result.stdout || "{}");
    } catch {
      return { ok: false, error: "could not parse GitLab project response" };
    }
    if (isInactiveGitLabProject(project, name)) {
      return { ok: true, exists: false, inactive: true };
    }
    return { ok: true, exists: true };
  }

  const detail = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  if (/404|not found/i.test(detail)) return { ok: true, exists: false };

  return { ok: false, error: resultError(result, "glab API request failed") };
}

export async function groupExists(
  group,
  { runGlab: runGlabDependency = runGlab } = {},
) {
  const path = `groups/${encodeURIComponent(group)}`;
  const result = await runGlabDependency(["api", path]);
  if (result.status === 0) return { ok: true, exists: true };

  const detail = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  if (/404|not found/i.test(detail)) return { ok: true, exists: false };

  return { ok: false, error: resultError(result, "glab API request failed") };
}

/**
 * Create a private group. Nested paths (parent/leaf) are created as a subgroup
 * under the parent (top-level group create is often forbidden by token scopes).
 */
export async function createPrivateGroup(
  group,
  { runGlab: runGlabDependency = runGlab } = {},
) {
  const slash = group.lastIndexOf("/");
  if (slash === -1) {
    const result = await runGlabDependency([
      "api",
      "--method",
      "POST",
      "groups",
      "-f",
      `name=${group}`,
      "-f",
      `path=${group}`,
      "-f",
      "visibility=private",
    ]);
    if (result.status !== 0) {
      return { ok: false, error: resultError(result, "failed to create GitLab group") };
    }
    return { ok: true, stdout: result.stdout, stderr: result.stderr };
  }

  const parentPath = group.slice(0, slash);
  const leaf = group.slice(slash + 1);
  const parentResult = await runGlabDependency([
    "api",
    `groups/${encodeURIComponent(parentPath)}`,
  ]);
  if (parentResult.status !== 0) {
    return {
      ok: false,
      error: resultError(parentResult, `parent group ${parentPath} not found`),
    };
  }

  let parentId;
  try {
    parentId = JSON.parse(parentResult.stdout).id;
  } catch {
    return { ok: false, error: `could not parse parent group id for ${parentPath}` };
  }
  if (parentId == null) {
    return { ok: false, error: `parent group ${parentPath} has no id` };
  }

  const result = await runGlabDependency([
    "api",
    "--method",
    "POST",
    "groups",
    "-f",
    `name=${leaf}`,
    "-f",
    `path=${leaf}`,
    "-f",
    `parent_id=${parentId}`,
    "-f",
    "visibility=private",
  ]);
  if (result.status !== 0) {
    return { ok: false, error: resultError(result, "failed to create GitLab subgroup") };
  }

  return { ok: true, stdout: result.stdout, stderr: result.stderr };
}

/**
 * Ensure the backup namespace/group exists; create it as private if missing.
 * @returns {Promise<{ ok: true, created: boolean } | { ok: false, error: string }>}
 */
export async function ensureBackupGroup(
  group,
  {
    runGlab: runGlabDependency = runGlab,
    groupExists: groupExistsDependency = groupExists,
    createPrivateGroup: createPrivateGroupDependency = createPrivateGroup,
  } = {},
) {
  const existing = await groupExistsDependency(group, { runGlab: runGlabDependency });
  if (!existing.ok) {
    return { ok: false, error: existing.error || "could not check GitLab group" };
  }
  if (existing.exists) return { ok: true, created: false };

  const created = await createPrivateGroupDependency(group, { runGlab: runGlabDependency });
  if (!created.ok) {
    return { ok: false, error: created.error || "failed to create GitLab group" };
  }

  return { ok: true, created: true };
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

/**
 * Prefer main, then develop, based on refs present in a local (mirror) repo.
 * Callers should protect both main and develop when those refs exist.
 * @returns {Promise<string|null>}
 */
export async function pickPreferredDefaultBranch(repoDir, { runGit } = {}) {
  const git = runGit;
  if (!git) return null;
  for (const branch of ["main", "develop"]) {
    const result = await git(
      ["show-ref", "--verify", "--quiet", `refs/heads/${branch}`],
      { cwd: repoDir },
    );
    if (result.status === 0) return branch;
  }
  return null;
}

export async function setDefaultBranch(
  group,
  name,
  branch,
  { runGlab: runGlabDependency = runGlab } = {},
) {
  const path = `projects/${encodeURIComponent(`${group}/${name}`)}`;
  const result = await runGlabDependency([
    "api",
    "--method",
    "PUT",
    path,
    "-f",
    `default_branch=${branch}`,
  ]);
  if (result.status !== 0) {
    return { ok: false, error: resultError(result, "failed to set default branch") };
  }
  return { ok: true };
}

/**
 * Protect a branch (Maintainers can push/merge; force-push allowed so later
 * mirror updates with +refs still work).
 * @returns {Promise<{ ok: true, alreadyProtected?: boolean } | { ok: false, error: string }>}
 */
export async function protectBranch(
  group,
  name,
  branch,
  { runGlab: runGlabDependency = runGlab } = {},
) {
  const path = `projects/${encodeURIComponent(`${group}/${name}`)}/protected_branches`;
  const result = await runGlabDependency([
    "api",
    "--method",
    "POST",
    path,
    "-f",
    `name=${branch}`,
    "-f",
    "push_access_level=40",
    "-f",
    "merge_access_level=40",
    "-f",
    "allow_force_push=true",
  ]);
  if (result.status === 0) return { ok: true };

  const detail = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  if (/already exists|already protected/i.test(detail)) {
    return { ok: true, alreadyProtected: true };
  }

  return { ok: false, error: resultError(result, "failed to protect branch") };
}
