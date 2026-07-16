# SKM JavaScript Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Zsh `skm` implementation with a Node.js CLI while preserving its commands, data, terminal UI, and documented compatibility contract.

**Architecture:** Keep `mac_scripts/skm` as a Node preflight wrapper and move the implementation into six focused ECMAScript modules. Pure data and selector state functions sit behind filesystem, terminal, and child-process adapters so unit tests can cover behavior without mutating the user's config or invoking the real `npx`.

**Tech Stack:** Node.js 20+, ECMAScript modules (`.mjs`), `node:test`, Node built-ins only, Zsh wrapper, macOS PTY smoke tests.

## Global Constraints

- Node.js 20 is the implementation and CI compatibility baseline.
- Production code uses only Node.js built-ins; no package manager metadata or production dependency is added.
- The public command set, aliases, data path, bootstrap rules, ANSI UI, stream assignment, and exit statuses must match `docs/skm-js-migration-spec.md`.
- Source values are opaque JavaScript strings and must never pass through a shell or delimiter-separated intermediate representation.
- `npx` child processes use argument arrays with inherited stdio.
- Invalid existing data is never rewritten; modifying writes use `<file>.<pid>.tmp` followed by an atomic rename.
- Interactive code restores the prior TTY mode and removes listeners on every completion, failure, EOF, and supported signal path.
- Preserve unrelated working-tree changes in `mac_init/alias`, `mac_scripts/functions/skills-manager/list.json.example`, `.agents/`, and `skills-lock.json`.

## File Map

| Path | Responsibility |
| --- | --- |
| `mac_scripts/skm` | Zsh compatibility wrapper and missing-Node preflight |
| `mac_scripts/functions/skills-manager/cli.mjs` | startup, validation order, command dispatch, exit status |
| `mac_scripts/functions/skills-manager/config.mjs` | config-directory resolution and two-pass bootstrap |
| `mac_scripts/functions/skills-manager/sources.mjs` | source-list validation, transformation, and atomic persistence |
| `mac_scripts/functions/skills-manager/ui.mjs` | ANSI primitives and complete command/selector rendering |
| `mac_scripts/functions/skills-manager/skills-cli.mjs` | `PATH` prerequisite lookup and shell-free `npx` execution |
| `mac_scripts/functions/skills-manager/selector.mjs` | selector state machine, input decoding, raw mode, signals, cleanup |
| `mac_scripts/functions/skills-manager/__tests__/helpers.mjs` | isolated config, executable stubs, process capture, normalization |
| `mac_scripts/functions/skills-manager/__tests__/*.test.mjs` | unit, command, golden, wrapper, and PTY coverage |
| `mac_scripts/functions/skills-manager/__tests__/fixtures/golden.json` | normalized legacy transcripts captured before cutover |
| `docs/commands.md` | user-facing entrypoint and command documentation |

---

### Task 1: Capture the Legacy Contract

**Files:**

- Create: `mac_scripts/functions/skills-manager/__tests__/helpers.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/fixtures/golden.json`

**Interfaces:**

- Consumes: current `main.zsh`, current `mac_scripts/skm`, real Node executable.
- Produces: `makeSandbox()`, `runLegacy(args, sandbox, options)`, `runJavaScript(args, sandbox, options)`, `normalizeCapture(capture, sandbox)`, and committed normalized transcripts used by Tasks 7–9.

- [ ] **Step 1: Add a test that imports the missing process harness**

Create `legacy-contract.test.mjs`:

```js
import assert from "node:assert/strict";
import test from "node:test";
import { makeSandbox, normalizeCapture, runLegacy } from "./helpers.mjs";

test("legacy help capture has stable streams and status", (t) => {
  const sandbox = makeSandbox(t);
  const result = normalizeCapture(runLegacy(["--help"], sandbox), sandbox);

  assert.equal(result.status, 0);
  assert.equal(result.stderr, "");
  assert.match(result.stdout, /SKILLS MANAGER/);
  assert.match(result.stdout, /Usage: skm <command> \[args\]/);
});
```

- [ ] **Step 2: Run the test and confirm the harness is absent**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `helpers.mjs`.

- [ ] **Step 3: Implement the reusable isolated-process harness**

Create `helpers.mjs`:

```js
import { chmodSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
export const MANAGER_DIR = resolve(TEST_DIR, "..");
export const REPO_ROOT = resolve(MANAGER_DIR, "../../..");
export const LEGACY_MAIN = join(MANAGER_DIR, "main.zsh");
export const JS_ENTRY = join(MANAGER_DIR, "cli.mjs");
export const WRAPPER = join(REPO_ROOT, "mac_scripts/skm");

function executable(path, body) {
  writeFileSync(path, body, "utf8");
  chmodSync(path, 0o755);
}

export function makeSandbox(t, { list = [] } = {}) {
  const root = mkdtempSync(join(tmpdir(), "skm-test-"));
  const configDir = join(root, "config");
  const binDir = join(root, "bin");
  const skillsDir = join(configDir, "skm");
  const skillsFile = join(skillsDir, "list.json");
  const argvLog = join(root, "npx-argv.jsonl");
  mkdirSync(skillsDir, { recursive: true });
  mkdirSync(binDir, { recursive: true });
  writeFileSync(skillsFile, `${JSON.stringify(list, null, 2)}\n`, "utf8");

  executable(
    join(binDir, "node"),
    `#!/bin/zsh\nexec ${JSON.stringify(process.execPath)} "$@"\n`,
  );
  executable(
    join(binDir, "npx"),
    `#!/bin/zsh\nprintf '%s\\n' "$@" | ${JSON.stringify(process.execPath)} -e '
const fs = require("fs");
const values = fs.readFileSync(0, "utf8").split("\\n").slice(0, -1);
fs.appendFileSync(process.env.SKM_ARGV_LOG, JSON.stringify(values) + "\\n");
process.exit(Number(process.env.SKM_NPX_STATUS || 0));
'\n`,
  );

  const sandbox = {
    root,
    configDir,
    skillsFile,
    binDir,
    argvLog,
    env: {
      ...process.env,
      CLOUD_UTILS_CONFIG_DIR: configDir,
      HOME: join(root, "home"),
      PATH: `${binDir}${delimiter}${dirname(process.execPath)}:/usr/bin:/bin`,
      SKM_ARGV_LOG: argvLog,
      TERM: "xterm-256color",
    },
  };
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return sandbox;
}

function capture(command, args, sandbox, options = {}) {
  const result = spawnSync(command, args, {
    env: { ...sandbox.env, ...options.env },
    encoding: "utf8",
    input: options.input,
  });
  return {
    status: result.status,
    signal: result.signal,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
  };
}

export function runLegacy(args, sandbox, options = {}) {
  const script = 'source "$1"; shift; skm "$@"';
  return capture("zsh", ["-c", script, "skm-test", LEGACY_MAIN, ...args], sandbox, options);
}

export function runJavaScript(args, sandbox, options = {}) {
  return capture(process.execPath, [JS_ENTRY, ...args], sandbox, options);
}

export function normalizeCapture(captureResult, sandbox) {
  const replaceRoot = (value) => value.split(sandbox.root).join("<sandbox>");
  return {
    ...captureResult,
    stdout: replaceRoot(captureResult.stdout),
    stderr: replaceRoot(captureResult.stderr),
  };
}
```

- [ ] **Step 4: Expand characterization coverage and save the exact normalized results**

Add these tests below the first test in `legacy-contract.test.mjs`:

```js
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GOLDEN_FILE = join(dirname(fileURLToPath(import.meta.url)), "fixtures/golden.json");
const GOLDENS = JSON.parse(readFileSync(GOLDEN_FILE, "utf8"));

for (const entry of GOLDENS) {
  test(`legacy golden: ${entry.name}`, (t) => {
    const sandbox = makeSandbox(t, { list: entry.list });
    const actual = normalizeCapture(runLegacy(entry.args, sandbox), sandbox);
    assert.deepEqual(actual, entry.capture);
    if (entry.fileAfter !== undefined) {
      assert.equal(readFileSync(sandbox.skillsFile, "utf8"), entry.fileAfter);
    }
  });
}

