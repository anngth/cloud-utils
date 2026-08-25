import { loadDesiredDocument } from "./config.mjs";
import {
  createBrewRunner,
  listBrewTaps,
  loadBrewState,
  resolveBrewBinary,
} from "./brew.mjs";

const GAP = 2;

/**
 * @param {string[]} desired
 * @param {string[]} installed
 */
function partitionCategory(desired, installed) {
  const has = new Set(installed);
  const want = new Set(desired);
  const installedInList = [];
  const missing = [];

  for (const item of desired) {
    if (has.has(item)) {
      installedInList.push(item);
    } else {
      missing.push(item);
    }
  }

  const extra = [];
  const seen = new Set();
  for (const item of installed) {
    if (seen.has(item)) {
      continue;
    }
    seen.add(item);
    if (!want.has(item)) {
      extra.push(item);
    }
  }

  return { installed: installedInList, missing, extra };
}

/**
 * @param {{ desired: { formulas: string[], casks: string[], taps: string[] }, installed: { formulas: string[], casks: string[], taps: string[] } }} input
 */
export function partitionLists({ desired, installed }) {
  const formulas = partitionCategory(desired.formulas ?? [], installed.formulas ?? []);
  const casks = partitionCategory(desired.casks ?? [], installed.casks ?? []);
  const taps = (desired.taps ?? []).length > 0
    ? partitionCategory(desired.taps, installed.taps ?? [])
    : { installed: [], missing: [], extra: [] };

  return { formulas, taps, casks };
}

/**
 * @param {number} usableWidth
 * @param {string[]} items
 */
export function initGridLayout(usableWidth, items) {
  let maxLen = 1;
  for (const item of items) {
    if (item.length > maxLen) {
      maxLen = item.length;
    }
  }

  if (maxLen > usableWidth) {
    return { cols: 1, cell: usableWidth };
  }

  const cell = maxLen;
  let cols = Math.floor((usableWidth + GAP) / (maxLen + GAP));
  if (cols < 1) {
    cols = 1;
  }
  return { cols, cell };
}

/**
 * @param {string} text
 * @param {number} cell
 */
function fitCell(text, cell) {
  if (text.length > cell) {
    return text.slice(0, cell);
  }
  return text.padEnd(cell, " ");
}

/**
 * @param {string[]} items
 * @param {{ cols: number, cell: number }} layout
 */
export function formatGrid(items, { cols, cell }) {
  const n = items.length;
  if (!n || !cols) {
    return [];
  }

  const nrow = Math.ceil(n / cols);
  const empty = fitCell("", cell);
  const lines = [];

  for (let row = 0; row < nrow; row++) {
    let lastCol = -1;
    for (let col = 0; col < cols; col++) {
      const index = col * nrow + row;
      if (index < n) {
        lastCol = col;
      }
    }

    const parts = [];
    for (let col = 0; col <= lastCol; col++) {
      const index = col * nrow + row;
      parts.push(index < n ? fitCell(items[index], cell) : empty);
    }
    lines.push(`  ${parts.join("  ")}`);
  }

  return lines;
}

/**
 * @param {string[]} _args
 * @param {object} context
 */
export async function runListCommand(_args, context = {}) {
  const {
    env = process.env,
    ui,
    stdout = process.stdout,
    loadDesired = loadDesiredDocument,
    resolveBrew = resolveBrewBinary,
    loadState = loadBrewState,
    listTaps = listBrewTaps,
  } = context;

  const brewBin = resolveBrew({ env });
  if (!brewBin) {
    ui.error("Homebrew not found.");
    return 1;
  }

  const runner = context.runBrew ?? createBrewRunner({ brewBin, ui });

  ui.title();

  const loaded = await loadDesired({
    env,
    listBrewTaps: () => listTaps({ brewBin, runBrew: runner }),
  });
  if (!loaded.ok) {
    ui.error(loaded.error);
    return 1;
  }

  ui.step("Loading Homebrew state");
  const state = await loadState({ brewBin, runBrew: runner });
  ui.active("Loaded Homebrew state");
  const partitions = partitionLists({ desired: loaded.document, installed: state });
  const columns = stdout.columns || 120;
  ui.desiredStatus(partitions, {
    columns,
    desiredCounts: {
      formulas: loaded.document.formulas.length,
      taps: loaded.document.taps.length,
      casks: loaded.document.casks.length,
    },
  });
  return 0;
}
