import { runGit as runGitDefault } from "./git.mjs";
import { createUi } from "./ui.mjs";

const FETCH_USAGE = "Usage: gt fetch [--sync-upstream]";
const PROTECTED_BRANCHES = ["main", "master", "develop"];

async function refreshPrimaryRemoteRefs(git, ui) {
  for (const branch of ["main", "develop"]) {
    const lsRemote = await git(["ls-remote", "--heads", "origin", branch]);
    if (!lsRemote.stdout.trim()) continue;

    ui.status(`⬇️ Fetching origin/${branch} without checkout...`);
    const fetchResult = await git(["fetch", "origin", branch]);
    ui.status(
      fetchResult.status === 0
        ? `✅ Successfully fetched origin/${branch}`
        : `⚠️ Failed to fetch origin/${branch}`,
    );
  }
}

async function fetchAndPrune(git, ui) {
  ui.status("⬇️ Fetching from all remotes...");
  const allResult = await git(["fetch", "--all", "--prune"]);
  if (allResult.status === 0) {
    ui.status("✅ Fetch & prune successful");
    return true;
  }

  ui.status("⚠️ Failed to fetch from all remotes. Trying origin only...");
  const originResult = await git(["fetch", "origin", "--prune"]);
  if (originResult.status === 0) {
    ui.status("✅ Fetch origin successful");
    return true;
  }

  ui.error("❌ Failed to fetch");
  return false;
}

async function syncLocalBranchWithOriginFF(git, ui, branch, currentBranch) {
  const remoteRefResult = await git(["show-ref", "--verify", "--quiet", `refs/remotes/origin/${branch}`]);
  if (remoteRefResult.status !== 0) return;
  if (currentBranch === branch) return;

  const localRefResult = await git(["show-ref", "--verify", "--quiet", `refs/heads/${branch}`]);
  if (localRefResult.status !== 0) {
    ui.status(`🌱 Creating local '${branch}' from origin/${branch}...`);
    const createResult = await git(["branch", branch, `origin/${branch}`]);
    ui.status(
      createResult.status === 0
        ? `✅ Created local '${branch}'`
        : `⚠️ Failed to create local '${branch}'`,
    );
    return;
  }

  const localShaResult = await git(["rev-parse", `refs/heads/${branch}`]);
  const remoteShaResult = await git(["rev-parse", `refs/remotes/origin/${branch}`]);
  const localSha = localShaResult.stdout.trim();
  const remoteSha = remoteShaResult.stdout.trim();
  if (!localSha || !remoteSha || localSha === remoteSha) return;

  const isAncestorResult = await git(["merge-base", "--is-ancestor", branch, `origin/${branch}`]);
  if (isAncestorResult.status === 0) {
    ui.status(`🔄 Fast-forwarding local '${branch}' to origin/${branch}...`);
    const updateResult = await git(["update-ref", `refs/heads/${branch}`, remoteSha, localSha]);
    ui.status(
      updateResult.status === 0
        ? `✅ Updated local '${branch}' to origin/${branch}`
        : `⚠️ Failed to update local '${branch}'`,
    );
  } else {
    ui.status(`⚠️ Local '${branch}' has local-only commits; skipping auto-sync`);
    ui.status(`💡 Run: git switch ${branch} && git pull --ff-only origin ${branch}`);
  }
}

async function syncWithOrigin(git, ui, branch) {
  const remoteRefResult = await git(["show-ref", "--verify", "--quiet", `refs/remotes/origin/${branch}`]);
  if (remoteRefResult.status !== 0) {
    ui.status(`ℹ️ origin/${branch} not found - skipping sync`);
    return true;
  }

  ui.status(`🔄 Syncing ${branch} branch...`);
  const pullResult = await git(["pull", "--ff-only", "origin", branch]);
  if (pullResult.status === 0) {
    ui.status(`✅ Pulled from origin/${branch}`);
    return true;
  }

  ui.error("❌ Pull failed (non fast-forward or uncommitted changes)");
  return false;
}

async function syncWithUpstream(git, ui) {
  const upstreamRemoteResult = await git(["remote", "get-url", "upstream"]);
  if (upstreamRemoteResult.status !== 0) return;

  const currentBranchResult = await git(["rev-parse", "--abbrev-ref", "HEAD"]);
  const currentBranch = currentBranchResult.stdout.trim();
  if (currentBranch !== "main") return;

  const upstreamMainResult = await git(["ls-remote", "--heads", "upstream", "main"]);
  if (!upstreamMainResult.stdout.trim()) return;

  const dirtyResult = await git(["diff", "--quiet"]);
  const dirtyCachedResult = await git(["diff", "--cached", "--quiet"]);
  if (dirtyResult.status !== 0 || dirtyCachedResult.status !== 0) {
    ui.status("⚠️ You have uncommitted changes. Skipping upstream merge.");
    ui.status("💡 Commit or stash your changes, then run 'gt fetch' again to merge upstream");
    return;
  }

  ui.status("🔄 Merging upstream/main into main...");
  const mergeResult = await git(["merge", "upstream/main", "--no-edit"]);
  if (mergeResult.status !== 0) {
    ui.error("❌ Merge failed - there might be conflicts");
    ui.error("💡 Resolve conflicts manually, then run 'git add .' and 'git commit'");
    return;
  }

  ui.status("✅ Merged upstream changes successfully");
  ui.status("🔄 Pushing updated main to origin...");
  const pushResult = await git(["push", "origin", "main"]);
  ui.status(
    pushResult.status === 0
      ? "✅ Pushed updated main to origin"
      : "⚠️ Failed to push to origin, but local merge was successful",
  );
}