test("legacy harness preserves a source argument containing spaces", (t) => {
  const sandbox = makeSandbox(t);
  const result = runLegacy(["show", "owner/repo with space"], sandbox);
  assert.equal(result.status, 0);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","owner/repo with space","--list"]\n',
  );
});
```

Create `fixtures/golden.json` by running the following temporary one-liner from
the repository root, then inspect the generated JSON before staging it:

```bash
node --input-type=module -e '
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { makeSandbox, normalizeCapture, runLegacy } from "./mac_scripts/functions/skills-manager/__tests__/helpers.mjs";
const cases = [
  { name: "help", args: ["--help"], list: [] },
  { name: "unknown", args: ["wat"], list: [] },
  { name: "list-empty", args: ["list"], list: [] },
  { name: "list-values", args: ["ls"], list: [{ source: "a/one" }, { source: "b/two" }] },
  { name: "add-new-and-duplicate", args: ["add", "b/two", "a/one", "a/one"], list: [] },
  { name: "remove-repeated-and-missing", args: ["remove", "a/one", "a/one", "x/missing"], list: [{ source: "a/one" }, { source: "b/two" }] },
  { name: "show-too-many", args: ["show", "a/one", "b/two"], list: [] },
  { name: "add-missing-argument", args: ["add"], list: [] },
];
const rows = [];
for (const item of cases) {
  const cleanups = [];
  const sandbox = makeSandbox({ after: (fn) => cleanups.push(fn) }, { list: item.list });
  const capture = normalizeCapture(runLegacy(item.args, sandbox), sandbox);
  rows.push({ ...item, capture, fileAfter: readFileSync(sandbox.skillsFile, "utf8") });
  cleanups.reverse().forEach((fn) => fn());
}
const target = "mac_scripts/functions/skills-manager/__tests__/fixtures/golden.json";
mkdirSync("mac_scripts/functions/skills-manager/__tests__/fixtures", { recursive: true });
writeFileSync(target, `${JSON.stringify(rows, null, 2)}\n`);
'
```

Expected: the fixture contains eight named cases; only the disposable root is
normalized to `<sandbox>`. ANSI sequences, wording, blank lines, streams,
statuses, and file bytes remain exact.

- [ ] **Step 5: Run the characterization suite**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs
```

Expected: PASS for all legacy contract cases.

- [ ] **Step 6: Commit the contract harness**

```bash
git add mac_scripts/functions/skills-manager/__tests__/helpers.mjs mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs mac_scripts/functions/skills-manager/__tests__/fixtures/golden.json
git commit -m "test(skm): capture legacy command contract"
```

---

### Task 2: Port Configuration and Bootstrap

**Files:**

- Create: `mac_scripts/functions/skills-manager/config.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/config.test.mjs`

**Interfaces:**

- Consumes: `env`, manager directory, stderr stream, and Node filesystem calls.
- Produces: `defaultConfigDir(env) -> string`, `bootstrapFile(options) -> boolean`, and `initializeConfig(options) -> { configDir, skillsFile }`.

- [ ] **Step 1: Write failing configuration tests**

Create `config.test.mjs`:

```js
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { makeSandbox } from "./helpers.mjs";
import { bootstrapFile, defaultConfigDir, initializeConfig } from "../config.mjs";

test("defaultConfigDir uses HOME without os.homedir fallback", () => {
  assert.equal(
    defaultConfigDir({ HOME: "/Users/test" }),
    "/Users/test/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
  );
  assert.equal(
    defaultConfigDir({}),
    "/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils",
  );
});

test("bootstrapFile prefers legacy and never overwrites destination", (t) => {
  const sandbox = makeSandbox(t);
  const dest = join(sandbox.root, "dest/list.json");
  const example = join(sandbox.root, "example.json");
  const legacy = join(sandbox.root, "legacy.json");
  const writes = [];
  writeFileSync(example, "[]\n");
  writeFileSync(legacy, '[{"source":"legacy/repo"}]\n');

  assert.equal(bootstrapFile({ dest, example, legacy, stderr: { write: (s) => writes.push(s) } }), true);
  assert.equal(readFileSync(dest, "utf8"), '[{"source":"legacy/repo"}]\n');
  assert.match(writes.join(""), /Migrated list\.json/);

  writeFileSync(legacy, '[{"source":"changed/repo"}]\n');
  assert.equal(bootstrapFile({ dest, example, legacy }), true);
  assert.equal(readFileSync(dest, "utf8"), '[{"source":"legacy/repo"}]\n');
});

test("bootstrapFile silently falls back to the example", (t) => {
  const sandbox = makeSandbox(t);
  const dest = join(sandbox.root, "example-dest/list.json");
  const example = join(sandbox.root, "example-only.json");
  const writes = [];
  writeFileSync(example, '[{"source":"example/repo"}]\n');
  assert.equal(bootstrapFile({
    dest,
    example,
    legacy: join(sandbox.root, "absent.json"),
    stderr: { write: (value) => writes.push(value) },
  }), true);
  assert.equal(readFileSync(dest, "utf8"), '[{"source":"example/repo"}]\n');
  assert.deepEqual(writes, []);
});

test("initializeConfig performs the second legacy attempt when first pass creates nothing", (t) => {
  const sandbox = makeSandbox(t);
  const managerDir = join(sandbox.root, "manager");
  const configDir = join(sandbox.root, "config-two");
  mkdirSync(join(configDir, "skills"), { recursive: true });
  writeFileSync(join(configDir, "skills/list.json"), '[{"source":"old/repo"}]\n');

  const result = initializeConfig({
    env: { CLOUD_UTILS_CONFIG_DIR: configDir, HOME: sandbox.env.HOME },
    managerDir,
    stderr: { write() {} },
  });

  assert.equal(result.skillsFile, join(configDir, "skm/list.json"));
  assert.equal(readFileSync(result.skillsFile, "utf8"), '[{"source":"old/repo"}]\n');
  assert.equal(existsSync(join(configDir, "skills/list.json")), true);
});
```

- [ ] **Step 2: Run the tests and confirm the module is absent**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/config.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `config.mjs`.

- [ ] **Step 3: Implement configuration resolution and bootstrap**

Create `config.mjs`:

```js
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { basename, dirname, join } from "node:path";

const CYAN = "\u001b[36m";
const FG_RESET = "\u001b[39m";

export function defaultConfigDir(env = process.env) {
  return `${env.HOME ?? ""}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils`;
}

export function bootstrapFile({
  dest,
  example,
  legacy = "",
  stderr = process.stderr,
  fs = { copyFileSync, existsSync, mkdirSync },
}) {
  if (fs.existsSync(dest)) return true;
  fs.mkdirSync(dirname(dest), { recursive: true });

  if (legacy && fs.existsSync(legacy)) {
    fs.copyFileSync(legacy, dest);
    stderr.write(`${CYAN}◇${FG_RESET} Migrated ${basename(dest)} → ${dest}\n`);
    return true;
  }

  if (fs.existsSync(example)) {
    fs.copyFileSync(example, dest);
    return true;
  }

  return false;
}

export function initializeConfig({
  env = process.env,
  managerDir,
  stderr = process.stderr,
  fs = { copyFileSync, existsSync, mkdirSync },
}) {
  const configDir = env.CLOUD_UTILS_CONFIG_DIR || defaultConfigDir(env);
  fs.mkdirSync(configDir, { recursive: true });
  const skillsFile = join(configDir, "skm/list.json");
  const example = join(managerDir, "list.json.example");

  try {
    bootstrapFile({
      dest: skillsFile,
      example,
      legacy: join(managerDir, "list.json"),
      stderr,
      fs,
    });
  } catch {}

  if (!fs.existsSync(skillsFile)) {
    try {
      bootstrapFile({
        dest: skillsFile,
        example,
        legacy: join(configDir, "skills/list.json"),
        stderr,
        fs,
      });
    } catch {}
  }

  return { configDir, skillsFile };
}
```

- [ ] **Step 4: Add the explicit mkdir failure assertion**

Append to `config.test.mjs`:

```js
test("initializeConfig propagates config directory creation failure", () => {
  const failure = new Error("mkdir denied");
  assert.throws(
    () => initializeConfig({
      env: { CLOUD_UTILS_CONFIG_DIR: "/denied" },
      managerDir: "/manager",
      fs: {
        copyFileSync() {},
        existsSync() { return false; },
        mkdirSync() { throw failure; },
      },
    }),
    failure,
  );
});
```

