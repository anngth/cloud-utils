import { resolveSourceToken } from "./catalog.mjs";
import {
  catalogRequirements,
  classifyStatus,
  createInstallPlan,
  createUninstallPlan,
} from "./planner.mjs";
import { redactSource } from "./source-id.mjs";

class LifecycleCommandError extends Error {}

function lifecycleUsage(action) {
  return `Usage: skm ${action} <source|index...> [--all] [(-y | --yes)] [(-d | --dry-run)]`;
}

function parseLifecycleOptions(args, { allowAll = false, allowPositionals = true } = {}) {
  const parsed = {
    tokens: [],
    yes: false,
    dryRun: false,
    all: false,
  };
  const flags = new Map([
    ["-y", "yes"],
    ["--yes", "yes"],
    ["-d", "dryRun"],
    ["--dry-run", "dryRun"],
  ]);
  if (allowAll) flags.set("--all", "all");

  for (const arg of args) {
    const key = flags.get(arg);
    if (key) {
      parsed[key] = true;
    } else if (arg.startsWith("-")) {
      throw new LifecycleCommandError(`Unknown option: ${arg}`);
    } else if (allowPositionals && !parsed.tokens.includes(arg)) {
      parsed.tokens.push(arg);
    } else if (!allowPositionals) {
      throw new LifecycleCommandError(`Unexpected argument: ${arg}`);
    }
  }

  if (parsed.all && parsed.tokens.length > 0) {
    throw new LifecycleCommandError("Cannot combine --all with explicit source targets");
  }
  return parsed;
}

export function validateLifecycleCommandGrammar(action, args) {
  if (action === "status") {
    parseLifecycleOptions(args, { allowPositionals: false });
    return;
  }
  if (action === "add" || action === "remove") {
    parseLifecycleOptions(args, { allowAll: true });
  }
}

export function resolveSourceTargets(parsed, catalog, { cwd } = {}) {
  if (!parsed.all && parsed.tokens.length === 0) {
    throw new LifecycleCommandError(lifecycleUsage(parsed.action ?? "add"));
  }
  if (parsed.all) {
    return catalog.sources.map((entry, index) => ({ index, entry }));
  }
  return parsed.tokens.map((token) => resolveSourceToken(catalog, token, { cwd }));
}

function catalogSlice(entries) {
  return catalogRequirements({ version: 1, sources: entries.map(({ entry }) => entry) });
}

function remainingCatalogSlice(catalog, targets) {
  const selectedSources = new Set(targets.map(({ entry }) => entry.source));
  const remaining = catalog.sources.filter((entry) => !selectedSources.has(entry.source));
  return catalogRequirements({ version: 1, sources: remaining });
}

function targetSourceLabels(targets) {
  return targets.map(({ entry }) => redactSource(entry.source));
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
  const details = conflicts.map(({ skill, sources }) => (
    `${skill} (${sources.map(redactSource).join(" vs ")})`
  ));
  return `Conflicting desired skill sources: ${details.join("; ")}`;
}

function selectableInstallItems(plan) {
  return plan.install.map((item) => ({
    kind: "skill",
    key: item.key,
    value: item.key,
    label: item.skill,
    hint: redactSource(item.source),
  }));
}

function installConflictMessage(conflicts) {
  const names = [...new Set(conflicts.map((item) => item.skill))];
  return `Blocked by installed skill conflicts: ${names.join(", ")}`;
}

function retainedByRemaining(plan, remaining) {
  const byKey = new Map(remaining.requirements.map((item) => [item.key, item]));
  return {
    ...plan,
    retain: plan.retain.map((item) => byKey.get(item.key) ?? item),
  };
}
export async function runStatusCommand(args, context) {
  try {
    parseLifecycleOptions(args, { allowPositionals: false });
    const projectRoot = context.resolveProjectRoot({ cwd: context.cwd });
    const merged = catalogRequirements(context.config.catalog);
    const installed = await context.loadInstalledState({ projectRoot });
    const status = classifyStatus(merged, installed);
    context.ui.status({
      projectRoot,
      profileNames: [],
      catalog: context.config.catalog,
      status,
    });
    return statusOk(status) ? 0 : 1;
  } catch (error) {
    return reportError(context, error);
  }
}

