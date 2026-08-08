import { redactSource } from "./source-id.mjs";
import { requirementKey } from "./planner.mjs";

const C = {
  cyan: "\u001b[36m",
  green: "\u001b[32m",
  red: "\u001b[31m",
  yellow: "\u001b[33m",
  black: "\u001b[30m",
  gray: "\u001b[90m",
  brightGreen: "\u001b[92m",
  white: "\u001b[97m",
  bgCyan: "\u001b[46m",
  fgReset: "\u001b[39m",
  bgReset: "\u001b[49m",
  reset: "\u001b[0m",
};

const fg = (color, text) => `${color}${text}${C.fgReset}`;
const plural = (count, singular, pluralForm = `${singular}s`) => (
  `${count} ${count === 1 ? singular : pluralForm}`
);

function isCorrectlyInstalled(skill, source, installedState) {
  const actual = installedState.get(skill);
  return Boolean(
    actual
    && actual.source != null
    && actual.provenance !== "untracked"
    && actual.source === source,
  );
}

function isCatalogSourceSelected(state, sourceItem, sourceIndex) {
  const childValues = new Set(sourceItem.childValues ?? []);
  if (childValues.size === 0) return false;
  const childIndices = state.items.flatMap((item, index) => (
    item.kind === "skill" && childValues.has(item.value) ? [index] : []
  ));
  return childIndices.length > 0 && childIndices.every((index) => state.selected.has(index));
}

export function buildCatalogSelectorItems(catalog, { installedState }) {
  const items = [];
  const initial = [];

  for (const [sourceIndex, entry] of (catalog.sources ?? []).entries()) {
    const childValues = (entry.skills ?? []).map((skill) => requirementKey(entry.source, skill));
    items.push({
      kind: "source",
      value: entry.source,
      label: redactSource(entry.source),
      sourceIndex: sourceIndex + 1,
      childValues,
    });
    for (const skill of entry.skills ?? []) {
      const value = requirementKey(entry.source, skill);
      if (isCorrectlyInstalled(skill, entry.source, installedState)) initial.push(value);
      items.push({
        kind: "skill",
        value,
        label: skill,
        sourceIndex: sourceIndex + 1,
      });
    }
  }

  return { items, initial };
}

export function renderCatalogSelector(stdout, heading, state, { cancelled = false } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const pipe = fg(C.cyan, "│");
  stdout.write("\u001b[2J\u001b[H");
  out();
  out(`   ${C.bgCyan}${C.black} SKILLS MANAGER ${C.fgReset}${C.bgReset}`);
  out(pipe);
  out(`${fg(C.green, "◇")}  ${String(heading)}`);
  out(pipe);
  out(`${fg(C.cyan, "◆")}  Select items ${fg(C.white, "(space to toggle, enter to continue, q to quit)")}`);
  out(pipe);
  state.items.forEach((entry, index) => {
    const previous = state.items[index - 1];
    if (index > 0 && entry.kind === "skill" && previous?.kind === "skill") out(pipe);
    const selected = entry.kind === "source"
      ? isCatalogSourceSelected(state, entry, index)
      : state.selected.has(index);
    const box = selected ? "■" : "□";
    const boxColor = selected ? C.brightGreen : C.gray;
    const labelColor = entry.kind === "skill"
      ? C.brightGreen
      : index === state.cursor ? C.white : C.gray;
    if (entry.kind === "source") {
      const number = String(entry.sourceIndex ?? index + 1);
      out(`${pipe}  ${number}  ${boxColor}${box}${C.reset}  ${fg(labelColor, entry.label)}`);
      return;
    }
    out(`${pipe}    ${boxColor}${box}${C.reset} ${fg(labelColor, entry.label)}${entry.hint ? ` ${fg(C.gray, entry.hint)}` : ""}`);
  });
  if (cancelled) out(`${fg(C.cyan, "└")}  ${fg(C.red, "Selection cancelled")}`);
  else out(fg(C.cyan, "└"));
}

