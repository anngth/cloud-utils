import { mkdtempSync as mkdtempSyncDefault, rmSync as rmSyncDefault } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { spawnSync } from "node:child_process";
import {
  addBackupRepo as addBackupRepoDefault,
  addBackupRepos as addBackupReposDefault,
  recordLastBackupAt as recordLastBackupAtDefault,
  recordLastCheckedAt as recordLastCheckedAtDefault,
  removeBackupRepo as removeBackupRepoDefault,
  setSelectedLast as setSelectedLastDefault,
} from "./backup-list.mjs";
import {
  formatDisplayPath,
  loadBackupsDocument as loadBackupsDocumentDefault,
  resolveGtPaths as resolveGtPathsDefault,
} from "./config.mjs";
import { runGit as runGitDefault } from "./git.mjs";
import {
  BACKUP_GROUP,
  assertGlabReady as assertGlabReadyDefault,
  createPrivateProject as createPrivateProjectDefault,
  ensureBackupGroup as ensureBackupGroupDefault,
  groupExists as groupExistsDefault,
  pickPreferredDefaultBranch as pickPreferredDefaultBranchDefault,
  projectExists as projectExistsDefault,
  projectSshUrl,
  projectWebUrl,
  protectBranch as protectBranchDefault,
  setDefaultBranch as setDefaultBranchDefault,
} from "./gitlab.mjs";
import { runSelector as runSelectorDefault } from "./selector.mjs";
import {
  fingerprintsEqual,
  parseLsRemoteFingerprint,
} from "./refs-fingerprint.mjs";
import { parseSshGitUrl } from "./ssh-url.mjs";
import { isStaleRepo } from "./stale.mjs";
import { createUi } from "./ui.mjs";

const ADD_HINT = "Use `gt backup add <ssh-url>` to add a repo first.";
const FORCE_ONLY_HINT =
  "The --force flag is only valid for interactive backup, gt backup --all, and gt backup stale";
const DRY_RUN_ONLY_HINT =
  "The --dry-run flag is only valid for interactive backup, gt backup --all, and gt backup stale";
const STALE_USAGE =
  "Usage: gt backup stale [--days <n>] [--all] [-f|--force] [--dry-run]";

function isForceFlag(arg) {
  return arg === "-f" || arg === "--force";
}

function isDryRunFlag(arg) {
  return arg === "--dry-run";
}

function hasForceFlag(args, startIndex = 1) {
  return args.slice(startIndex).some(isForceFlag);
}

