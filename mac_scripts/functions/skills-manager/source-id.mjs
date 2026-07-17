import { realpathSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";

export class SourceIdentityError extends Error {}

const GITHUB_COMPONENT = "[A-Za-z0-9._-]+";
const SHORTHAND = new RegExp(`^(${GITHUB_COMPONENT})\/(${GITHUB_COMPONENT})(?:\\.git)?$`);
const SHORTHAND_CANDIDATE = /^[^\s/]+\/[^\s/]+$/;
const GITHUB_SSH_PREFIX = /^(?:git@github\.com:|ssh:\/\/git@github\.com\/)/i;
const GITHUB_SSH = new RegExp(
  `^(?:git@github\\.com:|ssh:\\/\\/git@github\\.com\\/)(${GITHUB_COMPONENT})\/(${GITHUB_COMPONENT}?)(?:\\.git)?$`,
  "i",
);

function stripQueryAndFragment(value) {
  return value.split(/[?#]/, 1)[0];
}

function hasOpaqueCredentialRisk(value) {
  return /[?#]/.test(value)
    || /:\/\/[^/\s]*@/.test(value)
    || /(?:access[_-]?token|token|auth|key|password|secret)\s*=/i.test(value);
}

function hasGitHubProviderCredentialRisk(value) {
  return /[?#]/.test(value)
    || /(?:access[_-]?token|token|auth|key|password|secret)\s*=/i.test(value);
}

function isGitHubProviderCandidate(value) {
  return GITHUB_SSH_PREFIX.test(value) || SHORTHAND_CANDIDATE.test(value);
}

function safeGitHubProviderSource(value) {
  return GITHUB_SSH.test(value) || SHORTHAND.test(value);
}

export function redactSource(source) {
  const value = String(source);
  const providerBase = stripQueryAndFragment(value);
  if (isGitHubProviderCandidate(value)) {
    return safeGitHubProviderSource(providerBase)
      ? providerBase
      : "[unsafe source redacted]";
  }
  try {
    const url = new URL(value);
    if (!url.hostname && hasOpaqueCredentialRisk(value)) return "[unsafe source redacted]";
    url.username = "";
    url.password = "";
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    const redacted = stripQueryAndFragment(value.replace(/\/\/[^/@]+@/, "//"));
    return hasOpaqueCredentialRisk(redacted) ? "[unsafe source redacted]" : redacted;
  }
}

export function canonicalizeSource(source, {
  cwd = process.cwd(),
  realpath = realpathSync,
} = {}) {
  const value = String(source).trim();
  if (!value) throw new SourceIdentityError("Source must not be empty");

  if (value.startsWith("./") || value.startsWith("../") || isAbsolute(value)) {
    return realpath(resolve(cwd, value));
  }

  const providerCandidate = isGitHubProviderCandidate(value);
  if (providerCandidate && hasGitHubProviderCredentialRisk(value)) {
    throw new SourceIdentityError("Unsafe source credentials");
  }

  if (GITHUB_SSH_PREFIX.test(value)) {
    const ssh = value.match(GITHUB_SSH);
    if (!ssh) throw new SourceIdentityError("Invalid GitHub SSH source");
    return `${ssh[1]}/${ssh[2].replace(/\.git$/, "")}`;
  }

  if (SHORTHAND_CANDIDATE.test(value)) {
    if (!SHORTHAND.test(value)) throw new SourceIdentityError("Invalid GitHub shorthand source");
    return value.replace(/\.git$/, "");
  }

  try {
    const url = new URL(value);
    if (!url.hostname && hasOpaqueCredentialRisk(value)) {
      throw new SourceIdentityError("Unsafe source credentials");
    }
    url.username = "";
    url.password = "";
    url.search = "";
    url.hash = "";
    url.hostname = url.hostname.toLowerCase();
    if (url.hostname === "github.com") {
      const parts = url.pathname.replace(/^\//, "").split("/").filter(Boolean);
      if (parts.length === 2) return `${parts[0]}/${parts[1].replace(/\.git$/, "")}`;
    }
    url.pathname = url.pathname.replace(/\.git\/?$/, "").replace(/\/$/, "");
    return url.toString().replace(/\/$/, "");
  } catch (error) {
    if (error instanceof SourceIdentityError || hasOpaqueCredentialRisk(value)) {
      throw new SourceIdentityError("Unsafe source credentials", { cause: error });
    }
    return value;
  }
}