- [ ] **Step 5: Run configuration tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/config.test.mjs
```

Expected: PASS.

- [ ] **Step 6: Commit configuration migration**

```bash
git add mac_scripts/functions/skills-manager/config.mjs mac_scripts/functions/skills-manager/__tests__/config.test.mjs
git commit -m "feat(skm): port configuration bootstrap to JavaScript"
```

---

### Task 3: Port Source Persistence

**Files:**

- Create: `mac_scripts/functions/skills-manager/sources.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/sources.test.mjs`

**Interfaces:**

- Consumes: the `skillsFile` returned by `initializeConfig()`.
- Produces: `SourceListError`, `normalizeSourceFile(file)`, `readSources(file)`, `hasSource(items, source)`, `addSources(items, requested)`, `removeSources(items, requested)`, and `writeSourcesAtomic(file, items, options)`.

- [ ] **Step 1: Write failing source-model tests**

Create `sources.test.mjs`:

```js
import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import * as realFs from "node:fs";
import test from "node:test";
import { makeSandbox } from "./helpers.mjs";
import {
  SourceListError,
  addSources,
  hasSource,
  normalizeSourceFile,
  readSources,
  removeSources,
  writeSourcesAtomic,
} from "../sources.mjs";

test("normalizeSourceFile creates a canonical empty file", (t) => {
  const sandbox = makeSandbox(t);
  const missing = `${sandbox.root}/nested/list.json`;
  normalizeSourceFile(missing);
  assert.equal(readFileSync(missing, "utf8"), "[]\n");
});

test("readSources accepts array and legacy presets while preserving item properties", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.skillsFile, '{"extra":true,"presets":[{"source":"a/one","label":"A"}]}\n');
  assert.deepEqual(readSources(sandbox.skillsFile), [{ source: "a/one", label: "A" }]);
});

test("readSources rejects malformed and structurally invalid data", (t) => {
  const sandbox = makeSandbox(t);
  for (const value of ["{", "{}", "null", "[null]", '[{"source":1}]']) {
    writeFileSync(sandbox.skillsFile, value);
    assert.throws(() => readSources(sandbox.skillsFile), SourceListError);
    assert.equal(readFileSync(sandbox.skillsFile, "utf8"), value);
  }
});

test("addSources handles same-command duplicates and opaque values", () => {
  const original = [{ source: "z/last", keep: true }];
  const result = addSources(original, ["a/space value", "雪/repo|%", "a/space value"]);
  assert.deepEqual(result.results, [
    { source: "a/space value", status: "added" },
    { source: "雪/repo|%", status: "added" },
    { source: "a/space value", status: "exists" },
  ]);
  assert.equal(result.added, 2);
  assert.equal(result.skipped, 1);
  assert.equal(result.items.find((item) => item.source === "z/last").keep, true);
});

test("removeSources classifies repeats against the original list", () => {
  const result = removeSources(
    [{ source: "a/one", keep: true }, { source: "b/two" }],
    ["a/one", "a/one", "x/missing"],
  );
  assert.deepEqual(result.results, [
    { source: "a/one", status: "removed" },
    { source: "a/one", status: "removed" },
    { source: "x/missing", status: "missing" },
  ]);
  assert.deepEqual(result.items, [{ source: "b/two" }]);
  assert.equal(result.removed, 2);
  assert.equal(result.skipped, 1);
});

test("atomic write uses the pid temporary name and removes it after success", (t) => {
  const sandbox = makeSandbox(t);
  writeSourcesAtomic(sandbox.skillsFile, [{ source: "a/one" }], { pid: 4242 });
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), '[\n  {\n    "source": "a/one"\n  }\n]\n');
  assert.equal(existsSync(`${sandbox.skillsFile}.4242.tmp`), false);
  assert.equal(hasSource(readSources(sandbox.skillsFile), "a/one"), true);
});

test("a modifying write drops legacy top-level fields and keeps item fields", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.skillsFile, '{"label":"legacy","presets":[{"source":"a/one","keep":true}]}\n');
  const result = addSources(readSources(sandbox.skillsFile), ["b/two"]);
  writeSourcesAtomic(sandbox.skillsFile, result.items);
  assert.deepEqual(JSON.parse(readFileSync(sandbox.skillsFile, "utf8")), [
    { source: "a/one", keep: true },
    { source: "b/two" },
  ]);
});
```

- [ ] **Step 2: Run tests and confirm the persistence module is absent**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/sources.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `sources.mjs`.

- [ ] **Step 3: Implement validation, transformations, and atomic writes**

Create `sources.mjs`:

```js
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";

const defaultFs = {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
};

export class SourceListError extends Error {
  constructor(filePath, cause) {
    super(`Could not read source list: ${filePath}`, { cause });
    this.name = "SourceListError";
    this.filePath = filePath;
  }
}

export function normalizeSourceFile(filePath, fs = defaultFs) {
  if (fs.existsSync(filePath)) return;
  fs.mkdirSync(dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, "[]\n", "utf8");
}

export function readSources(filePath, fs = defaultFs) {
  try {
    const data = JSON.parse(fs.readFileSync(filePath, "utf8"));
    const items = Array.isArray(data)
      ? data
      : data && typeof data === "object" && Array.isArray(data.presets)
        ? data.presets
        : null;
    if (
      !items ||
      items.some((item) => !item || typeof item !== "object" || Array.isArray(item) || typeof item.source !== "string")
    ) {
      throw new TypeError("invalid source-list shape");
    }
    return items;
  } catch (error) {
    throw new SourceListError(filePath, error);
  }
}

export function hasSource(items, source) {
  return items.some((item) => item.source === source);
}

export function addSources(items, requested) {
  const next = items.map((item) => ({ ...item }));
  const results = [];
  for (const source of requested) {
    if (hasSource(next, source)) {
      results.push({ source, status: "exists" });
    } else {
      next.push({ source });
      results.push({ source, status: "added" });
    }
  }
  next.sort((a, b) => a.source.localeCompare(b.source));
  return {
    items: next,
    results,
    added: results.filter((item) => item.status === "added").length,
    skipped: results.filter((item) => item.status === "exists").length,
  };
}

export function removeSources(items, requested) {
  const existing = new Set(items.map((item) => item.source));
  const wanted = new Set(requested);
  const results = requested.map((source) => ({
    source,
    status: existing.has(source) ? "removed" : "missing",
  }));
  return {
    items: items.filter((item) => !wanted.has(item.source)),
    results,
    removed: results.filter((item) => item.status === "removed").length,
    skipped: results.filter((item) => item.status === "missing").length,
  };
}

export function writeSourcesAtomic(filePath, items, { fs = defaultFs, pid = process.pid } = {}) {
  const tempPath = `${filePath}.${pid}.tmp`;
  try {
    fs.writeFileSync(tempPath, `${JSON.stringify(items, null, 2)}\n`, "utf8");
    fs.renameSync(tempPath, filePath);
  } catch (error) {
    try {
      fs.rmSync(tempPath, { force: true });
    } catch {}
    throw error;
  }
}
```

- [ ] **Step 4: Add a failed-rename regression test**

Append to `sources.test.mjs`:

```js
test("failed atomic rename keeps original bytes and cleans its own temp file", (t) => {
  const sandbox = makeSandbox(t);
  const before = readFileSync(sandbox.skillsFile, "utf8");
  const fs = {
    ...realFs,
    renameSync() { throw new Error("rename failed"); },
  };

  assert.throws(
    () => writeSourcesAtomic(sandbox.skillsFile, [{ source: "new/repo" }], { fs, pid: 9876 }),
    /rename failed/,
  );
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), before);
  assert.equal(existsSync(`${sandbox.skillsFile}.9876.tmp`), false);
});

test("serialization failure leaves the original untouched", (t) => {
  const sandbox = makeSandbox(t);
  const before = readFileSync(sandbox.skillsFile, "utf8");
  const circular = { source: "a/one" };
  circular.self = circular;
  assert.throws(() => writeSourcesAtomic(sandbox.skillsFile, [circular], { pid: 2468 }), TypeError);
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), before);
  assert.equal(existsSync(`${sandbox.skillsFile}.2468.tmp`), false);
});

test("temporary-file write failure leaves the original untouched", (t) => {
  const sandbox = makeSandbox(t);
  const before = readFileSync(sandbox.skillsFile, "utf8");
  const fs = {
    ...realFs,
    writeFileSync() { throw new Error("write failed"); },
  };
  assert.throws(
    () => writeSourcesAtomic(sandbox.skillsFile, [{ source: "new/repo" }], { fs, pid: 1357 }),
    /write failed/,
  );
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), before);
  assert.equal(existsSync(`${sandbox.skillsFile}.1357.tmp`), false);
});
```

- [ ] **Step 5: Run persistence tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/sources.test.mjs
```

Expected: PASS.

- [ ] **Step 6: Commit source persistence**

```bash
git add mac_scripts/functions/skills-manager/sources.mjs mac_scripts/functions/skills-manager/__tests__/sources.test.mjs
git commit -m "feat(skm): add JavaScript source persistence"
```

---

### Task 4: Port ANSI Rendering

**Files:**

- Create: `mac_scripts/functions/skills-manager/ui.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs`

