import { EMPTY_CATALOG } from "./catalog.mjs";
import {
  catalogRequirements,
  classifyStatus,
  createInstallPlan,
  createUninstallPlan,
  requirementKey,
} from "./planner.mjs";
import { redactSource } from "./source-id.mjs";
import { buildCatalogSelectorItems } from "./ui.mjs";

export function filterSkillRequirementKeys(selected, items) {
  const skillKeys = new Set(
    items.filter((item) => item.kind === "skill").map((item) => item.value),
  );
  return selected.filter((value) => skillKeys.has(value));
}

function catalogSliceFromKeys(catalog, desiredKeys) {
  const keySet = new Set(desiredKeys);
  const sources = [];
  for (const entry of catalog.sources ?? []) {
    const skills = (entry.skills ?? []).filter((skill) => (
      keySet.has(requirementKey(entry.source, skill))
    ));
    if (skills.length > 0) sources.push({ source: entry.source, skills });
  }
  return catalogRequirements({ version: 1, sources });
}

function inverseCatalogSlice(catalog, desiredKeys) {
  const keySet = new Set(desiredKeys);
  const sources = [];
  for (const entry of catalog.sources ?? []) {
    const skills = (entry.skills ?? []).filter((skill) => (
      !keySet.has(requirementKey(entry.source, skill))
    ));
    if (skills.length > 0) sources.push({ source: entry.source, skills });
  }
  return catalogRequirements({ version: 1, sources });
}

function desiredConflictMessage(conflicts) {
  const details = conflicts.map(({ skill, sources }) => (
    `${skill} (${sources.map(redactSource).join(" vs ")})`
  ));
  return `Conflicting desired skill sources: ${details.join("; ")}`;
}

function planHasWork(installPlan, uninstallPlan) {
  return installPlan.install.length > 0
    || installPlan.replace.length > 0
    || uninstallPlan.remove.length > 0;
}

export async function runInteractive(context) {
  try {
    const catalog = context.config.catalog ?? EMPTY_CATALOG;
    if (!catalog.sources?.length) {
      context.ui.error("Catalog is empty");
      context.ui.warn("Add a source with: skm source add");
      return 1;
    }

    const projectRoot = context.resolveProjectRoot({ cwd: context.cwd });
    const installedState = await context.loadInstalledState({ projectRoot });
    const { items, initial } = buildCatalogSelectorItems(catalog, { installedState });
    const title = "Choose skills";

    const selection = await context.selectCatalogItems(items, { initial, title });
    if (selection.type === "cancel") {
      context.ui.cancelledCatalogSelector(title, selection.state);
      return 1;
    }

    const desiredKeys = filterSkillRequirementKeys(selection.selected, items);
    const desired = catalogSliceFromKeys(catalog, desiredKeys);
    const toRemove = inverseCatalogSlice(catalog, desiredKeys);
    const fullCatalog = catalogRequirements(catalog);

    if (fullCatalog.desiredConflicts.length > 0) {
      throw new Error(desiredConflictMessage(fullCatalog.desiredConflicts));
    }

    const installStatus = classifyStatus(desired, installedState);
    const installPlan = createInstallPlan(installStatus, { force: false });
    const uninstallPlan = createUninstallPlan({
      selected: toRemove,
      remaining: desired,
      installedState,
      force: false,
      linkedSelected: [],
    });

    if (installPlan.conflicts.length > 0 || uninstallPlan.conflicts.length > 0) {
      const names = [
        ...installPlan.conflicts.map((item) => item.skill),
        ...uninstallPlan.conflicts.map((item) => item.skill),
      ];
      throw new Error(`Blocked by installed skill conflicts: ${[...new Set(names)].join(", ")}`);
    }

    if (!planHasWork(installPlan, uninstallPlan)) return 0;

    context.ui.installPlan({
      projectRoot,
      profileNames: [],
      plan: installPlan,
    });
    context.ui.uninstallPlan({
      projectRoot,
      profileNames: [],
      plan: uninstallPlan,
    });

    const confirmed = await context.confirm("Apply these changes?");
    if (!confirmed) return 1;

    if (context.requireNpx && !context.requireNpx()) return 1;

    let exitCode = 0;
    if (installPlan.install.length > 0 || installPlan.replace.length > 0) {
      const result = await context.executeInstallPlan(installPlan, { projectRoot });
      context.ui.executionSummary(result);
      if (!result.ok) exitCode = 1;
    }
    if (uninstallPlan.remove.length > 0) {
      const result = await context.executeUninstallPlan(uninstallPlan, { projectRoot });
      context.ui.executionSummary(result, { operation: "uninstall" });
      if (!result.ok) exitCode = 1;
    }
    return exitCode;
  } catch (error) {
    context.ui.error(error instanceof Error ? error.message : String(error));
    return 1;
  }
}
