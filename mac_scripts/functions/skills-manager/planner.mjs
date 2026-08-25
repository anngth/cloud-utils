export class PlannerError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "PlannerError";
  }
}

export function requirementKey(source, skill) {
  return JSON.stringify([source, skill]);
}

export function catalogRequirements(document) {
  const byKey = new Map();
  const bySkill = new Map();

  for (const entry of document.sources ?? []) {
    for (const skill of entry.skills ?? []) {
      const key = requirementKey(entry.source, skill);
      const item = byKey.get(key) ?? {
        key,
        source: entry.source,
        skill,
      };
      byKey.set(key, item);

      const sources = bySkill.get(skill) ?? new Map();
      if (!sources.has(entry.source)) sources.set(entry.source, true);
      bySkill.set(skill, sources);
    }
  }

  const desiredConflicts = [...bySkill]
    .filter(([, sources]) => sources.size > 1)
    .map(([skill, sources]) => ({
      skill,
      sources: [...sources.keys()].sort(),
      profiles: [],
    }));

  return {
    requirements: [...byKey.values()].sort((a, b) => a.skill.localeCompare(b.skill)),
    desiredConflicts,
  };
}

function isUntracked(actual) {
  return actual.source == null || actual.provenance === "untracked";
}

export function classifyStatus(mergeResult, installedState) {
  const installed = [];
  const missing = [];
  const mismatches = [];
  const untracked = [];
  const ambiguous = new Set(mergeResult.desiredConflicts.map((item) => item.skill));
  const desiredNames = new Set(mergeResult.requirements.map((item) => item.skill));

  for (const requirement of mergeResult.requirements) {
    if (ambiguous.has(requirement.skill)) continue;
    const actual = installedState.get(requirement.skill);
    if (!actual) {
      missing.push(requirement);
    } else if (isUntracked(actual)) {
      untracked.push(requirement);
    } else if (actual.source !== requirement.source) {
      mismatches.push(requirement);
    } else {
      installed.push(requirement);
    }
  }

  const extras = [...installedState.values()]
    .filter((actual) => !desiredNames.has(actual.name))
    .sort((a, b) => a.name.localeCompare(b.name));

  return {
    installed,
    missing,
    mismatches,
    untracked,
    extras,
    desiredConflicts: mergeResult.desiredConflicts,
  };
}

export function createInstallPlan(statusResult, { selectedKeys } = {}) {
  const selected = (item) => selectedKeys === undefined || selectedKeys.has(item.key);
  const blocked = [...statusResult.mismatches, ...statusResult.untracked];

  return {
    install: statusResult.missing.filter(selected),
    skip: [...statusResult.installed],
    conflicts: blocked,
    extras: [...statusResult.extras],
    desiredConflicts: statusResult.desiredConflicts,
  };
}

function combineDesiredConflicts(selected, remaining) {
  const conflicts = new Map();
  for (const conflict of [...selected.desiredConflicts, ...remaining.desiredConflicts]) {
    const key = JSON.stringify([conflict.skill, conflict.sources, conflict.profiles]);
    if (!conflicts.has(key)) conflicts.set(key, conflict);
  }
  return [...conflicts.values()].sort((a, b) => a.skill.localeCompare(b.skill));
}

export function createUninstallPlan({
  selected,
  remaining,
  installedState,
  linkedSelected,
}) {
  const remove = [];
  const retain = [];
  const absent = [];
  const conflicts = [];
  const desiredConflicts = combineDesiredConflicts(selected, remaining);
  const ambiguous = new Set(desiredConflicts.map((item) => item.skill));
  const remainingKeys = new Set(remaining.requirements.map((item) => item.key));

  for (const requirement of selected.requirements) {
    if (ambiguous.has(requirement.skill)) continue;
    if (remainingKeys.has(requirement.key)) {
      retain.push(requirement);
      continue;
    }

    const actual = installedState.get(requirement.skill);
    if (!actual) {
      absent.push(requirement);
    } else {
      remove.push(requirement);
    }
  }

  return {
    remove,
    retain,
    absent,
    conflicts,
    unlinkProfiles: [...linkedSelected],
    desiredConflicts,
  };
}