**Interfaces:**

- Consumes: writable stdout/stderr-like objects and command result objects from `sources.mjs`.
- Produces: `createUi({ stdout, stderr })` with `error`, `warn`, `usage`, `list`, `addResult`, `removeResult`, `selector`, `cancelledSelector`, `listEnd`, and `installing` methods.

- [ ] **Step 1: Write failing stream and rendering tests**

Create `ui.test.mjs`:

```js
import assert from "node:assert/strict";
import test from "node:test";
import { createUi } from "../ui.mjs";

function memoryStream() {
  let value = "";
  return { write(chunk) { value += chunk; }, read() { return value; } };
}

test("errors and warnings go only to stderr", () => {
  const stdout = memoryStream();
  const stderr = memoryStream();
  const ui = createUi({ stdout, stderr });
  ui.error("broken");
  ui.warn("careful");
  assert.equal(stdout.read(), "");
  assert.match(stderr.read(), /❌ broken/);
  assert.match(stderr.read(), /⚠️  careful/);
});

test("list output keeps ANSI when captured", () => {
  const stdout = memoryStream();
  const ui = createUi({ stdout, stderr: memoryStream() });
  ui.list("/tmp/list.json", [{ source: "a/one" }]);
  assert.match(stdout.read(), /\u001b\[46m/);
  assert.match(stdout.read(), /Found .*1.* sources/);
  assert.match(stdout.read(), /■.* a\/one/);
});

test("result renderers preserve opaque percent and pipe characters", () => {
  const stdout = memoryStream();
  const ui = createUi({ stdout, stderr: memoryStream() });
  ui.addResult("/tmp/list.json", 1, {
    results: [{ source: "雪/repo|%F{red}", status: "added" }],
    added: 1,
    skipped: 0,
  });
  assert.match(stdout.read(), /Added: 雪\/repo\|%F\{red\}/);
});
```

- [ ] **Step 2: Run tests and confirm the UI module is absent**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `ui.mjs`.

- [ ] **Step 3: Implement all rendering primitives and command views**

Create `ui.mjs`:

```js
const C = {
  cyan: "\u001b[36m",
  green: "\u001b[32m",
  red: "\u001b[31m",
  yellow: "\u001b[33m",
  black: "\u001b[30m",
  gray: "\u001b[90m",
  brightGreen: "\u001b[92m",
  white: "\u001b[97m",
  bgCyan: "\u001b[46m",
  fgReset: "\u001b[39m",
  bgReset: "\u001b[49m",
  reset: "\u001b[0m",
};

const fg = (color, text) => `${color}${text}${C.fgReset}`;
const pipe = fg(C.cyan, "│");

export function createUi({ stdout = process.stdout, stderr = process.stderr } = {}) {
  const out = (line = "") => stdout.write(`${line}\n`);
  const err = (line) => stderr.write(`${line}\n`);
  const title = () => {
    out();
    out(`   ${C.bgCyan}${C.black} SKILLS MANAGER ${C.fgReset}${C.bgReset}`);
    out(pipe);
  };
  const step = (text) => {
    out(`${fg(C.green, "◇")}  ${text}`);
    out(pipe);
  };
  const active = (text) => out(`${fg(C.cyan, "◆")}  ${text}`);
  const listEnd = () => out(fg(C.cyan, "└"));

  function usage() {
    title();
    step("Usage: skm <command> [args]");
    active("Available commands");
    out();
    out(`${pipe}  ${fg(C.green, "ls")}, ${fg(C.green, "list")}`);
    out(`${pipe}      Show saved sources from skm/list.json`);
    out(pipe);
    out(`${pipe}  ${fg(C.green, "show")} [source]`);
    out(`${pipe}      Show available skills; opens a single-select UI without source`);
    out(pipe);
    out(`${pipe}  ${fg(C.green, "add")} <source...>`);
    out(`${pipe}      Add or update saved sources`);
    out(pipe);
    out(`${pipe}  ${fg(C.green, "remove")} <source...>`);
    out(`${pipe}      Remove saved sources`);
    listEnd();
  }

  function list(file, items) {
    title();
    step(`Source list: ${file}`);
    step(`Found ${fg(C.green, String(items.length))} sources`);
    active("Saved sources");
    if (items.length === 0) {
      out(`${pipe}  ${fg(C.yellow, "■")} No sources saved`);
    } else {
      for (const item of items) out(`${pipe}  ${fg(C.green, "■")} ${item.source}`);
    }
    listEnd();
  }

  function addResult(file, requestCount, result) {
    title();
    step(`Source list: ${file}`);
    step(`Add request: ${requestCount} sources`);
    active("Add sources");
    for (const item of result.results) {
      const added = item.status === "added";
      out(`${pipe}  ${fg(added ? C.green : C.yellow, "■")} ${added ? "Added" : "Already exists"}: ${item.source}`);
    }
    out(`${fg(C.cyan, "└")}  Added ${result.added} sources, skipped ${result.skipped} existing`);
  }

  function removeResult(file, requestCount, result) {
    title();
    step(`Source list: ${file}`);
    step(`Remove request: ${requestCount} sources`);
    active("Remove sources");
    for (const item of result.results) {
      const removed = item.status === "removed";
      out(`${pipe}  ${fg(removed ? C.green : C.yellow, "■")} ${removed ? "Removed" : "Not found"}: ${item.source}`);
    }
    out(`${fg(C.cyan, "└")}  Removed ${result.removed} sources, skipped ${result.skipped} missing`);
  }

  function renderSelector(file, state, { mode, cancelled }) {
    stdout.write("\u001b[2J\u001b[H");
    title();
    step(`Source list: ${file}`);
    step(`Found ${fg(C.green, String(state.sources.length))} sources`);
    const install = mode === "install";
    active(install
      ? `Select sources to install ${fg(C.white, "(space to toggle, enter to start, q to quit)")}`
      : `Select source to inspect ${fg(C.white, "(enter to show, q to quit)")}`);
    out(pipe);
    state.sources.forEach((source, index) => {
      const selected = install ? state.selected.has(index) : index === state.cursor;
      const box = selected ? "■" : "□";
      const boxColor = selected ? C.brightGreen : C.gray;
      const sourceColor = index === state.cursor ? C.white : C.gray;
      out(`${pipe}  ${boxColor}${box}${C.reset} ${sourceColor}${source}${C.reset}`);
    });
    if (cancelled) {
      out(pipe);
      const label = install ? "Select sources to install" : "Select source to inspect";
      out(`${fg(C.cyan, "└")}  ${fg(C.red, `${label} cancelled`)}`);
    } else {
      listEnd();
    }
  }

  function selector(file, state, { mode }) {
    renderSelector(file, state, { mode, cancelled: false });
  }

  function cancelledSelector(file, state, { mode }) {
    renderSelector(file, state, { mode, cancelled: true });
  }

  return {
    usage,
    list,
    addResult,
    removeResult,
    selector,
    cancelledSelector,
    listEnd,
    installing(source) { out(`${fg(C.cyan, "▶ Installing source:")} ${source}`); },
    blank() { out(); },
    error(message) { err(fg(C.red, `❌ ${message}`)); },
    warn(message) { err(fg(C.yellow, `⚠️  ${message}`)); },
    usageLine(text) { out(text); },
  };
}
```

- [ ] **Step 4: Add exact help and selector assertions**

Append to `ui.test.mjs`:

```js
test("help contains every documented command and omits list-available", () => {
  const stdout = memoryStream();
  const ui = createUi({ stdout, stderr: memoryStream() });
  ui.usage();
  const rendered = stdout.read();
  for (const text of ["ls", "list", "show", "add", "remove"]) assert.match(rendered, new RegExp(text));
  assert.doesNotMatch(rendered, /list-available/);
});

test("install selector renders selected and cursor state", () => {
  const stdout = memoryStream();
  const ui = createUi({ stdout, stderr: memoryStream() });
  ui.selector("/tmp/list.json", {
    sources: ["a/one", "b/two"],
    cursor: 1,
    selected: new Set([0]),
  }, { mode: "install" });
  assert.match(stdout.read(), /space to toggle/);
  assert.match(stdout.read(), /■.*a\/one/);
  assert.match(stdout.read(), /□.*b\/two/);
});
```

- [ ] **Step 5: Run UI tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: PASS.

- [ ] **Step 6: Commit UI rendering**

```bash
git add mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git commit -m "feat(skm): port terminal rendering to JavaScript"
```

---

### Task 5: Add the Shell-Free Skills Process Adapter

**Files:**

- Create: `mac_scripts/functions/skills-manager/skills-cli.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs`

