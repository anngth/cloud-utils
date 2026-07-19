import { existsSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import {
  addProfileSkills,
  addProfileSource,
  createProfile,
  getProfile,
  removeProfile,
  removeProfileSkills,
  removeProfileSource,
  renameProfile,
  replaceProfileSourceSkills,
} from "./profiles.mjs";
import {
  linkProjectProfiles,
  removeProject,
  removeProjectProfileRefs,
  renameProjectProfileRefs,
  unlinkProjectProfiles,
} from "./projects.mjs";
import { canonicalizeSource, redactSource } from "./source-id.mjs";

class CommandUsageError extends Error {}

function parseOptions(args) {
  const parsed = {
    positionals: [],
    profile: null,
    source: null,
    skills: [],
    all: false,
    noSkills: false,
    force: false,
  };
  const valueFlags = new Map([
    ["-p", "profile"],
    ["--profile", "profile"],
    ["-s", "source"],
    ["--source", "source"],
    ["-k", "skills"],
    ["--skill", "skills"],
  ]);
  const booleanFlags = new Map([
    ["-a", "all"],
    ["--all", "all"],
    ["-n", "noSkills"],
    ["--no-skills", "noSkills"],
    ["-f", "force"],
    ["--force", "force"],
  ]);

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (valueFlags.has(arg)) {
      const key = valueFlags.get(arg);
      const value = args[index + 1];
      if (value === undefined || value.startsWith("-")) {
        throw new CommandUsageError(`Missing value for ${arg}`);
      }
      index += 1;
      if (key === "skills") parsed.skills.push(value);
      else {
        if (parsed[key] !== null) throw new CommandUsageError(`Repeated option: ${arg}`);
        parsed[key] = value;
      }
      continue;
    }
    if (booleanFlags.has(arg)) {
      parsed[booleanFlags.get(arg)] = true;
      continue;
    }
    if (arg.startsWith("-")) throw new CommandUsageError(`Unknown option: ${arg}`);
    parsed.positionals.push(arg);
  }

  const sourceModes = Number(parsed.skills.length > 0) + Number(parsed.all) + Number(parsed.noSkills);
  if (sourceModes > 1) {
    throw new CommandUsageError("--skill, --all, and --no-skills are mutually exclusive");
  }
  return parsed;
}

function requirePositionals(parsed, count, usage) {
  if (parsed.positionals.length !== count) throw new CommandUsageError(`Usage: ${usage}`);
}

function requireAtLeastOne(parsed, usage) {
  if (parsed.positionals.length === 0) throw new CommandUsageError(`Usage: ${usage}`);
}

function requireAtMostOne(parsed, usage) {
  if (parsed.positionals.length > 1) throw new CommandUsageError(`Usage: ${usage}`);
}

function rejectOptions(parsed, allowed = []) {
  const allow = new Set(allowed);
  if (parsed.profile !== null && !allow.has("profile")) throw new CommandUsageError("Unexpected --profile");
  if (parsed.source !== null && !allow.has("source")) throw new CommandUsageError("Unexpected --source");
  if (parsed.skills.length > 0 && !allow.has("skills")) throw new CommandUsageError("Unexpected --skill");
  if (parsed.all && !allow.has("all")) throw new CommandUsageError("Unexpected --all");
  if (parsed.noSkills && !allow.has("noSkills")) throw new CommandUsageError("Unexpected --no-skills");
  if (parsed.force && !allow.has("force")) throw new CommandUsageError("Unexpected --force");
}

