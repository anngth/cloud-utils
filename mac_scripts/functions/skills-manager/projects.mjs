import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";

export class ProjectConfigError extends Error {}

export function resolveProjectRoot({
  cwd = process.cwd(),
  execFile = (file, args, options) => execFileSync(file, args, options),
  realpath = realpathSync,
} = {}) {
  try {
    const root = execFile("git", ["rev-parse", "--show-toplevel"], {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    return realpath(root);
  } catch {
    return realpath(cwd);
  }
}

export function validateProjectsDocument(value, profileNames) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.version !== 1) {
    throw new ProjectConfigError("projects.json must have version 1");
  }
  if (!Array.isArray(value.projects)) throw new ProjectConfigError("projects must be an array");
  const next = structuredClone(value);
  const roots = new Set();
  for (const project of next.projects) {
    if (!project || typeof project.root !== "string" || !project.root.startsWith("/")) {
      throw new ProjectConfigError("Project root must be an absolute path");
    }
    if (roots.has(project.root)) throw new ProjectConfigError(`Duplicate project: ${project.root}`);
    roots.add(project.root);
    if (!Array.isArray(project.profiles) || project.profiles.length === 0) {
      throw new ProjectConfigError(`Project must link at least one profile: ${project.root}`);
    }
    if (new Set(project.profiles).size !== project.profiles.length) {
      throw new ProjectConfigError(`Duplicate profile link: ${project.root}`);
    }
    for (const name of project.profiles) {
      if (!profileNames.has(name)) throw new ProjectConfigError(`Missing profile: ${name}`);
    }
  }
  next.projects.sort((a, b) => a.root.localeCompare(b.root));
  return next;
}

export function linkProjectProfiles(document, root, requested, profileNames) {
  for (const name of requested) {
    if (!profileNames.has(name)) throw new ProjectConfigError(`Profile not found: ${name}`);
  }
  const next = structuredClone(document);
  let project = next.projects.find((item) => item.root === root);
  if (!project) {
    project = { root, profiles: [] };
    next.projects.push(project);
  }
  for (const name of requested) {
    if (!project.profiles.includes(name)) project.profiles.push(name);
  }
  next.projects.sort((a, b) => a.root.localeCompare(b.root));
  return validateProjectsDocument(next, profileNames);
}

export function unlinkProjectProfiles(document, root, requested) {
  const next = structuredClone(document);
  const project = next.projects.find((item) => item.root === root);
  if (!project) return next;
  const remove = new Set(requested);
  project.profiles = project.profiles.filter((name) => !remove.has(name));
  next.projects = next.projects.filter((item) => item.profiles.length > 0);
  return next;
}

export function removeProject(document, root) {
  return { ...structuredClone(document), projects: document.projects.filter((item) => item.root !== root) };
}

export function renameProjectProfileRefs(document, oldName, newName) {
  const next = structuredClone(document);
  for (const project of next.projects) {
    project.profiles = project.profiles.map((name) => name === oldName ? newName : name);
  }
  return next;
}

export function removeProjectProfileRefs(document, name) {
  const next = structuredClone(document);
  for (const project of next.projects) {
    project.profiles = project.profiles.filter((item) => item !== name);
  }
  next.projects = next.projects.filter((project) => project.profiles.length > 0);
  return next;
}
