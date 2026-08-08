const C = {
  cyan: "\u001b[36m",
  green: "\u001b[32m",
  brightGreen: "\u001b[92m",
  red: "\u001b[31m",
  yellow: "\u001b[33m",
  black: "\u001b[30m",
  gray: "\u001b[90m",
  white: "\u001b[97m",
  bgCyan: "\u001b[46m",
  fgReset: "\u001b[39m",
  bgReset: "\u001b[49m",
  reset: "\u001b[0m",
};

const fg = (color, text) => `${color}${text}${C.fgReset}`;

export function createUi({ stdout = process.stdout, stderr = process.stderr } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const err = (line) => stderr.write(`${line}\n`);
  const pipe = fg(C.cyan, "│");

  function title(label = "GT") {
    out();
    out(`   ${C.bgCyan}${C.black} ${label} ${C.fgReset}${C.bgReset}`);
    out(pipe);
  }

  function step(text) {
    out(`${fg(C.green, "◇")}  ${text}`);
    out(pipe);
  }

  function success(text) {
    out(`${fg(C.green, "◇")}  ${fg(C.brightGreen, text)}`);
    out(pipe);
  }

  function active(text) {
    out(`${fg(C.cyan, "◆")}  ${text}`);
  }

  function item(text, color = C.green) {
    out(`${pipe}  ${fg(color, "■")} ${text}`);
  }

  /** Indented continuation under an item (no ■). */
  function detail(text, color = C.gray) {
    out(`${pipe}      ${fg(color, text)}`);
  }

  function warn(text) {
    out(`${pipe}  ${fg(C.yellow, "■")} ${text}`);
  }

  function listEnd(text = "") {
    out(`${fg(C.cyan, "└")}${text ? `  ${fg(C.brightGreen, text)}` : ""}`);
  }

  function usage() {
    title("GT");
    step("Usage: gt <command>");
    active("Commands");
    item("push              Force push (safe --force-with-lease)");
    item("fetch [--sync-upstream]");
    item("backup              Interactive select from managed list");
    item("backup --all         Backup every repo in the list");
    item("backup add <ssh-url>  Add SSH URL to managed list");
    item("backup remove <index|ssh-url>  Remove by 1-based index or URL");
    listEnd("Run 'gt --help' for this message.");
  }

  function error(message) {
    err(fg(C.red, `❌ ${message}`));
  }

  function usageLine(message) {
    err(fg(C.gray, message));
  }

  /** Progress / status line — same visual language as skm `step`. */
  function status(message) {
    step(message);
  }

  function line(message = "") {
    out(message);
  }

  function renderBackupSelector(heading, state, { listPath } = {}) {
    stdout.write("\u001b[2J\u001b[H");
    title("REPO BACKUP");
    if (listPath) {
      step(listPath);
    }
    step(String(heading));
    active(`Select repos ${fg(C.white, "(space to toggle, enter to start, q to quit)")}`);
    out(pipe);
    state.items.forEach((entry, index) => {
      const label = typeof entry === "string" ? entry : entry.label;
      const selected = state.selected.has(index);
      const box = selected ? "■" : "□";
      const boxColor = selected ? C.brightGreen : C.gray;
      const labelColor = index === state.cursor ? C.white : C.gray;
      const number = String(index + 1);
      out(`${pipe}  ${number}  ${boxColor}${box}${C.reset}  ${fg(labelColor, label)}`);
    });
    listEnd();
  }

  return {
    usage,
    error,
    usageLine,
    status,
    line,
    title,
    step,
    success,
    active,
    item,
    detail,
    warn,
    listEnd,
    renderBackupSelector,
  };
}