**Interfaces:**

- Consumes: `PATH`, an optional injected `spawn` implementation, and exact source strings.
- Produces: `hasCommand(name, options) -> boolean` and `runNpx(args, options) -> Promise<number>`.

- [ ] **Step 1: Write failing prerequisite and argv tests**

Create `skills-cli.test.mjs`:

```js
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { hasCommand, runNpx } from "../skills-cli.mjs";
import { makeSandbox } from "./helpers.mjs";

test("hasCommand searches PATH without a shell", (t) => {
  const sandbox = makeSandbox(t);
  assert.equal(hasCommand("npx", { env: sandbox.env }), true);
  assert.equal(hasCommand("definitely-missing", { env: sandbox.env }), false);
});

test("runNpx preserves opaque arguments and inherited stdio", async () => {
  const calls = [];
  const spawnImpl = (command, args, options) => {
    calls.push({ command, args, options });
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("close", 7, null));
    return child;
  };
  const status = await runNpx(["skills", "add", "owner/repo with | %", "--list"], { spawnImpl });
  assert.equal(status, 7);
  assert.deepEqual(calls, [{
    command: "npx",
    args: ["skills", "add", "owner/repo with | %", "--list"],
    options: { stdio: "inherit" },
  }]);
});

test("runNpx maps spawn and signal-only failures to one", async () => {
  const spawnError = () => {
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("error", new Error("ENOENT")));
    return child;
  };
  const signalOnly = () => {
    const child = new EventEmitter();
    queueMicrotask(() => child.emit("close", null, "SIGTERM"));
    return child;
  };
  assert.equal(await runNpx([], { spawnImpl: spawnError }), 1);
  assert.equal(await runNpx([], { spawnImpl: signalOnly }), 1);
});
```

- [ ] **Step 2: Run tests and confirm the adapter is absent**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `skills-cli.mjs`.

- [ ] **Step 3: Implement executable lookup and child status mapping**

Create `skills-cli.mjs`:

```js
import { accessSync, constants } from "node:fs";
import { delimiter, join } from "node:path";
import { spawn } from "node:child_process";

export function hasCommand(name, { env = process.env, access = accessSync } = {}) {
  for (const directory of (env.PATH ?? "").split(delimiter).filter(Boolean)) {
    try {
      access(join(directory, name), constants.X_OK);
      return true;
    } catch {}
  }
  return false;
}

export function runNpx(args, { spawnImpl = spawn } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (status) => {
      if (settled) return;
      settled = true;
      resolve(status);
    };
    let child;
    try {
      child = spawnImpl("npx", args, { stdio: "inherit" });
    } catch {
      finish(1);
      return;
    }
    child.once("error", () => finish(1));
    child.once("close", (status) => finish(Number.isInteger(status) ? status : 1));
  });
}
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs
```

Expected: PASS.

- [ ] **Step 5: Commit process adapter**

```bash
git add mac_scripts/functions/skills-manager/skills-cli.mjs mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs
git commit -m "feat(skm): add shell-free skills process adapter"
```

---

### Task 6: Implement Selector State and Terminal Lifecycle

**Files:**

- Create: `mac_scripts/functions/skills-manager/selector.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/selector.test.mjs`

**Interfaces:**

- Consumes: source strings, a TTY-readable stream, render callback, and process-like signal emitter.
- Produces: `createSelectorState(sources)`, `createKeyDecoder()`, `decodeKeys(buffer)`, `reduceSelector(state, key, options)`, and `runSelector(options) -> Promise<{ type, state, selected }>`.

- [ ] **Step 1: Write failing pure state-machine tests**

Create `selector.test.mjs`:

```js
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { createKeyDecoder, createSelectorState, decodeKeys, reduceSelector, runSelector } from "../selector.mjs";

test("decodeKeys recognizes arrows, vim keys, space, enter, q, and Ctrl+C", () => {
  assert.deepEqual(
    decodeKeys(Buffer.from("\u001b[A\u001b[Bjk \rq\u0003")),
    ["up", "down", "down", "up", "toggle", "submit", "cancel", "cancel"],
  );
});

test("createKeyDecoder preserves a split arrow sequence", () => {
  const decoder = createKeyDecoder();
  assert.deepEqual(decoder.push(Buffer.from("\u001b[")), []);
  assert.deepEqual(decoder.push(Buffer.from("A")), ["up"]);
});

test("cursor movement clamps and multi-select returns display order", () => {
  let state = createSelectorState(["a", "b", "c"]);
  state = reduceSelector(state, "up", { multiple: true }).state;
  assert.equal(state.cursor, 0);
  state = reduceSelector(state, "toggle", { multiple: true }).state;
  state = reduceSelector(state, "down", { multiple: true }).state;
  state = reduceSelector(state, "down", { multiple: true }).state;
  state = reduceSelector(state, "toggle", { multiple: true }).state;
  const result = reduceSelector(state, "submit", { multiple: true });
  assert.deepEqual(result.selected, ["a", "c"]);
});
```

- [ ] **Step 2: Run tests and confirm the selector module is absent**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/selector.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `selector.mjs`.

- [ ] **Step 3: Implement decoding and the pure reducer**

Create `selector.mjs` with these exports:

```js
export function createSelectorState(sources) {
  return { sources: [...sources], cursor: 0, selected: new Set() };
}

export function decodeKeys(buffer) {
  const input = buffer.toString("utf8");
  const keys = [];
  for (let index = 0; index < input.length;) {
    const sequence = input.slice(index, index + 3);
    if (sequence === "\u001b[A") {
      keys.push("up");
      index += 3;
    } else if (sequence === "\u001b[B") {
      keys.push("down");
      index += 3;
    } else {
      const value = input[index++];
      if (value === "k") keys.push("up");
      else if (value === "j") keys.push("down");
      else if (value === " ") keys.push("toggle");
      else if (value === "\r" || value === "\n") keys.push("submit");
      else if (value === "q" || value === "\u0003") keys.push("cancel");
    }
  }
  return keys;
}

export function createKeyDecoder() {
  let pending = "";
  return {
    push(buffer) {
      pending += buffer.toString("utf8");
      const hold = pending.endsWith("\u001b") || pending.endsWith("\u001b[");
      const complete = hold ? pending.slice(0, pending.lastIndexOf("\u001b")) : pending;
      pending = hold ? pending.slice(pending.lastIndexOf("\u001b")) : "";
      return decodeKeys(Buffer.from(complete));
    },
  };
}

export function reduceSelector(state, key, { multiple }) {
  const next = {
    sources: state.sources,
    cursor: state.cursor,
    selected: new Set(state.selected),
  };
  if (key === "up") next.cursor = Math.max(0, next.cursor - 1);
  if (key === "down") next.cursor = Math.min(next.sources.length - 1, next.cursor + 1);
  if (key === "toggle" && multiple) {
    if (next.selected.has(next.cursor)) next.selected.delete(next.cursor);
    else next.selected.add(next.cursor);
  }
  if (key === "cancel") return { type: "cancel", state: next, selected: [] };
  if (key === "submit") {
    const indexes = multiple ? [...next.selected].sort((a, b) => a - b) : [next.cursor];
    return { type: "submit", state: next, selected: indexes.map((index) => next.sources[index]) };
  }
  return { type: "continue", state: next, selected: [] };
}
```

- [ ] **Step 4: Add terminal cleanup tests**

Append to `selector.test.mjs`:

