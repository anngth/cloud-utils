import assert from "node:assert/strict";
import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { PassThrough } from "node:stream";
import test from "node:test";
import { runCli } from "../cli.mjs";
import { makeSandbox, runJavaScript } from "./helpers.mjs";

function ttyStream() {
  const stream = new PassThrough();
  stream.isTTY = true;
  stream.isRaw = false;
  stream.setRawMode = (value) => { stream.isRaw = value; };
  return stream;
}

test("help and unknown commands bootstrap but do not require npx", (t) => {
  const sandbox = makeSandbox(t);
  const env = { PATH: sandbox.root };
  for (const alias of ["help", "-h", "--help"]) {
    assert.equal(runJavaScript([alias], sandbox, { env }).status, 0);
  }
  const unknown = runJavaScript(["wat"], sandbox, { env });
  assert.equal(unknown.status, 1);
  assert.match(unknown.stderr, /Unknown command: wat/);
});

test("usage validation precedes missing npx", (t) => {
  const sandbox = makeSandbox(t);
  for (const args of [["add"], ["remove"], ["show", "a", "b"]]) {
    const result = runJavaScript(args, sandbox, { env: { PATH: sandbox.root } });
    assert.equal(result.status, 1);
    assert.doesNotMatch(result.stderr, /npx is required/);
  }
});

test("every valid compatibility command that requires npx checks it first", (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  for (const args of [[], ["ls"], ["list"], ["show"], ["show", "a/one"], ["add", "b/two"], ["remove", "a/one"]]) {
    const result = runJavaScript(args, sandbox, { env: { PATH: sandbox.root } });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /npx is required to run 'npx skills add'/);
  }
});

test("all valid compatibility aliases dispatch", (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  assert.equal(runJavaScript(["ls"], sandbox).status, 0);
  assert.equal(runJavaScript(["list"], sandbox).status, 0);
  assert.equal(runJavaScript(["rm", "missing/repo"], sandbox).status, 0);
  assert.equal(runJavaScript(["list-available", "unsaved/repo"], sandbox).status, 0);
});

test("add and remove persist once without invoking npx", (t) => {
  const sandbox = makeSandbox(t);
  assert.equal(runJavaScript(["add", "b/two", "a/one", "a/one"], sandbox).status, 0);
  assert.deepEqual(JSON.parse(readFileSync(sandbox.skillsFile, "utf8")), [
    { source: "a/one" },
    { source: "b/two" },
  ]);
  assert.equal(runJavaScript(["remove", "a/one", "x/missing"], sandbox).status, 0);
  assert.deepEqual(JSON.parse(readFileSync(sandbox.skillsFile, "utf8")), [{ source: "b/two" }]);
  assert.equal(existsSync(sandbox.argvLog), false);
});

test("show passes an unsaved opaque source and propagates status", (t) => {
  const sandbox = makeSandbox(t);
  const result = runJavaScript(["show", "owner/repo with | %"], sandbox, {
    env: { SKM_NPX_STATUS: "7" },
  });
  assert.equal(result.status, 7);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","owner/repo with | %","--list"]\n',
  );
});

test("invalid data is deterministic, byte preserving, and does not invoke npx", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.skillsFile, "{broken", "utf8");
  const result = runJavaScript(["list"], sandbox);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Could not read source list/);
  assert.doesNotMatch(result.stderr, /SyntaxError|stack/);
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), "{broken");
  assert.equal(existsSync(sandbox.argvLog), false);
});

test("install runs selected sources sequentially and aggregates failure", async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }, { source: "b/two" }] });
  const calls = [];
  const statuses = [2, 0];
  const status = await runCli([], {
    env: sandbox.env,
    stdin: ttyStream(),
    stdout: ttyStream(),
    stderr: new PassThrough(),
    npxRunner: async (args) => { calls.push(args); return statuses.shift(); },
    selectorRunner: async ({ sources }) => ({
      type: "submit",
      state: { sources, cursor: 0, selected: new Set([0, 1]) },
      selected: sources,
    }),
  });
  assert.equal(status, 1);
  assert.deepEqual(calls, [
    ["skills", "add", "a/one"],
    ["skills", "add", "b/two"],
  ]);
});