function hasDryRunFlag(args, startIndex = 1) {
  return args.slice(startIndex).some(isDryRunFlag);
}

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
    addBackupRepos = addBackupReposDefault,
    removeBackupRepo = removeBackupRepoDefault,
    recordLastBackupAt = recordLastBackupAtDefault,
    recordLastCheckedAt = recordLastCheckedAtDefault,
    setSelectedLast = setSelectedLastDefault,
    loadBackupsDocument = loadBackupsDocumentDefault,
    now = () => new Date(),
    fs,
    runSelector = runSelectorDefault,
    force = false,
    dryRun = false,
    groupExists = groupExistsDefault,
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
    addBackupRepos,
    removeBackupRepo,
    recordLastBackupAt,
    recordLastCheckedAt,
    setSelectedLast,
    loadBackupsDocument,
    now,
    fs,
    runSelector,
    force,
    dryRun,
    groupExists,
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
 *   | { ok: true, skipped: boolean, dryRun?: boolean, webUrl: string, projectPath: string }
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
    force,
    dryRun,
    groupExists,
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

  ui.section(`${sourceUrl} → ${projectPath}`);

  if (dryRun) {
    const groupCheck = await groupExists(group);
    if (!groupCheck.ok) {
      return {
        ok: false,
        error: groupCheck.error || "could not check GitLab group",
      };
    }

    const existsResult = await projectExists(group, targetName);
    if (!existsResult.ok) {
      return {
        ok: false,
        error: existsResult.error || "could not check GitLab project",
      };
    }

    const webUrl = projectWebUrl(group, targetName);
    const destUrl = projectSshUrl(group, targetName);

    if (!existsResult.exists) {
      if (existsResult.inactive) {
        ui.status(`Would recreate inactive backup ${projectPath}`);
      } else {
        ui.status(`Would create ${projectPath}`);
      }
      ui.status("Would mirror");
      return {
        ok: true,
        skipped: false,
        dryRun: true,
        webUrl,
        projectPath,
      };
    }

    ui.status(`Would update existing backup ${projectPath}`);
    const sourceLs = await runGit(["ls-remote", sourceUrl], { cwd, env });
    if (sourceLs.status !== 0) {
      return {
        ok: false,
        error:
          sourceLs.stderr?.trim()
          || sourceLs.stdout?.trim()
          || "git ls-remote source failed",
      };
    }
    const destLs = await runGit(["ls-remote", destUrl], { cwd, env });
    if (destLs.status !== 0) {
      return {
        ok: false,
        error:
          destLs.stderr?.trim()
          || destLs.stdout?.trim()
          || "git ls-remote destination failed",
      };
    }
    const sourceFp = parseLsRemoteFingerprint(sourceLs.stdout);
    const destFp = parseLsRemoteFingerprint(destLs.stdout);
    if (fingerprintsEqual(sourceFp, destFp)) {
      ui.status("Would skip (unchanged)", { tone: "muted" });
      return {
        ok: true,
        skipped: true,
        dryRun: true,
        webUrl,
        projectPath,
      };
    }
    ui.status("Would mirror");
    return {
      ok: true,
      skipped: false,
      dryRun: true,
      webUrl,
      projectPath,
    };
  }

  const groupReady = await ensureBackupGroup(group);
  if (!groupReady.ok) {
    return { ok: false, error: groupReady.error || "could not ensure GitLab backup group" };
  }
  if (groupReady.created) {
    ui.status(`Created group ${group}`);
  }

  const existsResult = await projectExists(group, targetName);
  if (!existsResult.ok) {
    return { ok: false, error: existsResult.error || "could not check GitLab project" };
  }

  if (existsResult.exists) {
    ui.status(`Updating existing backup ${projectPath}`);
  } else {
    if (existsResult.inactive) {
      ui.status(`Previous backup pending deletion (inactive); creating ${projectPath}`);
    }
    const created = await createPrivateProject(group, targetName);
    if (!created.ok) {
      return { ok: false, error: created.error || "failed to create GitLab project" };
    }
    ui.status(`Created ${projectPath}`);
  }

  const destUrl = projectSshUrl(group, targetName);

  if (existsResult.exists && !force) {
    const sourceLs = await runGit(["ls-remote", sourceUrl], { cwd, env });
    if (sourceLs.status !== 0) {
      return {
        ok: false,
        error:
          sourceLs.stderr?.trim()
          || sourceLs.stdout?.trim()
          || "git ls-remote source failed",
      };
    }
    const destLs = await runGit(["ls-remote", destUrl], { cwd, env });
    if (destLs.status !== 0) {
      return {
        ok: false,
        error:
          destLs.stderr?.trim()
          || destLs.stdout?.trim()
          || "git ls-remote destination failed",
      };
    }
    const sourceFp = parseLsRemoteFingerprint(sourceLs.stdout);
    const destFp = parseLsRemoteFingerprint(destLs.stdout);
    if (fingerprintsEqual(sourceFp, destFp)) {
      ui.status("Unchanged; skipping mirror", { tone: "muted" });
      return {
        ok: true,
        skipped: true,
        webUrl: projectWebUrl(group, targetName),
        projectPath,
      };
    }
  }

  const tempRoot = mkdtempSync(join(tmpdir(), "gt-backup-"));
  const mirrorDir = join(tempRoot, "mirror.git");

  try {
    ui.status(`Cloning source to ${join(basename(tempRoot), "mirror.git")}`);
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
    ui.status("Clone complete");

    ui.status(`Pushing all branches + tags → ${projectPath}`);
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
    ui.status("Pushed all branches + tags");

    const preferred = await pickPreferredDefaultBranch(mirrorDir, { runGit });
    if (preferred) {
      const setDefault = await setDefaultBranch(group, targetName, preferred);
      if (!setDefault.ok) {
        ui.status(
          `Could not set default branch to ${preferred}: ${setDefault.error || "unknown error"}`,
          { tone: "warning" },
        );
      } else {
        ui.status(`Default branch ${preferred}`);
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
        ui.status(
          `Could not protect ${branch}: ${protectedResult.error || "unknown error"}`,
          { tone: "warning" },
        );
      } else {
        ui.status(
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
    skipped: false,
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
  const {
    ui,
    env,
    resolveGtPaths,
    recordLastBackupAt,
    recordLastCheckedAt,
    now,
    fs,
    dryRun,
  } = resolveContext(context);
  const results = [];
  const paths = resolveGtPaths(env);
  const recordOpts = fs ? { fs } : {};

  if (dryRun) {
    ui.status("Dry run (no changes)");
  }

  for (const url of urls ?? []) {
    const result = await backupOneRepo(url, context);
    if (!result.ok) {
      results.push({ kind: "fail", url, error: result.error });
      continue;
    }

    if (dryRun) {
      if (result.skipped) {
        results.push({ kind: "skip", url });
      } else {
        results.push({ kind: "ok", url });
      }
      continue;
    }

    if (result.skipped) {
      const recorded = recordLastCheckedAt(paths, url, {
        now: now(),
        ...recordOpts,
      });
      if (!recorded.ok) {
        results.push({
          kind: "fail",
          url,
          error: `Backup skipped but failed to save lastCheckedAt: ${recorded.error}`,
        });
        continue;
      }
      results.push({ kind: "skip", url });
      continue;
    }

    const recorded = recordLastBackupAt(paths, url, {
      now: now(),
      ...recordOpts,
    });
    if (!recorded.ok) {
      results.push({
        kind: "fail",
        url,
        error: `Backup succeeded but failed to save lastBackupAt: ${recorded.error}`,
      });
      continue;
    }

    results.push({ kind: "ok", url, webUrl: result.webUrl });
  }

  ui.section("Backup summary");
  for (const r of results) {
    if (r.kind === "ok") {
      ui.item(`ok  ${r.url}`);
      if (dryRun) {
        ui.detail("→ would mirror");
      } else {
        ui.detail(`→ ${r.webUrl}`);
      }
    } else if (r.kind === "skip") {
      ui.item(`skip  ${r.url}`, { tone: "muted" });
      ui.detail(dryRun ? "→ would skip (unchanged)" : "→ unchanged");
    } else {
      ui.item(`fail  ${r.url}`, { tone: "failure" });
      ui.detail(`— ${r.error}`, { tone: "failure" });
    }
  }
  ui.listEnd();

  return results.some((r) => r.kind === "fail") ? 1 : 0;
}

function startBackupFrame(ui, listPath, { dryRun = false } = {}) {
  ui.title("REPO BACKUP");
  ui.step(dryRun ? "Dry run: backup repositories" : "Backup repositories");
  ui.detail(listPath);
}

/**
 * Load the backups list; empty/missing → error + add hint.
 * @returns {{ ok: true, repos: Array<{ url: string, lastBackupAt: string | null }> } | { ok: false }}
 */
function loadReposOrError(paths, { loadBackupsDocument, ui }) {
  const load = loadBackupsDocument(paths.backupsFile);
  if (!load.ok) {
    if (load.missing) {
      ui.error(`No backups list found. ${ADD_HINT}`);
      return { ok: false };
    }
    ui.error(load.error);
    return { ok: false };
  }
  if (load.document.repos.length === 0) {
    ui.error(`Backups list is empty. ${ADD_HINT}`);
    return { ok: false };
  }
  return { ok: true, repos: load.document.repos };
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
    addBackupRepos,
    removeBackupRepo,
    loadBackupsDocument,
    runSelector,
    setSelectedLast,
    fs,
  } = resolved;
  const paths = resolveGtPaths(env);

  if (args[0] === "add") {
    if (hasForceFlag(args, 1)) {
      ui.error(FORCE_ONLY_HINT);
      return 1;
    }
    if (hasDryRunFlag(args, 1)) {
      ui.error(DRY_RUN_ONLY_HINT);
      return 1;
    }
    const urls = args.slice(1);
    if (urls.length === 0) {
      ui.error("Usage: gt backup add <ssh-url> [<ssh-url> ...]");
      return 1;
    }
    const addOpts = fs ? { fs } : {};
    const result = addBackupRepos(paths, urls, addOpts);
    if (result.error && result.added.length === 0) {
      ui.error(result.error);
      return 1;
    }
    if (result.added.length > 0) {
      ui.title("REPO BACKUP");
      ui.step("Add repositories");
    }
    if (result.error) {
      ui.error(result.error);
    }
    for (const item of result.added) {
      ui.success(`Added ${item.url} at index ${item.index}`);
    }
    for (const failure of result.failures) {
      ui.error(`${failure.url}: ${failure.error}`);
    }
    if (result.added.length > 0) {
      ui.item(formatDisplayPath(paths.backupsFile, { home: env.HOME }));
      ui.listEnd();
    }
    return result.ok ? 0 : 1;
  }

  if (args[0] === "remove") {
    if (hasForceFlag(args, 1)) {
      ui.error(FORCE_ONLY_HINT);
      return 1;
    }
    if (hasDryRunFlag(args, 1)) {
      ui.error(DRY_RUN_ONLY_HINT);
      return 1;
    }
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
    ui.title("REPO BACKUP");
    ui.step("Remove repository");
    ui.success(`Removed ${result.removed}`);
    ui.item(formatDisplayPath(paths.backupsFile, { home: env.HOME }));
    ui.listEnd();
    return 0;
  }

  if (args[0] === "stale") {
    let staleAll = false;
    let staleDays = 7;
    let force = false;
    let dryRun = context.dryRun === true;
    const staleArgs = args.slice(1);

    for (let i = 0; i < staleArgs.length; i++) {
      const arg = staleArgs[i];
      if (arg === "--all") {
        staleAll = true;
        continue;
      }
      if (arg === "--days") {
        const value = staleArgs[++i];
        if (value == null || value.startsWith("-")) {
          ui.error(STALE_USAGE);
          return 1;
        }
        const parsed = Number(value);
        if (!Number.isInteger(parsed) || parsed <= 0) {
          ui.error(`Invalid --days value: ${value} (must be a positive integer)`);
          return 1;
        }
        staleDays = parsed;
        continue;
      }
      if (isForceFlag(arg)) {
        force = true;
        continue;
      }
      if (isDryRunFlag(arg)) {
        dryRun = true;
        continue;
      }
      if (arg.startsWith("-")) {
        ui.error(`Unknown flag: ${arg}`);
        return 1;
      }
      ui.error(STALE_USAGE);
      return 1;
    }

    if (force && dryRun) {
      ui.error("Cannot combine --force and --dry-run");
      return 1;
    }

    const batchContext = {
      ...context,
      ...(force && { force: true }),
      ...(dryRun && { dryRun: true }),
    };

    const loaded = loadReposOrError(paths, { loadBackupsDocument, ui });
    if (!loaded.ok) return 1;

    const nowDate = resolved.now();
    const staleRepos = loaded.repos.filter((entry) =>
      isStaleRepo(entry, { now: nowDate, days: staleDays }),
    );

    const listPath = formatDisplayPath(paths.backupsFile, { home: env.HOME });

    if (staleRepos.length === 0) {
      startBackupFrame(ui, listPath, { dryRun });
      ui.status("No stale repos", { tone: "muted" });
      ui.listEnd();
      return 0;
    }

    if (staleAll) {
      startBackupFrame(ui, listPath, { dryRun });
      return runBackupBatch(
        staleRepos.map((entry) => entry.url),
        batchContext,
      );
    }

    if (!stdin.isTTY) {
      ui.error(
        "A terminal is required to select stale repos interactively. Use `gt backup stale --all` to back up every stale repo without selecting.",
      );
      return 1;
    }

    const items = staleRepos.map((entry) => ({
      label: entry.url,
      value: entry.url,
      lastBackupAt: entry.lastBackupAt,
      lastCheckedAt: entry.lastCheckedAt,
    }));
    const heading = "Select stale repos to backup";
    const initial = staleRepos
      .filter((r) => r.selectedLast)
      .map((r) => r.url);
    const selection = await runSelector({
      items,
      multiple: true,
      initial,
      input: stdin,
      render: (state) => ui.renderBackupSelector(heading, state, { listPath }),
    });

    if (selection.type === "cancel") {
      if (selection.state) {
        ui.cancelledBackupSelector(heading, selection.state, { listPath });
      }
      return 1;
    }

    if (!selection.selected || selection.selected.length === 0) {
      ui.error("No repos selected");
      return 1;
    }

    if (!dryRun) {
      const saved = setSelectedLast(
        paths,
        selection.selected,
        fs ? { fs } : {},
      );
      if (!saved.ok) {
        ui.error(`Failed to save selection: ${saved.error}`);
        return 1;
      }
    }

    startBackupFrame(ui, listPath, { dryRun });
    return runBackupBatch(selection.selected, batchContext);
  }

  let all = false;
  let force = false;
  let dryRun = context.dryRun === true;
  for (const arg of args) {
    if (arg === "--all") {
      all = true;
      continue;
    }
    if (isForceFlag(arg)) {
      force = true;
      continue;
    }
    if (isDryRunFlag(arg)) {
      dryRun = true;
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

  if (force && dryRun) {
    ui.error("Cannot combine --force and --dry-run");
    return 1;
  }

  const batchContext = {
    ...context,
    ...(force && { force: true }),
    ...(dryRun && { dryRun: true }),
  };

  const loaded = loadReposOrError(paths, { loadBackupsDocument, ui });
  if (!loaded.ok) return 1;

  const listPath = formatDisplayPath(paths.backupsFile, { home: env.HOME });

  if (all) {
    startBackupFrame(ui, listPath, { dryRun });
    return runBackupBatch(
      loaded.repos.map((entry) => entry.url),
      batchContext,
    );
  }

  if (!stdin.isTTY) {
    ui.error(
      "A terminal is required to select repos interactively. Use `gt backup --all` to back up every listed repo without selecting.",
    );
    return 1;
  }

  const items = loaded.repos.map((entry) => ({
    label: entry.url,
    value: entry.url,
    lastBackupAt: entry.lastBackupAt,
    lastCheckedAt: entry.lastCheckedAt,
  }));
  const heading = "Select repos to backup";
  const initial = loaded.repos
    .filter((r) => r.selectedLast)
    .map((r) => r.url);
  const selection = await runSelector({
    items,
    multiple: true,
    initial,
    input: stdin,
    render: (state) => ui.renderBackupSelector(heading, state, { listPath }),
  });

  if (selection.type === "cancel") {
    if (selection.state) {
      ui.cancelledBackupSelector(heading, selection.state, { listPath });
    }
    return 1;
  }

  if (!selection.selected || selection.selected.length === 0) {
    ui.error("No repos selected");
    return 1;
  }

  if (!dryRun) {
    const saved = setSelectedLast(
      paths,
      selection.selected,
      fs ? { fs } : {},
    );
    if (!saved.ok) {
      ui.error(`Failed to save selection: ${saved.error}`);
      return 1;
    }
  }

  startBackupFrame(ui, listPath, { dryRun });
  return runBackupBatch(selection.selected, batchContext);
}
