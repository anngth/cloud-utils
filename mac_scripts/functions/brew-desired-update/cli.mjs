#!/usr/bin/env node
import { pathToFileURL } from "node:url";
import { createUi } from "./ui.mjs";

const HELP = new Set(["help", "-h", "--help"]);
const KNOWN = new Set(["list", "ls", "add", "remove"]);

export async function runCli(argv, dependencies = {}) {
  const {
    stdout = process.stdout,
    stderr = process.stderr,
    ui = createUi({ stdout, stderr }),
  } = dependencies;

  const action = argv[0];

  if (action === undefined || action === "-e" || action === "--exclude") {
    ui.error("Update command not wired yet");
    return 1;
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

  ui.error(`Command not wired yet: ${action}`);
  return 1;
}

async function main() {
  process.exitCode = await runCli(process.argv.slice(2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
