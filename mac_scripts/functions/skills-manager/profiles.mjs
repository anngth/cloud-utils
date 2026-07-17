export class ProfileConfigError extends Error {}

const clone = (value) => structuredClone(value);

function validateProfileName(name) {
  if (typeof name !== "string" || name.trim() === "") {
    throw new ProfileConfigError("Profile name must not be empty");
  }
}

export function getProfile(document, name) {
  const profile = document.profiles.find((item) => item.name === name);
  if (!profile) throw new ProfileConfigError(`Profile not found: ${name}`);
  return profile;
}

export function validateProfilesDocument(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.version !== 1) {
    throw new ProfileConfigError("profiles.json must have version 1");
  }
  if (!Array.isArray(value.profiles) || value.profiles.length === 0) {
    throw new ProfileConfigError("profiles.json must contain at least one profile");
  }
  const next = clone(value);
  const names = new Set();
  for (const profile of next.profiles) {
    validateProfileName(profile?.name);
    if (names.has(profile.name)) throw new ProfileConfigError(`Duplicate profile: ${profile.name}`);
    names.add(profile.name);
    if (!Array.isArray(profile.sources)) throw new ProfileConfigError(`Invalid sources: ${profile.name}`);
    const sources = new Set();
    const skillOwners = new Map();
    for (const entry of profile.sources) {
      if (!entry || typeof entry.source !== "string" || !Array.isArray(entry.skills)) {
        throw new ProfileConfigError(`Invalid source in profile: ${profile.name}`);
      }
      if (sources.has(entry.source)) throw new ProfileConfigError(`Duplicate source: ${entry.source}`);
      sources.add(entry.source);
      const skills = new Set();
      for (const skill of entry.skills) {
        if (typeof skill !== "string" || skill.trim() === "") {
          throw new ProfileConfigError(`Invalid skill in source: ${entry.source}`);
        }
        if (skills.has(skill)) throw new ProfileConfigError(`Duplicate skill: ${skill}`);
        skills.add(skill);
        const owner = skillOwners.get(skill);
        if (owner && owner !== entry.source) {
          throw new ProfileConfigError(`${skill} is selected from a different source: ${owner}`);
        }
        skillOwners.set(skill, entry.source);
      }
    }
  }
  next.profiles.sort((a, b) => a.name.localeCompare(b.name));
  for (const profile of next.profiles) {
    profile.sources.sort((a, b) => a.source.localeCompare(b.source));
  }
  return next;
}

export function createProfile(document, name) {
  validateProfileName(name);
  if (document.profiles.some((item) => item.name === name)) {
    throw new ProfileConfigError(`Profile already exists: ${name}`);
  }
  const next = clone(document);
  next.profiles.push({ name, sources: [] });
  next.profiles.sort((a, b) => a.name.localeCompare(b.name));
  return validateProfilesDocument(next);
}

export function renameProfile(document, oldName, newName) {
  validateProfileName(newName);
  if (document.profiles.some((item) => item.name === newName)) {
    throw new ProfileConfigError(`Profile already exists: ${newName}`);
  }
  const next = clone(document);
  getProfile(next, oldName).name = newName;
  return validateProfilesDocument(next);
}

export function removeProfile(document, name) {
  if (document.profiles.length === 1) throw new ProfileConfigError("Cannot remove the final profile");
  getProfile(document, name);
  return validateProfilesDocument({
    ...clone(document),
    profiles: document.profiles.filter((item) => item.name !== name),
  });
}

export function addProfileSource(document, profileName, entry) {
  const next = clone(document);
  const profile = getProfile(next, profileName);
  if (profile.sources.some((item) => item.source === entry.source)) {
    throw new ProfileConfigError(`Source already exists in ${profileName}: ${entry.source}`);
  }
  profile.sources.push({ source: entry.source, skills: [...entry.skills] });
  profile.sources.sort((a, b) => a.source.localeCompare(b.source));
  return validateProfilesDocument(next);
}

export function replaceProfileSourceSkills(document, profileName, source, skills) {
  const next = clone(document);
  const entry = getProfile(next, profileName).sources.find((item) => item.source === source);
  if (!entry) throw new ProfileConfigError(`Source not found in ${profileName}: ${source}`);
  entry.skills = [...skills];
  return validateProfilesDocument(next);
}

export function removeProfileSource(document, profileName, source) {
  const next = clone(document);
  const profile = getProfile(next, profileName);
  if (!profile.sources.some((item) => item.source === source)) {
    throw new ProfileConfigError(`Source not found in ${profileName}: ${source}`);
  }
  profile.sources = profile.sources.filter((item) => item.source !== source);
  return validateProfilesDocument(next);
}

export function addProfileSkills(document, profileName, source, requested) {
  const current = getProfile(document, profileName).sources.find((item) => item.source === source);
  if (!current) throw new ProfileConfigError(`Source not found in ${profileName}: ${source}`);
  return replaceProfileSourceSkills(document, profileName, source, [
    ...current.skills,
    ...requested.filter((skill) => !current.skills.includes(skill)),
  ]);
}

export function removeProfileSkills(document, profileName, source, requested) {
  const current = getProfile(document, profileName).sources.find((item) => item.source === source);
  if (!current) throw new ProfileConfigError(`Source not found in ${profileName}: ${source}`);
  const remove = new Set(requested);
  return replaceProfileSourceSkills(
    document,
    profileName,
    source,
    current.skills.filter((skill) => !remove.has(skill)),
  );
}
