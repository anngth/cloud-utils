import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { validateProfilesDocument } from "./profiles.mjs";
import { validateProjectsDocument } from "./projects.mjs";
import { canonicalizeSource } from "./source-id.mjs";

const defaultFs = {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

export const EMPTY_PROFILES = Object.freeze({
  version: 1,
  profiles: [{ name: "default", sources: [] }],
});

export const EMPTY_PROJECTS = Object.freeze({ version: 1, projects: [] });

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

function bootstrapDocuments(paths, { fs, pid }) {
  const profilesExists = fs.existsSync(paths.profilesFile);
  const projectsExists = fs.existsSync(paths.projectsFile);

  if (!profilesExists && projectsExists) {
    throw new ConfigFileError(`profiles.json is missing while projects.json exists: ${paths.profilesFile}`, {
      filePath: paths.profilesFile,
    });
  }

  if (!profilesExists) {
    const sources = fs.existsSync(paths.legacyFile)
      ? readLegacySources(paths.legacyFile, fs)
      : [];
    const profiles = validateProfilesDocument({
      version: 1,
      profiles: [{ name: "default", sources: sources.map((source) => ({ source, skills: [] })) }],
    });
    writeJsonAtomic(paths.profilesFile, profiles, { fs, pid });
  }

  if (!projectsExists) writeJsonAtomic(paths.projectsFile, EMPTY_PROJECTS, { fs, pid });
}

export function recoverConfigTransaction(paths, { fs = defaultFs } = {}) {
  if (fs.existsSync(paths.transactionFile)) {
    throw new ConfigFileError(`Pending SKM transaction requires recovery: ${paths.transactionFile}`, {
      filePath: paths.transactionFile,
    });
  }
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
    profilesFile: join(skmDir, "profiles.json"),
    projectsFile: join(skmDir, "projects.json"),
    legacyFile: join(skmDir, "list.json"),
    transactionFile: join(skmDir, ".transaction.json"),
  };
  fs.mkdirSync(skmDir, { recursive: true });
  recoverConfigTransaction(paths, { fs, pid });
  bootstrapDocuments(paths, { fs, pid });
  return { ...paths, skillsFile: paths.legacyFile };
}

export function readConfig(paths, { fs = defaultFs } = {}) {
  try {
    const profiles = validateProfilesDocument(readJson(paths.profilesFile, fs));
    const names = new Set(profiles.profiles.map((profile) => profile.name));
    const projects = validateProjectsDocument(readJson(paths.projectsFile, fs), names);
    return { profiles, projects };
  } catch (cause) {
    if (cause instanceof ConfigFileError) throw cause;
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

export function writeProfiles(paths, document, options) {
  writeJsonAtomic(paths.profilesFile, validateProfilesDocument(document), options);
}

export function writeProjects(paths, profilesDocument, projectsDocument, options) {
  const names = new Set(profilesDocument.profiles.map((profile) => profile.name));
  writeJsonAtomic(
    paths.projectsFile,
    validateProjectsDocument(projectsDocument, names),
    options,
  );
}