```js
class FakeInput extends EventEmitter {
  constructor() {
    super();
    this.isTTY = true;
    this.isRaw = false;
    this.rawCalls = [];
  }
  setRawMode(value) { this.isRaw = value; this.rawCalls.push(value); }
  resume() {}
  pause() {}
}

test("runSelector restores raw mode and listeners on submit", async () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  processRef.pid = 123;
  processRef.kill = () => assert.fail("submit must not signal the process");
  const promise = runSelector({
    sources: ["a", "b"],
    multiple: true,
    input,
    render() {},
    processRef,
  });
  input.emit("data", Buffer.from(" \r"));
  const result = await promise;
  assert.deepEqual(result.selected, ["a"]);
  assert.deepEqual(input.rawCalls, [true, false]);
  assert.equal(input.listenerCount("data"), 0);
  assert.equal(processRef.listenerCount("SIGINT"), 0);
});

test("EOF and SIGINT use cancellation cleanup", async () => {
  for (const finish of [
    (input) => input.emit("end"),
    (_input, processRef) => processRef.emit("SIGINT"),
  ]) {
    const input = new FakeInput();
    const processRef = new EventEmitter();
    processRef.pid = 123;
    processRef.kill = () => {};
    const promise = runSelector({ sources: ["a"], multiple: false, input, render() {}, processRef });
    finish(input, processRef);
    assert.equal((await promise).type, "cancel");
    assert.equal(input.isRaw, false);
  }
});

test("SIGTERM and SIGHUP restore raw mode before re-signalling", async () => {
  for (const signal of ["SIGTERM", "SIGHUP"]) {
    const input = new FakeInput();
    const processRef = new EventEmitter();
    processRef.pid = 123;
    let observed;
    processRef.kill = (pid, sentSignal) => {
      observed = { pid, sentSignal, raw: input.isRaw };
    };
    runSelector({ sources: ["a"], multiple: false, input, render() {}, processRef });
    processRef.emit(signal);
    assert.deepEqual(observed, { pid: 123, sentSignal: signal, raw: false });
  }
});

test("SIGTSTP restores mode and SIGCONT resumes and redraws", () => {
  const input = new FakeInput();
  const processRef = new EventEmitter();
  processRef.pid = 123;
  const signals = [];
  let renders = 0;
  processRef.kill = (_pid, signal) => signals.push({ signal, raw: input.isRaw });
  runSelector({
    sources: ["a"],
    multiple: false,
    input,
    render() { renders += 1; },
    processRef,
  });
  processRef.emit("SIGTSTP");
  assert.deepEqual(signals, [{ signal: "SIGTSTP", raw: false }]);
  processRef.emit("SIGCONT");
  assert.equal(input.isRaw, true);
  assert.equal(renders, 2);
  processRef.emit("SIGINT");
});
```

- [ ] **Step 5: Implement the terminal session in the same module**

Append to `selector.mjs`:

```js
export function runSelector({
  sources,
  multiple,
  input = process.stdin,
  render,
  processRef = process,
}) {
  return new Promise((resolve, reject) => {
    let state = createSelectorState(sources);
    let active = true;
    const decoder = createKeyDecoder();
    const priorRaw = Boolean(input.isRaw);

    const setInputMode = (enabled) => {
      if (input.isTTY && typeof input.setRawMode === "function") input.setRawMode(enabled ? true : priorRaw);
      if (enabled) input.resume();
      else input.pause();
    };

    const removeListeners = () => {
      input.off("data", onData);
      input.off("end", onEnd);
      for (const signal of ["SIGINT", "SIGTERM", "SIGHUP", "SIGTSTP", "SIGCONT"]) {
        processRef.off(signal, signalHandlers[signal]);
      }
    };

    const cleanup = () => {
      if (!active) return;
      active = false;
      removeListeners();
      setInputMode(false);
    };

    const finish = (result) => {
      cleanup();
      resolve(result);
    };

    const onData = (chunk) => {
      try {
        for (const key of decoder.push(chunk)) {
          const result = reduceSelector(state, key, { multiple });
          state = result.state;
          if (result.type !== "continue") {
            finish(result);
            return;
          }
          render(state);
        }
      } catch (error) {
        cleanup();
        reject(error);
      }
    };
    const onEnd = () => finish({ type: "cancel", state, selected: [] });
    const cancel = () => finish({ type: "cancel", state, selected: [] });
    const terminate = (signal) => {
      cleanup();
      processRef.kill(processRef.pid, signal);
    };
    const suspend = () => terminate("SIGTSTP");
    const resume = () => {
      if (!active) return;
      setInputMode(true);
      render(state);
    };
    const signalHandlers = {
      SIGINT: cancel,
      SIGTERM: () => terminate("SIGTERM"),
      SIGHUP: () => terminate("SIGHUP"),
      SIGTSTP: suspend,
      SIGCONT: resume,
    };

    input.on("data", onData);
    input.on("end", onEnd);
    for (const [signal, handler] of Object.entries(signalHandlers)) processRef.on(signal, handler);
    try {
      setInputMode(true);
      render(state);
    } catch (error) {
      cleanup();
      reject(error);
    }
  });
}
```

- [ ] **Step 6: Correct suspend/resume to keep the selector active**

Replace `suspend`, `resume`, and the `SIGTSTP`/`SIGCONT` handling with this
explicit suspended state. This avoids resolving the selector during a shell
job-control round trip:

```js
let suspended = false;

const suspend = () => {
  if (!active || suspended) return;
  suspended = true;
  input.off("data", onData);
  setInputMode(false);
  processRef.off("SIGTSTP", signalHandlers.SIGTSTP);
  processRef.kill(processRef.pid, "SIGTSTP");
  processRef.on("SIGTSTP", signalHandlers.SIGTSTP);
};

const resume = () => {
  if (!active || !suspended) return;
  suspended = false;
  input.on("data", onData);
  setInputMode(true);
  render(state);
};
```

Add `suspended = false;` inside `cleanup()` immediately before
`removeListeners()`.

- [ ] **Step 7: Run selector tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/selector.test.mjs
```

Expected: PASS with no remaining listeners and raw mode restored.

- [ ] **Step 8: Commit selector implementation**

```bash
git add mac_scripts/functions/skills-manager/selector.mjs mac_scripts/functions/skills-manager/__tests__/selector.test.mjs
git commit -m "feat(skm): implement JavaScript terminal selectors"
```

---

### Task 7: Implement Command Dispatch

**Files:**

- Create: `mac_scripts/functions/skills-manager/cli.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/cli.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/helpers.mjs`

**Interfaces:**

- Consumes: all exports from Tasks 2–6.
- Produces: `runCli(argv, options) -> Promise<number>` for tests and an executable `cli.mjs` entrypoint for the wrapper.

- [ ] **Step 1: Write failing non-interactive dispatch tests**

Create `cli.test.mjs`:

```js
import assert from "node:assert/strict";
import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import test from "node:test";
import { makeSandbox, runJavaScript } from "./helpers.mjs";

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

test("invalid data is deterministic and byte preserving", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.skillsFile, "{broken", "utf8");
  const result = runJavaScript(["list"], sandbox);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Could not read source list/);
  assert.doesNotMatch(result.stderr, /SyntaxError|stack/);
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), "{broken");
});
```

- [ ] **Step 2: Run tests and confirm the CLI is absent**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/cli.test.mjs
```

Expected: FAIL because `cli.mjs` does not exist.

- [ ] **Step 3: Implement startup, validation order, and non-interactive commands**

Create `cli.mjs`:

