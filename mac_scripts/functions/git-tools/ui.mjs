export function createUi({ stdout = process.stdout, stderr = process.stderr } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const err = (line) => stderr.write(`${line}\n`);

  function usage() {
    out("gt — git workflow helpers");
    out();
    out("Usage: gt <command>");
    out();
    out("Commands:");
    out("  push              Force push (safe --force-with-lease)");
    out("  fetch [--sync-upstream]");
    out("  backup <ssh-url>  Mirror repo to gitlab.com/anngth-backups");
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

  function line(message = "") {
    out(message);
  }

  return { usage, error, usageLine, status, line };
}
