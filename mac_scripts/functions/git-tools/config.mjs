import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";

const defaultFs = {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

export const EMPTY_BACKUPS = Object.freeze({ version: 1, repos: [] });

export function defaultConfigDir(env = process.env) {
  return `${env.HOME ?? ""}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils`;
}

export function resolveGtPaths(env = process.env) {
  const configDir = env.CLOUD_UTILS_CONFIG_DIR || defaultConfigDir(env);
  const gtDir = join(configDir, "gt");
  return {
    configDir,
    gtDir,
    backupsFile: join(gtDir, "backups.json"),
  };
}

function isValidBackupsDocument(value) {
  return (
    value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && value.version === 1
    && Array.isArray(value.repos)
    && value.repos.every((repo) => typeof repo === "string" && repo.length > 0)
  );
}

export function readBackupsDocument(filePath, { fs = defaultFs } = {}) {
  let raw;
  try {
    raw = fs.readFileSync(filePath, "utf8");
  } catch (error) {
    if (error && (error.code === "ENOENT" || !fs.existsSync?.(filePath))) {
      return { ok: false, missing: true, error: `Backups file not found: ${filePath}` };
    }
    return { ok: false, error: `Could not read backups file: ${filePath}` };
  }

  let document;
  try {
    document = JSON.parse(raw);
  } catch {
    return { ok: false, error: `Invalid JSON in backups file: ${filePath}` };
  }

  if (!isValidBackupsDocument(document)) {
    return { ok: false, error: `Invalid backups document: ${filePath}` };
  }

  return { ok: true, document };
}

export function writeBackupsDocument(filePath, document, { fs = defaultFs } = {}) {
  if (!isValidBackupsDocument(document)) {
    return { ok: false, error: "Invalid backups document" };
  }

  const tempPath = `${filePath}.tmp`;
  try {
    fs.mkdirSync(dirname(filePath), { recursive: true });
    fs.writeFileSync(tempPath, `${JSON.stringify(document, null, 2)}\n`, "utf8");
    fs.renameSync(tempPath, filePath);
    return { ok: true };
  } catch (error) {
    try {
      fs.rmSync?.(tempPath, { force: true });
    } catch {
      // ignore cleanup failures
    }
    return {
      ok: false,
      error: error instanceof Error ? error.message : `Could not write backups file: ${filePath}`,
    };
  }
}
