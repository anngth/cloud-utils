import { runGit as runGitDefault } from "./git.mjs";
import { createUi } from "./ui.mjs";

export async function runPushCommand(_args, context = {}) {
  const {
    cwd = process.cwd(),
    env = process.env,
    runGit = runGitDefault,
    ui = createUi(),
  } = context;
  const git = (args) => runGit(args, { cwd, env });

  const repo = await git(["rev-parse", "--git-dir"]);
  if (repo.status !== 0) {
    ui.error("❌ Error: Not in a git repository");
    return 1;
  }

  ui.status("🔄 Force Push (safe)...");
  const branchResult = await git(["rev-parse", "--abbrev-ref", "HEAD"]);
  const currentBranch = branchResult.stdout.trim();
  if (branchResult.status !== 0 || !currentBranch || currentBranch === "HEAD") {
    ui.error("Cannot push from detached HEAD");
    return 1;
  }
  ui.status(`🌿 Current branch: ${currentBranch}`);

  const upstreamResult = await git([
    "rev-parse",
    "--abbrev-ref",
    "--symbolic-full-name",
    "@{u}",
  ]);
  const trackingBranch = upstreamResult.status === 0 ? upstreamResult.stdout.trim() : "";
  let targetRemote = "origin";
  if (trackingBranch) {
    const separator = trackingBranch.indexOf("/");
    if (separator > 0) {
      targetRemote = trackingBranch.slice(0, separator);
      ui.status(`🎯 Tracking branch: ${trackingBranch}`);
      if (trackingBranch.slice(separator + 1) !== currentBranch) {
        ui.status(`⚠️ Tracking branch name differs from local branch`);
        ui.status(`ℹ️ Will push explicitly to ${targetRemote}/${currentBranch}`);
      }
    }
  } else {
    ui.status("⚠️ No upstream tracking branch configured");
    ui.status(`ℹ️ Will push explicitly to origin/${currentBranch}`);
  }

  const remoteRef = `refs/remotes/${targetRemote}/${currentBranch}`;
  const remoteRefResult = await git(["show-ref", "--verify", "--quiet", remoteRef]);
  if (remoteRefResult.status === 0) {
    const remoteBranchResult = await git([
      "ls-remote",
      "--exit-code",
      "--heads",
      targetRemote,
      currentBranch,
    ]);
    if (remoteBranchResult.status === 2) {
      ui.status(`🧹 Remote ${targetRemote}/${currentBranch} no longer exists; pruning stale tracking ref...`);
      const pruneResult = await git(["fetch", targetRemote, "--prune"]);
      ui.status(pruneResult.status === 0
        ? "✅ Pruned stale remote-tracking refs"
        : "⚠️ Failed to prune stale remote-tracking refs; continuing with push");
    } else if (remoteBranchResult.status !== 0) {
      ui.status(`⚠️ Could not verify whether ${targetRemote}/${currentBranch} exists; continuing with push`);
    }
  }

  const pushResult = await git([
    "push",
    "--force-with-lease",
    targetRemote,
    `HEAD:${currentBranch}`,
  ]);
  if (pushResult.status !== 0) {
    ui.error("❌ Force push failed");
    if (pushResult.stdout.trim()) ui.error(pushResult.stdout.trim());
    if (pushResult.stderr.trim()) ui.error(pushResult.stderr.trim());
    ui.error("💡 Tip: run 'git remote -v' and verify write permission to the remote");
    return 1;
  }

  if (pushResult.stdout.trim()) ui.status(pushResult.stdout.trim());
  if (pushResult.stderr.trim()) ui.status(pushResult.stderr.trim());
  ui.status("✅ Force push successful");
  ui.status("⬇️ Fetching...");
  const fetchResult = await git(["fetch"]);
  ui.status(fetchResult.status === 0 ? "✅ Fetch successful" : "❌ Fetch failed");
  return 0;
}
