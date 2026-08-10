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

  function title(label = "2FA") {
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
    const note = (text) => out(`${pipe}  ${fg(C.gray, text)}`);

    title("2FA");
    step("Usage: 2fa [command]");
    command("2fa", "Prompt for Base32 secret; copy 6-digit TOTP");
    command("2fa (help | -h | --help)", "Show this help");

    section("Notes");
    note("Secret is read hidden from the terminal; code goes to the clipboard.");
    listEnd();
  }

  function error(message) {
    err(fg(C.red, `❌ ${message}`));
  }

  function successCopied(otp) {
    title("2FA");
    step(`Code copied: ${otp}`);
    listEnd();
  }

  return {
    usage,
    error,
    successCopied,
    title,
    step,
    success,
    active,
    item,
    detail,
    warn,
    listEnd,
  };
}
