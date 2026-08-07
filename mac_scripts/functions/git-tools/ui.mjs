export function createUi({ stdout = process.stdout, stderr = process.stderr } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const err = (line) => stderr.write(`${line}\n`);

  function usage() {
    out("gt — git workflow helpers");
    out();
    out("Usage: gt <command>");
    out();
    out("Commands:");
    out("  push   Force push (safe with --force-with-lease)");
    out("  fetch  Fetch/sync; optional upstream->origin sync when on main");
    out();
    out("Run 'gt --help' for this message.");
  }

  function error(message) {
    err(message);
  }

  function usageLine(message) {
    err(message);
  }

  function status(message) {
    out(`--- ${message}`);
  }

  return { usage, error, usageLine, status };
}
