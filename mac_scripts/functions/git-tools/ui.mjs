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
    const section = (name) => {
      out(pipe);
      active(name);
    };
    const command = (syntax, description) => {
      out(`${pipe}  ${fg(C.green, syntax)}${description ? `  ${fg(C.gray, description)}` : ""}`);
    };
    const continuation = (syntax, description) => {
      if (syntax) {
        out(`${pipe}      ${fg(C.green, syntax)}${description ? `  ${fg(C.gray, description)}` : ""}`);
      } else {
        out(`${pipe}      ${fg(C.gray, description)}`);
      }
    };
    const note = (text) => out(`${pipe}  ${fg(C.gray, text)}`);

    title("GT");
    step("Usage: gt <command>");
    command("gt (help | -h | --help)", "Show this help");

    section("Core");
    command("gt push", "Force push (safe --force-with-lease)");
    command("gt fetch [--sync-upstream]", "Fetch with optional upstream sync");

    section("Backup");
    command("gt backup [(-f | --force) | --dry-run]");
    continuation("", "Interactive select; force and dry-run are mutually exclusive");
    command("gt backup --all [(-f | --force) | --dry-run]");
    continuation("", "Backup or preview every listed repo");
    command("gt backup stale [--days <n>] [--all] [(-f | --force) | --dry-run]");
    continuation("", "Stale repos only (default 7 days)");
    command("gt backup add <ssh-url> [<ssh-url> ...]", "Add SSH URL(s) to managed list");
    command("gt backup remove <index|ssh-url>", "Remove by 1-based index or URL");

    section("Notes");
    note("Selector: space toggle, a all, c clear, enter start, q quit");
    note("Remove indexes are 1-based (same as skm).");
    note("--force skips fingerprint short-circuit; cannot combine with --dry-run.");
    listEnd();
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

  function renderBackupSelector(heading, state, { listPath, now, cancelled = false } = {}) {
    stdout.write("\u001b[2J\u001b[H");
    title("REPO BACKUP");
    if (listPath) {
      step(listPath);
    }
    step(String(heading));
    active(`Select repos ${fg(C.white, "(space toggle, a all, c clear, enter to start, q to quit)")}`);
    out(pipe);
    state.items.forEach((entry, index) => {
      const label = typeof entry === "string" ? entry : entry.label;
      const selected = state.selected.has(index);
      const box = selected ? "■" : "□";
      const boxColor = selected ? C.brightGreen : C.gray;
      const labelColor = index === state.cursor ? C.white : C.gray;
      const number = String(index + 1);
      out(`${pipe}  ${number}  ${boxColor}${box}${C.reset}  ${fg(labelColor, label)}`);
      out(pipe);
    });
    if (cancelled) {
      out(`${fg(C.cyan, "└")}  ${fg(C.red, "Selection cancelled")}`);
    } else {
      listEnd();
    }
  }

  function cancelledBackupSelector(heading, state, opts = {}) {
    renderBackupSelector(heading, state, { ...opts, cancelled: true });
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
    cancelledBackupSelector,
  };
}
