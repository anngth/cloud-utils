# SKM Project Remove Current Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `skm project remove` remove the resolved current project when no project path is provided, while preserving explicit-path removal.

**Architecture:** Keep project-root resolution inside the existing management command layer. Relax both the pre-dispatch grammar validator and command handler from exactly one positional to at most one positional, then route the omitted case through the existing `currentProjectRoot(context)` helper.

**Tech Stack:** Node.js 24, ES modules, `node:test`, `node:assert/strict`

## Global Constraints

- The public syntax is `skm project remove [project-path]`.
- An omitted path uses the same project-root resolution as `link`, `unlink`, and `show`.
- An explicit relative or absolute path preserves the existing behavior.
- More than one project path remains a usage error.
- Removing a registration never modifies project files or installed skills.

---

### Task 1: Optional project path for remove

**Files:**
- Modify: `mac_scripts/functions/skills-manager/manage-commands.mjs`
- Modify: `mac_scripts/functions/skills-manager/ui.mjs`
- Test: `mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs`
- Test: `mac_scripts/functions/skills-manager/__tests__/cli.test.mjs`
- Test: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs`

**Interfaces:**
- Consumes: `currentProjectRoot(context): string` and `runProjectCommand(args, context): Promise<number>`
- Produces: `skm project remove [project-path]`, where zero or one positional path is valid

- [ ] **Step 1: Write failing grammar and behavior tests**

Add a CLI grammar test that dispatches `project remove` with no path:

```js
test("project remove without a path passes grammar validation", async () => {
  const harness = cliHarness();
  assert.equal(await runCli(["project", "remove"], harness.dependencies), 0);
  assert.deepEqual(harness.calls.map(([name]) => name), ["project"]);
  assert.deepEqual(harness.calls[0][1], ["remove"]);
});
```

Add a command test that distinguishes `cwd` from the resolved project root:

```js
test("project remove without a path removes the resolved current project", async (t) => {
  const harness = makeManagementHarness(t, {
    projects: projects({ root: "/repo", profiles: ["default"] }),
  });
  harness.context.cwd = "/repo/packages/app";
  harness.context.resolveProjectRoot = () => "/repo";

  assert.equal(await runProjectCommand(["remove"], harness.context), 0);
  assert.deepEqual(harness.writtenProjects, projects());
  assert.deepEqual(harness.uiCalls.at(-1), [
    "projectChanged",
    { action: "removed", root: "/repo", profiles: [] },
  ]);
});
```

Change the expected help line to:

```js
"│  skm project remove [project-path]  Remove a project registration",
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
node --test \
  mac_scripts/functions/skills-manager/__tests__/cli.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: FAIL because zero positional paths are rejected and help still renders `<project-path>`.

- [ ] **Step 3: Implement the minimal behavior**

Add an at-most-one positional validator:

```js
function requireAtMostOne(parsed, usage) {
  if (parsed.positionals.length > 1) throw new CommandUsageError(`Usage: ${usage}`);
}
```

Use it in both project-remove validation paths with `skm project remove [project-path]`. Resolve the target in `runProjectRemove` as follows:

```js
const rawRoot = parsed.positionals[0];
const root = rawRoot === undefined
  ? currentProjectRoot(context)
  : isAbsolute(rawRoot) ? resolve(rawRoot) : resolve(context.cwd, rawRoot);
```

Update the help signature in `ui.mjs` to `skm project remove [project-path]`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused `node --test` command from Step 2.

Expected: all focused tests pass with exit status 0.

- [ ] **Step 5: Run full verification and review**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
git diff --check
git diff -- mac_scripts/functions/skills-manager docs/superpowers
```

Expected: all tests pass, `git diff --check` exits 0, and the diff contains only the optional-path behavior, help, tests, spec, and this plan.

- [ ] **Step 6: Commit**

```bash
git add \
  mac_scripts/functions/skills-manager/manage-commands.mjs \
  mac_scripts/functions/skills-manager/ui.mjs \
  mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/cli.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/ui.test.mjs \
  docs/superpowers/plans/2026-07-20-skm-project-remove-current-root.md
git commit -m "fix(skm): remove current project by default"
```
