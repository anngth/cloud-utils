import assert from "node:assert/strict";
import test from "node:test";
import { runCli } from "../cli.mjs";

function harness() {
  let stdout = "", stderr = "";
  const deps = {
    stdout: { write: (v) => { stdout += v; } },
    stderr: { write: (v) => { stderr += v; } },
  };
  return {
    deps,
    stdout: () => stdout,
    stderr: () => stderr,
  };
}

test("help aliases exit 0 and print usage", async () => {
  for (const argv of [["-h"], ["--help"], ["help"]]) {
    const h = harness();
    assert.equal(await runCli(argv, h.deps), 0);
    assert.match(h.stdout(), /Usage: bud/);
    assert.equal(h.stderr(), "");
  }
});

test("unknown command prints error on stderr and help on stdout", async () => {
  const h = harness();
  assert.equal(await runCli(["nope"], h.deps), 1);
  assert.match(h.stderr(), /Unknown command: nope/);
  assert.match(h.stdout(), /Usage: bud/);
  assert.doesNotMatch(h.stderr() + h.stdout(), /Use '.*--help'/);
});