```js
#!/usr/bin/env node

import { dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { initializeConfig } from "./config.mjs";
import { runSelector } from "./selector.mjs";
import { hasCommand, runNpx } from "./skills-cli.mjs";
import {
  SourceListError,
  addSources,
  hasSource,
  normalizeSourceFile,
  readSources,
  removeSources,
  writeSourcesAtomic,
} from "./sources.mjs";
import { createUi } from "./ui.mjs";

const MANAGER_DIR = dirname(fileURLToPath(import.meta.url));
const NO_SOURCES = "No sources saved. Add one with: skm add <source>";

export async function runCli(argv, {
  env = process.env,
  stdin = process.stdin,
  stdout = process.stdout,
  stderr = process.stderr,
  managerDir = MANAGER_DIR,
  npxRunner = runNpx,
  selectorRunner = runSelector,
} = {}) {
  const ui = createUi({ stdout, stderr });
  let skillsFile;
  try {
    ({ skillsFile } = initializeConfig({ env, managerDir, stderr }));
  } catch {
    ui.error("Could not create config directory.");
    return 1;
  }

  const requireNpx = () => {
    if (hasCommand("npx", { env })) return true;
    ui.error("npx is required to run 'npx skills add'");
    return false;
  };

  const load = () => {
    normalizeSourceFile(skillsFile);
    return readSources(skillsFile);
  };

  const action = argv[0];
  const args = argv.slice(1);

  try {
    if (["help", "-h", "--help"].includes(action)) {
      ui.usage();
      return 0;
    }
    if (action === undefined) return runInstallSelector();
    if (action === "ls" || action === "list") {
      if (!requireNpx()) return 1;
      ui.list(skillsFile, load());
      return 0;
    }
    if (action === "show" || action === "list-available") return runShow(args);
    if (action === "add") return runAdd(args);
    if (action === "remove" || action === "rm") return runRemove(args);
    ui.error(`Unknown command: ${action}`);
    ui.usageLine("Use 'skm --help' for usage information");
    return 1;
  } catch (error) {
    if (error instanceof SourceListError) {
      ui.error(`Could not read source list: ${error.filePath}`);
    } else {
      ui.error(error instanceof Error ? error.message : String(error));
    }
    return 1;
  }

  async function runShow(showArgs) {
    if (showArgs.length > 1) {
      ui.error("show accepts at most one source");
      ui.usageLine("Usage: skm show [source]");
      return 1;
    }
    if (!requireNpx()) return 1;
    if (showArgs.length === 1) {
      return npxRunner(["skills", "add", showArgs[0], "--list"]);
    }
    normalizeSourceFile(skillsFile);
    if (!stdin.isTTY || !stdout.isTTY) {
      ui.error("skm show requires an interactive terminal when no source is provided");
      ui.usageLine("Usage: skm show <source>");
      return 1;
    }
    const items = readSources(skillsFile);
    if (items.length === 0) {
      ui.warn(NO_SOURCES);
      return 1;
    }
    const result = await selectorRunner({
      sources: items.map((item) => item.source),
      multiple: false,
      input: stdin,
      render: (state) => ui.selector(skillsFile, state, { mode: "show" }),
    });
    if (result.type === "cancel") {
      ui.cancelledSelector(skillsFile, result.state, { mode: "show" });
      return 0;
    }
    return npxRunner(["skills", "add", result.selected[0], "--list"]);
  }

  function runAdd(requested) {
    if (requested.length === 0) {
      ui.error("At least one source is required");
      ui.usageLine("Usage: skm add <source...>");
      return 1;
    }
    if (!requireNpx()) return 1;
    const result = addSources(load(), requested);
    writeSourcesAtomic(skillsFile, result.items);
    ui.addResult(skillsFile, requested.length, result);
    return 0;
  }

  function runRemove(requested) {
    if (requested.length === 0) {
      ui.error("At least one source is required");
      ui.usageLine("Usage: skm remove <source...>");
      return 1;
    }
    if (!requireNpx()) return 1;
    const result = removeSources(load(), requested);
    writeSourcesAtomic(skillsFile, result.items);
    ui.removeResult(skillsFile, requested.length, result);
    return 0;
  }

  async function runInstallSelector() {
    if (!requireNpx()) return 1;
    normalizeSourceFile(skillsFile);
    if (!stdin.isTTY || !stdout.isTTY) {
      ui.error("skm requires an interactive terminal");
      return 1;
    }
    const items = readSources(skillsFile);
    if (items.length === 0) {
      ui.warn(NO_SOURCES);
      return 1;
    }
    const result = await selectorRunner({
      sources: items.map((item) => item.source),
      multiple: true,
      input: stdin,
      render: (state) => ui.selector(skillsFile, state, { mode: "install" }),
    });
    if (result.type === "cancel") {
      ui.cancelledSelector(skillsFile, result.state, { mode: "install" });
      return 0;
    }
    if (result.selected.length === 0) {
      ui.listEnd();
      ui.warn("No sources selected");
      return 1;
    }
    ui.listEnd();
    ui.blank();
    let failed = false;
    for (const source of result.selected) {
      const current = readSources(skillsFile);
      if (!hasSource(current, source)) {
        ui.error(`Source not found: ${source}`);
        failed = true;
        continue;
      }
      ui.installing(source);
      if (await npxRunner(["skills", "add", source]) !== 0) failed = true;
    }
    return failed ? 1 : 0;
  }
}

async function main() {
  process.exitCode = await runCli(process.argv.slice(2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
```

- [ ] **Step 4: Add injected interactive command tests**

Append to `cli.test.mjs`:

```js
import { PassThrough } from "node:stream";
import { runCli } from "../cli.mjs";

function ttyStream() {
  const stream = new PassThrough();
  stream.isTTY = true;
  stream.isRaw = false;
  stream.setRawMode = (value) => { stream.isRaw = value; };
  return stream;
}

test("install runs selected sources sequentially and aggregates failure", async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }, { source: "b/two" }] });
  const calls = [];
  const statuses = [2, 0];
  const stdin = ttyStream();
  const stdout = ttyStream();
  const status = await runCli([], {
    env: sandbox.env,
    stdin,
    stdout,
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

test("interactive cancellation exits zero without spawning", async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const stdin = ttyStream();
  const stdout = ttyStream();
  const status = await runCli(["show"], {
    env: sandbox.env,
    stdin,
    stdout,
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
  const stdin = ttyStream();
  const stdout = ttyStream();
  const stderr = new PassThrough();
  let errorOutput = "";
  stderr.setEncoding("utf8");
  stderr.on("data", (chunk) => { errorOutput += chunk; });
  const status = await runCli([], {
    env: sandbox.env,
    stdin,
    stdout,
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
  const sandbox = makeSandbox(t);
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

test("help bootstraps before dispatch without checking npx", async (t) => {
  const sandbox = makeSandbox(t);
  rmSync(sandbox.skillsFile);
  writeFileSync(`${sandbox.root}/list.json.example`, '[{"source":"example/repo"}]\n');
  const status = await runCli(["--help"], {
    env: { ...sandbox.env, PATH: sandbox.root },
    managerDir: sandbox.root,
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  assert.equal(status, 0);
  assert.equal(readFileSync(sandbox.skillsFile, "utf8"), '[{"source":"example/repo"}]\n');
});
```

- [ ] **Step 5: Run command tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/cli.test.mjs
```

Expected: PASS for non-interactive dispatch, opaque argv, invalid data, install
aggregation, and cancellation.

- [ ] **Step 6: Run all JavaScript unit tests together**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
```

Expected: PASS. `legacy-contract.test.mjs` still runs against `main.zsh`; all
other tests run against the new modules.

- [ ] **Step 7: Commit command dispatch**

```bash
git add mac_scripts/functions/skills-manager/cli.mjs mac_scripts/functions/skills-manager/__tests__/cli.test.mjs mac_scripts/functions/skills-manager/__tests__/helpers.mjs
git commit -m "feat(skm): implement JavaScript command dispatch"
```

---

### Task 8: Verify Differential and PTY Behavior Before Cutover

**Files:**

- Create: `mac_scripts/functions/skills-manager/__tests__/parity.test.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/pty.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs`

**Interfaces:**

- Consumes: normalized golden fixtures, both legacy and JavaScript entrypoints, and macOS `script` when available.
- Produces: exact ordinary-case differential checks and signal/TTY regression checks that gate removal of `main.zsh`.

- [ ] **Step 1: Write the differential test against the committed contract**

Create `parity.test.mjs`:

```js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  makeSandbox,
  normalizeCapture,
  runJavaScript,
  runLegacy,
} from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const GOLDENS = JSON.parse(readFileSync(join(TEST_DIR, "fixtures/golden.json"), "utf8"));

for (const entry of GOLDENS) {
  test(`JavaScript matches legacy golden: ${entry.name}`, (t) => {
    const sandbox = makeSandbox(t, { list: entry.list });
    const actual = normalizeCapture(runJavaScript(entry.args, sandbox), sandbox);
    assert.deepEqual(actual, entry.capture);
    if (entry.fileAfter !== undefined) {
      assert.equal(readFileSync(sandbox.skillsFile, "utf8"), entry.fileAfter);
    }
  });
}

test("legacy and JavaScript agree on representative ordinary cases", (t) => {
  const legacySandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const jsSandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const legacy = normalizeCapture(runLegacy(["add", "b/two"], legacySandbox), legacySandbox);
  const javascript = normalizeCapture(runJavaScript(["add", "b/two"], jsSandbox), jsSandbox);
  assert.deepEqual(javascript, legacy);
  assert.equal(readFileSync(jsSandbox.skillsFile, "utf8"), readFileSync(legacySandbox.skillsFile, "utf8"));
});
```

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/parity.test.mjs
```

Expected: PASS. If it fails, stop the task and compare the mismatch with the
four approved differences in spec Section 3 before changing code or fixtures.

- [ ] **Step 2: Run one focused golden assertion to verify test filtering**

Run:

```bash
node --test --test-name-pattern="JavaScript matches legacy golden: help" mac_scripts/functions/skills-manager/__tests__/parity.test.mjs
```

Expected: the `help` case passes and unrelated cases are skipped by the filter.

- [ ] **Step 3: Add a PTY driver for cancellation and raw-mode restoration**

Create `pty.test.mjs`:

```js
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import { JS_ENTRY, makeSandbox } from "./helpers.mjs";

const SCRIPT = "/usr/bin/script";
const hasScript = existsSync(SCRIPT);

