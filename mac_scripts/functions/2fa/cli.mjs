#!/usr/bin/env node
import { pathToFileURL } from "node:url";
import { createUi } from "./ui.mjs";

export async function runCli(argv, dependencies = {}) {
  const {
    stdout = process.stdout,
    stderr = process.stderr,
    ui = createUi({ stdout, stderr }),
  } = dependencies;

  const action = argv[0];

  if (action !== undefined && ["help", "-h", "--help"].includes(action)) {
    ui.usage();
    return 0;
  }

  if (action !== undefined) {
    ui.error(`Unknown command: ${action}`);
    ui.usage();
    return 1;
  }

  // Bare path filled in Task 3
  ui.error("not implemented");
  return 1;
}

async function main() {
  process.exitCode = await runCli(process.argv.slice(2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
