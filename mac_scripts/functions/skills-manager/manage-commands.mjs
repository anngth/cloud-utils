import { removeSourceAt, resolveSourceToken, upsertSource } from "./catalog.mjs";
import { canonicalizeSource, redactSource } from "./source-id.mjs";

class CommandUsageError extends Error {}

function parseOptions(args) {
  const parsed = {
    positionals: [],
    skills: [],
    all: false,
    noSkills: false,
    yes: false,
  };
  const valueFlags = new Map([
    ["-k", "skills"],
    ["--skill", "skills"],
  ]);
  const booleanFlags = new Map([
    ["-a", "all"],
    ["--all", "all"],
    ["-n", "noSkills"],
    ["--no-skills", "noSkills"],
    ["-y", "yes"],
    ["--yes", "yes"],
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

function rejectOptions(parsed, allowed = []) {
  const allow = new Set(allowed);
  if (parsed.skills.length > 0 && !allow.has("skills")) throw new CommandUsageError("Unexpected --skill");
  if (parsed.all && !allow.has("all")) throw new CommandUsageError("Unexpected --all");
  if (parsed.noSkills && !allow.has("noSkills")) throw new CommandUsageError("Unexpected --no-skills");
  if (parsed.yes && !allow.has("yes")) throw new CommandUsageError("Unexpected --yes");
}

export function validateManagementCommandGrammar(family, args) {
  if (family !== "source") {
    throw new CommandUsageError(`${family} commands have been removed`);
  }
  const [action, ...rest] = args;
  if (action === "add") {
    const parsed = parseOptions(rest);
    requirePositionals(parsed, 1, "skm source add <source> [-k skill...] [-a|--all] [-n|--no-skills] [-y]");
    rejectOptions(parsed, ["skills", "all", "noSkills", "yes"]);
    return;
  }
  if (action === "remove") {
    const parsed = parseOptions(rest);
    requirePositionals(parsed, 1, "skm source remove <source|index>");
    rejectOptions(parsed);
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

async function sourceSelection(parsed, source, context, { initial = [] } = {}) {
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
    available.map(({ name, description }) => ({
      kind: "skill",
      value: name,
      label: name,
      hint: description,
    })),
    {
      initial,
      multiple: true,
      title: `Select skills from ${redactSource(source)}`,
    },
  );
  return { ...result, available };
}

function currentProjectRoot(context) {
  return context.resolveProjectRoot({ cwd: context.cwd });
}

function installedSkillsFromSource(installedState, source) {
  return [...installedState.values()].filter((entry) => entry.source === source);
}

async function runSourceAdd(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm source add <source> [-k skill...] [-a|--all] [-n|--no-skills] [-y]");
  rejectOptions(parsed, ["skills", "all", "noSkills", "yes"]);
  const source = canonicalizeSource(parsed.positionals[0], { cwd: context.cwd });
  const existing = context.config.catalog.sources.find((entry) => entry.source === source);
  const selection = await sourceSelection(parsed, source, context, {
    initial: existing?.skills ?? [],
  });
  if (selection.type !== "submit") return 0;
  const skills = [...new Set(selection.selected)];
  const next = upsertSource(context.config.catalog, source, skills, { cwd: context.cwd });
  context.writeCatalog(context.paths, next);
  context.ui.sourceChanged({
    action: existing ? "edited" : "added",
    profile: null,
    source,
    skills,
  });
  return 0;
}

async function runSourceRemove(args, context) {
  const parsed = parseOptions(args);
  requirePositionals(parsed, 1, "skm source remove <source|index>");
  rejectOptions(parsed);
  const { index, entry } = resolveSourceToken(
    context.config.catalog,
    parsed.positionals[0],
    { cwd: context.cwd },
  );
  const installed = installedSkillsFromSource(
    await context.loadInstalledState({ projectRoot: currentProjectRoot(context) }),
    entry.source,
  );
  if (installed.length > 0) {
    throw new CommandUsageError(
      `Cannot remove source ${redactSource(entry.source)}: installed skills: ${installed.map(({ name }) => name).join(", ")}`,
    );
  }
  const next = removeSourceAt(context.config.catalog, index);
  context.writeCatalog(context.paths, next);
  context.ui.sourceChanged({
    action: "removed",
    profile: null,
    source: entry.source,
    skills: [],
  });
  return 0;
}

export async function runSourceCommand(args, context) {
  const [action, ...rest] = args;
  try {
    if (action === "add") return await runSourceAdd(rest, context);
    if (action === "remove") return await runSourceRemove(rest, context);
    context.ui.error(`Unknown source command: ${action ?? ""}`);
    return 1;
  } catch (error) {
    return reportError(context, error);
  }
}

async function removedCommand(context, family) {
  return reportError(context, new CommandUsageError(`${family} commands have been removed`));
}

export async function runProfileCommand(args, context) {
  return removedCommand(context, "Profile");
}

export async function runSkillCommand(args, context) {
  return removedCommand(context, "Skill");
}

export async function runProjectCommand(args, context) {
  return removedCommand(context, "Project");
}
