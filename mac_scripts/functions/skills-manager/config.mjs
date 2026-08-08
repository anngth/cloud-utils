import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import {
  CatalogError,
  EMPTY_CATALOG,
  migrateProfilesToCatalog,
  validateCatalogDocument,
} from "./catalog.mjs";
import { validateProfilesDocument } from "./profiles.mjs";
import { canonicalizeSource } from "./source-id.mjs";

const defaultFs = {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

export class ConfigFileError extends Error {
  constructor(message, { cause, filePath } = {}) {
    super(message, { cause });
    this.name = "ConfigFileError";
    this.filePath = filePath;
  }
}

export function defaultConfigDir(env = process.env) {
  return `${env.HOME ?? ""}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils`;
}

function readJson(filePath, fs) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (cause) {
    throw new ConfigFileError(`Could not read configuration file: ${filePath}`, { cause, filePath });
  }
}

function readLegacySources(filePath, fs) {
  const data = readJson(filePath, fs);
  const entries = Array.isArray(data)
    ? data
    : data && typeof data === "object" && Array.isArray(data.presets)
      ? data.presets
      : null;
  if (!entries || entries.some((entry) => (
    !entry || typeof entry !== "object" || Array.isArray(entry) || typeof entry.source !== "string"
  ))) {
    throw new ConfigFileError(`Invalid legacy source list: ${filePath}`, { filePath });
  }

  try {
    return [...new Set(entries.map(({ source }) => canonicalizeSource(source)))].sort((a, b) => a.localeCompare(b));
  } catch (cause) {
    throw new ConfigFileError(`Invalid legacy source list: ${filePath}`, { cause, filePath });
  }
}

function wrapCatalogError(cause, filePath) {
  if (cause instanceof CatalogError) {
    return new ConfigFileError(cause.message, { cause, filePath });
  }
  return cause;
}

function bootstrapCatalog(paths, { fs, pid }) {
  if (fs.existsSync(paths.sourcesFile)) return;

  let catalog;
  if (fs.existsSync(paths.profilesFile)) {
    try {
      const profiles = validateProfilesDocument(readJson(paths.profilesFile, fs));
      catalog = migrateProfilesToCatalog(profiles);
    } catch (cause) {
      if (cause instanceof ConfigFileError) throw cause;
      if (cause instanceof CatalogError) {
        throw new ConfigFileError(cause.message, { cause, filePath: paths.sourcesFile });
      }
      throw new ConfigFileError(`Invalid profiles file: ${paths.profilesFile}`, {
        cause,
        filePath: paths.profilesFile,
      });
    }
  } else if (fs.existsSync(paths.legacyFile)) {
    const sources = readLegacySources(paths.legacyFile, fs);
    try {
      catalog = validateCatalogDocument({
        version: 1,
        sources: sources.map((source) => ({ source, skills: [] })),
      });
    } catch (cause) {
      throw wrapCatalogError(cause, paths.sourcesFile);
    }
  } else {
    catalog = EMPTY_CATALOG;
  }

  writeJsonAtomic(paths.sourcesFile, catalog, { fs, pid });
}

export function initializeConfig({
  env = process.env,
  fs = defaultFs,
  pid = process.pid,
} = {}) {
  const configDir = env.CLOUD_UTILS_CONFIG_DIR || defaultConfigDir(env);
  const skmDir = join(configDir, "skm");
  const paths = {
    configDir,
    skmDir,
    sourcesFile: join(skmDir, "sources.json"),
    profilesFile: join(skmDir, "profiles.json"),
    projectsFile: join(skmDir, "projects.json"),
    legacyFile: join(skmDir, "list.json"),
  };
  fs.mkdirSync(skmDir, { recursive: true });
  bootstrapCatalog(paths, { fs, pid });
  return paths;
}

export function readConfig(paths, { fs = defaultFs } = {}) {
  try {
    const catalog = validateCatalogDocument(readJson(paths.sourcesFile, fs));
    return { catalog };
  } catch (cause) {
    if (cause instanceof ConfigFileError) throw cause;
    if (cause instanceof CatalogError) {
      throw new ConfigFileError(`Invalid sources file: ${paths.sourcesFile}`, { cause, filePath: paths.sourcesFile });
    }
    throw new ConfigFileError("Invalid SKM configuration", { cause });
  }
}

export function writeJsonAtomic(filePath, value, {
  fs = defaultFs,
  pid = process.pid,
} = {}) {
  const tempPath = `${filePath}.${pid}.tmp`;
  try {
    fs.writeFileSync(tempPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
    fs.renameSync(tempPath, filePath);
  } catch (error) {
    try {
      fs.rmSync(tempPath, { force: true });
    } catch {}
    throw error;
  }
}

export function writeCatalog(paths, document, options = {}) {
  writeJsonAtomic(paths.sourcesFile, validateCatalogDocument(document), options);
}
