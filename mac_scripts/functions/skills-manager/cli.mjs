#!/usr/bin/env node

import { dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { initializeConfig } from "./config.mjs";
import { runSelector } from "./selector.mjs";
import { hasCommand, runNpx } from "./skills-cli.mjs";
import {
  SourceListError,
  addSources,
  hasSource,
  normalizeSourceFile,
  readSources,
  removeSources,
  writeSourcesAtomic,
} from "./sources.mjs";
import { createUi } from "./ui.mjs";

const MANAGER_DIR = dirname(fileURLToPath(import.meta.url));
const NO_SOURCES = "No sources saved. Add one with: skm add <source>";

export async function runCli(argv, {
  env = process.env,
  stdin = process.stdin,
  stdout = process.stdout,
  stderr = process.stderr,
  managerDir = MANAGER_DIR,
  npxRunner = runNpx,
  selectorRunner = runSelector,
} = {}) {
  const ui = createUi({ stdout, stderr });
  let skillsFile;
  try {
    ({ skillsFile } = initializeConfig({ env, managerDir, stderr }));
  } catch {
    ui.error("Could not create config directory.");
    return 1;
  }

  const requireNpx = () => {
    if (hasCommand("npx", { env })) return true;
    ui.error("npx is required to run 'npx skills add'");
    return false;
  };

  const load = () => {
    normalizeSourceFile(skillsFile);
    return readSources(skillsFile);
  };

  const action = argv[0];
  const args = argv.slice(1);

  try {
    if (["help", "-h", "--help"].includes(action)) {
      ui.usage();
      return 0;
    }
    if (action === undefined) return await runInstallSelector();
    if (action === "ls" || action === "list") {
      if (!requireNpx()) return 1;
      ui.list(skillsFile, load());
      return 0;
    }
    if (action === "show" || action === "list-available") {
      return await runShow(args);
    }
    if (action === "add") return runAdd(args);
    if (action === "remove" || action === "rm") return runRemove(args);
    ui.error(`Unknown command: ${action}`);
    ui.usageLine("Use 'skm --help' for usage information");
    return 1;
  } catch (error) {
    renderError(error);
    return 1;
  }

  function renderError(error) {
    if (error instanceof SourceListError) {
      ui.error(`Could not read source list: ${error.filePath}`);
    } else {
      ui.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function runShow(showArgs) {
    if (showArgs.length > 1) {
      ui.error("show accepts at most one source");
      ui.usageLine("Usage: skm show [source]");
      return 1;
    }
    if (!requireNpx()) return 1;
    if (showArgs.length === 1) {
      return await npxRunner(["skills", "add", showArgs[0], "--list"]);
    }
    normalizeSourceFile(skillsFile);
    if (!stdin.isTTY || !stdout.isTTY) {
      ui.error("skm show requires an interactive terminal when no source is provided");
      ui.usageLine("Usage: skm show <source>");
      return 1;
    }
    const items = readSources(skillsFile);
    if (items.length === 0) {
      ui.warn(NO_SOURCES);
      return 1;
    }
    const result = await selectorRunner({
      sources: items.map((item) => item.source),
      multiple: false,
      input: stdin,
      render: (state) => ui.selector(skillsFile, state, { mode: "show" }),
    });
    if (result.type === "cancel") {
      ui.cancelledSelector(skillsFile, result.state, { mode: "show" });
      return 0;
    }
    return await npxRunner(["skills", "add", result.selected[0], "--list"]);
  }

  function runAdd(requested) {
    if (requested.length === 0) {
      ui.error("At least one source is required");
      ui.usageLine("Usage: skm add <source...>");
      return 1;
    }
    if (!requireNpx()) return 1;
    const result = addSources(load(), requested);
    writeSourcesAtomic(skillsFile, result.items);
    ui.addResult(skillsFile, requested.length, result);
    return 0;
  }

  function runRemove(requested) {
    if (requested.length === 0) {
      ui.error("At least one source is required");
      ui.usageLine("Usage: skm remove <source...>");
      return 1;
    }
    if (!requireNpx()) return 1;
    const result = removeSources(load(), requested);
    writeSourcesAtomic(skillsFile, result.items);
    ui.removeResult(skillsFile, requested.length, result);
    return 0;
  }

  async function runInstallSelector() {
    if (!requireNpx()) return 1;
    normalizeSourceFile(skillsFile);
    if (!stdin.isTTY || !stdout.isTTY) {
      ui.error("skm requires an interactive terminal");
      return 1;
    }
    const items = readSources(skillsFile);
    if (items.length === 0) {
      ui.warn(NO_SOURCES);
      return 1;
    }
    const result = await selectorRunner({
      sources: items.map((item) => item.source),
      multiple: true,
      input: stdin,
      render: (state) => ui.selector(skillsFile, state, { mode: "install" }),
    });
    if (result.type === "cancel") {
      ui.cancelledSelector(skillsFile, result.state, { mode: "install" });
      return 0;
    }
    if (result.selected.length === 0) {
      ui.listEnd();
      ui.warn("No sources selected");
      return 1;
    }

    ui.listEnd();
    ui.blank();
    let failed = false;
    for (const source of result.selected) {
      let current;
      try {
        current = readSources(skillsFile);
      } catch (error) {
        renderError(error);
        failed = true;
        continue;
      }
      if (!hasSource(current, source)) {
        ui.error(`Source not found: ${source}`);
        failed = true;
        continue;
      }

      ui.installing(source);
      try {
        if (await npxRunner(["skills", "add", source]) !== 0) failed = true;
      } catch (error) {
        renderError(error);
        failed = true;
      }
    }
    return failed ? 1 : 0;
  }
}

async function main() {
  process.exitCode = await runCli(process.argv.slice(2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
