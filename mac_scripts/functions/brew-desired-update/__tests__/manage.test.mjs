import assert from "node:assert/strict";
import test from "node:test";
import {
  applyAdd,
  applyRemove,
  detectBrewType,
  parseManageArgs,
} from "../manage.mjs";
import { createUi } from "../ui.mjs";

function silentUi() {
  return createUi({
    stdout: { write() {} },
    stderr: { write() {} },
  });
}

test("parseManageArgs extracts force type and names", () => {
  assert.deepEqual(parseManageArgs(["--formula", "bat", "gh"]), {
    forceType: "formula",
    names: ["bat", "gh"],
  });
  assert.deepEqual(parseManageArgs(["cursor", "--cask"]), {
    forceType: "cask",
    names: ["cursor"],
  });
  assert.deepEqual(parseManageArgs(["mongodb/brew"]), {
    forceType: null,
    names: ["mongodb/brew"],
  });
});

test("applyAdd auto formula and sorts", async () => {
  const doc = { version: 1, formulas: [], casks: [], taps: [] };
  const { document, failed } = await applyAdd(["bat"], {
    forceType: null,
    document: doc,
    deps: {
      ui: silentUi(),
      detectBrewType: async () => ({ type: "formula" }),
    },
  });
  assert.equal(failed, 0);
  assert.deepEqual(document.formulas, ["bat"]);
});

test("applyAdd tap formula adds tap + short name", async () => {
  const { document } = await applyAdd(["atlassian/acli/acli"], {
    forceType: null,
    document: { version: 1, formulas: [], casks: [], taps: [] },
    deps: {
      ui: silentUi(),
      detectBrewType: async () => ({ error: "missing" }),
    },
  });
  assert.deepEqual(document.formulas, ["acli"]);
  assert.deepEqual(document.taps, ["atlassian/acli"]);
});

test("applyAdd two-segment name adds tap", async () => {
  const { document, failed } = await applyAdd(["mongodb/brew"], {
    forceType: null,
    document: { version: 1, formulas: [], casks: [], taps: [] },
    deps: {
      ui: silentUi(),
      detectBrewType: async () => ({ error: "missing" }),
    },
  });
  assert.equal(failed, 0);
  assert.deepEqual(document.taps, ["mongodb/brew"]);
  assert.deepEqual(document.formulas, []);
});

test("applyRemove finds across lists", () => {
  const { document, failed } = applyRemove(["cursor"], {
    document: { version: 1, formulas: [], casks: ["cursor"], taps: [] },
    ui: silentUi(),
  });
  assert.equal(failed, 0);
  assert.deepEqual(document.casks, []);
});

test("applyRemove searches formulas and taps", () => {
  const formulaResult = applyRemove(["bat"], {
    document: { version: 1, formulas: ["bat"], casks: [], taps: [] },
    ui: silentUi(),
  });
  assert.equal(formulaResult.failed, 0);
  assert.deepEqual(formulaResult.document.formulas, []);

  const tapResult = applyRemove(["a/b"], {
    document: { version: 1, formulas: [], casks: [], taps: ["a/b"] },
    ui: silentUi(),
  });
  assert.equal(tapResult.failed, 0);
  assert.deepEqual(tapResult.document.taps, []);
});

test("applyRemove missing name increments failed", () => {
  const { failed } = applyRemove(["missing"], {
    document: { version: 1, formulas: [], casks: [], taps: [] },
    ui: silentUi(),
  });
  assert.equal(failed, 1);
});

test("dual cask+formula fails without force", async () => {
  const { failed } = await applyAdd(["wget"], {
    forceType: null,
    document: { version: 1, formulas: [], casks: [], taps: [] },
    deps: {
      ui: silentUi(),
      detectBrewType: async () => ({ error: "dual" }),
    },
  });
  assert.equal(failed, 1);
});

test("applyAdd duplicate fails", async () => {
  const { failed, document } = await applyAdd(["bat"], {
    forceType: null,
    document: { version: 1, formulas: ["bat"], casks: [], taps: [] },
    deps: {
      ui: silentUi(),
      detectBrewType: async () => ({ type: "formula" }),
    },
  });
  assert.equal(failed, 1);
  assert.deepEqual(document.formulas, ["bat"]);
});

test("applyAdd --tap rejects three-segment name", async () => {
  const { failed, document } = await applyAdd(["atlassian/acli/acli"], {
    forceType: "tap",
    document: { version: 1, formulas: [], casks: [], taps: [] },
    deps: {
      ui: silentUi(),
      detectBrewType: async () => ({ error: "missing" }),
    },
  });
  assert.equal(failed, 1);
  assert.deepEqual(document.taps, []);
});

test("detectBrewType returns formula when only formula matches", async () => {
  const result = await detectBrewType("bat", {
    brewBin: "/brew",
    ui: silentUi(),
    brewInfoToken: async (type) => (type === "formula" ? "bat" : null),
  });
  assert.deepEqual(result, { type: "formula" });
});

test("detectBrewType returns dual when both match", async () => {
  const result = await detectBrewType("wget", {
    brewBin: "/brew",
    ui: silentUi(),
    brewInfoToken: async () => "wget",
  });
  assert.deepEqual(result, { error: "dual" });
});

test("applyAdd --formula validates once via brew info", async () => {
  const infoCalls = [];
  const { document, failed } = await applyAdd(["bat"], {
    forceType: "formula",
    document: { version: 1, formulas: [], casks: [], taps: [] },
    deps: {
      ui: silentUi(),
      brewBin: "/brew",
      runBrew: async (args) => {
        infoCalls.push(args);
        return { code: 0, stdout: "", stderr: "" };
      },
      brewInfoToken: async () => "bat",
    },
  });
  assert.equal(failed, 0);
  assert.deepEqual(document.formulas, ["bat"]);
  assert.deepEqual(
    infoCalls.filter((args) => args[0] === "info" && args[1] === "--formula"),
    [["info", "--formula", "bat"]],
  );
});

test("applyAdd reports list size after add", async () => {
  const messages = [];
  const doc = { version: 1, formulas: ["gh"], casks: [], taps: [] };
  await applyAdd(["bat"], {
    forceType: null,
    document: doc,
    deps: {
      detectBrewType: async () => ({ type: "formula" }),
      ui: { info: (m) => messages.push(m), warn() {}, error() {} },
    },
  });
  assert.ok(messages.some((m) => m === "Added 'bat' to formulas (now 2)"));
});

test("applyRemove reports list size after remove", () => {
  const messages = [];
  applyRemove(["cursor"], {
    document: { version: 1, formulas: [], casks: ["cursor", "slack"], taps: [] },
    ui: { info: (m) => messages.push(m), warn() {} },
  });
  assert.ok(messages.some((m) => m === "Removed 'cursor' from casks (now 1)"));
});
