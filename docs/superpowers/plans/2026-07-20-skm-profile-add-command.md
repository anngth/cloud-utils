# SKM Profile Add Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the public `skm profile create <profile>` command with `skm profile add <profile>` and update all help and documentation.

**Architecture:** Keep the profile data model and `createProfile()` domain helper unchanged. Rename only the CLI grammar, dispatcher action, command handler, help copy, tests, and public docs; do not provide a legacy alias.

**Tech Stack:** Node.js ES modules, `node:test`, Markdown documentation.

## Global Constraints

- `skm profile create <profile>` must not remain as an alias.
- `createProfile()` remains the internal domain helper.
- `.agents/` and `skills-lock.json` are unrelated and must remain untouched.

---

### Task 1: Rename the public profile command

**Files:**
- Modify: `mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/manage-commands.mjs`
- Modify: `mac_scripts/functions/skills-manager/ui.mjs`
- Modify: `docs/commands.md`

**Interfaces:**
- Consumes: `runProfileCommand(args, context)` and `createProfile(document, name)`.
- Produces: public action `profile add`; no new exported interface.

- [ ] **Step 1: Write failing command and help tests**

Add management assertions equivalent to:

```js
test("profile add creates a profile and create is not an alias", async (t) => {
  const added = makeManagementHarness(t);
  assert.equal(await runProfileCommand(["add", "frontend"], added.context), 0);
  assert.deepEqual(added.writtenProfiles.profiles.map(({ name }) => name), ["default", "frontend"]);

  const legacy = makeManagementHarness(t);
  assert.equal(await runProfileCommand(["create", "frontend"], legacy.context), 1);
  assert.match(legacy.stderr(), /Unknown profile command: create/);
  assert.equal(legacy.writtenProfiles, undefined);
});
```

Change the help expectation to:

```js
"│  skm profile add <profile>  Create a profile"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: the new command test fails because `add` is unknown, the legacy
command is still accepted, and help still renders `profile create`.

- [ ] **Step 3: Implement the minimal CLI rename**

Update management grammar and dispatch to recognize only `add`, change usage
copy to `skm profile add <profile>`, and rename the local handler to
`runProfileAdd`. Update the help signature to:

```js
command("skm profile add <profile>", "Create a profile");
```

Replace public examples and the Profile supported-command table entry in
`docs/commands.md` with `profile add`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command.

Expected: all focused management and UI tests pass.

- [ ] **Step 5: Verify the complete change**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
rg -n --hidden --glob '!.git' --glob '!**/__tests__/**' 'profile create|create <profile>' README.md docs/commands.md mac_scripts
git diff --check
```

Expected: the full suite passes, the search returns no stale public command
references, and `git diff --check` prints no errors.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-skm-profile-add-command-design.md docs/superpowers/plans/2026-07-20-skm-profile-add-command.md docs/commands.md mac_scripts/functions/skills-manager/manage-commands.mjs mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git commit -m "feat(skm): rename profile create command"
```
