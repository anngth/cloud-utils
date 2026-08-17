import { brewInfoToken, createBrewRunner, resolveBrewBinary } from "./brew.mjs";
import {
  loadDesiredDocument,
  normalizeDesiredDocument,
  resolveBudPaths,
  writeDesiredDocument,
} from "./config.mjs";

/**
 * @param {string} name
 */
export function isTapFormulaSpec(name) {
  return name.split("/").length === 3;
}

/**
 * @param {string} name
 */
export function tapFromFormulaSpec(name) {
  return name.slice(0, name.lastIndexOf("/"));
}

/**
 * @param {string} name
 */
export function formulaNameFromSpec(name) {
  return name.slice(name.lastIndexOf("/") + 1);
}

/**
 * @param {string} token
 * @param {string} name
 */
function tokenMatchesName(token, name) {
  return token === name || token.slice(token.lastIndexOf("/") + 1) === name;
}

/**
 * @param {string[]} argv
 */
export function parseManageArgs(argv) {
  let forceType = null;
  const names = [];
  for (const arg of argv) {
    if (arg === "--cask") {
      forceType = "cask";
    } else if (arg === "--formula") {
      forceType = "formula";
    } else if (arg === "--tap") {
      forceType = "tap";
    } else {
      names.push(arg);
    }
  }
  return { forceType, names };
}

/**
 * @param {string} name
 * @param {object} deps
 */
export async function detectBrewType(name, deps) {
  const {
    brewBin,
    runBrew: runBrewFn,
    ui,
    brewInfoToken: infoToken = brewInfoToken,
  } = deps;

  const formulaToken = await infoToken("formula", name, { brewBin, runBrew: runBrewFn });
  const caskToken = await infoToken("cask", name, { brewBin, runBrew: runBrewFn });

  const isFormula = Boolean(formulaToken && tokenMatchesName(formulaToken, name));
  const isCask = Boolean(caskToken && tokenMatchesName(caskToken, name));

  if (isCask && isFormula) {
    ui?.warn(`'${name}' exists as both cask and formula`);
    ui?.info(`Use 'budj add --cask ${name}' or 'budj add --formula ${name}'`);
    return { error: "dual" };
  }
  if (isCask) {
    return { type: "cask" };
  }
  if (isFormula) {
    return { type: "formula" };
  }

  ui?.warn(`'${name}' not found in Homebrew`);
  ui?.info(`Use 'brew search ${name}' to find similar packages`);
  return { error: "missing" };
}

/**
 * @param {string[]} list
 * @param {string} item
 */
function addUnique(list, item) {
  if (list.includes(item)) {
    return false;
  }
  list.push(item);
  return true;
}

/**
 * @param {string} pkgName
 * @param {object} options
 */
async function applyOneAdd(pkgName, { forceType, document, deps, detect }) {
  const { ui, brewBin, runBrew: runBrewFn } = deps;
  const runner = runBrewFn ?? (brewBin ? createBrewRunner({ brewBin, ui }) : null);
  const brewDeps = runner ? { ...deps, runBrew: runner } : deps;

  let pkgType = forceType;
  let listName = pkgName;
  let tapForFormula = "";
  let detectedViaBrew = false;
  let validatedViaBrew = false;

  if (!pkgType) {
    if (isTapFormulaSpec(pkgName)) {
      pkgType = "formula";
      listName = formulaNameFromSpec(pkgName);
      tapForFormula = tapFromFormulaSpec(pkgName);
    } else if (pkgName.includes("/")) {
      pkgType = "tap";
    } else {
      const detected = await detect(pkgName, brewDeps);
      if (detected.error) {
        return false;
      }
      pkgType = detected.type;
      detectedViaBrew = true;
    }
  } else if (pkgType === "tap") {
    if (!pkgName.includes("/") || isTapFormulaSpec(pkgName)) {
      ui?.warn("Tap name must be user/repo (e.g. mongodb/brew)");
      return false;
    }
  } else if (pkgType === "formula" && isTapFormulaSpec(pkgName)) {
    listName = formulaNameFromSpec(pkgName);
    tapForFormula = tapFromFormulaSpec(pkgName);
  } else if ((pkgType === "cask" || pkgType === "formula") && runner) {
    const flag = pkgType === "cask" ? "--cask" : "--formula";
    const info = await runner(["info", flag, pkgName]);
    if (info.code !== 0) {
      ui?.warn(`'${pkgName}' not found as a Homebrew ${pkgType}`);
      return false;
    }
    validatedViaBrew = true;
  }

  const listKey = pkgType === "cask" ? "casks" : pkgType === "formula" ? "formulas" : "taps";
  const label = pkgType === "cask" ? "cask" : pkgType === "formula" ? "formula" : "tap";

  if (document[listKey].includes(listName)) {
    ui?.warn(`'${listName}' already exists in ${label}s list`);
    return false;
  }

  if (pkgType === "formula" && !tapForFormula && runner) {
    const infoToken = deps.brewInfoToken ?? brewInfoToken;
    const token = await infoToken("formula", pkgName, { brewBin, runBrew: runner });
    if (token?.includes("/")) {
      tapForFormula = token.slice(0, token.lastIndexOf("/"));
    }
  }

  if (pkgType === "formula" && !detectedViaBrew && !validatedViaBrew && !isTapFormulaSpec(pkgName) && runner) {
    const info = await runner(["info", "--formula", pkgName]);
    if (info.code !== 0) {
      ui?.warn(`'${pkgName}' not found as a Homebrew formula`);
      return false;
    }
  }

  if (tapForFormula) {
    if (addUnique(document.taps, tapForFormula)) {
      ui?.info(`Added '${tapForFormula}' to taps (now ${document.taps.length})`);
    }
  }

  document[listKey].push(listName);
  ui?.info(`Added '${listName}' to ${label}s (now ${document[listKey].length})`);
  return true;
}

