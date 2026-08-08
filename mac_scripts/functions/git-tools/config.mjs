import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir as osTmpdir } from "node:os";
import { dirname, join, relative, sep } from "node:path";

const defaultFs = {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

export const EMPTY_BACKUPS = Object.freeze({ version: 2, repos: [] });

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

/**
 * Shorten absolute paths for logs: ~/… under HOME, else path relative to tmpdir.
 * @param {string} filePath
 * @param {{ home?: string, tempDir?: string }} [options]
 * @returns {string}
 */
export function formatDisplayPath(filePath, {
  home = process.env.HOME,
  tempDir = osTmpdir(),
} = {}) {
  if (typeof filePath !== "string" || !filePath) return filePath;

  const withSep = (root) => (root.endsWith(sep) ? root : `${root}${sep}`);

  if (home && (filePath === home || filePath.startsWith(withSep(home)))) {
    const rest = filePath.slice(home.length).replace(/^\//, "");
    return rest ? `~/${rest}` : "~";
  }

  if (tempDir && (filePath === tempDir || filePath.startsWith(withSep(tempDir)))) {
    const rel = relative(tempDir, filePath);
    if (rel && !rel.startsWith("..")) return rel;
  }

  return filePath;
}

export function isValidBackupsDocumentV1(value) {
  return (
    value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && value.version === 1
    && Array.isArray(value.repos)
    && value.repos.every((repo) => typeof repo === "string" && repo.length > 0)
  );
}

export function isValidBackupsDocumentV2(value) {
  return (
    value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && value.version === 2
    && Array.isArray(value.repos)
    && value.repos.every(
      (repo) =>
        repo !== null
        && typeof repo === "object"
        && !Array.isArray(repo)
        && typeof repo.url === "string"
        && repo.url.length > 0
        && (
          repo.lastBackupAt === null
          || (typeof repo.lastBackupAt === "string" && repo.lastBackupAt.length > 0)
        ),
    )
  );
}

function isValidBackupsDocument(value) {
  return isValidBackupsDocumentV1(value) || isValidBackupsDocumentV2(value);
}

export function migrateBackupsDocument(document) {
  if (isValidBackupsDocumentV2(document)) {
    return { ok: true, document, migrated: false };
  }

  if (isValidBackupsDocumentV1(document)) {
    return {
      ok: true,
      migrated: true,
      document: {
        version: 2,
        repos: document.repos.map((url) => ({ url, lastBackupAt: null })),
      },
    };
  }

  return { ok: false, error: "Invalid backups document" };
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
  if (!isValidBackupsDocumentV2(document)) {
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

export function loadBackupsDocument(filePath, { fs = defaultFs } = {}) {
  const readResult = readBackupsDocument(filePath, { fs });
  if (!readResult.ok) {
    return readResult;
  }

  const migrateResult = migrateBackupsDocument(readResult.document);
  if (!migrateResult.ok) {
    return migrateResult;
  }

  if (migrateResult.migrated) {
    const writeResult = writeBackupsDocument(filePath, migrateResult.document, { fs });
    if (!writeResult.ok) {
      return writeResult;
    }
  }

  return {
    ok: true,
    document: migrateResult.document,
    migrated: migrateResult.migrated,
  };
}
