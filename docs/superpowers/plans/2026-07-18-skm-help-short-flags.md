# SKM Complete Help and Short Flags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `skm -h` show the exact syntax of every supported command and add a real short alias for every existing long flag.

**Architecture:** Keep command routing unchanged. Extend the two existing option parsers with route-scoped aliases, then replace only the `usage()` renderer with grouped, complete command signatures. Update the existing command reference so parser behavior, terminal help, tests, and documentation stay synchronized.

**Tech Stack:** Node.js 24+, ECMAScript modules, built-in `node:test` and `node:assert`, Zsh wrapper, Markdown documentation.

## Global Constraints

- Preserve all existing long flags and their semantics.
- Short and long forms must be interchangeable, including when mixed in one command.
- Preserve existing validation for missing values, repeated non-repeatable flags, mutually exclusive source-selection modes, and unknown flags.
- Keep the existing SKM color and renderer style; do not introduce terminal-width-dependent alignment.
- Do not add per-subcommand help, new commands, new long flags, or dependencies.
- Management commands continue to change configuration only; lifecycle commands remain the only commands that mutate installed project skills.
- Use test-driven development: add each behavior test and observe the expected failure before modifying production code.
- Do not stage or modify the existing unrelated worktree changes in `mac_init/alias`, `mac_scripts/functions/skills-manager/list.json.example`, `.agents/`, or `skills-lock.json`.

---

### Task 1: Management short aliases

**Files:**
- Modify: `mac_scripts/functions/skills-manager/manage-commands.mjs:34-45`
- Test: `mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs`

**Interfaces:**
- Consumes: `runProfileCommand(args, context)`, `runSourceCommand(args, context)`, and `runSkillCommand(args, context)` already exercised through `makeManagementHarness()`.
- Produces: management aliases `-s` → `--source`, `-k` → `--skill`, `-a` → `--all`, `-n` → `--no-skills`, and `-f` → `--force`; existing `-p` → `--profile` remains unchanged.

- [ ] **Step 1: Add failing behavior tests for management aliases**

Append focused tests that invoke real command handlers rather than exporting or testing the private parser:

```js
test("management short value flags match their long forms", async (t) => {
  const source = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "-k", "a", "-k", "b",
  ], source.context), 0);
  assert.deepEqual(source.writtenProfiles, profileWithSource(
    "default", "acme/skills", ["a", "b"],
  ));

  const skill = makeManagementHarness(t, {
    profiles: profileWithSource("default", "acme/skills", ["a"]),
  });
  assert.equal(await runSkillCommand([
    "remove", "a", "-s", "acme/skills", "-p", "default",
  ], skill.context), 0);
  assert.deepEqual(skill.writtenProfiles, profileWithSource("default", "acme/skills", []));
});

test("management short boolean flags match their long forms", async (t) => {
  const all = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "-a",
  ], all.context), 0);
  assert.deepEqual(all.writtenProfiles, profileWithSource(
    "default", "acme/skills", ["a", "b"],
  ));

  const none = makeManagementHarness(t);
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "-n",
  ], none.context), 0);
  assert.equal(none.upstreamCalls.length, 0);
  assert.deepEqual(none.writtenProfiles, profileWithSource("default", "acme/skills", []));

  const forced = makeManagementHarness(t, {
    profiles: profiles("default", "frontend"),
    projects: projects({ root: "/repo", profiles: ["frontend"] }),
  });
  assert.equal(await runProfileCommand(["remove", "frontend", "-f"], forced.context), 0);
  assert.deepEqual(forced.transactions[0], {
    profiles: profiles("default"),
    projects: projects(),
  });
});

test("mixed short and long source selection modes remain mutually exclusive", async (t) => {
  const harness = makeManagementHarness(t);
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "-k", "a", "--all",
  ], harness.context), 1);
  assert.equal(harness.upstreamCalls.length, 0);
  assert.equal(harness.writtenProfiles, undefined);
  assert.match(harness.stderr(), /mutually exclusive/i);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
npm_config_cache=/private/tmp/skm-npm-cache npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs
```

Expected: FAIL because `-k`, `-s`, `-a`, `-n`, and `-f` are reported as unknown options; the existing tests remain green.

- [ ] **Step 3: Add the minimal management alias mappings**

Update the two maps inside `parseOptions(args)`:

```js
  const valueFlags = new Map([
    ["-p", "profile"],
    ["--profile", "profile"],
    ["-s", "source"],
    ["--source", "source"],
    ["-k", "skills"],
    ["--skill", "skills"],
  ]);
  const booleanFlags = new Map([
    ["-a", "all"],
    ["--all", "all"],
    ["-n", "noSkills"],
    ["--no-skills", "noSkills"],
    ["-f", "force"],
    ["--force", "force"],
  ]);
```

