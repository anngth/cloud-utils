import { realpathSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";

export class SourceIdentityError extends Error {}

const SHORTHAND = /^[^\s/:]+\/[^\s/]+(?:\.git)?$/;
const GITHUB_SSH = /^(?:git@github\.com:|ssh:\/\/git@github\.com\/)([^/]+)\/([^/]+?)(?:\.git)?$/i;

function stripQueryAndFragment(value) {
  return value.split(/[?#]/, 1)[0];
}

function hasOpaqueCredentialRisk(value) {
  return /[?#]/.test(value)
    || /:\/\/[^/\s]*@/.test(value)
    || /(?:access[_-]?token|token|auth|key|password|secret)\s*=/i.test(value);
}

export function redactSource(source) {
  const value = String(source);
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

  const ssh = value.match(GITHUB_SSH);
  if (ssh) return `${ssh[1]}/${ssh[2].replace(/\.git$/, "")}`;

  if (SHORTHAND.test(value)) return value.replace(/\.git$/, "");

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