test("install continues after per-source lookup, read, runner, and child failures", async (t) => {
  const sources = ["a/child", "b/missing", "c/spawn", "d/read", "e/success"];
  const sandbox = makeSandbox(t, { list: sources.map((source) => ({ source })) });
  const calls = [];
  const stderr = new PassThrough();
  let errorOutput = "";
  stderr.setEncoding("utf8");
  stderr.on("data", (chunk) => {
    errorOutput += chunk;
    if (chunk.includes("Could not read source list")) {
      writeFileSync(
        sandbox.skillsFile,
        `${JSON.stringify([{ source: "d/read" }, { source: "e/success" }], null, 2)}\n`,
        "utf8",
      );
    }
  });

  const status = await runCli([], {
    env: sandbox.env,
    stdin: ttyStream(),
    stdout: ttyStream(),
    stderr,
    selectorRunner: async ({ sources: selectedSources }) => ({
      type: "submit",
      state: { sources: selectedSources, cursor: 0, selected: new Set([0, 1, 2, 3, 4]) },
      selected: selectedSources,
    }),
    npxRunner: async (args) => {
      calls.push(args);
      const source = args[2];
      if (source === "a/child") {
        writeFileSync(
          sandbox.skillsFile,
          `${JSON.stringify(sources.filter((item) => item !== "b/missing").map((item) => ({ source: item })), null, 2)}\n`,
          "utf8",
        );
        return 2;
      }
      if (source === "c/spawn") {
        writeFileSync(sandbox.skillsFile, "{broken", "utf8");
        throw new Error("spawn exploded");
      }
      return 0;
    },
  });

  assert.equal(status, 1);
  assert.deepEqual(calls, [
    ["skills", "add", "a/child"],
    ["skills", "add", "c/spawn"],
    ["skills", "add", "e/success"],
  ]);
  assert.match(errorOutput, /Source not found: b\/missing/);
  assert.match(errorOutput, /spawn exploded/);
  assert.match(errorOutput, /Could not read source list/);
});

test("interactive cancellation exits zero without spawning", async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const status = await runCli(["show"], {
    env: sandbox.env,
    stdin: ttyStream(),
    stdout: ttyStream(),
    stderr: new PassThrough(),
    npxRunner: async () => assert.fail("cancel must not spawn"),
    selectorRunner: async ({ sources }) => ({
      type: "cancel",
      state: { sources, cursor: 0, selected: new Set() },
      selected: [],
    }),
  });
  assert.equal(status, 0);
});

test("install detects a source removed after selection and does not spawn", async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const stderr = new PassThrough();
  let errorOutput = "";
  stderr.setEncoding("utf8");
  stderr.on("data", (chunk) => { errorOutput += chunk; });
  const status = await runCli([], {
    env: sandbox.env,
    stdin: ttyStream(),
    stdout: ttyStream(),
    stderr,
    npxRunner: async () => assert.fail("missing selected source must not spawn"),
    selectorRunner: async ({ sources }) => {
      writeFileSync(sandbox.skillsFile, "[]\n");
      return {
        type: "submit",
        state: { sources, cursor: 0, selected: new Set([0]) },
        selected: sources,
      };
    },
  });
  assert.equal(status, 1);
  assert.match(errorOutput, /Source not found: a\/one/);
});

test("empty install selection warns and exits one", async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const stderr = new PassThrough();
  let errorOutput = "";
  stderr.setEncoding("utf8");
  stderr.on("data", (chunk) => { errorOutput += chunk; });
  const status = await runCli([], {
    env: sandbox.env,
    stdin: ttyStream(),
    stdout: ttyStream(),
    stderr,
    selectorRunner: async ({ sources }) => ({
      type: "submit",
      state: { sources, cursor: 0, selected: new Set() },
      selected: [],
    }),
  });
  assert.equal(status, 1);
  assert.match(errorOutput, /No sources selected/);
});

test("invalid usage does not normalize while interactive show normalizes before TTY validation", async (t) => {
  const sandbox = makeSandbox(t, { list: [] });
  rmSync(sandbox.skillsFile);
  const managerDir = `${sandbox.root}/manager-without-example`;
  const stdout = new PassThrough();
  const stderr = new PassThrough();

  assert.equal(await runCli(["add"], {
    env: { ...sandbox.env, PATH: sandbox.root },
    managerDir,
    stdout,
    stderr,
  }), 1);
  assert.equal(existsSync(sandbox.skillsFile), false);

  assert.equal(await runCli(["show"], {
    env: sandbox.env,
    managerDir,
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  }), 1);
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), "[]\n");
});

test("help bootstraps new documents before dispatch without checking npx", async (t) => {
  const sandbox = makeSandbox(t, { createProfiles: false, createProjects: false });
  const status = await runCli(["--help"], {
    env: { ...sandbox.env, PATH: sandbox.root },
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  assert.equal(status, 0);
  assert.deepEqual(JSON.parse(readFileSync(sandbox.profilesFile, "utf8")), {
    version: 1,
    profiles: [{ name: "default", sources: [] }],
  });
  assert.deepEqual(JSON.parse(readFileSync(sandbox.projectsFile, "utf8")), {
    version: 1,
    projects: [],
  });
  assert.equal(existsSync(sandbox.legacyFile), false);
});
