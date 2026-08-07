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
  nextSuffixedName as nextSuffixedNameDefault,
  pickPreferredDefaultBranch as pickPreferredDefaultBranchDefault,
  projectExists as projectExistsDefault,
  projectSshUrl,
  projectWebUrl,
  setDefaultBranch as setDefaultBranchDefault,
} from "./gitlab.mjs";
import { parseSshGitUrl } from "./ssh-url.mjs";
import { createUi } from "./ui.mjs";

function defaultHasCommand(name) {
  return spawnSync("which", [name], { stdio: "ignore" }).status === 0;
}

/**
 * @returns {{ ok: true, sshUrl: string, createNew: boolean }
 *   | { ok: false, error: string }}
 */
export function parseBackupArgs(args) {
  let createNew = false;
  const positionals = [];
  for (const arg of args ?? []) {
    if (arg === "-n" || arg === "--new") {
      createNew = true;
      continue;
    }
    if (arg.startsWith("-")) {
      return { ok: false, error: `Unknown option: ${arg}` };
    }
    positionals.push(arg);
  }
  if (positionals.length !== 1) {
    return { ok: false, error: "Usage: gt backup [-n|--new] <ssh-url>" };
  }
  return { ok: true, sshUrl: positionals[0].trim(), createNew };
}

/**
 * @param {string[]} args
 * @param {Record<string, unknown>} context
 * @returns {Promise<number>}
 */
export async function runBackupCommand(args, context = {}) {
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
    nextSuffixedName = nextSuffixedNameDefault,
    pickPreferredDefaultBranch = pickPreferredDefaultBranchDefault,
    setDefaultBranch = setDefaultBranchDefault,
    runGit = runGitDefault,
    mkdtempSync = mkdtempSyncDefault,
    rmSync = rmSyncDefault,
  } = context;

  const parsedArgs = parseBackupArgs(args);
  if (!parsedArgs.ok) {
    ui.error(parsedArgs.error);
    return 1;
  }

  const { sshUrl: sourceUrl, createNew } = parsedArgs;

  const parsed = parseSshGitUrl(sourceUrl);
  if (!parsed.ok) {
    ui.error(parsed.error);
    return 1;
  }

  if (!(await hasCommand("git"))) {
    ui.error("git is not installed or not available on PATH");
    return 1;
  }

  const glabReady = await assertGlabReady({ hasCommand, env });
  if (!glabReady.ok) {
    ui.error(glabReady.error);
    return 1;
  }

  const baseName = parsed.projectName;
  const group = BACKUP_GROUP;
  const projectPath = `${group}/${baseName}`;

  ui.title("REPO BACKUP");
  ui.step(`${sourceUrl} → ${projectPath}`);

  const groupReady = await ensureBackupGroup(group);
  if (!groupReady.ok) {
    ui.error(groupReady.error || "could not ensure GitLab backup group");
    return 1;
  }
  if (groupReady.created) {
    ui.success(`Created group ${group}`);
  }

  const existsResult = await projectExists(group, baseName);
  if (!existsResult.ok) {
    ui.error(existsResult.error || "could not check GitLab project");
    return 1;
  }

  let targetName = baseName;

  if (existsResult.exists) {
    if (createNew) {
      const nextName = await nextSuffixedName(group, baseName);
      if (!nextName.ok) {
        ui.error(nextName.error || "could not find available backup name");
        return 1;
      }
      targetName = nextName.name;
      const created = await createPrivateProject(group, targetName);
      if (!created.ok) {
        ui.error(created.error || "failed to create GitLab project");
        return 1;
      }
      ui.success(`Created ${group}/${targetName}`);
    } else {
      targetName = baseName;
      ui.step(`Updating existing backup ${group}/${targetName}`);
    }
  } else {
    if (existsResult.inactive) {
      ui.step(`Previous backup pending deletion (inactive); creating ${projectPath}`);
    }
    const created = await createPrivateProject(group, targetName);
    if (!created.ok) {
      ui.error(created.error || "failed to create GitLab project");
      return 1;
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
      ui.error(cloneResult.stderr?.trim() || cloneResult.stdout?.trim() || "git clone --mirror failed");
      return 1;
    }
    ui.success("Clone complete");

    ui.step(`Pushing all branches + tags → ${group}/${targetName}`);
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
      ui.error(pushResult.stderr?.trim() || pushResult.stdout?.trim() || "git push failed");
      return 1;
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

  ui.listEnd(projectWebUrl(group, targetName));
  return 0;
}
