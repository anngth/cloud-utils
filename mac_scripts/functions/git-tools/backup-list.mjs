import {
  EMPTY_BACKUPS,
  loadBackupsDocument,
  writeBackupsDocument,
} from "./config.mjs";
import { canonicalizeSshGitUrl } from "./ssh-url.mjs";

const ADD_HINT = "Use `gt backup add <ssh-url>` to add a repo first.";

/**
 * @param {string} url
 * @returns {string | null}
 */
function canonicalKey(url) {
  const result = canonicalizeSshGitUrl(url);
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

  const loadOpts = fs ? { fs } : {};
  const writeOpts = fs ? { fs } : {};
  const load = loadBackupsDocument(paths.backupsFile, loadOpts);

  let document;
  let createdFile = false;

  if (load.ok) {
    document = {
      version: load.document.version,
      repos: [...load.document.repos],
    };
  } else if (load.missing) {
    document = { version: EMPTY_BACKUPS.version, repos: [] };
    createdFile = true;
  } else {
    return { ok: false, error: load.error };
  }

  const newKey = canonicalized.canonical;
  const duplicate = document.repos.some(
    (repo) => canonicalKey(repo.url) === newKey,
  );
  if (duplicate) {
    return {
      ok: false,
      error: `Duplicate repo (already listed): ${canonicalized.sshUrl}`,
    };
  }

  document.repos.push({
    url: canonicalized.sshUrl,
    lastBackupAt: null,
    lastCheckedAt: null,
    selectedLast: false,
  });

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
 * @param {string[]} urls
 * @param {{ fs?: object }} [options]
 * @returns {{ ok: boolean, added: Array<{ url: string, index: number }>, failures: Array<{ url: string, error: string }>, document?: object, createdFile?: boolean, error?: string }}
 */
export function addBackupRepos(paths, urls, { fs } = {}) {
  const loadOpts = fs ? { fs } : {};
  const writeOpts = fs ? { fs } : {};
  const load = loadBackupsDocument(paths.backupsFile, loadOpts);

  let document;
  let createdFile = false;

  if (load.ok) {
    document = {
      version: load.document.version,
      repos: [...load.document.repos],
    };
  } else if (load.missing) {
    document = { version: EMPTY_BACKUPS.version, repos: [] };
    createdFile = true;
  } else {
    return { ok: false, added: [], failures: [], error: load.error };
  }

  const added = [];
  const failures = [];

  for (const sshUrl of urls) {
    const canonicalized = canonicalizeSshGitUrl(sshUrl);
    if (!canonicalized.ok) {
      failures.push({ url: sshUrl, error: canonicalized.error });
      continue;
    }

    const newKey = canonicalized.canonical;
    const duplicate = document.repos.some(
      (repo) => canonicalKey(repo.url) === newKey,
    );
    if (duplicate) {
      failures.push({
        url: sshUrl,
        error: `Duplicate repo (already listed): ${canonicalized.sshUrl}`,
      });
      continue;
    }

    document.repos.push({
      url: canonicalized.sshUrl,
      lastBackupAt: null,
      lastCheckedAt: null,
      selectedLast: false,
    });
    added.push({ url: canonicalized.sshUrl, index: document.repos.length });
  }

  if (added.length === 0) {
    return {
      ok: failures.length === 0,
      added,
      failures,
    };
  }

  const written = writeBackupsDocument(paths.backupsFile, document, writeOpts);
  if (!written.ok) {
    return { ok: false, added, failures, error: written.error };
  }

  return {
    ok: failures.length === 0 && added.length === urls.length,
    added,
    failures,
    document,
    createdFile: createdFile && added.length > 0,
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
  const loadOpts = fs ? { fs } : {};
  const writeOpts = fs ? { fs } : {};
  const load = loadBackupsDocument(paths.backupsFile, loadOpts);

  if (!load.ok) {
    if (load.missing) {
      return {
        ok: false,
        error: `No backups list found. ${ADD_HINT}`,
      };
    }
    return { ok: false, error: load.error };
  }

  if (load.document.repos.length === 0) {
    return {
      ok: false,
      error: `Backups list is empty. ${ADD_HINT}`,
    };
  }

  const document = {
    version: load.document.version,
    repos: [...load.document.repos],
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
      (repo) => canonicalKey(repo.url) === canonicalized.canonical,
    );
    if (removeIndex === -1) {
      return {
        ok: false,
        error: `Repo not found in backups list: ${token}`,
      };
    }
  }

  const removed = document.repos[removeIndex].url;
  document.repos.splice(removeIndex, 1);
  const written = writeBackupsDocument(paths.backupsFile, document, writeOpts);
  if (!written.ok) {
    return written;
  }

  return { ok: true, removed, document };
}

/**
 * @param {{ backupsFile: string }} paths
 * @param {string} sshUrl
 * @param {{ now?: Date, fs?: object }} [options]
 * @returns {{ ok: true, document: object } | { ok: false, error: string }}
 */
export function recordLastBackupAt(paths, sshUrl, { now = new Date(), fs } = {}) {
  const canonicalized = canonicalizeSshGitUrl(sshUrl);
  if (!canonicalized.ok) {
    return canonicalized;
  }

  const loadOpts = fs ? { fs } : {};
  const writeOpts = fs ? { fs } : {};
  const load = loadBackupsDocument(paths.backupsFile, loadOpts);

  if (!load.ok) {
    if (load.missing) {
      return {
        ok: false,
        error: `No backups list found. ${ADD_HINT}`,
      };
    }
    return { ok: false, error: load.error };
  }

  const document = {
    version: load.document.version,
    repos: [...load.document.repos],
  };

  const index = document.repos.findIndex(
    (repo) => canonicalKey(repo.url) === canonicalized.canonical,
  );
  if (index === -1) {
    return {
      ok: false,
      error: `Repo not found in backups list: ${sshUrl}`,
    };
  }

  document.repos[index] = {
    ...document.repos[index],
    lastBackupAt: now.toISOString(),
    lastCheckedAt: now.toISOString(),
  };

  const written = writeBackupsDocument(paths.backupsFile, document, writeOpts);
  if (!written.ok) {
    return written;
  }

  return { ok: true, document };
}

/**
 * @param {{ backupsFile: string }} paths
 * @param {string} sshUrl
 * @param {{ now?: Date, fs?: object }} [options]
 * @returns {{ ok: true, document: object } | { ok: false, error: string }}
 */
export function recordLastCheckedAt(paths, sshUrl, { now = new Date(), fs } = {}) {
  const canonicalized = canonicalizeSshGitUrl(sshUrl);
  if (!canonicalized.ok) {
    return canonicalized;
  }

  const loadOpts = fs ? { fs } : {};
  const writeOpts = fs ? { fs } : {};
  const load = loadBackupsDocument(paths.backupsFile, loadOpts);

  if (!load.ok) {
    if (load.missing) {
      return {
        ok: false,
        error: `No backups list found. ${ADD_HINT}`,
      };
    }
    return { ok: false, error: load.error };
  }

  const document = {
    version: load.document.version,
    repos: [...load.document.repos],
  };

  const index = document.repos.findIndex(
    (repo) => canonicalKey(repo.url) === canonicalized.canonical,
  );
  if (index === -1) {
    return {
      ok: false,
      error: `Repo not found in backups list: ${sshUrl}`,
    };
  }

  document.repos[index] = {
    ...document.repos[index],
    lastCheckedAt: now.toISOString(),
  };

  const written = writeBackupsDocument(paths.backupsFile, document, writeOpts);
  if (!written.ok) {
    return written;
  }

  return { ok: true, document };
}

/**
 * @param {{ backupsFile: string }} paths
 * @param {string[]} selectedUrls
 * @param {{ fs?: object }} [options]
 * @returns {{ ok: true, document: object } | { ok: false, error: string }}
 */
export function setSelectedLast(paths, selectedUrls, { fs } = {}) {
  const loadOpts = fs ? { fs } : {};
  const writeOpts = fs ? { fs } : {};
  const load = loadBackupsDocument(paths.backupsFile, loadOpts);

  if (!load.ok) {
    if (load.missing) {
      return {
        ok: false,
        error: `No backups list found. ${ADD_HINT}`,
      };
    }
    return { ok: false, error: load.error };
  }

  const selectedKeys = new Set();
  for (const url of selectedUrls) {
    const key = canonicalKey(url);
    if (key !== null) {
      selectedKeys.add(key);
    }
  }

  const document = {
    version: load.document.version,
    repos: load.document.repos.map((repo) => ({
      ...repo,
      selectedLast: selectedKeys.has(canonicalKey(repo.url)),
    })),
  };

  const written = writeBackupsDocument(paths.backupsFile, document, writeOpts);
  if (!written.ok) {
    return written;
  }

  return { ok: true, document };
}
