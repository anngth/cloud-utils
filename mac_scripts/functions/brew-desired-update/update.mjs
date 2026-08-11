import { createBrewRunner, ensureTap, loadBrewState, resolveBrewBinary } from "./brew.mjs";
import { loadDesiredDocument } from "./config.mjs";
import { formatNameList } from "./ui.mjs";

const SUBCOMMANDS = new Set(["add", "remove", "list", "ls", "help"]);

/**
 * @param {string[]} argv
 */
export function parseArgv(argv) {
  const exclude = [];
  let i = 0;
  if (argv[0] === "-e" || argv[0] === "--exclude") {
    i = 1;
    while (
      i < argv.length &&
      !argv[i].startsWith("-") &&
      !SUBCOMMANDS.has(argv[i])
    ) {
      exclude.push(argv[i++]);
    }
  }
  const action = argv[i];
  const rest = argv.slice(i + (action ? 1 : 0));
  return { exclude, action, rest };
}

/**
 * @param {string[]} desiredCasks
 * @param {string[]} installedCasks
 */
export function collectCasksToUpgrade(desiredCasks, installedCasks) {
  const has = new Set(installedCasks);
  return desiredCasks.filter((cask) => has.has(cask));
}

/**
 * @param {string[]} casks
 * @param {string[]} exclude
 */
export function filterExcludedCasks(casks, exclude) {
  const excluded = new Set(exclude);
  return casks.filter((cask) => !excluded.has(cask));
}

/**
 * @param {{ exclude?: string[] }} options
 * @param {object} context
 */
export async function runUpdateCommand({ exclude = [] } = {}, context = {}) {
  const {
    env = process.env,
    ui,
    resolveBrew = resolveBrewBinary,
    loadDesired = loadDesiredDocument,
    loadBrewState: loadState = loadBrewState,
    ensureTap: ensureTapFn = ensureTap,
    runBrew: runBrewFn,
  } = context;

  const brewBin = context.brewBin ?? resolveBrew({ env });
  if (!brewBin) {
    ui.error("Homebrew not found.");
    return 1;
  }

  const runner = runBrewFn ?? createBrewRunner({ brewBin, ui });

  const loaded = await loadDesired({ env });
  if (!loaded.ok) {
    ui.error(loaded.error);
    return 1;
  }

  const { document } = loaded;

  ui.title();
  ui.step(
    `Desired lists: ${document.formulas.length} formulae · ${document.taps.length} taps · ${document.casks.length} casks`,
  );
  ui.step("Loading Homebrew state");
  let state = await loadState({ brewBin, runBrew: runner });

  const missingTaps = document.taps.filter((t) => !state.taps.includes(t));
  if (missingTaps.length > 0) {
    ui.active(`Ensuring taps · ${missingTaps.length} missing: ${formatNameList(missingTaps)}`);
    for (const tap of missingTaps) {
      const ok = await ensureTapFn(tap, { brewBin, runBrew: runner });
      if (!ok) {
        ui.error(`Failed to tap ${tap}`);
        return 1;
      }
    }
  } else {
    ui.step("Ensuring taps · all present");
  }

  ui.step("Updating Homebrew (brew update)");
  let result = await runner(["update"]);
  if (result.code !== 0) {
    ui.error("brew update failed");
    return 1;
  }

  ui.active("Upgrading formulae (brew upgrade --formula)");
  result = await runner(["upgrade", "--formula", "-y"]);
  if (result.code !== 0) {
    ui.error("brew upgrade --formula failed");
    return 1;
  }

  ui.step("Repairing taps");
  result = await runner(["tap", "--repair"]);
  if (result.code !== 0) {
    ui.error("brew tap --repair failed");
    return 1;
  }

  ui.step("Reloading Homebrew state");
  state = await loadState({ brewBin, runBrew: runner });

  const eligible = collectCasksToUpgrade(document.casks, state.casks);
  const toUpgrade = filterExcludedCasks(eligible, exclude);
  const eligibleCount = eligible.length;

  if (exclude.length > 0) {
    ui.info(`Excluding ${exclude.length} cask(s): ${formatNameList(exclude)}`);
  }

  if (toUpgrade.length > 0) {
    ui.active(
      `Upgrading casks · ${toUpgrade.length} of ${eligible.length} eligible: ${formatNameList(toUpgrade)}`,
    );
    result = await runner(["upgrade", "--cask", "-y", ...toUpgrade]);
    if (result.code !== 0) {
      ui.error("brew upgrade --cask failed");
      return 1;
    }
  } else if (eligibleCount > 0 && exclude.length > 0) {
    ui.info("All eligible casks were excluded; no cask upgrade.");
  } else {
    ui.info("No casks in list are installed; skipping cask upgrade.");
  }

  ui.step("Cleanup (brew cleanup --prune=1)");
  result = await runner(["cleanup", "--prune=1"]);
  if (result.code !== 0) {
    ui.error("brew cleanup failed");
    return 1;
  }

  return 0;
}
