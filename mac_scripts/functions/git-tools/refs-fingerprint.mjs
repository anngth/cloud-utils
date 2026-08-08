export function parseLsRemoteFingerprint(stdout) {
  const lines = String(stdout ?? "")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const [sha, ref] = l.split(/\s+/);
      return { sha, ref };
    })
    .filter(({ sha, ref }) => sha && ref && /^refs\/(heads|tags)\//.test(ref))
    .map(({ sha, ref }) => `${sha}\t${ref}`)
    .sort();
  return lines.join("\n");
}

export function fingerprintsEqual(a, b) {
  return a === b;
}