Do not change `parseOptions()` control flow or any command-specific allow-list. Both aliases map to the same parsed property, so existing missing-value, repeated-option, and mutually-exclusive-mode checks continue to apply.

- [ ] **Step 4: Run management tests and verify GREEN**

Run:

```bash
npm_config_cache=/private/tmp/skm-npm-cache npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs
```

Expected: all management command tests PASS with no skips or warnings.

- [ ] **Step 5: Commit the management aliases**

```bash
git add mac_scripts/functions/skills-manager/manage-commands.mjs mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs
git commit -m "feat(skm): add management short flags"
```

---

### Task 2: Lifecycle short aliases

**Files:**
- Modify: `mac_scripts/functions/skills-manager/lifecycle-commands.mjs:18-27`
- Test: `mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs`

**Interfaces:**
- Consumes: `runInstallCommand(args, context)` and `runUninstallCommand(args, context)` exercised through `lifecycleHarness()` and `makeUninstallHarness()`.
- Produces: lifecycle aliases `-y` → `--yes`, `-f` → `--force`, `-d` → `--dry-run`, and uninstall-only `-l` → `--keep-link`. `status` continues to accept no flags.

- [ ] **Step 1: Add failing lifecycle alias tests**

Add tests beside the existing long-flag behavior tests:

```js
test("install short flags match yes dry-run and force", async () => {
  const dryRun = lifecycleHarness({ installed: new Map() });
  assert.equal(await runInstallCommand(["-d"], dryRun.context), 0);
  assert.equal(dryRun.executionCalls, 0);
  assert.equal(dryRun.confirmations, 0);
  assert.equal(dryRun.uiCalls[0][1].dryRun, true);

  const yes = lifecycleHarness();
  assert.equal(await runInstallCommand(["-y"], yes.context), 0);
  assert.equal(yes.skillSelections, 0);
  assert.equal(yes.confirmations, 0);
  assert.equal(yes.executionCalls, 1);

  const force = lifecycleHarness({
    installed: new Map([
      ["frontend-design", actualSkill("frontend-design", null)],
    ]),
  });
  assert.equal(await runInstallCommand(["-y", "-f"], force.context), 0);
  assert.deepEqual(force.capturedPlan.replace.map((item) => item.skill), [
    "frontend-design",
  ]);
});

test("uninstall short keep-link preserves project links", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["frontend"] });
  assert.equal(await runUninstallCommand([
    "frontend", "-y", "-l",
  ], harness.context), 0);
  assert.deepEqual(harness.removedNames, ["code-review", "frontend-design"]);
  assert.equal(harness.projectWrites, 0);
  assert.equal(harness.uiCalls[0][1].keepLink, true);
});

test("unsupported lifecycle short flags still fail before discovery", async () => {
  const harness = lifecycleHarness();
  assert.equal(await runInstallCommand(["-z"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /unknown option: -z/i);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
npm_config_cache=/private/tmp/skm-npm-cache npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
```

Expected: the new `-d`, `-y`, `-f`, and `-l` assertions FAIL with unknown-option errors; the `-z` rejection and existing tests pass.

- [ ] **Step 3: Add the minimal lifecycle mappings**

Extend only the route-specific flag map in `parseLifecycleOptions()`:

```js
  const flags = new Map();
  if (install || uninstall) {
    flags.set("-y", "yes");
    flags.set("--yes", "yes");
    flags.set("-f", "force");
    flags.set("--force", "force");
    flags.set("-d", "dryRun");
    flags.set("--dry-run", "dryRun");
  }
  if (uninstall) {
    flags.set("-l", "keepLink");
    flags.set("--keep-link", "keepLink");
  }
```

Do not add flags to `status` and do not alter lifecycle planning or mutation code.

- [ ] **Step 4: Run lifecycle tests and verify GREEN**

Run:

```bash
npm_config_cache=/private/tmp/skm-npm-cache npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
```

Expected: all lifecycle command tests PASS with no skips or warnings.

- [ ] **Step 5: Commit the lifecycle aliases**

```bash
git add mac_scripts/functions/skills-manager/lifecycle-commands.mjs mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
git commit -m "feat(skm): add lifecycle short flags"
```

---

### Task 3: Complete grouped help and synchronized documentation