export function validateManagementCommandGrammar(family, args) {
  const [action, ...rest] = args;
  if (family === "profile") {
    if (action === "list") { const parsed = parseOptions(rest); requirePositionals(parsed, 0, "skm profile list"); rejectOptions(parsed); }
    if (action === "show") { const parsed = parseOptions(rest); requirePositionals(parsed, 1, "skm profile show <profile>"); rejectOptions(parsed); }
    if (action === "create") { const parsed = parseOptions(rest); requirePositionals(parsed, 1, "skm profile create <profile>"); rejectOptions(parsed); }
    if (action === "rename") { const parsed = parseOptions(rest); requirePositionals(parsed, 2, "skm profile rename <old> <new>"); rejectOptions(parsed); }
    if (action === "remove") { const parsed = parseOptions(rest); requirePositionals(parsed, 1, "skm profile remove <profile> [--force]"); rejectOptions(parsed, ["force"]); }
    return;
  }
  if (family === "source") {
    if (action === "add") { const parsed = parseOptions(rest); requirePositionals(parsed, 1, "skm source add <source> [(-p | --profile) <profile>]"); rejectOptions(parsed, ["profile", "skills", "all", "noSkills"]); }
    if (action === "edit") { const parsed = parseOptions(rest); requirePositionals(parsed, 1, "skm source edit <source> [(-p | --profile) <profile>]"); rejectOptions(parsed, ["profile"]); }
    if (action === "remove") { const parsed = parseOptions(rest); requirePositionals(parsed, 1, "skm source remove <source> [(-p | --profile) <profile>]"); rejectOptions(parsed, ["profile"]); }
    if (action === "show") { const parsed = parseOptions(rest); requirePositionals(parsed, 1, "skm source show <source>"); rejectOptions(parsed); }
    return;
  }
  if (family === "skill") {
    if (!["add", "remove"].includes(action)) return;
    const parsed = parseOptions(rest);
    requireAtLeastOne(parsed, `skm skill ${action} <skill...> --source <source> --profile <profile>`);
    rejectOptions(parsed, ["profile", "source"]);
    if (parsed.profile === null) throw new CommandUsageError("Missing --profile");
    if (parsed.source === null) throw new CommandUsageError("Missing --source");
    return;
  }
  if (family === "project") {
    if (action === "link") { const parsed = parseOptions(rest); requireAtLeastOne(parsed, "skm project link <profile...>"); rejectOptions(parsed); }
    if (action === "unlink") { const parsed = parseOptions(rest); rejectOptions(parsed); }
    if (action === "show") { const parsed = parseOptions(rest); requirePositionals(parsed, 0, "skm project show"); rejectOptions(parsed); }
    if (action === "list") { const parsed = parseOptions(rest); requirePositionals(parsed, 0, "skm project list"); rejectOptions(parsed); }
    if (action === "remove") { const parsed = parseOptions(rest); requireAtMostOne(parsed, "skm project remove [project-path]"); rejectOptions(parsed); }
  }
}

function reportError(context, error) {
  context.ui.error(error instanceof Error ? error.message : String(error));
  return 1;
}

async function selectedValues(context, items, {
  initial = [],
  multiple = true,
  title,
} = {}) {
  if (!context.stdin?.isTTY || !context.stdout?.isTTY) {
    throw new CommandUsageError(`${title} requires an interactive terminal`);
  }
  return context.selectItems({
    items,
    initial,
    multiple,
    title,
    stdin: context.stdin,
    stdout: context.stdout,
    ui: context.ui,
  });
}

async function resolveProfileName(parsed, context) {
  if (parsed.profile !== null) {
    getProfile(context.config.profiles, parsed.profile);
    return { type: "submit", value: parsed.profile };
  }
  const result = await selectedValues(
    context,
    context.config.profiles.profiles.map(({ name }) => ({ value: name, label: name })),
    { multiple: false, title: "Select a profile" },
  );
  if (result.type !== "submit") return { type: "cancel" };
  const value = result.selected[0];
  getProfile(context.config.profiles, value);
  return { type: "submit", value };
}

function profileNames(document) {
  return new Set(document.profiles.map(({ name }) => name));
}

function currentProjectRoot(context) {
  return context.resolveProjectRoot({ cwd: context.cwd });
}

function profileSourceIdentity(rawSource, profile, context) {
  const value = String(rawSource).trim();
  if (isAbsolute(value) && profile.sources.some((entry) => entry.source === value)) return value;
  return canonicalizeSource(value, { cwd: context.cwd });
}

async function runProfileList(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 0, "skm profile list");
  rejectOptions(parsed);
  context.ui.profileList({
    profiles: context.config.profiles.profiles,
    projects: context.config.projects.projects,
  });
  return 0;
}

async function runProfileShow(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm profile show <profile>");
  rejectOptions(parsed);
  const profile = getProfile(context.config.profiles, parsed.positionals[0]);
  const projects = context.config.projects.projects.filter(({ profiles }) => profiles.includes(profile.name));
  context.ui.profileShow({ profile, projects });
  return 0;
}

async function runProfileCreate(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm profile create <profile>");
  rejectOptions(parsed);
  const name = parsed.positionals[0];
  const next = createProfile(context.config.profiles, name);
  context.writeProfiles(context.paths, next);
  context.ui.profileChanged({ action: "created", name });
  return 0;
}

