import { createHash } from "node:crypto";
import {
  copyFileSync,
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
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

function transactionEntry(target, pid) {
  return {
    target,
    backup: `${target}.${pid}.bak`,
    next: `${target}.${pid}.next`,
    beforeHash: "",
    nextHash: "",
  };
}

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

function hashFile(filePath, fs) {
  return sha256(fs.readFileSync(filePath));
}

function readAndValidateJournal(paths, fs) {
  const filePath = paths.transactionFile;
  let value;
  try {
    value = JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    throw new ConfigFileError(`Could not read transaction journal: ${filePath}`, { cause: error });
  }
  if (
    value?.version !== 1 ||
    !["prepared", "profiles-written", "targets-written"].includes(value.phase) ||
    !Array.isArray(value.files) ||
    value.files.length !== 2
  ) {
    throw new ConfigFileError(`Invalid transaction journal: ${filePath}`);
  }
  for (const item of value.files) {
    for (const key of ["target", "backup", "next", "beforeHash", "nextHash"]) {
      if (typeof item[key] !== "string" || item[key] === "") {
        throw new ConfigFileError(`Invalid transaction journal field: ${key}`);
      }
    }
  }
  const expectedTargets = new Set([paths.profilesFile, paths.projectsFile]);
  const transactionIds = new Set();
  for (const item of value.files) {
    if (!expectedTargets.delete(item.target)) {
      throw new ConfigFileError(`Invalid transaction journal target: ${item.target}`);
    }
    const backupPrefix = `${item.target}.`;
    const backupSuffix = ".bak";
    if (!item.backup.startsWith(backupPrefix) || !item.backup.endsWith(backupSuffix)) {
      throw new ConfigFileError(`Invalid transaction journal backup: ${item.backup}`);
    }
    const transactionId = item.backup.slice(backupPrefix.length, -backupSuffix.length);
    if (!/^\d+$/.test(transactionId) || item.next !== `${item.target}.${transactionId}.next`) {
      throw new ConfigFileError(`Invalid transaction journal artifacts: ${item.target}`);
    }
    transactionIds.add(transactionId);
  }
  if (expectedTargets.size !== 0 || transactionIds.size !== 1) {
    throw new ConfigFileError(`Invalid transaction journal paths: ${filePath}`);
  }
  return value;
}

function cleanupTransaction(journal, journalPath, fs) {
  for (const item of journal.files) {
    fs.rmSync(item.backup, { force: true });
    fs.rmSync(item.next, { force: true });
  }
  fs.rmSync(journalPath, { force: true });
}

export function recoverConfigTransaction(paths, { fs = defaultFs } = {}) {
  if (!fs.existsSync(paths.transactionFile)) return;
  const journal = readAndValidateJournal(paths, fs);
  const bothNext = journal.files.every((item) => hashFile(item.target, fs) === item.nextHash);
  if (journal.phase === "targets-written" && bothNext) {
    cleanupTransaction(journal, paths.transactionFile, fs);
    return;
  }
  for (const item of journal.files) {
    if (!fs.existsSync(item.backup) || hashFile(item.backup, fs) !== item.beforeHash) {
      throw new ConfigFileError(`Cannot recover transaction: ${item.target}`);
    }
  }
  for (const item of journal.files) fs.copyFileSync(item.backup, item.target);
  cleanupTransaction(journal, paths.transactionFile, fs);
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

export function writeConfigTransaction(paths, { profiles, projects }, {
  fs = defaultFs,
  pid = process.pid,
} = {}) {
  if (!Number.isSafeInteger(pid) || pid < 0) {
    throw new ConfigFileError("Invalid transaction identifier");
  }
  const validProfiles = validateProfilesDocument(profiles);
  const profileNames = new Set(validProfiles.profiles.map((item) => item.name));
  const validProjects = validateProjectsDocument(projects, profileNames);
  const documents = [
    [paths.profilesFile, validProfiles],
    [paths.projectsFile, validProjects],
  ];
  const files = documents.map(([target, document]) => {
    const entry = transactionEntry(target, pid);
    const before = fs.readFileSync(target);
    const next = Buffer.from(`${JSON.stringify(document, null, 2)}\n`);
    fs.writeFileSync(entry.backup, before);
    fs.writeFileSync(entry.next, next);
    entry.beforeHash = sha256(before);
    entry.nextHash = sha256(next);
    return entry;
  });
  const journal = { version: 1, phase: "prepared", files };
  const saveJournal = () => writeJsonAtomic(paths.transactionFile, journal, { fs, pid });
  saveJournal();
  try {
    fs.renameSync(files[0].next, files[0].target);
    journal.phase = "profiles-written";
    saveJournal();
    fs.renameSync(files[1].next, files[1].target);
    journal.phase = "targets-written";
    saveJournal();
    cleanupTransaction(journal, paths.transactionFile, fs);
  } catch (error) {
    try {
      recoverConfigTransaction(paths, { fs });
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
