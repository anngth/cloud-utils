import { mkdtempSync as mkdtempSyncDefault, rmSync as rmSyncDefault } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { spawnSync } from "node:child_process";
import {
  addBackupRepo as addBackupRepoDefault,
  removeBackupRepo as removeBackupRepoDefault,
} from "./backup-list.mjs";
import {
  formatDisplayPath,
  readBackupsDocument as readBackupsDocumentDefault,
  resolveGtPaths as resolveGtPathsDefault,
} from "./config.mjs";
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
  protectBranch as protectBranchDefault,
  setDefaultBranch as setDefaultBranchDefault,
} from "./gitlab.mjs";
import { runSelector as runSelectorDefault } from "./selector.mjs";
import { parseSshGitUrl } from "./ssh-url.mjs";
import { createUi } from "./ui.mjs";

const RED = "\u001b[31m";
const ADD_HINT = "Use `gt backup add <ssh-url>` to add a repo first.";

function defaultHasCommand(name) {
  return spawnSync("which", [name], { stdio: "ignore" }).status === 0;
}

function resolveContext(context = {}) {
  const {
    cwd = process.cwd(),
    env = process.env,
    stdout = process.stdout,
    stdin = process.stdin,
    ui = createUi({ stdout, stderr: context.stderr ?? process.stderr }),
    hasCommand = defaultHasCommand,
    assertGlabReady = assertGlabReadyDefault,
    ensureBackupGroup = ensureBackupGroupDefault,
    projectExists = projectExistsDefault,
    createPrivateProject = createPrivateProjectDefault,
    pickPreferredDefaultBranch = pickPreferredDefaultBranchDefault,
    setDefaultBranch = setDefaultBranchDefault,
    protectBranch = protectBranchDefault,
    runGit = runGitDefault,
    mkdtempSync = mkdtempSyncDefault,
    rmSync = rmSyncDefault,
    resolveGtPaths = resolveGtPathsDefault,
    addBackupRepo = addBackupRepoDefault,
    removeBackupRepo = removeBackupRepoDefault,
    readBackupsDocument = readBackupsDocumentDefault,
    runSelector = runSelectorDefault,
  } = context;

  return {
    cwd,
    env,
    stdout,
    stdin,
    ui,
    hasCommand,
    assertGlabReady,
    ensureBackupGroup,
    projectExists,
    createPrivateProject,
    pickPreferredDefaultBranch,
    setDefaultBranch,
    protectBranch,
    runGit,
    mkdtempSync,
    rmSync,
    resolveGtPaths,
    addBackupRepo,
    removeBackupRepo,
    readBackupsDocument,
    runSelector,
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
    protectBranch,
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
    ui.step(`Cloning source to ${join(basename(tempRoot), "mirror.git")}`);
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

    for (const branch of ["main", "develop"]) {
      const hasBranch = await runGit(
        ["show-ref", "--verify", "--quiet", `refs/heads/${branch}`],
        { cwd: mirrorDir },
      );
      if (hasBranch.status !== 0) continue;

      const protectedResult = await protectBranch(group, targetName, branch);
      if (!protectedResult.ok) {
        ui.warn(
          `Could not protect ${branch}: ${protectedResult.error || "unknown error"}`,
        );
      } else {
        ui.success(
          protectedResult.alreadyProtected
            ? `${branch} already protected`
            : `Protected ${branch}`,
        );
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
    ui.item(`ok  ${s.url}`);
    ui.detail(`→ ${s.webUrl}`);
  }
  for (const f of failures) {
    ui.item(`fail  ${f.url}`, RED);
    ui.detail(`— ${f.error}`, RED);
  }
  ui.listEnd();

  return failures.length === 0 ? 0 : 1;
}

/**
 * Load the backups list; empty/missing → error + add hint.
 * @returns {{ ok: true, repos: string[] } | { ok: false }}
 */
function loadReposOrError(paths, { readBackupsDocument, ui }) {
  const read = readBackupsDocument(paths.backupsFile);
  if (!read.ok) {
    if (read.missing) {
      ui.error(`No backups list found. ${ADD_HINT}`);
      return { ok: false };
    }
    ui.error(read.error);
    return { ok: false };
  }
  if (read.document.repos.length === 0) {
    ui.error(`Backups list is empty. ${ADD_HINT}`);
    return { ok: false };
  }
  return { ok: true, repos: read.document.repos };
}

/**
 * CLI dispatcher for `gt backup` subcommands and interactive / `--all` modes.
 * @param {string[]} args
 * @param {Record<string, unknown>} context
 * @returns {Promise<number>}
 */
export async function runBackupCommand(args = [], context = {}) {
  const resolved = resolveContext(context);
  const {
    env,
    ui,
    stdin,
    resolveGtPaths,
    addBackupRepo,
    removeBackupRepo,
    readBackupsDocument,
    runSelector,
  } = resolved;
  const paths = resolveGtPaths(env);

  if (args[0] === "add") {
    const sshUrl = args[1];
    if (!sshUrl || args.length !== 2) {
      ui.error("Usage: gt backup add <ssh-url>");
      return 1;
    }
    const result = addBackupRepo(paths, sshUrl);
    if (!result.ok) {
      ui.error(result.error);
      return 1;
    }
    ui.success(`Added ${result.document.repos[result.index - 1]} at index ${result.index}`);
    ui.item(formatDisplayPath(paths.backupsFile, { home: env.HOME }));
    ui.listEnd();
    return 0;
  }

  if (args[0] === "remove") {
    const token = args[1];
    if (!token || args.length !== 2) {
      ui.error("Usage: gt backup remove <index|ssh-url>");
      return 1;
    }
    const result = removeBackupRepo(paths, token);
    if (!result.ok) {
      ui.error(result.error);
      return 1;
    }
    ui.success(`Removed ${result.removed}`);
    ui.item(formatDisplayPath(paths.backupsFile, { home: env.HOME }));
    ui.listEnd();
    return 0;
  }

  let all = false;
  for (const arg of args) {
    if (arg === "--all") {
      all = true;
      continue;
    }
    if (arg.startsWith("-")) {
      ui.error(`Unknown flag: ${arg}`);
      return 1;
    }
    ui.error(
      "gt backup one-shot URL is retired; use gt backup add / gt backup / gt backup --all",
    );
    return 1;
  }

  const loaded = loadReposOrError(paths, { readBackupsDocument, ui });
  if (!loaded.ok) return 1;

  const listPath = formatDisplayPath(paths.backupsFile, { home: env.HOME });

  if (all) {
    ui.title("REPO BACKUP");
    ui.step(listPath);
    return runBackupBatch(loaded.repos, context);
  }

  if (!stdin.isTTY) {
    ui.error(
      "A terminal is required to select repos interactively. Use `gt backup --all` to back up every listed repo without selecting.",
    );
    return 1;
  }

  const items = loaded.repos.map((url) => ({ label: url, value: url }));
  const heading = "Select repos to backup";
  const selection = await runSelector({
    items,
    multiple: true,
    input: stdin,
    render: (state) => ui.renderBackupSelector(heading, state, { listPath }),
  });

  if (selection.type === "cancel") {
    return 1;
  }

  if (!selection.selected || selection.selected.length === 0) {
    ui.error("No repos selected");
    return 1;
  }

  return runBackupBatch(selection.selected, context);
}