async function runProfileRename(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 2, "skm profile rename <old> <new>");
  rejectOptions(parsed);
  const [oldName, newName] = parsed.positionals;
  const profiles = renameProfile(context.config.profiles, oldName, newName);
  const projects = renameProjectProfileRefs(context.config.projects, oldName, newName);
  context.writeConfigTransaction(context.paths, { profiles, projects });
  context.ui.profileChanged({ action: "renamed", name: newName, oldName });
  return 0;
}

async function runProfileRemove(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm profile remove <profile> [--force]");
  rejectOptions(parsed, ["force"]);
  const name = parsed.positionals[0];
  getProfile(context.config.profiles, name);
  const profiles = removeProfile(context.config.profiles, name);
  const linked = context.config.projects.projects.filter(({ profiles }) => profiles.includes(name));
  if (linked.length > 0 && !parsed.force) {
    throw new CommandUsageError(
      `Profile ${name} is linked to: ${linked.map(({ root }) => root).join(", ")}. Use --force to unlink it.`,
    );
  }
  if (parsed.force) {
    const projects = removeProjectProfileRefs(context.config.projects, name);
    context.writeConfigTransaction(context.paths, { profiles, projects });
  } else {
    context.writeProfiles(context.paths, profiles);
  }
  context.ui.profileChanged({ action: "removed", name, projects: linked });
  return 0;
}

export async function runProfileCommand(args, context) {
  const [action, ...rest] = args;
  try {
    if (action === "list") return await runProfileList(rest, context);
    if (action === "show") return await runProfileShow(rest, context);
    if (action === "create") return await runProfileCreate(rest, context);
    if (action === "rename") return await runProfileRename(rest, context);
    if (action === "remove") return await runProfileRemove(rest, context);
    context.ui.error(`Unknown profile command: ${action ?? ""}`);
    return 1;
  } catch (error) {
    return reportError(context, error);
  }
}

async function discover(source, context) {
  return context.discoverAvailableSkills(source, { cwd: context.cwd });
}

function verifyRequestedSkills(requested, available) {
  const names = new Set(available.map(({ name }) => name));
  const missing = requested.filter((name) => !names.has(name));
  if (missing.length > 0) {
    throw new CommandUsageError(`Skills not found in source: ${missing.join(", ")}`);
  }
}

async function sourceSelection(parsed, source, context) {
  if (parsed.noSkills) return { type: "submit", selected: [] };
  const available = await discover(source, context);
  if (parsed.all) return { type: "submit", selected: available.map(({ name }) => name), available };
  if (parsed.skills.length > 0) {
    const requested = [...new Set(parsed.skills)];
    verifyRequestedSkills(requested, available);
    return { type: "submit", selected: requested, available };
  }
  const result = await selectedValues(
    context,
    available.map(({ name, description }) => ({ value: name, label: name, hint: description })),
    { multiple: true, title: `Select skills from ${redactSource(source)}` },
  );
  return { ...result, available };
}

async function runSourceAdd(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm source add <source> [(-p | --profile) <profile>]");
  rejectOptions(parsed, ["profile", "skills", "all", "noSkills"]);
  const source = canonicalizeSource(parsed.positionals[0], { cwd: context.cwd });
  const chosenProfile = await resolveProfileName(parsed, context);
  if (chosenProfile.type === "cancel") return 0;
  const profile = getProfile(context.config.profiles, chosenProfile.value);
  if (profile.sources.some((entry) => entry.source === source)) {
    throw new CommandUsageError(
      `Source already exists in ${chosenProfile.value}: ${redactSource(source)}. Use source edit or skill add.`,
    );
  }
  const selection = await sourceSelection(parsed, source, context);
  if (selection.type !== "submit") return 0;
  const next = addProfileSource(context.config.profiles, chosenProfile.value, {
    source,
    skills: [...new Set(selection.selected)],
  });
  context.writeProfiles(context.paths, next);
  context.ui.sourceChanged({
    action: "added",
    profile: chosenProfile.value,
    source,
    skills: [...new Set(selection.selected)],
  });
  return 0;
}

