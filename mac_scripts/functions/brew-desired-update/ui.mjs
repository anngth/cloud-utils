const C = {
  cyan: "\u001b[36m",
  green: "\u001b[32m",
  brightGreen: "\u001b[92m",
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

export function createUi({ stdout = process.stdout, stderr = process.stderr } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const err = (line) => stderr.write(`${line}\n`);
  const pipe = fg(C.cyan, "│");

  function title(label = "BUD") {
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

    title("BUD");
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

  return {
    usage,
    error,
    warn,
    info,
    title,
    step,
    active,
    listEnd,
  };
}
