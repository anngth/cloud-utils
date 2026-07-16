const C = {
  cyan: "\u001b[36m",
  green: "\u001b[32m",
  red: "\u001b[31m",
  yellow: "\u001b[33m",
  black: "\u001b[30m",
  gray: "\u001b[90m",
  brightGreen: "\u001b[92m",
  white: "\u001b[97m",
  bgCyan: "\u001b[46m",
  fgReset: "\u001b[39m",
  bgReset: "\u001b[49m",
  reset: "\u001b[0m",
};

const fg = (color, text) => `${color}${text}${C.fgReset}`;
const pipe = fg(C.cyan, "│");

export function createUi({ stdout = process.stdout, stderr = process.stderr } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const err = (line) => stderr.write(`${line}\n`);
  const title = () => {
    out();
    out(`   ${C.bgCyan}${C.black} SKILLS MANAGER ${C.fgReset}${C.bgReset}`);
    out(pipe);
  };
  const step = (text) => {
    out(`${fg(C.green, "◇")}  ${text}`);
    out(pipe);
  };
  const active = (text) => out(`${fg(C.cyan, "◆")}  ${text}`);
  const listEnd = () => out(fg(C.cyan, "└"));

  function usage() {
    title();
    step("Usage: skm <command> [args]");
    active("Available commands");
    out();
    out(`${pipe}  ${fg(C.green, "ls")}, ${fg(C.green, "list")}`);
    out(`${pipe}      Show saved sources from skm/list.json`);
    out(pipe);
    out(`${pipe}  ${fg(C.green, "show")} [source]`);
    out(`${pipe}      Show available skills; opens a single-select UI without source`);
    out(pipe);
    out(`${pipe}  ${fg(C.green, "add")} <source...>`);
    out(`${pipe}      Add or update saved sources`);
    out(pipe);
    out(`${pipe}  ${fg(C.green, "remove")} <source...>`);
    out(`${pipe}      Remove saved sources`);
    listEnd();
  }

  function list(file, items) {
    title();
    step(`Source list: ${file}`);
    step(`Found ${fg(C.green, String(items.length))} sources`);
    active("Saved sources");
    if (items.length === 0) {
      out(`${pipe}  ${fg(C.yellow, "■")} No sources saved`);
    } else {
      for (const item of items) out(`${pipe}  ${fg(C.green, "■")} ${item.source}`);
    }
    listEnd();
  }

  function addResult(file, requestCount, result) {
    title();
    step(`Source list: ${file}`);
    step(`Add request: ${requestCount} sources`);
    active("Add sources");
    for (const item of result.results) {
      const added = item.status === "added";
      out(`${pipe}  ${fg(added ? C.green : C.yellow, "■")} ${added ? "Added" : "Already exists"}: ${item.source}`);
    }
    out(`${fg(C.cyan, "└")}  Added ${result.added} sources, skipped ${result.skipped} existing`);
  }

  function removeResult(file, requestCount, result) {
    title();
    step(`Source list: ${file}`);
    step(`Remove request: ${requestCount} sources`);
    active("Remove sources");
    for (const item of result.results) {
      const removed = item.status === "removed";
      out(`${pipe}  ${fg(removed ? C.green : C.yellow, "■")} ${removed ? "Removed" : "Not found"}: ${item.source}`);
    }
    out(`${fg(C.cyan, "└")}  Removed ${result.removed} sources, skipped ${result.skipped} missing`);
  }

  function renderSelector(file, state, { mode, cancelled }) {
    stdout.write("\u001b[2J\u001b[H");
    title();
    step(`Source list: ${file}`);
    step(`Found ${fg(C.green, String(state.sources.length))} sources`);
    const install = mode === "install";
    active(install
      ? `Select sources to install ${fg(C.white, "(space to toggle, enter to start, q to quit)")}`
      : `Select source to inspect ${fg(C.white, "(enter to show, q to quit)")}`);
    out(pipe);
    state.sources.forEach((source, index) => {
      const selected = install ? state.selected.has(index) : index === state.cursor;
      const box = selected ? "■" : "□";
      const boxColor = selected ? C.brightGreen : C.gray;
      const sourceColor = index === state.cursor ? C.white : C.gray;
      out(`${pipe}  ${boxColor}${box}${C.reset} ${sourceColor}${source}${C.reset}`);
    });
    if (cancelled) {
      out(pipe);
      const label = install ? "Select sources to install" : "Select source to inspect";
      out(`${fg(C.cyan, "└")}  ${fg(C.red, `${label} cancelled`)}`);
    } else {
      listEnd();
    }
  }

  function selector(file, state, { mode }) {
    renderSelector(file, state, { mode, cancelled: false });
  }

  function cancelledSelector(file, state, { mode }) {
    renderSelector(file, state, { mode, cancelled: true });
  }

  return {
    usage,
    list,
    addResult,
    removeResult,
    selector,
    cancelledSelector,
    listEnd,
    installing(source) { out(`${fg(C.cyan, "▶ Installing source:")} ${source}`); },
    blank() { out(); },
    error(message) { err(fg(C.red, `❌ ${message}`)); },
    warn(message) { err(fg(C.yellow, `⚠️  ${message}`)); },
    usageLine(text) { out(text); },
  };
}
