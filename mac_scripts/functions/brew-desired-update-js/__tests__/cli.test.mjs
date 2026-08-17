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
    assert.match(h.stdout(), /Usage: budj/);
    assert.equal(h.stderr(), "");
  }
});

test("unknown command prints error on stderr and help on stdout", async () => {
  const h = harness();
  assert.equal(await runCli(["nope"], h.deps), 1);
  assert.match(h.stderr(), /Unknown command: nope/);
  assert.match(h.stdout(), /Usage: budj/);
  assert.doesNotMatch(h.stderr() + h.stdout(), /Use '.*--help'/);
});

test("list exits 0 and prints section header", async () => {
  const h = harness();
  const code = await runCli(["list"], {
    ...h.deps,
    runList: async () => {
      h.deps.stdout.write("Formulae · in list, installed\n");
      return 0;
    },
  });
  assert.equal(code, 0);
  assert.match(h.stdout(), /Formulae · in list, installed/);
});

test("ls alias calls list command", async () => {
  const h = harness();
  let called = false;
  const code = await runCli(["ls"], {
    ...h.deps,
    runList: async () => {
      called = true;
      return 0;
    },
  });
  assert.equal(code, 0);
  assert.ok(called);
});

test("add --formula bat delegates to runAdd with remaining args", async () => {
  const h = harness();
  let capturedArgs = null;
  const code = await runCli(["add", "--formula", "bat"], {
    ...h.deps,
    runAdd: async (args) => {
      capturedArgs = args;
      return 0;
    },
  });
  assert.equal(code, 0);
  assert.deepEqual(capturedArgs, ["--formula", "bat"]);
});

test("remove delegates to runRemove with names", async () => {
  const h = harness();
  let capturedArgs = null;
  const code = await runCli(["remove", "cursor", "bat"], {
    ...h.deps,
    runRemove: async (args) => {
      capturedArgs = args;
      return 0;
    },
  });
  assert.equal(code, 0);
  assert.deepEqual(capturedArgs, ["cursor", "bat"]);
});

test("bare bud delegates to runUpdate", async () => {
  const h = harness();
  let called = false;
  const code = await runCli([], {
    ...h.deps,
    runUpdate: async (options) => {
      called = true;
      assert.deepEqual(options, { exclude: [] });
      return 0;
    },
  });
  assert.equal(code, 0);
  assert.ok(called);
});

test("bud -e slack passes exclude to runUpdate", async () => {
  const h = harness();
  let captured = null;
  const code = await runCli(["-e", "slack"], {
    ...h.deps,
    runUpdate: async (options) => {
      captured = options;
      return 0;
    },
  });
  assert.equal(code, 0);
  assert.deepEqual(captured, { exclude: ["slack"] });
});

test("bud --exclude postman spotify passes exclude to runUpdate", async () => {
  const h = harness();
  let captured = null;
  const code = await runCli(["--exclude", "postman", "spotify"], {
    ...h.deps,
    runUpdate: async (options) => {
      captured = options;
      return 0;
    },
  });
  assert.equal(code, 0);
  assert.deepEqual(captured, { exclude: ["postman", "spotify"] });
});
