import {
  EMPTY_BACKUPS,
  readBackupsDocument,
  writeBackupsDocument,
} from "./config.mjs";
import { canonicalizeSshGitUrl } from "./ssh-url.mjs";

const ADD_HINT = "Use `gt backup add <ssh-url>` to add a repo first.";

/**
 * @param {string} stored
 * @returns {string | null}
 */
function canonicalKey(stored) {
  const result = canonicalizeSshGitUrl(stored);
  return result.ok ? result.canonical : null;
}

/**
 * @param {{ backupsFile: string }} paths
 * @param {string} sshUrl
 * @param {{ fs?: object }} [options]
 * @returns {{ ok: true, index: number, document: object, createdFile: boolean }
 *   | { ok: false, error: string }}
 */
export function addBackupRepo(paths, sshUrl, { fs } = {}) {
  const canonicalized = canonicalizeSshGitUrl(sshUrl);
  if (!canonicalized.ok) {
    return canonicalized;
  }

  const readOpts = fs ? { fs } : {};
  const writeOpts = fs ? { fs } : {};
  const read = readBackupsDocument(paths.backupsFile, readOpts);

  let document;
  let createdFile = false;

  if (read.ok) {
    document = {
      version: read.document.version,
      repos: [...read.document.repos],
    };
  } else if (read.missing) {
    document = { version: EMPTY_BACKUPS.version, repos: [] };
    createdFile = true;
  } else {
    return { ok: false, error: read.error };
  }

  const newKey = canonicalized.canonical;
  const duplicate = document.repos.some((repo) => canonicalKey(repo) === newKey);
  if (duplicate) {
    return {
      ok: false,
      error: `Duplicate repo (already listed): ${canonicalized.sshUrl}`,
    };
  }

  document.repos.push(canonicalized.sshUrl);

  const written = writeBackupsDocument(paths.backupsFile, document, writeOpts);
  if (!written.ok) {
    return written;
  }

  return {
    ok: true,
    index: document.repos.length,
    document,
    createdFile,
  };
}

/**
 * @param {{ backupsFile: string }} paths
 * @param {string} token
 * @param {{ fs?: object }} [options]
 * @returns {{ ok: true, removed: string, document: object }
 *   | { ok: false, error: string }}
 */
export function removeBackupRepo(paths, token, { fs } = {}) {
  const readOpts = fs ? { fs } : {};
  const writeOpts = fs ? { fs } : {};
  const read = readBackupsDocument(paths.backupsFile, readOpts);

  if (!read.ok) {
    if (read.missing) {
      return {
        ok: false,
        error: `No backups list found. ${ADD_HINT}`,
      };
    }
    return { ok: false, error: read.error };
  }

  if (read.document.repos.length === 0) {
    return {
      ok: false,
      error: `Backups list is empty. ${ADD_HINT}`,
    };
  }

  const document = {
    version: read.document.version,
    repos: [...read.document.repos],
  };

  let removeIndex = -1;

  if (/^\d+$/.test(token)) {
    const index = Number(token);
    if (index < 1 || index > document.repos.length) {
      return {
        ok: false,
        error: `Index out of range: ${token} (valid 1–${document.repos.length})`,
      };
    }
    removeIndex = index - 1;
  } else {
    const canonicalized = canonicalizeSshGitUrl(token);
    if (!canonicalized.ok) {
      return canonicalized;
    }
    removeIndex = document.repos.findIndex(
      (repo) => canonicalKey(repo) === canonicalized.canonical,
    );
    if (removeIndex === -1) {
      return {
        ok: false,
        error: `Repo not found in backups list: ${token}`,
      };
    }
  }

  const [removed] = document.repos.splice(removeIndex, 1);
  const written = writeBackupsDocument(paths.backupsFile, document, writeOpts);
  if (!written.ok) {
    return written;
  }

  return { ok: true, removed, document };
}
