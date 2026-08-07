import { mkdtempSync as mkdtempSyncDefault, rmSync as rmSyncDefault } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { runGit as runGitDefault } from "./git.mjs";
import {
  BACKUP_GROUP,
  assertGlabReady as assertGlabReadyDefault,
  createPrivateProject as createPrivateProjectDefault,
  ensureBackupGroup as ensureBackupGroupDefault,
  pickPreferredDefaultBranch as pickPreferredDefaultBranchDefault,
  projectExists as projectExistsDefault,
  projectSshUrl,
  projectWebUrl,
  setDefaultBranch as setDefaultBranchDefault,
} from "./gitlab.mjs";
import { parseSshGitUrl } from "./ssh-url.mjs";
import { createUi } from "./ui.mjs";

const RED = "\u001b[31m";

function defaultHasCommand(name) {
  return spawnSync("which", [name], { stdio: "ignore" }).status === 0;
}

function resolveContext(context = {}) {
  const {
    cwd = process.cwd(),
    env = process.env,
    stdout = process.stdout,
    ui = createUi({ stdout, stderr: context.stderr ?? process.stderr }),
    hasCommand = defaultHasCommand,
    assertGlabReady = assertGlabReadyDefault,
    ensureBackupGroup = ensureBackupGroupDefault,
    projectExists = projectExistsDefault,
    createPrivateProject = createPrivateProjectDefault,
    pickPreferredDefaultBranch = pickPreferredDefaultBranchDefault,
    setDefaultBranch = setDefaultBranchDefault,
    runGit = runGitDefault,
    mkdtempSync = mkdtempSyncDefault,
    rmSync = rmSyncDefault,
  } = context;

  return {
    cwd,
    env,
    ui,
    hasCommand,
    assertGlabReady,
    ensureBackupGroup,
    projectExists,
    createPrivateProject,
    pickPreferredDefaultBranch,
    setDefaultBranch,
    runGit,
    mkdtempSync,
    rmSync,
  };
}

/**
 * Mirror one source SSH URL into the GitLab backup group.
 * Always updates live projects, recreates inactive ones, and creates missing ones.
 * Never uses suffix / --new naming.
 *
 * @param {string} sourceUrl
 * @param {Record<string, unknown>} context
 * @returns {Promise<
 *   | { ok: true, webUrl: string, projectPath: string }
 *   | { ok: false, error: string }
 * >}
 */
export async function backupOneRepo(sourceUrl, context = {}) {
  const {
    cwd,
    env,
    ui,
    hasCommand,
    assertGlabReady,
    ensureBackupGroup,
    projectExists,
    createPrivateProject,
    pickPreferredDefaultBranch,
    setDefaultBranch,
    runGit,
    mkdtempSync,
    rmSync,
  } = resolveContext(context);

  const parsed = parseSshGitUrl(sourceUrl);
  if (!parsed.ok) {
    return { ok: false, error: parsed.error };
  }

  if (!(await hasCommand("git"))) {
    return { ok: false, error: "git is not installed or not available on PATH" };
  }

  const glabReady = await assertGlabReady({ hasCommand, env });
  if (!glabReady.ok) {
    return { ok: false, error: glabReady.error };
  }

  const targetName = parsed.projectName;
  const group = BACKUP_GROUP;
  const projectPath = `${group}/${targetName}`;

  ui.step(`${sourceUrl} → ${projectPath}`);

  const groupReady = await ensureBackupGroup(group);
  if (!groupReady.ok) {
    return { ok: false, error: groupReady.error || "could not ensure GitLab backup group" };
  }
  if (groupReady.created) {
    ui.success(`Created group ${group}`);
  }

  const existsResult = await projectExists(group, targetName);
  if (!existsResult.ok) {
    return { ok: false, error: existsResult.error || "could not check GitLab project" };
  }

  if (existsResult.exists) {
    ui.step(`Updating existing backup ${projectPath}`);
  } else {
    if (existsResult.inactive) {
      ui.step(`Previous backup pending deletion (inactive); creating ${projectPath}`);
    }
    const created = await createPrivateProject(group, targetName);
    if (!created.ok) {
      return { ok: false, error: created.error || "failed to create GitLab project" };
    }
    ui.success(`Created ${projectPath}`);
  }

  const destUrl = projectSshUrl(group, targetName);
  const tempRoot = mkdtempSync(join(tmpdir(), "gt-backup-"));
  const mirrorDir = join(tempRoot, "mirror.git");

  try {
    ui.step(`Cloning source to ${mirrorDir}`);
    const cloneResult = await runGit(
      ["clone", "--mirror", sourceUrl, mirrorDir],
      { cwd, env },
    );
    if (cloneResult.status !== 0) {
      return {
        ok: false,
        error: cloneResult.stderr?.trim() || cloneResult.stdout?.trim() || "git clone --mirror failed",
      };
    }
    ui.success("Clone complete");

    ui.step(`Pushing all branches + tags → ${projectPath}`);
    const pushResult = await runGit(
      [
        "push",
        "--prune",
        destUrl,
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
      ],
      { cwd: mirrorDir, env },
    );
    if (pushResult.status !== 0) {
      return {
        ok: false,
        error: pushResult.stderr?.trim() || pushResult.stdout?.trim() || "git push failed",
      };
    }
    ui.success("Pushed all branches + tags");

    const preferred = await pickPreferredDefaultBranch(mirrorDir, { runGit });
    if (preferred) {
      const setDefault = await setDefaultBranch(group, targetName, preferred);
      if (!setDefault.ok) {
        ui.warn(
          `Could not set default branch to ${preferred}: ${setDefault.error || "unknown error"}`,
        );
      } else {
        ui.success(`Default branch ${preferred}`);
      }
    }
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }

  return {
    ok: true,
    webUrl: projectWebUrl(group, targetName),
    projectPath,
  };
}

/**
 * Run backups sequentially; continue after failures; print summary.
 *
 * @param {string[]} urls
 * @param {Record<string, unknown>} context
 * @returns {Promise<number>} 0 if all ok, else 1
 */
export async function runBackupBatch(urls, context = {}) {
  const { ui } = resolveContext(context);
  const successes = [];
  const failures = [];

  for (const url of urls ?? []) {
    const result = await backupOneRepo(url, context);
    if (result.ok) {
      successes.push({ url, webUrl: result.webUrl, projectPath: result.projectPath });
    } else {
      failures.push({ url, error: result.error });
    }
  }

  ui.step("Backup summary");
  for (const s of successes) {
    ui.item(`ok  ${s.url} → ${s.webUrl}`);
  }
  for (const f of failures) {
    ui.item(`fail  ${f.url} — ${f.error}`, RED);
  }
  ui.listEnd();

  return failures.length === 0 ? 0 : 1;
}

/**
 * Temporary stub until Task 5 rewires CLI dispatch.
 * @param {string[]} _args
 * @param {Record<string, unknown>} context
 * @returns {Promise<number>}
 */
export async function runBackupCommand(_args, context = {}) {
  const { ui } = resolveContext(context);
  ui.error(
    "gt backup one-shot URL is retired; use gt backup add / gt backup / gt backup --all",
  );
  return 1;
}
