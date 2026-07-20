import { redactSource } from "./source-id.mjs";

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
    command("skm", "Open interactive dashboard");
    command("skm (help | -h | --help)", "Show this help");

    section("Lifecycle");
    command("skm status [profile...]", "Compare desired and installed skills");
    command("skm install [profile...] [(-y | --yes)] [(-f | --force)] [(-d | --dry-run)]", "Install selected profile skills");
    command("skm uninstall [profile...] [(-y | --yes)] [(-f | --force)]");
    continuation("[(-d | --dry-run)] [(-l | --keep-link)]", "Uninstall selected profile skills");

    section("Profiles");
    command("skm profile list", "List profiles");
    command("skm profile show <profile>", "Show one profile");
    command("skm profile create <profile>", "Create a profile");
    command("skm profile rename <old> <new>", "Rename a profile");
    command("skm profile remove <profile> [(-f | --force)]", "Remove a profile");

    section("Sources");
    command("skm source add <source> [(-p | --profile) <profile>]");
    continuation("[[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)]", "Add a source and select skills");
    command("skm source edit <source> [(-p | --profile) <profile>]", "Edit selected source skills");
    command("skm source remove <source> [(-p | --profile) <profile>]", "Remove a source from a profile");
    command("skm source show <source>", "Show available source skills");

    section("Skills");
    command("skm skill add <skill...> (-s | --source) <source>");
    continuation("(-p | --profile) <profile>", "Add skills to a profile source");
    command("skm skill remove <skill...> (-s | --source) <source>");
    continuation("(-p | --profile) <profile>", "Remove skills from a profile source");

    section("Projects");
    command("skm project link <profile...>", "Link profiles to the current project");
    command("skm project unlink [profile...]", "Unlink profiles from the current project");
    command("skm project show", "Show the current project");
    command("skm project list", "List registered projects");
    command("skm project remove [project-path]", "Remove a project registration");

    section("Notes");
    note("Profile names omitted from lifecycle commands use current project links.");
    note("Profile, source, skill, and project commands change configuration only.");
    note("--force permits linked-profile removal or mismatch/untracked skill changes.");
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

  const requirementText = ({ skill, source, profiles = [] }) => (
    `${skill} — ${redactSource(source)}${profiles.length > 0 ? ` — required by ${profiles.join(", ")}` : ""}`
  );

  function requirementSection(label, values, color = C.green) {
    active(label);
    if (values.length === 0) item("None", C.gray);
    for (const value of values) item(requirementText(value), color);
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
    for (const extra of result.extras) {
      item(`${extra.name}${extra.source ? ` — ${redactSource(extra.source)}` : ""}`, C.yellow);
    }
    out(pipe);
    active("Desired-source conflict");
    if (result.desiredConflicts.length === 0) item("None", C.gray);
    for (const conflict of result.desiredConflicts) {
      item(
        `${conflict.skill} — ${conflict.sources.map(redactSource).join(" vs ")} — required by ${conflict.profiles.join(", ")}`,
        C.red,
      );
    }
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
    for (const extra of plan.extras) {
      item(`${extra.name}${extra.source ? ` — ${redactSource(extra.source)}` : ""}`, C.gray);
    }
    out(pipe);
    active("Desired-source conflict");
    if (plan.desiredConflicts.length === 0) item("None", C.gray);
    for (const conflict of plan.desiredConflicts) {
      item(`${conflict.skill} — ${conflict.sources.map(redactSource).join(" vs ")}`, C.red);
    }
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
      for (const record of incompleteReplacements) {
        const retry = retryCommand({
          action: "install",
          source: record.source,
          skills: [record.skill],
        });
        item(
          `Old skill ${record.skill} was removed, but replacement from ${redactSource(record.source)} failed. Retry: ${retry} (status ${record.installStatus})`,
          C.red,
        );
      }
      out(pipe);
    }
    if (result.succeeded.length > 0) {
      active("Succeeded");
      for (const record of result.succeeded) {
        item(`${record.action}: ${record.skills.join(", ")}`);
      }
      out(pipe);
    }
    if (result.failed.length > 0) {
      active("Failed — retry these commands");
      for (const record of result.failed) {
        item(`${retryCommand(record)} (status ${record.status})`, C.red);
      }
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
      const selected = mode === "install" ? state.selected.has(index) : index === state.cursor;
      const box = selected ? "■" : "□";
      const boxColor = selected ? C.brightGreen : C.gray;
      const labelColor = index === state.cursor ? C.white : C.gray;
      out(`${pipe}  ${boxColor}${box}${C.reset} ${labelColor}${label}${C.reset}${hint ? ` ${fg(C.gray, hint)}` : ""}`);
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
    error(message) { err(fg(C.red, `❌ ${message}`)); },
    warn(message) { err(fg(C.yellow, `⚠️  ${message}`)); },
    info(message) { out(message); },
  };
}