**Files:**
- Modify: `mac_scripts/functions/skills-manager/ui.mjs:40-55`
- Test: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs:355-363`
- Modify: `docs/commands.md:66-105`

**Interfaces:**
- Consumes: `createUi({ stdout, stderr }).usage()` and the aliases delivered by Tasks 1 and 2.
- Produces: one complete top-level help screen containing the dashboard entry point, all help aliases, all 19 business subcommands, every exact argument form, every short/long flag pair, concise descriptions, and three behavior notes.

- [ ] **Step 1: Replace the broad usage assertion with a failing exact-surface test**

Replace `usage documents management families and the non-installing skill boundary` with:

```js
test("usage documents every command signature and short flag", () => {
  const { stdout, ui } = makeUi();
  ui.usage();
  const lines = stdout.read()
    .replace(/\u001b\[[0-9;]*m/g, "")
    .split(/\r?\n/)
    .map((line) => line.trimEnd());

  assert.equal(lines.find((line) => line.includes("Usage:")), "◇  Usage: skm [command]");

  const sections = ["Lifecycle", "Profiles", "Sources", "Skills", "Projects", "Notes"];
  const sectionIndexes = sections.map((section) => lines.indexOf(`◆  ${section}`));
  assert.ok(sectionIndexes.every((index) => index >= 0), "all help sections are present");
  assert.deepEqual(sectionIndexes, [...sectionIndexes].sort((left, right) => left - right));

  for (const [first, continuation] of [
    [
      "│  skm uninstall [profile...] [(-y | --yes)] [(-f | --force)]",
      "│      [(-d | --dry-run)] [(-l | --keep-link)]  Uninstall selected profile skills",
    ],
    [
      "│  skm source add <source> [(-p | --profile) <profile>]",
      "│      [[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)]  Add a source and select skills",
    ],
    [
      "│  skm skill add <skill...> (-s | --source) <source>",
      "│      (-p | --profile) <profile>  Add skills to a profile source",
    ],
    [
      "│  skm skill remove <skill...> (-s | --source) <source>",
      "│      (-p | --profile) <profile>  Remove skills from a profile source",
    ],
  ]) {
    const index = lines.indexOf(first);
    assert.ok(index >= 0, `missing wrapped signature: ${first}`);
    assert.equal(lines[index + 1], continuation);
  }

  for (const line of [
    "│  skm source edit <source> [(-p | --profile) <profile>]  Edit selected source skills",
    "│  skm source remove <source> [(-p | --profile) <profile>]  Remove a source from a profile",
  ]) assert.ok(lines.includes(line), `missing source signature: ${line}`);

  for (const line of [
    "│  skm  Open interactive dashboard",
    "│  skm (help | -h | --help)  Show this help",
    "│  skm status [profile...]  Compare desired and installed skills",
    "│  skm install [profile...] [(-y | --yes)] [(-f | --force)] [(-d | --dry-run)]  Install selected profile skills",
    "│  skm profile list  List profiles",
    "│  skm profile show <profile>  Show one profile",
    "│  skm profile create <profile>  Create a profile",
    "│  skm profile rename <old> <new>  Rename a profile",
    "│  skm profile remove <profile> [(-f | --force)]  Remove a profile",
    "│  skm source show <source>  Show available source skills",
    "│  skm project link <profile...>  Link profiles to the current project",
    "│  skm project unlink [profile...]  Unlink profiles from the current project",
    "│  skm project show  Show the current project",
    "│  skm project list  List registered projects",
    "│  skm project remove <project-path>  Remove a project registration",
    "│  Profile names omitted from lifecycle commands use current project links.",
    "│  Profile, source, skill, and project commands change configuration only.",
    "│  --force permits linked-profile removal or mismatch/untracked skill changes.",
  ]) assert.ok(lines.includes(line), `missing help line: ${line}`);
});
```

- [ ] **Step 2: Run the UI test and verify RED**

Run:

```bash
npm_config_cache=/private/tmp/skm-npm-cache npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: FAIL at the first incomplete wrapped signature or source command whose
profile option is rendered as required.

- [ ] **Step 3: Implement the complete usage renderer**

Replace only `usage()` in `ui.mjs`. Use renderer helpers instead of calculating terminal columns:

```js
  function usage() {
    const section = (name) => {
      out(pipe);
      active(name);
    };
    const command = (syntax, description) => {
      out(`${pipe}  ${fg(C.green, syntax)}${description ? `  ${fg(C.gray, description)}` : ""}`);
    };
    const continuation = (syntax, description) => {
      out(`${pipe}      ${fg(C.green, syntax)}${description ? `  ${fg(C.gray, description)}` : ""}`);
    };
    const note = (text) => out(`${pipe}  ${fg(C.gray, text)}`);

    title();
    step("Usage: skm [command]");
    command("skm", "Open interactive dashboard");
    command("skm (help | -h | --help)", "Show this help");

    section("Lifecycle");
    command("skm status [profile...]", "Compare desired and installed skills");
    command("skm install [profile...] [(-y | --yes)] [(-f | --force)] [(-d | --dry-run)]", "Install selected profile skills");
    command("skm uninstall [profile...] [(-y | --yes)] [(-f | --force)]");
    continuation("[(-d | --dry-run)] [(-l | --keep-link)]", "Uninstall selected profile skills");

    section("Profiles");
    command("skm profile list", "List profiles");
    command("skm profile show <profile>", "Show one profile");
    command("skm profile create <profile>", "Create a profile");
    command("skm profile rename <old> <new>", "Rename a profile");
    command("skm profile remove <profile> [(-f | --force)]", "Remove a profile");

    section("Sources");
    command("skm source add <source> [(-p | --profile) <profile>]");
    continuation("[[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)]", "Add a source and select skills");
    command("skm source edit <source> [(-p | --profile) <profile>]", "Edit selected source skills");
    command("skm source remove <source> [(-p | --profile) <profile>]", "Remove a source from a profile");
    command("skm source show <source>", "Show available source skills");

    section("Skills");
    command("skm skill add <skill...> (-s | --source) <source>");
    continuation("(-p | --profile) <profile>", "Add skills to a profile source");
    command("skm skill remove <skill...> (-s | --source) <source>");
    continuation("(-p | --profile) <profile>", "Remove skills from a profile source");

    section("Projects");
    command("skm project link <profile...>", "Link profiles to the current project");
    command("skm project unlink [profile...]", "Unlink profiles from the current project");
    command("skm project show", "Show the current project");
    command("skm project list", "List registered projects");
    command("skm project remove <project-path>", "Remove a project registration");

    section("Notes");
    note("Profile names omitted from lifecycle commands use current project links.");
    note("Profile, source, skill, and project commands change configuration only.");
    note("--force permits linked-profile removal or mismatch/untracked skill changes.");
    listEnd();
  }
```

- [ ] **Step 4: Run the UI test and verify GREEN**

Run:

```bash
npm_config_cache=/private/tmp/skm-npm-cache npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: all UI tests PASS and the exact-surface test confirms every signature.

- [ ] **Step 5: Synchronize the command reference**

Update examples that benefit from compact flags and replace the flag table with the complete alias pairs:

```markdown
| Flag | Meaning |
| --- | --- |
| `-h`, `--help` | Show top-level SKM help |
| `-p`, `--profile` | Select the profile changed by a source or skill command |
| `-s`, `--source` | Select the saved profile source changed by `skill add` or `skill remove` |
| `-k`, `--skill` | Select an explicit skill for `source add`; repeat for multiple skills |
| `-a`, `--all` | Snapshot every currently discovered skill into one profile source |
| `-n`, `--no-skills` | Save a source with an empty skill selection |
| `-y`, `--yes` | Skip SKM and upstream confirmation prompts |
| `-f`, `--force` | Permit linked-profile removal or lifecycle mismatch/untracked replacement/removal |
| `-d`, `--dry-run` | Render a lifecycle plan without mutation |
| `-l`, `--keep-link` | Uninstall files without unlinking the selected profile |
```

Keep the existing lifecycle safety explanation and the statement that there is no `--all-profiles` mode.

- [ ] **Step 6: Verify top-level help routing and rendered output**

Run:

```bash
npm_config_cache=/private/tmp/skm-npm-cache npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/cli.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs mac_scripts/functions/skills-manager/__tests__/wrapper.test.mjs
```

Expected: all focused tests PASS with no skips. The CLI help aliases still bypass configuration and `npx` checks.

Run:

```bash
mac_scripts/skm -h
```

Expected: the terminal displays the grouped command surface from the design, with all 19 business subcommands and short aliases visible.

- [ ] **Step 7: Run the full regression suite and static checks**

Run:

```bash
npm_config_cache=/private/tmp/skm-npm-cache npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
```

Expected: every SKM test passes with zero failures and zero skips.

Run:

```bash
zsh -n mac_scripts/skm
```

Expected: exits 0 with no output.

Run:

```bash
git diff --check -- mac_scripts/functions/skills-manager/manage-commands.mjs mac_scripts/functions/skills-manager/lifecycle-commands.mjs mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs docs/commands.md
```

Expected: exits 0 with no output; unrelated user-owned worktree changes are not
part of this check.

- [ ] **Step 8: Commit help and documentation**

```bash
git add mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs docs/commands.md
git commit -m "feat(skm): document complete command help"
```
