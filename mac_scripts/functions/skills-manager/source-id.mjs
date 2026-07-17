import { realpathSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";

export class SourceIdentityError extends Error {}

const SHORTHAND = /^[^\s/:]+\/[^\s/]+(?:\.git)?$/;
const GITHUB_SSH = /^(?:git@github\.com:|ssh:\/\/git@github\.com\/)([^/]+)\/([^/]+?)(?:\.git)?$/i;

export function redactSource(source) {
  const value = String(source);
  try {
    const url = new URL(value);
    url.username = "";
    url.password = "";
    for (const key of ["token", "access_token", "auth", "key"]) url.searchParams.delete(key);
    url.search = url.searchParams.toString();
    return url.toString().replace(/\/$/, "");
  } catch {
    return value.replace(/\/\/[^/@]+@/, "//");
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
    url.username = "";
    url.password = "";
    for (const key of ["token", "access_token", "auth", "key"]) url.searchParams.delete(key);
    url.search = url.searchParams.toString();
    url.hash = "";
    url.hostname = url.hostname.toLowerCase();
    if (url.hostname === "github.com") {
      const parts = url.pathname.replace(/^\//, "").split("/").filter(Boolean);
      if (parts.length === 2) return `${parts[0]}/${parts[1].replace(/\.git$/, "")}`;
    }
    url.pathname = url.pathname.replace(/\.git\/?$/, "").replace(/\/$/, "");
    return url.toString().replace(/\/$/, "");
  } catch {
    return value;
  }
}
