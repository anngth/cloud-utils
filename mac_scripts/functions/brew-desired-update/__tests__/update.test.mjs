import assert from "node:assert/strict";
import test from "node:test";
import {
  collectCasksToUpgrade,
  filterExcludedCasks,
  parseArgv,
  runUpdateCommand,
} from "../update.mjs";
import { createUi } from "../ui.mjs";

function silentUi() {
  return createUi({
    stdout: { write() {} },
    stderr: { write() {} },
  });
}

test("parseArgv extracts exclude and bare update", () => {
  assert.deepEqual(parseArgv([]), { exclude: [], action: undefined, rest: [] });
  assert.deepEqual(parseArgv(["-e", "slack"]), {
    exclude: ["slack"],
    action: undefined,
    rest: [],
  });
  assert.deepEqual(parseArgv(["--exclude", "postman", "spotify"]), {
    exclude: ["postman", "spotify"],
    action: undefined,
    rest: [],
  });
  assert.deepEqual(parseArgv(["-e", "slack", "list"]), {
    exclude: ["slack"],
    action: "list",
    rest: [],
  });
  assert.deepEqual(parseArgv(["list"]), {
    exclude: [],
    action: "list",
    rest: [],
  });
});

test("collectCasksToUpgrade keeps installed desired casks", () => {
  assert.deepEqual(
    collectCasksToUpgrade(["cursor", "slack", "missing"], ["cursor", "slack", "extra"]),
    ["cursor", "slack"],
  );
});

test("filterExcludedCasks removes excluded names", () => {
  assert.deepEqual(filterExcludedCasks(["cursor", "slack"], ["slack"]), ["cursor"]);
});

test("runUpdateCommand order and exclude", async () => {
  const calls = [];
  const runBrew = async (args) => {
    calls.push(args.join(" "));
    return { code: 0, stdout: "", stderr: "" };
  };
  const code = await runUpdateCommand(
    { exclude: ["slack"] },
    {
      brewBin: "/brew",
      runBrew,
      loadDesired: async () => ({
        ok: true,
        document: { version: 1, formulas: [], casks: ["cursor", "slack"], taps: [] },
      }),
      loadBrewState: async () => ({
        formulas: [],
        casks: ["cursor", "slack"],
        taps: [],
      }),
      ensureTap: async () => true,
      ui: silentUi(),
    },
  );
  assert.equal(code, 0);
  assert.ok(calls.some((c) => c.startsWith("update")));
  assert.ok(calls.some((c) => c.includes("upgrade --formula")));
  assert.ok(calls.some((c) => c.includes("tap --repair")));
  assert.ok(
    calls.some((c) => c.includes("upgrade --cask") && c.includes("cursor") && !c.includes("slack")),
  );
  assert.ok(calls.some((c) => c.includes("cleanup")));
});

test("runUpdateCommand ensures missing desired taps", async () => {
  const tapped = [];
  const runBrew = async (args) => ({ code: 0, stdout: "", stderr: "" });
  const code = await runUpdateCommand(
    { exclude: [] },
    {
      brewBin: "/brew",
      runBrew,
      loadDesired: async () => ({
        ok: true,
        document: { version: 1, formulas: [], casks: [], taps: ["mongodb/brew"] },
      }),
      loadBrewState: async () => ({
        formulas: [],
        casks: [],
        taps: [],
      }),
      ensureTap: async (tap) => {
        tapped.push(tap);
        return true;
      },
      ui: silentUi(),
    },
  );
  assert.equal(code, 0);
  assert.deepEqual(tapped, ["mongodb/brew"]);
});

test("runUpdateCommand returns 1 when brew missing", async () => {
  const code = await runUpdateCommand(
    { exclude: [] },
    {
      resolveBrew: () => null,
      ui: silentUi(),
    },
  );
  assert.equal(code, 1);
});

test("runUpdateCommand returns 1 on brew failure", async () => {
  const runBrew = async (args) => {
    if (args[0] === "update") {
      return { code: 1, stdout: "", stderr: "fail" };
    }
    return { code: 0, stdout: "", stderr: "" };
  };
  const code = await runUpdateCommand(
    { exclude: [] },
    {
      brewBin: "/brew",
      runBrew,
      loadDesired: async () => ({
        ok: true,
        document: { version: 1, formulas: [], casks: [], taps: [] },
      }),
      loadBrewState: async () => ({ formulas: [], casks: [], taps: [] }),
      ensureTap: async () => true,
      ui: silentUi(),
    },
  );
  assert.equal(code, 1);
});
