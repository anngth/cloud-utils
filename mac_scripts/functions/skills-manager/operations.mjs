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
  projectRoot = process.cwd(),
  runMutation = (args, options) => runSkillsMutation(args, options),
  onEvent = () => {},
} = {}) {
  if (plan.desiredConflicts.length > 0) {
    return { ok: false, succeeded: [], failed: [] };
  }

  const succeeded = [];
  const failed = [];
  const eligible = plan.install;
  for (const batch of groupBySource(eligible)) {
    const args = ["skills", "add", batch.source];
    for (const skill of batch.skills) args.push("--skill", skill);
    if (yes) args.push("--yes");
    const status = await runMutation(args, { cwd: projectRoot });
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
  projectRoot = process.cwd(),
  runMutation = (args, options) => runSkillsMutation(args, options),
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
  const status = await runMutation(args, { cwd: projectRoot });
  const record = { action: "uninstall", source: null, skills: names, status };
  onEvent(record);
  return {
    ok: status === 0 && plan.conflicts.length === 0,
    succeeded: status === 0 ? [record] : [],
    failed: status === 0 ? [] : [record],
  };
}
