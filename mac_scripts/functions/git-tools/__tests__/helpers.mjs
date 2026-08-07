export function cliHarness() {
  const calls = [];
  let stdout = "";
  let stderr = "";
  const handler = (name) => async (args, context) => {
    calls.push([name, args]);
    return 0;
  };
  return {
    calls,
    stdout: () => stdout,
    stderr: () => stderr,
    dependencies: {
      cwd: "/repo",
      env: process.env,
      stdout: { write: (v) => { stdout += v; } },
      stderr: { write: (v) => { stderr += v; } },
      runPushCommand: handler("push"),
      runFetchCommand: handler("fetch"),
      ui: {
        usage() { stdout += "usage\n"; },
        error(message) { stderr += `${message}\n`; },
        usageLine(message) { stderr += `${message}\n`; },
        status() {},
      },
    },
  };
}
