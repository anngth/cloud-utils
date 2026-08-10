#!/usr/bin/env node
import { pathToFileURL } from "node:url";
import { runListCommand } from "./list.mjs";
import { runAddCommand, runRemoveCommand } from "./manage.mjs";
import { parseArgv, runUpdateCommand } from "./update.mjs";
import { createUi } from "./ui.mjs";

const HELP = new Set(["help", "-h", "--help"]);
const KNOWN = new Set(["list", "ls", "add", "remove"]);

export async function runCli(argv, dependencies = {}) {
  const {
    stdout = process.stdout,
    stderr = process.stderr,
    ui = createUi({ stdout, stderr }),
    runList = runListCommand,
    runAdd = runAddCommand,
    runRemove = runRemoveCommand,
    runUpdate = runUpdateCommand,
  } = dependencies;

  const { exclude, action, rest } = parseArgv(argv);

  if (action === undefined) {
    return runUpdate({ exclude }, { env: process.env, ui, stdout, stderr, ...dependencies });
  }

  if (HELP.has(action)) {
    ui.usage();
    return 0;
  }

  if (!KNOWN.has(action)) {
    ui.error(`Unknown command: ${action}`);
    ui.usage();
    return 1;
  }

  if (action === "list" || action === "ls") {
    return runList(rest, { env: process.env, ui, stdout, ...dependencies });
  }

  if (action === "add") {
    return runAdd(rest, { env: process.env, ui, ...dependencies });
  }

  if (action === "remove") {
    return runRemove(rest, { env: process.env, ui, ...dependencies });
  }

  ui.error(`Command not wired yet: ${action}`);
  return 1;
}

async function main() {
  process.exitCode = await runCli(process.argv.slice(2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