function runInPty(args, sandbox, input) {
  return new Promise((resolve, reject) => {
    const command = [process.execPath, JS_ENTRY, ...args]
      .map((part) => `'${part.replaceAll("'", "'\\''")}'`)
      .join(" ");
    const child = spawn(SCRIPT, ["-q", "/dev/null", "/bin/zsh", "-c", command], {
      env: sandbox.env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", reject);
    child.once("close", (status, signal) => resolve({ status, signal, stdout, stderr }));
    child.stdin.end(input);
  });
}

test("q cancels install selector with status zero", { skip: !hasScript }, async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const result = await runInPty([], sandbox, "q");
  assert.equal(result.status, 0);
  assert.match(result.stdout, /Select sources to install cancelled/);
});

test("raw Ctrl+C cancels show selector with status zero", { skip: !hasScript }, async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const result = await runInPty(["show"], sandbox, "\u0003");
  assert.equal(result.status, 0);
  assert.match(result.stdout, /Select source to inspect cancelled/);
});

test("Enter disables selector input before child inherits stdio", { skip: !hasScript }, async (t) => {
  const sandbox = makeSandbox(t, { list: [{ source: "a/one" }] });
  const result = await runInPty(["show"], sandbox, "\r");
  assert.equal(result.status, 0);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","a/one","--list"]\n',
  );
});
```

- [ ] **Step 4: Run differential and PTY suites**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/parity.test.mjs mac_scripts/functions/skills-manager/__tests__/pty.test.mjs
```

Expected: all differential tests PASS; PTY tests PASS on the project macOS
terminal or report SKIP only when `script` is not available.

- [ ] **Step 5: Manually verify non-cancellation signals**

In a macOS terminal, point `CLOUD_UTILS_CONFIG_DIR` at a disposable directory,
start `node mac_scripts/functions/skills-manager/cli.mjs show`, and verify:

```text
SIGTERM: terminal echo is restored and the process terminates by SIGTERM
SIGHUP: terminal echo is restored and the process terminates by SIGHUP
Ctrl+Z: terminal echo is restored before suspension
fg: raw selector mode resumes and the selector redraws
```

Do not run these checks against the user's normal configuration directory.

- [ ] **Step 6: Commit the parity gate**

```bash
git add mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/cli.mjs mac_scripts/functions/skills-manager/sources.mjs mac_scripts/functions/skills-manager/__tests__/parity.test.mjs mac_scripts/functions/skills-manager/__tests__/pty.test.mjs mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs
git commit -m "test(skm): verify JavaScript parity and terminal lifecycle"
```

---

### Task 9: Switch the Wrapper and Remove the Zsh Implementation

**Files:**

- Modify: `mac_scripts/skm`
- Delete: `mac_scripts/functions/skills-manager/main.zsh`
- Modify: `mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/wrapper.test.mjs`
- Modify: `docs/commands.md`

**Interfaces:**

- Consumes: the verified `cli.mjs` executable contract.
- Produces: the final public `mac_scripts/skm` entrypoint with no runtime dependency on `main.zsh`.

- [ ] **Step 1: Write failing wrapper cutover tests**

Create `wrapper.test.mjs`:

```js
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { WRAPPER, makeSandbox } from "./helpers.mjs";

test("wrapper forwards opaque argument boundaries and child status", (t) => {
  const sandbox = makeSandbox(t);
  const result = spawnSync(WRAPPER, ["show", "owner/repo with space"], {
    env: { ...sandbox.env, SKM_NPX_STATUS: "7" },
    encoding: "utf8",
  });
  assert.equal(result.status, 7);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","owner/repo with space","--list"]\n',
  );
});

test("wrapper reports missing node after creating the config directory", (t) => {
  const sandbox = makeSandbox(t);
  const noNodeBin = join(sandbox.root, "no-node-bin");
  mkdirSync(noNodeBin);
  symlinkSync("/usr/bin/dirname", join(noNodeBin, "dirname"));
  symlinkSync("/bin/mkdir", join(noNodeBin, "mkdir"));
  const result = spawnSync("/bin/zsh", [WRAPPER, "--help"], {
    env: { ...sandbox.env, PATH: noNodeBin },
    encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /node is required to read and update/);
});
```

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/wrapper.test.mjs
```

Expected: FAIL because the current wrapper still sources `main.zsh` and checks
Node inside the function.

- [ ] **Step 2: Replace the wrapper with the approved compatibility entrypoint**

Replace `mac_scripts/skm` with:

```zsh
#!/bin/zsh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

CONFIG_DIR="${CLOUD_UTILS_CONFIG_DIR:-${HOME}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils}"
if ! mkdir -p "$CONFIG_DIR"; then
  print -P "%F{red}❌ Could not create config directory.%f" >&2
  exit 1
fi

SKILLS_FILE="$CONFIG_DIR/skm/list.json"
if ! command -v node >/dev/null 2>&1; then
  print -P "%F{red}❌ node is required to read and update ${SKILLS_FILE}%f" >&2
  exit 1
fi

exec node "$SCRIPT_DIR/functions/skills-manager/cli.mjs" "$@"
```

Preserve executable mode `100755`.

- [ ] **Step 3: Run wrapper tests before deleting legacy code**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/wrapper.test.mjs
```

Expected: PASS.

- [ ] **Step 4: Convert the legacy test into a fixture integrity test**

Replace `legacy-contract.test.mjs` with:

```js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(TEST_DIR, "fixtures/golden.json"), "utf8"),
);

test("legacy golden fixture retains the required named cases", () => {
  assert.deepEqual(
    fixture.map((entry) => entry.name),
    [
      "help",
      "unknown",
      "list-empty",
      "list-values",
      "add-new-and-duplicate",
      "remove-repeated-and-missing",
      "show-too-many",
      "add-missing-argument",
    ],
  );
  for (const entry of fixture) {
    assert.equal(typeof entry.capture.stdout, "string");
    assert.equal(typeof entry.capture.stderr, "string");
    assert.equal(Number.isInteger(entry.capture.status), true);
  }
});
```

Remove the now-unused `runLegacy` and `LEGACY_MAIN` exports from `helpers.mjs`.

- [ ] **Step 5: Remove `main.zsh` and update command documentation**

Delete `mac_scripts/functions/skills-manager/main.zsh`.

Add this sentence immediately after the `## skm` heading in `docs/commands.md`:

```markdown
Skills manager implemented as a Node.js CLI, invoked through the `mac_scripts/skm` compatibility wrapper. It stores sources in `$CLOUD_UTILS_CONFIG_DIR/skm/list.json`.
```

Remove the old standalone sentence `Skills manager —
$CLOUD_UTILS_CONFIG_DIR/skm/list.json` to avoid duplication.

- [ ] **Step 6: Run the complete automated suite on the cutover tree**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
```

Expected: PASS; PTY tests may SKIP only for a missing `script` executable.

Run:

```bash
rg -n "node -e|main\.zsh" mac_scripts/skm mac_scripts/functions/skills-manager docs/commands.md
```

Expected: no matches.

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 7: Run the manual public-command smoke checklist**

With a disposable `CLOUD_UTILS_CONFIG_DIR`, execute and confirm the expected UI
and status for every public form:

```bash
mac_scripts/skm
mac_scripts/skm ls
mac_scripts/skm list
mac_scripts/skm show
mac_scripts/skm show owner/repository
mac_scripts/skm list-available owner/repository
mac_scripts/skm add owner/repository
mac_scripts/skm remove owner/repository
mac_scripts/skm rm owner/repository
mac_scripts/skm help
mac_scripts/skm -h
mac_scripts/skm --help
mac_scripts/skm unknown-command
```

Expected: command, UI, and exit behavior matches `docs/skm-js-migration-spec.md`;
interactive tests leave terminal echo enabled.

- [ ] **Step 8: Commit the cutover**

```bash
git add mac_scripts/skm mac_scripts/functions/skills-manager docs/commands.md
git commit -m "feat(skm): migrate command implementation to JavaScript"
```

---

## Final Verification

- [ ] Run the full suite on Node.js 20:

  ```bash
  node --version
  node --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
  ```

  Expected: version starts with `v20.` and all supported tests pass.

- [ ] Confirm only the compatibility wrapper remains Zsh:

  ```bash
  rg -n "node -e|main\.zsh|function skm" mac_scripts/skm mac_scripts/functions/skills-manager
  ```

  Expected: no matches.

- [ ] Confirm the final file set:

  ```bash
  find mac_scripts/functions/skills-manager -maxdepth 2 -type f | sort
  ```

  Expected: `cli.mjs`, `config.mjs`, `sources.mjs`, `selector.mjs`,
  `skills-cli.mjs`, `ui.mjs`, `list.json.example`, and the test files; no
  `main.zsh`.

- [ ] Confirm worktree hygiene:

  ```bash
  git status --short
  git diff --check
  ```

  Expected: no migration changes remain unstaged after the final commit and no
  whitespace errors; pre-existing unrelated changes remain untouched.
