#!/usr/bin/env node

import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";
import {
  initializeConfig as initializeConfigDefault,
  readConfig as readConfigDefault,
  writeConfigTransaction as writeConfigTransactionDefault,
  writeProfiles as writeProfilesDefault,
  writeProjects as writeProjectsDefault,
} from "./config.mjs";
import { runDashboard as runDashboardDefault } from "./dashboard.mjs";
import { loadInstalledState as loadInstalledStateDefault } from "./installed-state.mjs";
import {
  runInstallCommand as runInstallCommandDefault,
  runStatusCommand as runStatusCommandDefault,
  runUninstallCommand as runUninstallCommandDefault,
} from "./lifecycle-commands.mjs";
import {
  runProfileCommand as runProfileCommandDefault,
  runProjectCommand as runProjectCommandDefault,
  runSkillCommand as runSkillCommandDefault,
  runSourceCommand as runSourceCommandDefault,
} from "./manage-commands.mjs";
import {
  executeInstallPlan as executeInstallPlanDefault,
  executeUninstallPlan as executeUninstallPlanDefault,
} from "./operations.mjs";
import { resolveProjectRoot as resolveProjectRootDefault } from "./projects.mjs";
import { runSelector as runSelectorDefault } from "./selector.mjs";
import {
  discoverAvailableSkills as discoverAvailableSkillsDefault,
  hasCommand as hasCommandDefault,
} from "./skills-cli.mjs";
import { createUi } from "./ui.mjs";

function requiresNpx(action, args) {
  if (["status", "install", "uninstall"].includes(action)) return true;
  if (action === "source") {
    const subcommand = args[0];
    if (subcommand === "show" || subcommand === "edit") return true;
    return subcommand === "add" && !args.includes("--no-skills");
  }
  return action === "skill" && args[0] === "add";
}

export async function runCli(argv, dependencies = {}) {
  const {
    cwd = process.cwd(),
    env = process.env,
    stdin = process.stdin,
    stdout = process.stdout,
    stderr = process.stderr,
    hasCommand = hasCommandDefault,
    initializeConfig = initializeConfigDefault,
    readConfig = readConfigDefault,
    writeProfiles = writeProfilesDefault,
    writeProjects = writeProjectsDefault,
    writeConfigTransaction = writeConfigTransactionDefault,
    resolveProjectRoot = resolveProjectRootDefault,
    discoverAvailableSkills = discoverAvailableSkillsDefault,
    loadInstalledState = loadInstalledStateDefault,
    executeInstallPlan = executeInstallPlanDefault,
    executeUninstallPlan = executeUninstallPlanDefault,
    selectorRunner = runSelectorDefault,
    runProfileCommand = runProfileCommandDefault,
    runSourceCommand = runSourceCommandDefault,
    runSkillCommand = runSkillCommandDefault,
    runProjectCommand = runProjectCommandDefault,
    runStatusCommand = runStatusCommandDefault,
    runInstallCommand = runInstallCommandDefault,
    runUninstallCommand = runUninstallCommandDefault,
    runDashboard = runDashboardDefault,
    pathExists = existsSync,
    ui = createUi({ stdout, stderr }),
  } = dependencies;

  const action = argv[0];
  const args = argv.slice(1);
  const requireNpx = () => {
    if (hasCommand("npx", { env })) return true;
    ui.error("npx is required to run skills commands");
    return false;
  };
  if (["help", "-h", "--help"].includes(action)) {
    ui.usage();
    return 0;
  }

  const routes = {
    profile: runProfileCommand,
    source: runSourceCommand,
    skill: runSkillCommand,
    project: runProjectCommand,
    status: runStatusCommand,
    install: runInstallCommand,
    uninstall: runUninstallCommand,
  };

  if (action !== undefined && !Object.hasOwn(routes, action)) {
    ui.error(`Unknown command: ${action}`);
    ui.usageLine?.("Use 'skm --help' for usage information");
    return 1;
  }
  if (action === undefined && (!stdin.isTTY || !stdout.isTTY)) {
    ui.error("skm requires an interactive terminal");
    return 1;
  }
  if (requiresNpx(action, args) && !requireNpx()) return 1;

  let paths;
  let config;
  try {
    paths = initializeConfig({ env });
  } catch {
    ui.error("Could not create config directory.");
    return 1;
  }
  try {
    config = readConfig(paths);
  } catch (error) {
    ui.error(error instanceof Error ? error.message : String(error));
    return 1;
  }

  const select = async (items, {
    initial = [],
    multiple = true,
    title = "Select items",
    render,
  } = {}) => {
    const result = await selectorRunner({
      items,
      initial,
      multiple,
      input: stdin,
      render: render ?? ((state) => ui.selector(title, state, {
        mode: multiple ? "install" : "select",
      })),
    });
    if (result.type === "cancel" && !render) {
      ui.cancelledSelector(title, result.state, { mode: multiple ? "install" : "select" });
    }
    return result;
  };

  const context = {
    cwd,
    env,
    stdin,
    stdout,
    stderr,
    paths,
    config,
    ui,
    pathExists,
    requireNpx,
    resolveProjectRoot,
    discoverAvailableSkills,
    loadInstalledState: ({ projectRoot }) => loadInstalledState({ projectRoot, env }),
    executeInstallPlan,
    executeUninstallPlan,
    writeProfiles,
    writeProjects,
    writeConfigTransaction,
    selectItems: ({ items, ...options }) => select(items, options),
    selectProfiles: (items, options) => select(items, options),
    selectSkills: (items, options) => select(items, options),
    selectAction: (items, options) => select(items, options),
    confirm: async (message) => {
      ui.confirm(message);
      const result = await select([
        { value: true, label: "Yes" },
        { value: false, label: "No" },
      ], { multiple: false, title: message });
      return result.type === "submit" && result.selected[0] === true;
    },
    confirmSaveLinks: async ({ projectRoot, profileNames }) => {
      const message = `Link ${profileNames.join(", ")} to ${projectRoot}?`;
      ui.confirm(message);
      const result = await select([
        { value: true, label: "Yes" },
        { value: false, label: "No" },
      ], { multiple: false, title: message });
      return result.type === "submit" && result.selected[0] === true;
    },
    runProfileCommand,
    runSourceCommand,
    runSkillCommand,
    runProjectCommand,
    runStatusCommand,
    runInstallCommand,
    runUninstallCommand,
  };

  if (action === undefined) return runDashboard(context);
  return routes[action](args, context);
}

async function main() {
  process.exitCode = await runCli(process.argv.slice(2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