/**
 * @param {string[]} names
 * @param {{ forceType: string | null, document: object, deps: object }} options
 */
export async function applyAdd(names, { forceType, document, deps }) {
  const doc = {
    version: document.version,
    formulas: [...document.formulas],
    casks: [...document.casks],
    taps: [...document.taps],
  };
  const detect = deps.detectBrewType ?? detectBrewType;
  let succeeded = 0;
  let failed = 0;

  for (const name of names) {
    const ok = await applyOneAdd(name, { forceType, document: doc, deps, detect });
    if (ok) {
      succeeded += 1;
    } else {
      failed += 1;
    }
  }

  return {
    document: normalizeDesiredDocument(doc),
    succeeded,
    failed,
  };
}

/**
 * @param {string[]} names
 * @param {{ document: object, ui?: object }} options
 */
export function applyRemove(names, { document, ui }) {
  const doc = {
    version: document.version,
    formulas: [...document.formulas],
    casks: [...document.casks],
    taps: [...document.taps],
  };
  let succeeded = 0;
  let failed = 0;

  for (const name of names) {
    if (doc.casks.includes(name)) {
      doc.casks = doc.casks.filter((item) => item !== name);
      ui?.info(`Removed '${name}' from casks (now ${doc.casks.length})`);
      succeeded += 1;
    } else if (doc.formulas.includes(name)) {
      doc.formulas = doc.formulas.filter((item) => item !== name);
      ui?.info(`Removed '${name}' from formulas (now ${doc.formulas.length})`);
      succeeded += 1;
    } else if (doc.taps.includes(name)) {
      doc.taps = doc.taps.filter((item) => item !== name);
      ui?.info(`Removed '${name}' from taps (now ${doc.taps.length})`);
      succeeded += 1;
    } else {
      ui?.warn(`'${name}' not found in casks, formulae, or taps list`);
      failed += 1;
    }
  }

  return {
    document: normalizeDesiredDocument(doc),
    succeeded,
    failed,
  };
}

/**
 * @param {string[]} args
 * @param {object} context
 */
export async function runAddCommand(args, context = {}) {
  const {
    env = process.env,
    ui,
    loadDesired = loadDesiredDocument,
    resolveBrew = resolveBrewBinary,
    writeDesired = writeDesiredDocument,
    add = applyAdd,
  } = context;

  const { forceType, names } = parseManageArgs(args);
  if (names.length === 0) {
    ui.error("Package name is required for add action");
    return 1;
  }

  const brewBin = resolveBrew({ env });
  if (!brewBin) {
    ui.error("Homebrew not found.");
    return 1;
  }

  const loaded = await loadDesired({ env });
  if (!loaded.ok) {
    ui.error(loaded.error);
    return 1;
  }

  const { document, succeeded, failed } = await add(names, {
    forceType,
    document: loaded.document,
    deps: { ui, brewBin, runBrew: context.runBrew ?? createBrewRunner({ brewBin, ui }) },
  });

  if (succeeded > 0) {
    const { desiredFile } = resolveBudPaths(env);
    const written = writeDesired(desiredFile, document);
    if (!written.ok) {
      ui.error(written.error);
      return 1;
    }
    ui.info(`Changes saved to ${desiredFile}`);
  }

  return failed > 0 ? 1 : 0;
}

/**
 * @param {string[]} args
 * @param {object} context
 */
export async function runRemoveCommand(args, context = {}) {
  const {
    env = process.env,
    ui,
    loadDesired = loadDesiredDocument,
    writeDesired = writeDesiredDocument,
    remove = applyRemove,
  } = context;

  const { names } = parseManageArgs(args);
  if (names.length === 0) {
    ui.error("Package name is required for remove action");
    return 1;
  }

  const loaded = await loadDesired({ env });
  if (!loaded.ok) {
    ui.error(loaded.error);
    return 1;
  }

  const { document, succeeded, failed } = remove(names, {
    document: loaded.document,
    ui,
  });

  if (succeeded > 0) {
    const { desiredFile } = resolveBudPaths(env);
    const written = writeDesired(desiredFile, document);
    if (!written.ok) {
      ui.error(written.error);
      return 1;
    }
    ui.info(`Changes saved to ${desiredFile}`);
  }

  return failed > 0 ? 1 : 0;
}
