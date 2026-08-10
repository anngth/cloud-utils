import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const defaultFs = {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

export const EMPTY_DESIRED = Object.freeze({
  version: 1,
  formulas: [],
  casks: [],
  taps: [],
});

const DESIRED_ARRAY_FIELDS = ["formulas", "casks", "taps"];

export function defaultConfigDir(env = process.env) {
  return `${env.HOME ?? ""}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils`;
}

export function resolveBudPaths(env = process.env) {
  const configDir = env.CLOUD_UTILS_CONFIG_DIR || defaultConfigDir(env);
  const budDir = join(configDir, "bud");
  const moduleDir = dirname(fileURLToPath(import.meta.url));
  return {
    configDir,
    budDir,
    desiredFile: join(budDir, "desired.json"),
    exampleFile: join(moduleDir, "desired.json.example"),
  };
}

/**
 * @param {unknown} value
 * @returns {boolean}
 */
function isNonEmptyString(value) {
  return typeof value === "string" && value.length > 0;
}

/**
 * @param {unknown} value
 * @returns {boolean}
 */
export function isValidDesiredDocument(value) {
  return (
    value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && value.version === 1
    && DESIRED_ARRAY_FIELDS.every(
      (field) =>
        Array.isArray(value[field])
        && value[field].every(isNonEmptyString),
    )
  );
}

/**
 * @param {{ version: number, formulas: string[], casks: string[], taps: string[] }} doc
 * @returns {{ version: number, formulas: string[], casks: string[], taps: string[] }}
 */
export function normalizeDesiredDocument(doc) {
  const normalized = { version: doc.version };
  for (const field of DESIRED_ARRAY_FIELDS) {
    normalized[field] = [...new Set(doc[field])].sort();
  }
  return normalized;
}

/**
 * @param {{ env?: Record<string, string | undefined>, fs?: typeof defaultFs, examplePath?: string, listBrewTaps?: () => Promise<string[]> }} [options]
 */
export async function loadDesiredDocument({
  env = process.env,
  fs = defaultFs,
  examplePath,
  listBrewTaps,
} = {}) {
  const { desiredFile } = resolveBudPaths(env);
  const resolvedExamplePath = examplePath ?? resolveBudPaths(env).exampleFile;

  const existing = readDesiredDocument(desiredFile, fs);
  if (existing.ok) {
    if (!isValidDesiredDocument(existing.document)) {
      return { ok: false, error: existing.error ?? "invalid desired.json" };
    }
    return { ok: true, document: existing.document };
  }
  if (!existing.missing) {
    return { ok: false, error: existing.error ?? "invalid desired.json" };
  }

  let exampleRaw;
  try {
    exampleRaw = fs.readFileSync(resolvedExamplePath, "utf8");
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : `Could not read example file: ${resolvedExamplePath}`,
    };
  }

  let document;
  try {
    document = JSON.parse(exampleRaw);
  } catch {
    return { ok: false, error: `Invalid JSON in example file: ${resolvedExamplePath}` };
  }
  if (!isValidDesiredDocument(document)) {
    return { ok: false, error: "invalid example file" };
  }

  if (document.taps.length === 0 && listBrewTaps) {
    document = { ...document, taps: await listBrewTaps() };
  }

  document = normalizeDesiredDocument(document);

  const written = writeDesiredDocument(desiredFile, document, fs);
  if (!written.ok) {
    return { ok: false, error: written.error };
  }

  return { ok: true, document, bootstrapped: true };
}

export function readDesiredDocument(filePath, fs = defaultFs) {
  let raw;
  try {
    raw = fs.readFileSync(filePath, "utf8");
  } catch (error) {
    if (error && (error.code === "ENOENT" || !fs.existsSync?.(filePath))) {
      return { ok: false, missing: true, error: `Desired file not found: ${filePath}` };
    }
    return { ok: false, error: `Could not read desired file: ${filePath}` };
  }

  let document;
  try {
    document = JSON.parse(raw);
  } catch {
    return { ok: false, error: `Invalid JSON in desired file: ${filePath}` };
  }

  return { ok: true, document };
}

export function writeDesiredDocument(filePath, document, fs = defaultFs) {
  if (!isValidDesiredDocument(document)) {
    return { ok: false, error: "Invalid desired document" };
  }

  const normalized = normalizeDesiredDocument(document);
  const tempPath = `${filePath}.tmp`;
  try {
    fs.mkdirSync(dirname(filePath), { recursive: true });
    fs.writeFileSync(tempPath, `${JSON.stringify(normalized, null, 2)}\n`, "utf8");
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
      error: error instanceof Error ? error.message : `Could not write desired file: ${filePath}`,
    };
  }
}
