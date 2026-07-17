import { readFileSync } from "node:fs";
import { join } from "node:path";
import { listInstalledSkills } from "./skills-cli.mjs";
import { canonicalizeSource } from "./source-id.mjs";

const defaultFs = { readFileSync };

export class InstalledStateError extends Error {
  constructor(message, { cause } = {}) {
    super(message, { cause });
    this.name = "InstalledStateError";
  }
}

function readLock(filePath, { projectRoot, fs }) {
  let document;
  try {
    document = JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return new Map();
  }

  if (!document || typeof document !== "object" || Array.isArray(document)
    || !document.skills || typeof document.skills !== "object" || Array.isArray(document.skills)) {
    return new Map();
  }

  const provenance = new Map();
  for (const [name, entry] of Object.entries(document.skills)) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry) || typeof entry.source !== "string") continue;
    try {
      provenance.set(name, {
        source: canonicalizeSource(entry.source, { cwd: projectRoot }),
        sourceType: typeof entry.sourceType === "string" ? entry.sourceType : null,
        origin: filePath,
      });
    } catch {}
  }
  return provenance;
}

function reconcileTier(paths, context) {
  const provenance = new Map();
  const ambiguous = new Set();
  for (const filePath of paths) {
    for (const [name, entry] of readLock(filePath, context)) {
      if (ambiguous.has(name)) continue;
      const existing = provenance.get(name);
      if (!existing) {
        provenance.set(name, entry);
      } else if (existing.source !== entry.source || existing.sourceType !== entry.sourceType) {
        provenance.delete(name);
        ambiguous.add(name);
      }
    }
  }
  return { provenance, ambiguous };
}

export function readProjectProvenance({
  projectRoot,
  env = process.env,
  fs = defaultFs,
} = {}) {
  const local = reconcileTier([
    join(projectRoot, "skills-lock.json"),
    join(projectRoot, ".agents", ".skill-lock.json"),
  ], { projectRoot, fs });
  const globalPaths = [];
  if (env.XDG_STATE_HOME) globalPaths.push(join(env.XDG_STATE_HOME, "skills", ".skill-lock.json"));
  if (env.HOME) globalPaths.push(join(env.HOME, ".agents", ".skill-lock.json"));
  const global = reconcileTier(globalPaths, { projectRoot, fs });

  for (const name of local.ambiguous) global.provenance.delete(name);
  for (const [name, entry] of local.provenance) global.provenance.set(name, entry);
  return global.provenance;
}

export async function loadInstalledState({
  projectRoot,
  env = process.env,
  listInstalled = ({ cwd }) => listInstalledSkills({ cwd }),
  fs = defaultFs,
} = {}) {
  let actual;
  try {
    actual = await listInstalled({ cwd: projectRoot });
  } catch (cause) {
    throw new InstalledStateError(`Could not list installed skills in ${projectRoot}`, { cause });
  }
  const provenance = readProjectProvenance({ projectRoot, env, fs });
  return new Map(actual.map((skill) => {
    const entry = provenance.get(skill.name);
    return [skill.name, {
      name: skill.name,
      path: skill.path,
      agents: [...skill.agents],
      source: entry?.source ?? null,
      provenance: entry ? "tracked" : "untracked",
    }];
  }));
}
