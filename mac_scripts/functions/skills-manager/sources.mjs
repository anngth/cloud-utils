import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";

const defaultFs = {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

export class SourceListError extends Error {
  constructor(filePath, cause) {
    super(`Could not read source list: ${filePath}`, { cause });
    this.name = "SourceListError";
    this.filePath = filePath;
  }
}

export function normalizeSourceFile(filePath, fs = defaultFs) {
  if (fs.existsSync(filePath)) return;
  fs.mkdirSync(dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, "[]\n", "utf8");
}

export function readSources(filePath, fs = defaultFs) {
  try {
    const data = JSON.parse(fs.readFileSync(filePath, "utf8"));
    const items = Array.isArray(data)
      ? data
      : data && typeof data === "object" && Array.isArray(data.presets)
        ? data.presets
        : null;
    if (
      !items ||
      items.some((item) => !item || typeof item !== "object" || Array.isArray(item) || typeof item.source !== "string")
    ) {
      throw new TypeError("invalid source-list shape");
    }
    return items;
  } catch (error) {
    throw new SourceListError(filePath, error);
  }
}

export function hasSource(items, source) {
  return items.some((item) => item.source === source);
}

export function addSources(items, requested) {
  const next = items.map((item) => ({ ...item }));
  const results = [];
  for (const source of requested) {
    if (hasSource(next, source)) {
      results.push({ source, status: "exists" });
    } else {
      next.push({ source });
      results.push({ source, status: "added" });
    }
  }
  next.sort((a, b) => a.source.localeCompare(b.source));
  return {
    items: next,
    results,
    added: results.filter((item) => item.status === "added").length,
    skipped: results.filter((item) => item.status === "exists").length,
  };
}

export function removeSources(items, requested) {
  const existing = new Set(items.map((item) => item.source));
  const wanted = new Set(requested);
  const results = requested.map((source) => ({
    source,
    status: existing.has(source) ? "removed" : "missing",
  }));
  return {
    items: items.filter((item) => !wanted.has(item.source)),
    results,
    removed: results.filter((item) => item.status === "removed").length,
    skipped: results.filter((item) => item.status === "missing").length,
  };
}

export function writeSourcesAtomic(filePath, items, { fs = defaultFs, pid = process.pid } = {}) {
  const tempPath = `${filePath}.${pid}.tmp`;
  try {
    fs.writeFileSync(tempPath, `${JSON.stringify(items, null, 2)}\n`, "utf8");
    fs.renameSync(tempPath, filePath);
  } catch (error) {
    try {
      fs.rmSync(tempPath, { force: true });
    } catch {}
    throw error;
  }
}