async function cleanupGoneBranches(git, ui) {
  const branchResult = await git(["branch", "-vv"]);
  const goneBranches = branchResult.stdout
    .split("\n")
    .filter((line) => line.includes(": gone]") && !line.startsWith("*"))
    .map((line) => line.trim().split(/\s+/)[0])
    .filter(Boolean);

  if (goneBranches.length === 0) return;

  ui.status(`🧹 Cleaning gone branches: ${goneBranches.join("\n")}`);
  for (const branch of goneBranches) {
    const deleteResult = await git(["branch", "-d", branch]);
    ui.status(
      deleteResult.status === 0
        ? `✅ Deleted: ${branch}`
        : `⚠️ Skipped (not merged): ${branch}`,
    );
  }
}

async function getCleanupBaseBranch(git, currentBranch) {
  if (currentBranch === "main" || currentBranch === "develop") return currentBranch;

  const mainRefResult = await git(["show-ref", "--verify", "--quiet", "refs/heads/main"]);
  if (mainRefResult.status === 0) return "main";

  const developRefResult = await git(["show-ref", "--verify", "--quiet", "refs/heads/develop"]);
  if (developRefResult.status === 0) return "develop";

  return currentBranch;
}

async function cleanupMergedBranches(git, ui, baseBranch, currentBranch) {
  if (!baseBranch) return;

  const baseRefResult = await git(["show-ref", "--verify", "--quiet", `refs/heads/${baseBranch}`]);
  if (baseRefResult.status !== 0) return;

  const forEachResult = await git(["for-each-ref", "--format=%(refname:short)", "refs/heads"]);
  const branches = forEachResult.stdout
    .split("\n")
    .map((line) => line.trim())
    .filter((name) => name && !PROTECTED_BRANCHES.includes(name));

  if (branches.length === 0) return;

  ui.status(`🧹 Cleaning merged branches (base: ${baseBranch})...`);
  let cleanedAny = false;
  for (const branch of branches) {
    if (branch === currentBranch) continue;

    const ancestorResult = await git(["merge-base", "--is-ancestor", branch, baseBranch]);
    if (ancestorResult.status !== 0) continue;

    const deleteResult = await git(["branch", "-d", branch]);
    if (deleteResult.status === 0) {
      ui.status(`✅ Deleted merged branch: ${branch}`);
      cleanedAny = true;
    }
  }

  if (!cleanedAny) {
    ui.status("ℹ️ No merged local branches to clean");
  }
}

function parseArgs(args, ui) {
  let syncUpstream = false;
  for (const arg of args) {
    if (arg === "--sync-upstream") {
      syncUpstream = true;
      continue;
    }
    if (arg === "-h" || arg === "--help") {
      ui.status(FETCH_USAGE);
      ui.status("--sync-upstream  Merge upstream/main into local main and push origin/main (main only)");
      return { help: true };
    }
    ui.error(`❌ Unknown option for fetch: ${arg}`);
    ui.usageLine(FETCH_USAGE);
    return { error: true };
  }
  return { syncUpstream };
}

export async function runFetchCommand(args, context = {}) {
  const {
    cwd = process.cwd(),
    env = process.env,
    runGit = runGitDefault,
    ui = createUi(),
  } = context;
  const git = (gitArgs) => runGit(gitArgs, { cwd, env });

  const parsed = parseArgs(args, ui);
  if (parsed.help) return 0;
  if (parsed.error) return 1;

  const repoResult = await git(["rev-parse", "--git-dir"]);
  if (repoResult.status !== 0) {
    ui.error("❌ Error: Not in a git repository");
    return 1;
  }

  ui.status("⬇️ Fetching and syncing repository ---");

  await refreshPrimaryRemoteRefs(git, ui);

  if (!(await fetchAndPrune(git, ui))) {
    return 1;
  }

  const currentBranchResult = await git(["rev-parse", "--abbrev-ref", "HEAD"]);
  const currentBranch = currentBranchResult.stdout.trim();

  await syncLocalBranchWithOriginFF(git, ui, "main", currentBranch);
  await syncLocalBranchWithOriginFF(git, ui, "develop", currentBranch);

  if (currentBranch === "main" || currentBranch === "develop") {
    if (!(await syncWithOrigin(git, ui, currentBranch))) {
      return 1;
    }

    if (currentBranch === "main") {
      if (parsed.syncUpstream) {
        await syncWithUpstream(git, ui);
      } else {
        ui.status("ℹ️ Skipping upstream sync (use 'gt fetch --sync-upstream' to enable)");
      }
    }
  } else {
    ui.status(`ℹ️ On branch '${currentBranch}' - no auto-sync performed`);
  }

  await cleanupGoneBranches(git, ui);
  const cleanupBaseBranch = await getCleanupBaseBranch(git, currentBranch);
  await cleanupMergedBranches(git, ui, cleanupBaseBranch, currentBranch);

  return 0;
}
