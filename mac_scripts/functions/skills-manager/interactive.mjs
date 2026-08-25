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
  return installPlan.install.length > 0 || uninstallPlan.remove.length > 0;
}

const emptyExecution = { ok: true, succeeded: [], failed: [] };

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
    const installPlan = createInstallPlan(installStatus);
    const uninstallPlan = createUninstallPlan({
      selected: toRemove,
      remaining: desired,
      installedState,
      linkedSelected: [],
    });

    if (installPlan.conflicts.length > 0) {
      const names = [...new Set(installPlan.conflicts.map((item) => item.skill))];
      throw new Error(`Blocked by installed skill conflicts: ${names.join(", ")}`);
    }

    if (!planHasWork(installPlan, uninstallPlan)) return 0;

    const confirmed = await context.confirmApply({
      install: installPlan.install,
      remove: uninstallPlan.remove,
      catalog,
    });
    if (!confirmed) return 1;

    if (context.requireNpx && !context.requireNpx()) return 1;

    const installResult = installPlan.install.length > 0
      ? await context.executeInstallPlan(installPlan, { projectRoot })
      : emptyExecution;
    const uninstallResult = uninstallPlan.remove.length > 0
      ? await context.executeUninstallPlan(uninstallPlan, { projectRoot })
      : emptyExecution;

    const ok = installResult.ok && uninstallResult.ok;
    context.ui.executionSummary({
      ok,
      succeeded: [...installResult.succeeded, ...uninstallResult.succeeded],
      failed: [...installResult.failed, ...uninstallResult.failed],
    }, { operation: "changes" });
    return ok ? 0 : 1;
  } catch (error) {
    context.ui.error(error instanceof Error ? error.message : String(error));
    return 1;
  }
}
