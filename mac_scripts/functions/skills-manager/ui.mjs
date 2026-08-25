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
  dim: "\u001b[2m",
  bgCyan: "\u001b[46m",
  fgReset: "\u001b[39m",
  bgReset: "\u001b[49m",
  reset: "\u001b[0m",
};

export const SELECTOR_DESCRIPTION_COLOR = `${C.dim}${C.gray}`;

export function selectorNameColor({ isCursor, isSelected }) {
  if (isCursor) return C.white;
  if (isSelected) return C.green;
  return C.gray;
}

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

export function groupRequirementsByCatalogSource(requirements, catalog) {
  const groups = [];
  for (const [sourceIndex, entry] of (catalog.sources ?? []).entries()) {
    const skills = requirements
      .filter((item) => item.source === entry.source)
      .map((item) => item.skill);
    if (skills.length === 0) continue;
    groups.push({
      sourceIndex: sourceIndex + 1,
      source: entry.source,
      label: redactSource(entry.source),
      skills,
    });
  }
  return groups;
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
  out(`${fg(C.cyan, "◆")}  Select items ${fg(C.white, "(space toggle, a all, c clear, enter to continue, q to quit)")}`);
  out(pipe);
  state.items.forEach((entry, index) => {
    if (index > 0 && entry.kind === "source") out(pipe);
    const isCursor = index === state.cursor;
    const selected = entry.kind === "source"
      ? isCatalogSourceSelected(state, entry, index)
      : state.selected.has(index);
    const box = selected ? "■" : "□";
    const boxColor = selected ? C.brightGreen : C.gray;
    const labelColor = selectorNameColor({ isCursor, isSelected: selected });
    const hintText = entry.hint
      ? ` ${SELECTOR_DESCRIPTION_COLOR}${entry.hint}${C.reset}`
      : "";
    if (entry.kind === "source") {
      const number = String(entry.sourceIndex ?? index + 1);
      out(`${pipe}  ${number}  ${boxColor}${box}${C.reset}  ${fg(labelColor, entry.label)}`);
      return;
    }
    out(`${pipe}      ${boxColor}${box}${C.reset} ${fg(labelColor, entry.label)}${hintText}`);
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
    continuation("[(-y | --yes)] [(-d | --dry-run)]", "Install catalog skills for selected sources");
    command("skm remove <source|index...> [(-a | --all)]");
    continuation("[(-y | --yes)] [(-d | --dry-run)]", "Uninstall catalog skills for selected sources");
    command("skm status", "Compare catalog and installed skills");

    section("Catalog");
    command("skm source add <source>");
    continuation("[[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)] [(-y | --yes)]", "Add a catalog source");
    command("skm source edit <source|index>");
    continuation("[[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)] [(-y | --yes)]", "Update skills for a catalog source");
    command("skm source remove <source|index>", "Remove a catalog source");

    section("Notes");
    note("Source indexes are 1-based, matching the interactive selector and gt backup.");
    note("source add, source edit, and source remove change the catalog only; use add/remove to change disk.");
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

  const requirementSuffix = ({ source, profiles = [] }) => (
    `— ${redactSource(source)}`
      + `${profiles.length > 0 ? ` — required by ${profiles.join(", ")}` : ""}`
  );

  function requirementSection(label, values, color = C.green) {
    if (values.length === 0) return;
    active(label);
    skillList(values, (value) => skillItem({
      name: value.skill,
      suffix: requirementSuffix(value),
      markerColor: color,
      suffixColor: color,
    }));
    out(pipe);
  }

  function renderStatusTree(catalog, result) {
    const installedKeys = new Set(result.installed.map((item) => requirementKey(item.source, item.skill)));
    const missingKeys = new Set(result.missing.map((item) => requirementKey(item.source, item.skill)));
    const mismatchKeys = new Set(result.mismatches.map((item) => requirementKey(item.source, item.skill)));
    const untrackedKeys = new Set(result.untracked.map((item) => requirementKey(item.source, item.skill)));

    for (const [sourceIndex, entry] of (catalog?.sources ?? []).entries()) {
      if ((entry.skills ?? []).length === 0) continue;
      const number = String(sourceIndex + 1);
      out(`${pipe}  ${number}  ${fg(C.gray, redactSource(entry.source))}`);
      for (const skill of entry.skills ?? []) {
        const key = requirementKey(entry.source, skill);
        let marker = "□";
        let markerColor = C.gray;
        if (installedKeys.has(key)) {
          marker = "■";
          markerColor = C.green;
        } else if (mismatchKeys.has(key) || untrackedKeys.has(key)) {
          marker = "▲";
          markerColor = C.red;
        } else if (missingKeys.has(key)) {
          marker = "□";
          markerColor = C.gray;
        }
        skillItem({
          name: skill,
          marker,
          markerColor,
          indent: "      ",
        });
      }
      out(pipe);
    }
  }

  function status({ projectRoot, profileNames, catalog, status: result }) {
    title();
    step(`Status: ${projectRoot}`);
    if (profileNames.length > 0) step(`Profiles: ${profileNames.join(", ")}`);
    renderStatusTree(catalog, result);
    requirementSection("Source mismatch", result.mismatches, C.red);
    requirementSection("Untracked", result.untracked, C.red);
    if (result.extras.length > 0) {
      active("Extra");
      skillList(result.extras, (extra) => skillItem({
        name: extra.name,
        suffix: extra.source ? `— ${redactSource(extra.source)}` : "",
        markerColor: C.yellow,
        suffixColor: C.yellow,
      }));
      out(pipe);
    }
    if (result.desiredConflicts.length > 0) {
      active("Desired-source conflict");
      skillList(result.desiredConflicts, (conflict) => skillItem({
        name: conflict.skill,
        suffix: `— ${conflict.sources.map(redactSource).join(" vs ")}`
          + ` — required by ${conflict.profiles.join(", ")}`,
        markerColor: C.red,
        suffixColor: C.red,
      }));
      out(pipe);
    }
    listEnd();
  }

  function installPlan({ projectRoot, profileNames, plan, dryRun = false }) {
    title();
    step(`${dryRun ? "DRY RUN — " : ""}Install plan: ${projectRoot}`);
    if (profileNames.length > 0) step(`Profiles: ${profileNames.join(", ")}`);
    requirementSection("Install", plan.install);
    requirementSection("Already installed", plan.skip, C.gray);
    requirementSection("Conflict", plan.conflicts, C.red);
    if (plan.extras.length > 0) {
      active("Extra");
      skillList(plan.extras, (extra) => skillItem({
        name: extra.name,
        suffix: extra.source ? `— ${redactSource(extra.source)}` : "",
        markerColor: C.gray,
        suffixColor: C.gray,
      }));
      out(pipe);
    }
    if (plan.desiredConflicts.length > 0) {
      active("Desired-source conflict");
      skillList(plan.desiredConflicts, (conflict) => skillItem({
        name: conflict.skill,
        suffix: `— ${conflict.sources.map(redactSource).join(" vs ")}`,
        markerColor: C.red,
        suffixColor: C.red,
      }));
      out(pipe);
    }
    listEnd();
  }

  function uninstallPlan({
    projectRoot,
    profileNames,
    plan,
    dryRun = false,
    keepLink = false,
  }) {
    title();
    step(`${dryRun ? "DRY RUN — " : ""}Uninstall plan: ${projectRoot}`);
    if (profileNames.length > 0) step(`Profiles: ${profileNames.join(", ")}`);
    requirementSection("Remove", plan.remove, C.yellow);
    requirementSection("Keep", plan.retain, C.gray);
    requirementSection("Already absent", plan.absent, C.gray);
    requirementSection("Conflict", plan.conflicts, C.red);
    if (!keepLink && plan.unlinkProfiles.length > 0) {
      active("Unlink");
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
    const label = operation === "uninstall"
      ? "Uninstall"
      : operation === "changes"
        ? "Changes"
        : "Install";
    step(result.ok ? `${label} complete` : `${label} incomplete`);
    step(`${result.succeeded.length} succeeded; ${result.failed.length} failed`);
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

  function applyPreview({
    install = [],
    remove = [],
    catalog,
    heading,
    confirmState,
  }) {
    stdout.write("\u001b[2J\u001b[H");
    title();
    step(String(heading));
    for (const [label, requirements] of [["Install", install], ["Remove", remove]]) {
      if (requirements.length === 0) continue;
      active(label);
      out(pipe);
      for (const group of groupRequirementsByCatalogSource(requirements, catalog)) {
        out(`${pipe}  ${group.sourceIndex}  ${fg(C.gray, group.label)}`);
        for (const skill of group.skills) {
          skillItem({ name: skill, indent: "      " });
        }
        out(pipe);
      }
    }
    active(`Select an item ${fg(C.white, "(enter to continue, q to quit)")}`);
    out(pipe);
    confirmState.items.forEach((entry, index) => {
      const selected = index === confirmState.cursor;
      const box = selected ? "■" : "□";
      const boxColor = selected ? C.brightGreen : C.gray;
      const labelColor = selected ? C.white : C.gray;
      out(`${pipe}  ${boxColor}${box}${C.reset} ${fg(labelColor, entry.label)}`);
    });
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
      ? `Select items ${fg(C.white, "(space toggle, a all, c clear, enter to continue, q to quit)")}`
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
      const isCursor = index === state.cursor;
      const labelColor = skillEntry
        ? selectorNameColor({ isCursor, isSelected: selected })
        : isCursor ? C.white : C.gray;
      const hintText = hint
        ? ` ${SELECTOR_DESCRIPTION_COLOR}${hint}${C.reset}`
        : "";
      out(`${pipe}  ${boxColor}${box}${C.reset} ${fg(labelColor, label)}${hintText}`);
    });
    if (cancelled) listEnd(fg(C.red, "Selection cancelled"));
    else listEnd();
  }

  return {
    usage,
    sourceChanged,
    status,
    installPlan,
    uninstallPlan,
    applyPreview,
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