async function runSourceEdit(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm source edit <source> [(-p | --profile) <profile>]");
  rejectOptions(parsed, ["profile"]);
  const chosenProfile = await resolveProfileName(parsed, context);
  if (chosenProfile.type === "cancel") return 0;
  const profile = getProfile(context.config.profiles, chosenProfile.value);
  const source = profileSourceIdentity(parsed.positionals[0], profile, context);
  const entry = profile.sources
    .find((item) => item.source === source);
  if (!entry) throw new CommandUsageError(`Source not found in ${chosenProfile.value}: ${redactSource(source)}`);

  const available = await discover(source, context);
  const found = new Set(available.map(({ name }) => name));
  const items = [
    ...entry.skills
      .filter((name) => !found.has(name))
      .map((name) => ({ value: name, label: name, hint: "saved; unavailable upstream" })),
    ...available.map(({ name, description }) => ({ value: name, label: name, hint: description })),
  ];
  const selection = await selectedValues(context, items, {
    initial: entry.skills,
    multiple: true,
    title: `Edit skills from ${redactSource(source)}`,
  });
  if (selection.type !== "submit") return 0;
  const skills = [...new Set(selection.selected)];
  const next = replaceProfileSourceSkills(context.config.profiles, chosenProfile.value, source, skills);
  context.writeProfiles(context.paths, next);
  context.ui.sourceChanged({ action: "edited", profile: chosenProfile.value, source, skills });
  return 0;
}

async function runSourceRemove(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm source remove <source> [(-p | --profile) <profile>]");
  rejectOptions(parsed, ["profile"]);
  const chosenProfile = await resolveProfileName(parsed, context);
  if (chosenProfile.type === "cancel") return 0;
  const profile = getProfile(context.config.profiles, chosenProfile.value);
  const source = profileSourceIdentity(parsed.positionals[0], profile, context);
  const next = removeProfileSource(context.config.profiles, chosenProfile.value, source);
  context.writeProfiles(context.paths, next);
  context.ui.sourceChanged({ action: "removed", profile: chosenProfile.value, source, skills: [] });
  return 0;
}

async function runSourceShow(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm source show <source>");
  rejectOptions(parsed);
  const source = canonicalizeSource(parsed.positionals[0], { cwd: context.cwd });
  const available = await discover(source, context);
  context.ui.sourceChanged({
    action: "shown",
    profile: null,
    source,
    skills: available.map(({ name }) => name),
    available,
  });
  return 0;
}

export async function runSourceCommand(args, context) {
  const [action, ...rest] = args;
  try {
    if (action === "add") return await runSourceAdd(rest, context);
    if (action === "edit") return await runSourceEdit(rest, context);
    if (action === "remove") return await runSourceRemove(rest, context);
    if (action === "show") return await runSourceShow(rest, context);
    context.ui.error(`Unknown source command: ${action ?? ""}`);
    return 1;
  } catch (error) {
    return reportError(context, error);
  }
}

async function runSkillAdd(args, context) {
  const parsed = parseOptions(args);
  requireAtLeastOne(parsed, "skm skill add <skill...> --source <source> --profile <profile>");
  rejectOptions(parsed, ["profile", "source"]);
  if (parsed.profile === null) throw new CommandUsageError("Missing --profile");
  if (parsed.source === null) throw new CommandUsageError("Missing --source");
  const profile = getProfile(context.config.profiles, parsed.profile);
  const source = profileSourceIdentity(parsed.source, profile, context);
  if (!profile.sources.some((entry) => entry.source === source)) {
    throw new CommandUsageError(`Source not found in ${parsed.profile}: ${redactSource(source)}`);
  }
  const requested = [...new Set(parsed.positionals)];
  const available = await discover(source, context);
  verifyRequestedSkills(requested, available);
  const next = addProfileSkills(context.config.profiles, parsed.profile, source, requested);
  context.writeProfiles(context.paths, next);
  context.ui.skillChanged({ action: "added", profile: parsed.profile, source, skills: requested });
  return 0;
}

