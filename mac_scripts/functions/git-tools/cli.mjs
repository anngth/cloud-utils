#!/usr/bin/env node

import { pathToFileURL } from "node:url";
import { runFetchCommand as runFetchCommandDefault } from "./fetch.mjs";
import { runPushCommand as runPushCommandDefault } from "./push.mjs";
import { createUi } from "./ui.mjs";

export async function runCli(argv, dependencies = {}) {
  const {
    cwd = process.cwd(),
    env = process.env,
    stdout = process.stdout,
    stderr = process.stderr,
    runPushCommand = runPushCommandDefault,
    runFetchCommand = runFetchCommandDefault,
    ui = createUi({ stdout, stderr }),
  } = dependencies;

  const action = argv[0];
  const args = argv.slice(1);

  if (!action) {
    ui.usage();
    return 1;
  }

  if (["help", "-h", "--help"].includes(action)) {
    ui.usage();
    return 0;
  }

  const context = { cwd, env, stdout, stderr, ui, ...dependencies };

  if (action === "push") return runPushCommand(args, context);
  if (action === "fetch") return runFetchCommand(args, context);

  ui.error(`Unknown command: ${action}`);
  ui.usage();
  return 1;
}

async function main() {
  process.exitCode = await runCli(process.argv.slice(2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
