import {
  classifyStatus,
  createInstallPlan,
  createUninstallPlan,
  mergeProfileRequirements,
} from "./planner.mjs";
import { linkProjectProfiles, unlinkProjectProfiles } from "./projects.mjs";

class LifecycleCommandError extends Error {}

function parseLifecycleOptions(args, { install = false, uninstall = false } = {}) {
  const parsed = {
    profileNames: [],
    yes: false,
    force: false,
    dryRun: false,
    keepLink: false,
  };
  const flags = new Map();
  if (install || uninstall) {
    flags.set("--yes", "yes");
    flags.set("--force", "force");
    flags.set("--dry-run", "dryRun");
  }
  if (uninstall) flags.set("--keep-link", "keepLink");

  for (const arg of args) {
    const key = flags.get(arg);
    if (key) {
      parsed[key] = true;
    } else if (arg.startsWith("-")) {
      throw new LifecycleCommandError(`Unknown option: ${arg}`);
    } else if (!parsed.profileNames.includes(arg)) {
      parsed.profileNames.push(arg);
    }
  }
  return parsed;
}

function linkedProfiles(projects, projectRoot) {
  return projects.projects.find(({ root }) => root === projectRoot)?.profiles ?? [];
}

function noProfilesMessage() {
  return "No profiles are linked to this project. Run 'skm project link <profile...>' or name profiles explicitly.";
}

function profileSelectorItems(document) {
  return document.profiles.map(({ name, sources }) => ({
    value: name,
    label: name,
    hint: `${sources.reduce((total, entry) => total + entry.skills.length, 0)} selected skills`,
  }));
}

export async function resolveLifecycleProfiles(parsed, context, {
  projectRoot,
  interactiveInstall = false,
} = {}) {
  if (parsed.profileNames.length > 0) {
    return { type: "submit", profileNames: parsed.profileNames, saveLinks: false };
  }

  const linked = linkedProfiles(context.config.projects, projectRoot);
  if (linked.length > 0) {
    return { type: "submit", profileNames: [...linked], saveLinks: false };
  }

  if (!interactiveInstall || parsed.yes || !context.stdin?.isTTY || !context.stdout?.isTTY) {
    throw new LifecycleCommandError(noProfilesMessage());
  }

  const selection = await context.selectProfiles(profileSelectorItems(context.config.profiles));
  if (selection.type !== "submit") return { type: "cancel" };
  const profileNames = [...new Set(selection.selected)];
  if (profileNames.length === 0) {
    context.ui.warn("No profiles selected");
    return { type: "cancel" };
  }
  const saveLinks = await context.confirmSaveLinks({ projectRoot, profileNames });
  return { type: "submit", profileNames, saveLinks: Boolean(saveLinks) };
}

function reportError(context, error) {
  context.ui.error(error instanceof Error ? error.message : String(error));
  return 1;
}

function statusOk(status) {
  return status.missing.length === 0
    && status.mismatches.length === 0
    && status.untracked.length === 0
    && status.desiredConflicts.length === 0;
}

function desiredConflictMessage(conflicts) {
  const details = conflicts.map(({ skill, sources, profiles }) => (
    `${skill} (${sources.join(" vs ")}; profiles: ${profiles.join(", ")})`
  ));
  return `Conflicting desired skill sources: ${details.join("; ")}`;
}

function selectableInstallItems(plan) {
  return [...plan.install, ...plan.replace].map((item) => ({
    key: item.key,
    value: item.key,
    label: item.skill,
    hint: `${item.source} — ${item.profiles.join(", ")}`,
  }));
}

export async function runStatusCommand(args, context) {
  try {
    const parsed = parseLifecycleOptions(args);
    const projectRoot = context.resolveProjectRoot({ cwd: context.cwd });
    const resolved = await resolveLifecycleProfiles(parsed, context, { projectRoot });
    if (resolved.type !== "submit") return 0;
    const merged = mergeProfileRequirements(context.config.profiles, resolved.profileNames);
    const installed = await context.loadInstalledState({ projectRoot });
    const status = classifyStatus(merged, installed);
    context.ui.status({ projectRoot, profileNames: resolved.profileNames, status });
    return statusOk(status) ? 0 : 1;
  } catch (error) {
    return reportError(context, error);
  }
}