async function runSkillRemove(args, context) {
  const parsed = parseOptions(args);
  requireAtLeastOne(parsed, "skm skill remove <skill...> --source <source> --profile <profile>");
  rejectOptions(parsed, ["profile", "source"]);
  if (parsed.profile === null) throw new CommandUsageError("Missing --profile");
  if (parsed.source === null) throw new CommandUsageError("Missing --source");
  const profile = getProfile(context.config.profiles, parsed.profile);
  const source = profileSourceIdentity(parsed.source, profile, context);
  const entry = profile.sources
    .find((item) => item.source === source);
  if (!entry) throw new CommandUsageError(`Source not found in ${parsed.profile}: ${redactSource(source)}`);
  const requested = [...new Set(parsed.positionals)];
  const missing = requested.filter((name) => !entry.skills.includes(name));
  if (missing.length > 0) context.ui.warn(`Skills not selected in profile: ${missing.join(", ")}`);
  const next = removeProfileSkills(context.config.profiles, parsed.profile, source, requested);
  context.writeProfiles(context.paths, next);
  context.ui.skillChanged({
    action: "removed",
    profile: parsed.profile,
    source,
    skills: requested.filter((name) => entry.skills.includes(name)),
    missing,
  });
  return 0;
}

export async function runSkillCommand(args, context) {
  const [action, ...rest] = args;
  try {
    if (action === "add") return await runSkillAdd(rest, context);
    if (action === "remove") return await runSkillRemove(rest, context);
    context.ui.error(`Unknown skill command: ${action ?? ""}`);
    return 1;
  } catch (error) {
    return reportError(context, error);
  }
}

async function runProjectLink(args, context) {
  const parsed = parseOptions(args);
  requireAtLeastOne(parsed, "skm project link <profile...>");
  rejectOptions(parsed);
  const root = currentProjectRoot(context);
  const requested = [...new Set(parsed.positionals)];
  const next = linkProjectProfiles(
    context.config.projects,
    root,
    requested,
    profileNames(context.config.profiles),
  );
  context.writeProjects(context.paths, context.config.profiles, next);
  context.ui.projectChanged({ action: "linked", root, profiles: requested });
  return 0;
}

async function runProjectUnlink(args, context) {
  const parsed = parseOptions(args);
  rejectOptions(parsed);
  const root = currentProjectRoot(context);
  const project = context.config.projects.projects.find((item) => item.root === root);
  let requested = [...new Set(parsed.positionals)];
  if (requested.length === 0) {
    if (!project) throw new CommandUsageError(`Project is not linked: ${root}`);
    const result = await selectedValues(
      context,
      project.profiles.map((name) => ({ value: name, label: name })),
      { initial: project.profiles, multiple: true, title: "Select profiles to unlink" },
    );
    if (result.type !== "submit") return 0;
    requested = [...new Set(result.selected)];
  }
  const next = unlinkProjectProfiles(context.config.projects, root, requested);
  context.writeProjects(context.paths, context.config.profiles, next);
  context.ui.projectChanged({ action: "unlinked", root, profiles: requested });
  return 0;
}

async function runProjectShow(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 0, "skm project show");
  rejectOptions(parsed);
  const root = currentProjectRoot(context);
  const project = context.config.projects.projects.find((item) => item.root === root);
  context.ui.projectShow({ root, profiles: project?.profiles ?? [] });
  return 0;
}

async function runProjectList(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 0, "skm project list");
  rejectOptions(parsed);
  const pathExists = context.pathExists ?? existsSync;
  context.ui.projectList({
    projects: context.config.projects.projects.map((project) => ({
      ...project,
      stale: !pathExists(project.root),
    })),
  });
  return 0;
}

async function runProjectRemove(args, context) {
  const parsed = parseOptions(args);
  requireAtMostOne(parsed, "skm project remove [project-path]");
  rejectOptions(parsed);
  const rawRoot = parsed.positionals[0];
  const root = rawRoot === undefined
    ? currentProjectRoot(context)
    : isAbsolute(rawRoot) ? resolve(rawRoot) : resolve(context.cwd, rawRoot);
  if (!context.config.projects.projects.some((item) => item.root === root)) {
    throw new CommandUsageError(`Project not found: ${root}`);
  }
  const next = removeProject(context.config.projects, root);
  context.writeProjects(context.paths, context.config.profiles, next);
  context.ui.projectChanged({ action: "removed", root, profiles: [] });
  return 0;
}

export async function runProjectCommand(args, context) {
  const [action, ...rest] = args;
  try {
    if (action === "link") return await runProjectLink(rest, context);
    if (action === "unlink") return await runProjectUnlink(rest, context);
    if (action === "show") return await runProjectShow(rest, context);
    if (action === "list") return await runProjectList(rest, context);
    if (action === "remove") return await runProjectRemove(rest, context);
    context.ui.error(`Unknown project command: ${action ?? ""}`);
    return 1;
  } catch (error) {
    return reportError(context, error);
  }
}
