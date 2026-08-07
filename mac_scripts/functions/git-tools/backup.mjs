import { mkdtempSync as mkdtempSyncDefault, rmSync as rmSyncDefault } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
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
 * @param {{
 *   projectPath: string,
 *   baseName: string,
 *   stdin?: NodeJS.ReadableStream,
 *   stdout?: NodeJS.WritableStream,
 *   isTTY?: boolean,
 * }} options
 * @returns {Promise<"update"|"new"|"cancel">}
 */
export async function chooseCollisionAction({
  projectPath,
  baseName,
  stdin = process.stdin,
  stdout = process.stdout,
  isTTY = Boolean(stdin.isTTY),
} = {}) {
  const write = (line = "") => {
    stdout.write(`${line}\n`);
  };

  write(`Project ${projectPath} already exists.`);
  write("  1) update  — mirror-push into the existing project");
  write(`  2) new     — create ${baseName}-N and mirror there`);
  write("  3) cancel");
  stdout.write("Choose [1/2/3]: ");

  const parseChoice = (line) => {
    const choice = String(line).trim().toLowerCase();
    if (choice === "1" || choice === "update") return "update";
    if (choice === "2" || choice === "new") return "new";
    if (choice === "3" || choice === "cancel") return "cancel";
    return null;
  };

  // Real Node streams are async-iterable by chunk, not by line — prefer readline
  // when EventEmitter APIs exist. Tests may inject a line async-iterable stub.
  const useLineIterator =
    typeof stdin[Symbol.asyncIterator] === "function" &&
    typeof stdin.on !== "function";
  const lines = useLineIterator
    ? stdin
    : createInterface({ input: stdin, crlfDelay: Infinity });

  try {
    for await (const line of lines) {
      const action = parseChoice(line);
      if (action) return action;
      stdout.write("Choose [1/2/3]: ");
    }
  } finally {
    if (typeof lines.close === "function") lines.close();
  }

  return "cancel";
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
    stdin = process.stdin,
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
    chooseCollisionAction: chooseCollision = chooseCollisionAction,
    runGit = runGitDefault,
    mkdtempSync = mkdtempSyncDefault,
    rmSync = rmSyncDefault,
  } = context;

  if (!args || args.length !== 1 || !String(args[0] ?? "").trim()) {
    ui.error("Usage: gt backup <ssh-url>");
    return 1;
  }

  const parsed = parseSshGitUrl(args[0]);
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
  const sourceUrl = args[0].trim();

  ui.status(`Backing up ${sourceUrl}`);
  ui.status(`Target project ${projectPath}`);

  ui.status(`Checking backup group ${group}...`);
  const groupReady = await ensureBackupGroup(group);
  if (!groupReady.ok) {
    ui.error(groupReady.error || "could not ensure GitLab backup group");
    return 1;
  }
  if (groupReady.created) {
    ui.status(`Created private group ${group}`);
  } else {
    ui.status(`Using existing group ${group}`);
  }

  ui.status(`Checking whether ${projectPath} already exists...`);
  const existsResult = await projectExists(group, baseName);
  if (!existsResult.ok) {
    ui.error(existsResult.error || "could not check GitLab project");
    return 1;
  }

  let targetName = baseName;

  if (existsResult.exists) {
    ui.status(`Project ${projectPath} already exists`);
    if (!stdin.isTTY) {
      ui.error(
        `Project ${projectPath} already exists. Re-run on a TTY to choose update / new / cancel.`,
      );
      return 1;
    }

    const action = await chooseCollision({
      projectPath,
      baseName,
      stdin,
      stdout,
      isTTY: Boolean(stdin.isTTY),
    });

    if (action === "cancel") {
      ui.error("Backup cancelled.");
      return 1;
    }

    if (action === "update") {
      targetName = baseName;
      ui.status(`Updating existing project ${group}/${targetName}`);
    } else if (action === "new") {
      ui.status("Finding next available project name...");
      const nextName = await nextSuffixedName(group, baseName);
      if (!nextName.ok) {
        ui.error(nextName.error || "could not find available backup name");
        return 1;
      }
      targetName = nextName.name;
      ui.status(`Creating private project ${group}/${targetName}...`);
      const created = await createPrivateProject(group, targetName);
      if (!created.ok) {
        ui.error(created.error || "failed to create GitLab project");
        return 1;
      }
      ui.status(`Created private project ${group}/${targetName}`);
    } else {
      ui.error("Backup cancelled.");
      return 1;
    }
  } else {
    ui.status(`Creating private project ${projectPath}...`);
    const created = await createPrivateProject(group, targetName);
    if (!created.ok) {
      ui.error(created.error || "failed to create GitLab project");
      return 1;
    }
    ui.status(`Created private project ${projectPath}`);
  }

  const destUrl = projectSshUrl(group, targetName);
  const tempRoot = mkdtempSync(join(tmpdir(), "gt-backup-"));
  const mirrorDir = join(tempRoot, "mirror.git");

  try {
    ui.status(`Cloning mirror from ${sourceUrl}...`);
    const cloneResult = await runGit(
      ["clone", "--mirror", sourceUrl, mirrorDir],
      { cwd, env },
    );
    if (cloneResult.status !== 0) {
      ui.error(cloneResult.stderr?.trim() || cloneResult.stdout?.trim() || "git clone --mirror failed");
      return 1;
    }
    ui.status("Mirror clone complete");

    ui.status(`Pushing branches and tags to ${destUrl}...`);
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
    ui.status("Push complete");

    ui.status("Choosing default branch (main, then develop)...");
    const preferred = await pickPreferredDefaultBranch(mirrorDir, { runGit });
    if (preferred) {
      const setDefault = await setDefaultBranch(group, targetName, preferred);
      if (!setDefault.ok) {
        ui.status(
          `⚠️ Could not set default branch to ${preferred}: ${setDefault.error || "unknown error"}`,
        );
      } else {
        ui.status(`Default branch set to ${preferred}`);
      }
    } else {
      ui.status("No main/develop branch found; leaving GitLab default unchanged");
    }
  } finally {
    ui.status("Cleaning up temporary mirror...");
    rmSync(tempRoot, { recursive: true, force: true });
  }

  ui.status("Backup finished");
  ui.line(projectWebUrl(group, targetName));
  return 0;
}
