import { runSkillsMutation } from "./skills-cli.mjs";

function groupBySource(requirements) {
  const groups = new Map();
  for (const item of requirements) {
    const skills = groups.get(item.source) ?? [];
    skills.push(item.skill);
    groups.set(item.source, skills);
  }
  return [...groups].map(([source, skills]) => ({ source, skills }));
}

export async function executeInstallPlan(plan, {
  yes = false,
  runMutation = (args) => runSkillsMutation(args),
  onEvent = () => {},
} = {}) {
  if (plan.desiredConflicts.length > 0) {
    return { ok: false, succeeded: [], failed: [] };
  }

  const succeeded = [];
  const failed = [];
  for (const replacement of plan.replace) {
    const removeArgs = ["skills", "remove", replacement.skill, "--yes"];
    const removeStatus = await runMutation(removeArgs);
    onEvent({ action: "remove-for-replace", requirement: replacement, status: removeStatus });
    if (removeStatus !== 0) {
      failed.push({
        action: "replace",
        source: replacement.source,
        skills: [replacement.skill],
        status: removeStatus,
      });
    }
  }

  const failedReplacements = new Set(
    failed.filter(({ action }) => action === "replace").flatMap(({ skills }) => skills),
  );
  const eligible = [
    ...plan.install,
    ...plan.replace.filter((item) => !failedReplacements.has(item.skill)),
  ];
  for (const batch of groupBySource(eligible)) {
    const args = ["skills", "add", batch.source];
    for (const skill of batch.skills) args.push("--skill", skill);
    if (yes) args.push("--yes");
    const status = await runMutation(args);
    const record = { action: "install", source: batch.source, skills: batch.skills, status };
    (status === 0 ? succeeded : failed).push(record);
    onEvent(record);
  }

  return {
    ok: failed.length === 0 && plan.conflicts.length === 0,
    succeeded,
    failed,
  };
}

export async function executeUninstallPlan(plan, {
  yes = false,
  runMutation = (args) => runSkillsMutation(args),
  onEvent = () => {},
} = {}) {
  if (plan.desiredConflicts.length > 0) {
    return { ok: false, succeeded: [], failed: [] };
  }

  const names = plan.remove.map((item) => item.skill);
  if (names.length === 0) {
    return { ok: plan.conflicts.length === 0, succeeded: [], failed: [] };
  }

  const args = ["skills", "remove", ...names];
  if (yes) args.push("--yes");
  const status = await runMutation(args);
  const record = { action: "uninstall", source: null, skills: names, status };
  onEvent(record);
  return {
    ok: status === 0 && plan.conflicts.length === 0,
    succeeded: status === 0 ? [record] : [],
    failed: status === 0 ? [] : [record],
  };
}