export function createUi({ stdout = process.stdout, stderr = process.stderr } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const err = (line) => stderr.write(`${line}\n`);
  const pipe = fg(C.cyan, "│");
  const title = () => {
    out();
    out(`   ${C.bgCyan}${C.black} SKILLS MANAGER ${C.fgReset}${C.bgReset}`);
    out(pipe);
  };
  const step = (text) => {
    out(`${fg(C.green, "◇")}  ${text}`);
    out(pipe);
  };
  const active = (text) => out(`${fg(C.cyan, "◆")}  ${text}`);
  const item = (text, color = C.green) => out(`${pipe}  ${fg(color, "■")} ${text}`);
  const skillItem = ({
    name,
    suffix = "",
    marker = "■",
    markerColor = C.green,
    suffixColor = C.gray,
    indent = "  ",
  }) => out(
    `${pipe}${indent}${fg(markerColor, marker)} ${fg(C.brightGreen, name)}`
      + `${suffix ? ` ${fg(suffixColor, suffix)}` : ""}`,
  );
  const skillList = (values, renderItem) => {
    values.forEach((value, index) => {
      if (index > 0) out(pipe);
      renderItem(value);
    });
  };
  const listEnd = (text = "") => out(`${fg(C.cyan, "└")}${text ? `  ${text}` : ""}`);

  function usage() {
    const section = (name) => {
      out(pipe);
      active(name);
    };
    const command = (syntax, description) => {
      out(`${pipe}  ${fg(C.green, syntax)}${description ? `  ${fg(C.gray, description)}` : ""}`);
    };
    const continuation = (syntax, description) => {
      out(`${pipe}      ${fg(C.green, syntax)}${description ? `  ${fg(C.gray, description)}` : ""}`);
    };
    const note = (text) => out(`${pipe}  ${fg(C.gray, text)}`);

    title();
    step("Usage: skm [command]");
    command("skm", "Open interactive catalog selector");
    command("skm (help | -h | --help)", "Show this help");

    section("Lifecycle");
    command("skm add <source|index...> [(-a | --all)]");
    continuation("[(-y | --yes)] [(-f | --force)] [(-d | --dry-run)]", "Install catalog skills for selected sources");
    command("skm remove <source|index...> [(-a | --all)]");
    continuation("[(-y | --yes)] [(-f | --force)] [(-d | --dry-run)]", "Uninstall catalog skills for selected sources");
    command("skm status", "Compare catalog and installed skills");

    section("Catalog");
    command("skm source add <source>");
    continuation("[[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)] [(-y | --yes)]", "Add or update a catalog source");
    command("skm source remove <source|index>", "Remove a catalog source");

    section("Notes");
    note("Source indexes are 1-based, matching the interactive selector and gt backup.");
    note("source add and source remove change the catalog only; use add/remove to change disk.");
    note("--force permits mismatch/untracked skill replacement or removal.");
    listEnd();
  }

  function dashboard({ projectRoot, linkedProfiles, actions, state }) {
    stdout.write("\u001b[2J\u001b[H");
    title();
    step(`Project: ${projectRoot}`);
    active("Linked profiles");
    if (linkedProfiles.length === 0) item("No linked profiles", C.yellow);
    else item(linkedProfiles.join(", "));
    out(pipe);
    active(`Actions ${fg(C.white, "(enter to continue, q to quit)")}`);
    out(pipe);
    const cursor = state?.cursor ?? 0;
    actions.forEach((action, index) => {
      const marker = index === cursor ? "◆" : "◇";
      const color = index === cursor ? C.brightGreen : C.gray;
      out(`${pipe}  ${fg(color, marker)} ${fg(index === cursor ? C.white : C.gray, action.label)}`);
    });
    listEnd();
  }

  function profileList(value, positionalProjects) {
    const profiles = Array.isArray(value) ? value : value.profiles;
    const projects = Array.isArray(value) ? (positionalProjects ?? []) : value.projects;
    title();
    step(`Found ${plural(profiles.length, "profile")}`);
    active("Profiles");
    if (profiles.length === 0) item("No profiles", C.yellow);
    for (const profile of profiles) {
      const sourceCount = profile.sources.length;
      const skillCount = profile.sources.reduce((count, source) => count + source.skills.length, 0);
      const linkedCount = projects.filter((project) => project.profiles.includes(profile.name)).length;
      item(
        `${profile.name} — ${plural(sourceCount, "source")}, ${plural(skillCount, "skill")}, ${plural(linkedCount, "linked project")}`,
      );
    }
    listEnd();
  }

  function profileShow(value, positionalProjects) {
    const profile = value.profile ?? value;
    const projects = value.profile ? value.projects : (positionalProjects ?? []);
    title();
    step(`Profile: ${profile.name}`);
    const skillCount = profile.sources.reduce((count, source) => count + source.skills.length, 0);
    step(`${plural(profile.sources.length, "source")}; ${plural(skillCount, "selected skill")}`);
    active("Sources and selected skills");
    if (profile.sources.length === 0) item("No sources", C.yellow);
    for (const source of profile.sources) {
      item(`${redactSource(source.source)} — ${plural(source.skills.length, "selected skill")}`);
      skillList(source.skills, (name) => skillItem({
        name,
        marker: "•",
        markerColor: C.gray,
        indent: "      ",
      }));
    }
    out(pipe);
    active("Linked projects");
    if (projects.length === 0) item("No linked projects", C.yellow);
    for (const project of projects) item(project.root);
    listEnd();
  }

  function profileChanged(value, name, extra = {}) {
    const change = typeof value === "string" ? { action: value, name, ...extra } : value;
    title();
    step(`Profile ${change.action}: ${change.oldName ? `${change.oldName} → ` : ""}${change.name}`);
    if (change.action === "removed") {
      out(`${pipe}  Configuration changed; installed project skills were not uninstalled`);
    }
    listEnd();
  }

  function sourceChanged(value, profile, source, skills = []) {
    const change = typeof value === "string"
      ? { action: value, profile, source, skills }
      : value;
    title();
    step(`Source ${change.action}: ${redactSource(change.source)}`);
    if (change.profile) step(`Profile: ${change.profile}`);
    step(`${plural(change.skills?.length ?? 0, "selected skill")}`);
    if (change.available?.length > 0) {
      active("Available skills");
      skillList(change.available, (record) => skillItem({
        name: record.name,
        suffix: record.description ? `— ${record.description}` : "",
      }));
    }
    listEnd();
  }

  function skillChanged(value, profile, source, skills = [], missing = []) {
    const change = typeof value === "string"
      ? { action: value, profile, source, skills, missing }
      : value;
    title();
    step(`Skills ${change.action}: ${plural(change.skills?.length ?? 0, "skill")}`);
    step(`Profile: ${change.profile}; source: ${redactSource(change.source)}`);
    skillList(change.skills ?? [], (name) => skillItem({ name }));
    if (change.missing?.length > 0) item(`Not selected: ${change.missing.join(", ")}`, C.yellow);
    out(pipe);
    out(`${pipe}  This changes the profile definition and does not change installed project skills`);
    listEnd();
  }

  function projectShow(value, positionalProfiles) {
    const project = typeof value === "string"
      ? { root: value, profiles: positionalProfiles ?? [] }
      : value;
    title();
    step(`Project: ${project.root}`);
    active("Linked profiles");
    if (project.profiles.length === 0) item("No linked profiles", C.yellow);
    for (const name of project.profiles) item(name);
    listEnd();
  }

  function projectList(value) {
    const projects = Array.isArray(value) ? value : value.projects;
    title();
    step(`Found ${plural(projects.length, "project")}`);
    active("Registered projects");
    if (projects.length === 0) item("No registered projects", C.yellow);
    for (const project of projects) {
      const stale = project.stale ? ` ${fg(C.yellow, "(stale root)")}` : "";
      item(`${project.root}${stale} — ${project.profiles.join(", ") || "no profiles"}`);
    }
    listEnd();
  }

  function projectChanged(value, root, profiles = []) {
    const change = typeof value === "string" ? { action: value, root, profiles } : value;
    title();
    step(`Project ${change.action}: ${change.root}`);
    if (change.profiles?.length > 0) step(`Profiles: ${change.profiles.join(", ")}`);
    out(`${pipe}  Configuration changed; installed project skills were not modified`);
    listEnd();
  }

  const requirementSuffix = ({ source, profiles = [] }) => (
    `— ${redactSource(source)}`
      + `${profiles.length > 0 ? ` — required by ${profiles.join(", ")}` : ""}`
  );

  function requirementSection(label, values, color = C.green) {
    active(label);
    if (values.length === 0) item("None", C.gray);
    skillList(values, (value) => skillItem({
      name: value.skill,
      suffix: requirementSuffix(value),
      markerColor: color,
      suffixColor: color,
    }));
    out(pipe);
  }

  function status({ projectRoot, profileNames, status: result }) {
    title();
    step(`Status: ${projectRoot}`);
    step(`Profiles: ${profileNames.join(", ")}`);
    requirementSection("Installed", result.installed);
    requirementSection("Missing", result.missing, C.yellow);
    requirementSection("Source mismatch", result.mismatches, C.red);
    requirementSection("Untracked", result.untracked, C.red);
    active("Extra");
    if (result.extras.length === 0) item("None", C.gray);
    skillList(result.extras, (extra) => skillItem({
      name: extra.name,
      suffix: extra.source ? `— ${redactSource(extra.source)}` : "",
      markerColor: C.yellow,
      suffixColor: C.yellow,
    }));
    out(pipe);
    active("Desired-source conflict");
    if (result.desiredConflicts.length === 0) item("None", C.gray);
    skillList(result.desiredConflicts, (conflict) => skillItem({
      name: conflict.skill,
      suffix: `— ${conflict.sources.map(redactSource).join(" vs ")}`
        + ` — required by ${conflict.profiles.join(", ")}`,
      markerColor: C.red,
      suffixColor: C.red,
    }));
    listEnd();
  }

  function installPlan({ projectRoot, profileNames, plan, dryRun = false }) {
    title();
    step(`${dryRun ? "DRY RUN — " : ""}Install plan: ${projectRoot}`);
    step(`Profiles: ${profileNames.join(", ")}`);
    requirementSection("Install", plan.install);
    requirementSection("Replace", plan.replace, C.yellow);
    requirementSection("Already installed", plan.skip, C.gray);
    requirementSection("Conflict", plan.conflicts, C.red);
    active("Extra");
    if (plan.extras.length === 0) item("None", C.gray);
    skillList(plan.extras, (extra) => skillItem({
      name: extra.name,
      suffix: extra.source ? `— ${redactSource(extra.source)}` : "",
      markerColor: C.gray,
      suffixColor: C.gray,
    }));
    out(pipe);
    active("Desired-source conflict");
    if (plan.desiredConflicts.length === 0) item("None", C.gray);
    skillList(plan.desiredConflicts, (conflict) => skillItem({
      name: conflict.skill,
      suffix: `— ${conflict.sources.map(redactSource).join(" vs ")}`,
      markerColor: C.red,
      suffixColor: C.red,
    }));
    listEnd();
  }

  function uninstallPlan({
    projectRoot,
    profileNames,
    plan,
    dryRun = false,
    force = false,
    keepLink = false,
  }) {
    title();
    step(`${dryRun ? "DRY RUN — " : ""}Uninstall plan: ${projectRoot}`);
    step(`Profiles: ${profileNames.join(", ")}`);
    if (force) step("Force enabled — mismatched or untracked skills may be removed");
    requirementSection("Remove", plan.remove, C.yellow);
    requirementSection("Keep", plan.retain, C.gray);
    requirementSection("Already absent", plan.absent, C.gray);
    requirementSection("Conflict", plan.conflicts, C.red);
    if (!keepLink) {
      active("Unlink");
      if (plan.unlinkProfiles.length === 0) item("None", C.gray);
      for (const name of plan.unlinkProfiles) item(name, C.yellow);
      out(pipe);
    }
    listEnd();
  }

  const shellArg = (value) => {
    const text = String(value);
    return /^[A-Za-z0-9_./:@%+=,-]+$/.test(text)
      ? text
      : `'${text.replaceAll("'", `'\\''`)}'`;
  };

  function retryCommand(record) {
    if (record.action === "replace") {
      return `npx skills remove ${record.skills.map(shellArg).join(" ")} --yes`;
    }
    if (record.action === "uninstall") {
      return `npx skills remove ${record.skills.map(shellArg).join(" ")}`;
    }
    const skills = record.skills.map((skill) => `--skill ${shellArg(skill)}`).join(" ");
    return `npx skills add ${shellArg(redactSource(record.source))} ${skills}`;
  }

  const operationSkillRows = (records) => records.flatMap((record) => (
    record.skills.map((name) => ({ name, record }))
  ));

  const uniqueRetryCommands = (records) => [
    ...new Set(records.map((record) => retryCommand(record))),
  ];

  function executionSummary(result, { operation = "install" } = {}) {
    title();
    const label = operation === "uninstall" ? "Uninstall" : "Install";
    step(result.ok ? `${label} complete` : `${label} incomplete`);
    step(`${result.succeeded.length} succeeded; ${result.failed.length} failed`);
    const incompleteReplacements = (result.replacements ?? []).filter((record) => (
      record.removeStatus === 0 && record.installStatus !== 0
    ));
    if (incompleteReplacements.length > 0) {
      active("Replacement warning");
      skillList(incompleteReplacements, (record) => skillItem({
        name: record.skill,
        suffix: `— old version removed; replacement from ${redactSource(record.source)}`
          + ` failed (status ${record.installStatus})`,
        markerColor: C.red,
        suffixColor: C.red,
      }));
      out(pipe);
    }
    if (result.succeeded.length > 0) {
      active("Succeeded");
      skillList(operationSkillRows(result.succeeded), ({ name, record }) => skillItem({
        name,
        suffix: `— ${record.action}`,
      }));
      out(pipe);
    }
    if (result.failed.length > 0) {
      active("Failed");
      skillList(operationSkillRows(result.failed), ({ name, record }) => skillItem({
        name,
        suffix: `— ${record.action} failed (status ${record.status})`,
        markerColor: C.red,
        suffixColor: C.red,
      }));
      out(pipe);
    }
    const retryCommands = uniqueRetryCommands(result.failed);
    if (retryCommands.length > 0) {
      active("Retry commands");
      for (const command of retryCommands) item(command, C.red);
    }
    listEnd();
  }

  function confirm(message) {
    active(message);
  }

  function selector(heading, state, { mode } = {}) {
    renderSelector(heading, state, { mode, cancelled: false });
  }

  function cancelledSelector(heading, state, { mode } = {}) {
    renderSelector(heading, state, { mode, cancelled: true });
  }

  function catalogSelector(heading, state) {
    renderCatalogSelector(stdout, heading, state);
  }

  function cancelledCatalogSelector(heading, state) {
    renderCatalogSelector(stdout, heading, state, { cancelled: true });
  }

  function renderSelector(heading, state, { mode, cancelled }) {
    stdout.write("\u001b[2J\u001b[H");
    title();
    const values = state.items;
    step(String(heading));
    active(mode === "install"
      ? `Select items ${fg(C.white, "(space to toggle, enter to continue, q to quit)")}`
      : `Select an item ${fg(C.white, "(enter to continue, q to quit)")}`);
    out(pipe);
    values.forEach((entry, index) => {
      const label = typeof entry === "string" ? entry : entry.label;
      const hint = typeof entry === "string" ? "" : entry.hint;
      const skillEntry = typeof entry !== "string" && entry.kind === "skill";
      const previous = values[index - 1];
      const previousSkill = typeof previous !== "string" && previous?.kind === "skill";
      if (index > 0 && skillEntry && previousSkill) out(pipe);
      const selected = mode === "install" ? state.selected.has(index) : index === state.cursor;
      const box = selected ? "■" : "□";
      const boxColor = selected ? C.brightGreen : C.gray;
      const labelColor = skillEntry
        ? C.brightGreen
        : index === state.cursor ? C.white : C.gray;
      out(`${pipe}  ${boxColor}${box}${C.reset} ${fg(labelColor, label)}${hint ? ` ${fg(C.gray, hint)}` : ""}`);
    });
    if (cancelled) listEnd(fg(C.red, "Selection cancelled"));
    else listEnd();
  }

  return {
    usage,
    dashboard,
    profileList,
    profileShow,
    profileChanged,
    sourceChanged,
    skillChanged,
    projectShow,
    projectList,
    projectChanged,
    status,
    installPlan,
    uninstallPlan,
    executionSummary,
    confirm,
    selector,
    cancelledSelector,
    catalogSelector,
    cancelledCatalogSelector,
    error(message) { err(fg(C.red, `❌ ${message}`)); },
    warn(message) { err(fg(C.yellow, `⚠️  ${message}`)); },
    info(message) { out(message); },
  };
}
