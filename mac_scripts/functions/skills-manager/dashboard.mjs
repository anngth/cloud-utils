import { linkProjectProfiles, removeProject } from "./projects.mjs";

export const DASHBOARD_ACTIONS = Object.freeze([
  { value: "install-linked", label: "Install linked profiles" },
  { value: "edit-links", label: "Link or unlink profiles" },
  { value: "install-once", label: "Install other profiles once" },
  { value: "status", label: "View status" },
  { value: "manage", label: "Manage profiles" },
  { value: "exit", label: "Exit" },
]);

function profileItems(profiles) {
  return profiles.profiles.map(({ name, sources }) => ({
    value: name,
    label: name,
    hint: `${sources.reduce((count, source) => count + source.skills.length, 0)} selected skills`,
  }));
}

async function chooseProfiles(context, { initial = [], title }) {
  return context.selectProfiles(profileItems(context.config.profiles), {
    initial,
    multiple: true,
    title,
  });
}

function saveProjectLinks(context, projectRoot, selected) {
  const names = new Set(context.config.profiles.profiles.map(({ name }) => name));
  let projects = removeProject(context.config.projects, projectRoot);
  if (selected.length > 0) {
    projects = linkProjectProfiles(projects, projectRoot, [...new Set(selected)], names);
  }
  context.writeProjects(context.paths, context.config.profiles, projects);
}

export async function runDashboard(context) {
  try {
    const projectRoot = context.resolveProjectRoot({ cwd: context.cwd });
    const linkedProfiles = context.config.projects.projects
      .find(({ root }) => root === projectRoot)?.profiles ?? [];
    context.ui.dashboard({ projectRoot, linkedProfiles, actions: DASHBOARD_ACTIONS });

    const selection = await context.selectAction(DASHBOARD_ACTIONS, {
      multiple: false,
      title: "Choose an action",
      render: (state) => context.ui.dashboard({
        projectRoot,
        linkedProfiles,
        actions: DASHBOARD_ACTIONS,
        state,
      }),
    });
    if (selection.type !== "submit") return 0;
    const action = selection.selected[0];

    if (action === "exit") return 0;
    if (action === "manage") return context.runProfileCommand(["list"], context);
    if (["install-linked", "install-once", "status"].includes(action)
      && context.requireNpx && !context.requireNpx()) return 1;
    if (action === "status") return context.runStatusCommand([], context);

    if (action === "edit-links") {
      const profiles = await chooseProfiles(context, {
        initial: linkedProfiles,
        title: "Select linked profiles",
      });
      if (profiles.type !== "submit") return 0;
      saveProjectLinks(context, projectRoot, profiles.selected);
      return 0;
    }

    if (action === "install-once") {
      const profiles = await chooseProfiles(context, { title: "Select profiles to install once" });
      if (profiles.type !== "submit") return 0;
      const selected = [...new Set(profiles.selected)];
      if (selected.length === 0) {
        context.ui.warn("No profiles selected");
        return 0;
      }
      return context.runInstallCommand(selected, context);
    }

    if (action === "install-linked") {
      if (linkedProfiles.length > 0) return context.runInstallCommand([], context);
      const profiles = await chooseProfiles(context, { title: "Select profiles to install" });
      if (profiles.type !== "submit") return 0;
      const selected = [...new Set(profiles.selected)];
      if (selected.length === 0) {
        context.ui.warn("No profiles selected");
        return 0;
      }
      const status = await context.runInstallCommand(selected, context);
      if (status !== 0) return status;
      const saveLinks = await context.confirmSaveLinks({ projectRoot, profileNames: selected });
      if (saveLinks) saveProjectLinks(context, projectRoot, selected);
      return 0;
    }

    context.ui.error(`Unknown dashboard action: ${action}`);
    return 1;
  } catch (error) {
    context.ui.error(error instanceof Error ? error.message : String(error));
    return 1;
  }
}
