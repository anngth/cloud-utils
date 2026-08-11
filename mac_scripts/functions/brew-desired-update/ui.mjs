import { formatGrid, initGridLayout } from "./list.mjs";

const C = {
  cyan: "\u001b[36m",
  green: "\u001b[32m",
  brightGreen: "\u001b[92m",
  blue: "\u001b[34m",
  red: "\u001b[31m",
  yellow: "\u001b[33m",
  black: "\u001b[30m",
  gray: "\u001b[90m",
  bgCyan: "\u001b[46m",
  fgReset: "\u001b[39m",
  bgReset: "\u001b[49m",
  reset: "\u001b[0m",
};

const fg = (color, text) => `${color}${text}${C.fgReset}`;

export function formatNameList(names, { limit = 8 } = {}) {
  if (!Array.isArray(names) || names.length === 0) return "";
  if (names.length <= limit) return names.join(" ");
  const shown = names.slice(0, limit).join(" ");
  return `${shown} … (+${names.length - limit} more)`;
}

export function createUi({ stdout = process.stdout, stderr = process.stderr } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const err = (line) => stderr.write(`${line}\n`);
  const pipe = fg(C.cyan, "│");
  const streamPrefix = `${pipe}  `;

  function title(label = "BREW DESIRED UPDATE") {
    out();
    out(`   ${C.bgCyan}${C.black} ${label} ${C.fgReset}${C.bgReset}`);
    out(pipe);
  }

  function step(text) {
    out(`${fg(C.green, "◇")}  ${text}`);
    out(pipe);
  }

  function active(text) {
    out(`${fg(C.cyan, "◆")}  ${text}`);
  }

  function warn(text) {
    out(`${pipe}  ${fg(C.yellow, "■")} ${text}`);
  }

  function info(text) {
    out(`${pipe}  ${fg(C.gray, text)}`);
  }

  function command(line) {
    out(`${pipe}  ${fg(C.green, line)}`);
  }

  function listEnd(text = "") {
    out(`${fg(C.cyan, "└")}${text ? `  ${fg(C.brightGreen, text)}` : ""}`);
  }

  function usage() {
    const section = (name) => {
      out(pipe);
      active(name);
    };
    const command = (syntax, description) => {
      out(`${pipe}  ${fg(C.green, syntax)}${description ? `  ${fg(C.gray, description)}` : ""}`);
    };
    const note = (text) => out(`${pipe}  ${fg(C.gray, text)}`);

    title();
    step("Usage: bud [command]");
    command("bud (help | -h | --help)", "Show this help");
    section("Update");
    command("bud [(-e | --exclude) <cask>...]", "Update brew; upgrade desired casks");
    section("Lists");
    command("bud (ls | list)", "Desired vs installed (formulae, taps, casks)");
    command("bud add <name...> [--cask | --formula | --tap]");
    command("bud remove <name...>");
    section("Notes");
    note("Bare bud does not install missing packages; it upgrades installed desired casks.");
    note("user/repo = tap; user/repo/formula adds tap + formula.");
    listEnd();
  }

  function error(message) {
    err(fg(C.red, `❌ ${message}`));
  }

  const STATUS_SECTIONS = [
    { key: "formulas", field: "installed", title: "Formulae · in list, installed", color: C.green },
    { key: "formulas", field: "missing", title: "Formulae · in list, not installed", color: C.yellow },
    { key: "formulas", field: "extra", title: "Formulae · installed, not in list", color: C.blue },
    { key: "taps", field: "installed", title: "Taps · in list, tapped", color: C.green },
    { key: "taps", field: "missing", title: "Taps · in list, not tapped", color: C.yellow },
    { key: "taps", field: "extra", title: "Taps · tapped, not in list", color: C.blue },
    { key: "casks", field: "installed", title: "Casks · in list, installed", color: C.green },
    { key: "casks", field: "missing", title: "Casks · in list, not installed", color: C.yellow },
    { key: "casks", field: "extra", title: "Casks · installed, not in list", color: C.blue },
  ];

  function desiredStatus(partitions, { columns = 120, desiredCounts } = {}) {
    const counts = {
      formulas: desiredCounts?.formulas ?? 0,
      taps: desiredCounts?.taps ?? 0,
      casks: desiredCounts?.casks ?? 0,
    };
    let termWidth = columns;
    if (termWidth < 20) {
      termWidth = 120;
    }
    const usableWidth = termWidth - 2;

    const allItems = STATUS_SECTIONS.flatMap(
      ({ key, field }) => partitions[key]?.[field] ?? [],
    );
    const layout = allItems.length > 0 ? initGridLayout(usableWidth, allItems) : null;

    title();
    step(`Desired vs installed · ${counts.formulas} formulae · ${counts.taps} taps · ${counts.casks} casks`);

    for (const section of STATUS_SECTIONS) {
      const items = partitions[section.key]?.[section.field] ?? [];
      if (!items.length) {
        continue;
      }

      out(pipe);
      out(`${pipe}  ${fg(section.color, "▸")} ${fg(C.brightGreen, section.title)} ${fg(C.gray, `(${items.length})`)}`);
      if (layout) {
        for (const line of formatGrid(items, layout)) {
          out(`${pipe}${line}`);
        }
      } else {
        for (const item of items) {
          out(`${pipe}  ${item}`);
        }
      }
    }

    listEnd();
  }

  return {
    usage,
    error,
    warn,
    info,
    command,
    title,
    step,
    active,
    listEnd,
    desiredStatus,
    streamPrefix,
  };
}