export async function runInstallCommand(args, context) {
  try {
    const parsed = parseLifecycleOptions(args, { install: true });
    const projectRoot = context.resolveProjectRoot({ cwd: context.cwd });
    const resolved = await resolveLifecycleProfiles(parsed, context, {
      projectRoot,
      interactiveInstall: true,
    });
    if (resolved.type !== "submit") return 0;

    const merged = mergeProfileRequirements(context.config.profiles, resolved.profileNames);
    if (merged.requirements.length === 0) {
      throw new LifecycleCommandError("Selected profiles contain no selected skills to install");
    }
    if (merged.desiredConflicts.length > 0) {
      throw new LifecycleCommandError(desiredConflictMessage(merged.desiredConflicts));
    }

    const installed = await context.loadInstalledState({ projectRoot });
    const status = classifyStatus(merged, installed);
    let plan = createInstallPlan(status, { force: parsed.force });

    if (!parsed.yes) {
      const items = selectableInstallItems(plan);
      if (items.length > 0) {
        const selection = await context.selectSkills(items);
        if (selection.type !== "submit") return 0;
        plan = createInstallPlan(status, {
          force: parsed.force,
          selectedKeys: new Set(selection.selected),
        });
      }
    }

    context.ui.installPlan({
      projectRoot,
      profileNames: resolved.profileNames,
      plan,
      dryRun: parsed.dryRun,
    });
    if (parsed.dryRun) return plan.conflicts.length === 0 ? 0 : 1;

    if (!parsed.yes) {
      const confirmed = await context.confirm("Apply this install plan?");
      if (!confirmed) return 0;
    }

    const result = await context.executeInstallPlan(plan, { yes: parsed.yes });
    if (result.ok && resolved.saveLinks) {
      const profileNames = new Set(context.config.profiles.profiles.map(({ name }) => name));
      const projects = linkProjectProfiles(
        context.config.projects,
        projectRoot,
        resolved.profileNames,
        profileNames,
      );
      context.writeProjects(context.paths, context.config.profiles, projects);
    }
    context.ui.executionSummary(result);
    return result.ok ? 0 : 1;
  } catch (error) {
    return reportError(context, error);
  }
}

function retainedByRemainingProfile(plan, remaining) {
  const byKey = new Map(remaining.requirements.map((item) => [item.key, item]));
  return {
    ...plan,
    retain: plan.retain.map((item) => byKey.get(item.key) ?? item),
  };
}

function forcedUninstallNames(plan, installedState) {
  return plan.remove
    .filter((requirement) => {
      const actual = installedState.get(requirement.skill);
      return actual && (
        actual.source == null
        || actual.provenance === "untracked"
        || actual.source !== requirement.source
      );
    })
    .map((item) => item.skill);
}

export async function runUninstallCommand(args, context) {
  try {
    const parsed = parseLifecycleOptions(args, { uninstall: true });
    const projectRoot = context.resolveProjectRoot({ cwd: context.cwd });
    const resolved = await resolveLifecycleProfiles(parsed, context, { projectRoot });
    if (resolved.type !== "submit") return 0;

    const currentLinks = linkedProfiles(context.config.projects, projectRoot);
    const selectedNames = resolved.profileNames;
    const selectedLinks = currentLinks.filter((name) => selectedNames.includes(name));
    const remainingNames = currentLinks.filter((name) => !selectedLinks.includes(name));
    const selected = mergeProfileRequirements(context.config.profiles, selectedNames);
    const remaining = mergeProfileRequirements(context.config.profiles, remainingNames);
    const desiredConflicts = [...selected.desiredConflicts, ...remaining.desiredConflicts];
    if (desiredConflicts.length > 0) {
      throw new LifecycleCommandError(desiredConflictMessage(desiredConflicts));
    }

    const installedState = await context.loadInstalledState({ projectRoot });
    let plan = createUninstallPlan({
      selected,
      remaining,
      installedState,
      force: parsed.force,
      linkedSelected: selectedLinks,
    });
    plan = retainedByRemainingProfile(plan, remaining);

    const forcedNames = parsed.force ? forcedUninstallNames(plan, installedState) : [];
    if (forcedNames.length > 0) {
      context.ui.warn(`--force will remove mismatched or untracked skills: ${forcedNames.join(", ")}`);
    }
    context.ui.uninstallPlan({
      projectRoot,
      profileNames: selectedNames,
      plan,
      dryRun: parsed.dryRun,
      force: parsed.force,
      keepLink: parsed.keepLink,
    });
    if (parsed.dryRun) return plan.conflicts.length === 0 ? 0 : 1;

    if (!parsed.yes) {
      const confirmed = await context.confirm("Apply this uninstall plan?");
      if (!confirmed) return 0;
    }

    const result = await context.executeUninstallPlan(plan, { yes: parsed.yes });
    if (result.ok && !parsed.keepLink && plan.unlinkProfiles.length > 0) {
      const projects = unlinkProjectProfiles(
        context.config.projects,
        projectRoot,
        plan.unlinkProfiles,
      );
      context.writeProjects(context.paths, context.config.profiles, projects);
    }
    context.ui.executionSummary(result, { operation: "uninstall" });
    return result.ok ? 0 : 1;
  } catch (error) {
    return reportError(context, error);
  }
}
