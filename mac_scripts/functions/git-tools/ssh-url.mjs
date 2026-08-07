const SSH_RE = /^git@([^:]+):([^/]+)\/([^/]+?)(?:\.git)?$/i;

/**
 * @param {string} value
 * @returns {string}
 */
export function slugifySegment(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

/**
 * @param {string} owner
 * @param {string} repo
 * @returns {string}
 */
export function buildProjectName(owner, repo) {
  return `${slugifySegment(owner)}-${slugifySegment(repo)}`;
}

/**
 * @param {string} input
 * @returns {{ ok: true, owner: string, repo: string, projectName: string }
 *   | { ok: false, error: string }}
 */
export function parseSshGitUrl(input) {
  if (typeof input !== "string" || !input.trim()) {
    return { ok: false, error: "Invalid SSH URL" };
  }

  const trimmed = input.trim();

  if (/^https?:\/\//i.test(trimmed)) {
    return { ok: false, error: "HTTPS URLs are not supported" };
  }

  const match = trimmed.match(SSH_RE);
  if (!match) {
    return { ok: false, error: "Invalid SSH URL" };
  }

  const [, , owner, repo] = match;

  return {
    ok: true,
    owner,
    repo,
    projectName: buildProjectName(owner, repo),
  };
}
