import { canonicalizeSource, redactSource } from "./source-id.mjs";

export const EMPTY_CATALOG = { version: 1, sources: [] };

export class CatalogError extends Error {}

const clone = (value) => structuredClone(value);

function validateSkillName(skill) {
  if (typeof skill !== "string" || skill.trim() === "") {
    throw new CatalogError("Invalid skill name");
  }
}

function enforceSkillUniquenessAcrossSources(sources) {
  const skillOwners = new Map();
  for (const entry of sources) {
    for (const skill of entry.skills) {
      const owner = skillOwners.get(skill);
      if (owner && owner !== entry.source) {
        throw new CatalogError(`${skill} is selected from a different source: ${owner}`);
      }
      skillOwners.set(skill, entry.source);
    }
  }
}

export function validateCatalogDocument(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.version !== 1) {
    throw new CatalogError("sources.json must have version 1");
  }
  if (!Array.isArray(value.sources)) {
    throw new CatalogError("sources.json must contain a sources array");
  }
  const next = clone(value);
  const sourceIds = new Set();
  for (const entry of next.sources) {
    if (!entry || typeof entry.source !== "string" || !Array.isArray(entry.skills)) {
      throw new CatalogError("Invalid catalog source entry");
    }
    if (sourceIds.has(entry.source)) {
      throw new CatalogError(`Duplicate source: ${entry.source}`);
    }
    sourceIds.add(entry.source);
    const skills = new Set();
    for (const skill of entry.skills) {
      validateSkillName(skill);
      if (skills.has(skill)) {
        throw new CatalogError(`Duplicate skill: ${skill}`);
      }
      skills.add(skill);
    }
  }
  enforceSkillUniquenessAcrossSources(next.sources);
  return next;
}

export function catalogSkillOwner(document, skillName) {
  for (const entry of document.sources) {
    if (entry.skills.includes(skillName)) {
      return entry.source;
    }
  }
  return null;
}

export function crossSourceSkillConflicts(document, source, skills, { cwd } = {}) {
  const canonical = canonicalizeSource(source, { cwd });
  const conflicts = [];
  for (const skill of skills) {
    const owner = catalogSkillOwner(document, skill);
    if (owner && owner !== canonical) {
      conflicts.push({ skill, ownerSource: owner });
    }
  }
  return conflicts;
}

export function skillOwnershipConflictMessage(conflicts) {
  const details = conflicts.map(({ skill, ownerSource }) => (
    `${skill} (${redactSource(ownerSource)})`
  ));
  return `Skill already in another source: ${details.join("; ")}`;
}

export function resolveSourceToken(document, token, { cwd } = {}) {
  if (/^\d+$/.test(token)) {
    const index = Number(token) - 1;
    if (index < 0 || index >= document.sources.length) {
      throw new CatalogError(`Source index out of range: ${token}`);
    }
    return { index, entry: document.sources[index] };
  }
  const canonical = canonicalizeSource(token, { cwd });
  const index = document.sources.findIndex((entry) => entry.source === canonical);
  if (index === -1) {
    throw new CatalogError(`Source not found: ${canonical}`);
  }
  return { index, entry: document.sources[index] };
}

export function upsertSource(document, source, skills, { cwd } = {}) {
  const canonical = canonicalizeSource(source, { cwd });
  if (!Array.isArray(skills)) {
    throw new CatalogError("skills must be an array");
  }
  const skillList = [];
  const seen = new Set();
  for (const skill of skills) {
    validateSkillName(skill);
    if (seen.has(skill)) {
      throw new CatalogError(`Duplicate skill: ${skill}`);
    }
    seen.add(skill);
    skillList.push(skill);
  }
  const next = clone(document);
  for (const skill of skillList) {
    const owner = catalogSkillOwner(next, skill);
    if (owner && owner !== canonical) {
      throw new CatalogError(`${skill} is selected from a different source: ${owner}`);
    }
  }
  const existingIndex = next.sources.findIndex((entry) => entry.source === canonical);
  if (existingIndex >= 0) {
    next.sources[existingIndex].skills = skillList;
  } else {
    next.sources.push({ source: canonical, skills: skillList });
  }
  return validateCatalogDocument(next);
}

export function removeSourceAt(document, index) {
  if (!Number.isInteger(index) || index < 0 || index >= document.sources.length) {
    throw new CatalogError(`Source index out of range: ${index}`);
  }
  const next = clone(document);
  next.sources.splice(index, 1);
  return validateCatalogDocument(next);
}

export function migrateProfilesToCatalog(profilesDocument) {
  const sources = [];
  const sourceIndex = new Map();
  const skillOwners = new Map();

  for (const profile of profilesDocument.profiles ?? []) {
    for (const entry of profile.sources ?? []) {
      const source = canonicalizeSource(entry.source);
      let catalogEntry;
      if (sourceIndex.has(source)) {
        catalogEntry = sources[sourceIndex.get(source)];
      } else {
        catalogEntry = { source, skills: [] };
        sourceIndex.set(source, sources.length);
        sources.push(catalogEntry);
      }
      for (const skill of entry.skills ?? []) {
        const owner = skillOwners.get(skill);
        if (owner && owner !== source) {
          throw new CatalogError(`${skill} is selected from a different source: ${owner}`);
        }
        skillOwners.set(skill, source);
        if (!catalogEntry.skills.includes(skill)) {
          catalogEntry.skills.push(skill);
        }
      }
    }
  }

  return validateCatalogDocument({ version: 1, sources });
}