export async function runAddCommand(args, context, { onOutcome = () => {} } = {}) {
  try {
    const parsed = parseLifecycleOptions(args, { allowAll: true });
    parsed.action = "add";
    const projectRoot = context.resolveProjectRoot({ cwd: context.cwd });
    const catalog = context.config.catalog;
    const targets = resolveSourceTargets(parsed, catalog, { cwd: context.cwd });
    const selected = catalogSlice(targets);
    if (selected.requirements.length === 0) {
      throw new LifecycleCommandError("Selected sources contain no skills to install");
    }
    if (selected.desiredConflicts.length > 0) {
      throw new LifecycleCommandError(desiredConflictMessage(selected.desiredConflicts));
    }

    const installed = await context.loadInstalledState({ projectRoot });
    const status = classifyStatus(selected, installed);
    let plan = createInstallPlan(status);

    if (!parsed.yes) {
      const items = selectableInstallItems(plan);
      if (items.length > 0) {
        const selection = await context.selectSkills(items);
        if (selection.type !== "submit") {
          onOutcome({ type: "cancelled", stage: "skill-selection" });
          return 0;
        }
        plan = createInstallPlan(status, {
          selectedKeys: new Set(selection.selected),
        });
      }
    }

    if (plan.conflicts.length > 0) {
      throw new LifecycleCommandError(installConflictMessage(plan.conflicts));
    }

    context.ui.installPlan({
      projectRoot,
      profileNames: targetSourceLabels(targets),
      plan,
      dryRun: parsed.dryRun,
    });
    if (parsed.dryRun) {
      const ok = plan.conflicts.length === 0;
      onOutcome({ type: "dry-run", ok });
      return ok ? 0 : 1;
    }

    if (!parsed.yes) {
      const confirmed = await context.confirm("Apply this install plan?");
      if (!confirmed) {
        onOutcome({ type: "cancelled", stage: "confirmation" });
        return 0;
      }
    }

    const result = await context.executeInstallPlan(plan, { yes: parsed.yes, projectRoot });
    context.ui.executionSummary(result);
    onOutcome({ type: "completed", ok: result.ok });
    return result.ok ? 0 : 1;
  } catch (error) {
    onOutcome({ type: "error" });
    return reportError(context, error);
  }
}

export async function runRemoveCommand(args, context) {
  try {
    const parsed = parseLifecycleOptions(args, { allowAll: true });
    parsed.action = "remove";
    const projectRoot = context.resolveProjectRoot({ cwd: context.cwd });
    const catalog = context.config.catalog;
    const targets = resolveSourceTargets(parsed, catalog, { cwd: context.cwd });
    const selected = catalogSlice(targets);
    const remaining = remainingCatalogSlice(catalog, targets);
    const desiredConflicts = catalogRequirements(catalog).desiredConflicts;
    if (desiredConflicts.length > 0) {
      throw new LifecycleCommandError(desiredConflictMessage(desiredConflicts));
    }

    const installedState = await context.loadInstalledState({ projectRoot });
    let plan = createUninstallPlan({
      selected,
      remaining,
      installedState,
      linkedSelected: [],
    });
    plan = retainedByRemaining(plan, remaining);

    context.ui.uninstallPlan({
      projectRoot,
      profileNames: targetSourceLabels(targets),
      plan,
      dryRun: parsed.dryRun,
    });
    if (parsed.dryRun) return plan.conflicts.length === 0 ? 0 : 1;

    if (!parsed.yes) {
      const confirmed = await context.confirm("Apply this uninstall plan?");
      if (!confirmed) return 0;
    }

    const result = await context.executeUninstallPlan(plan, { yes: parsed.yes, projectRoot });
    context.ui.executionSummary(result, { operation: "uninstall" });
    return result.ok ? 0 : 1;
  } catch (error) {
    return reportError(context, error);
  }
}
