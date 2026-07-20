# SKM Skill List Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Highlight every semantically listed skill name in bright green and place one framed blank line between adjacent skill entries without changing SKM behavior or compact non-skill lists.

**Architecture:** Keep formatting inside the UI boundary by adding semantic `skillItem` and `skillList` helpers to `ui.mjs`. Static renderers pass structured skill names and suffixes to those helpers, while interactive selector producers add `kind: "skill"` so the generic selector can apply the same treatment without guessing from text. Execution summaries flatten batched operation records into skill rows but deduplicate and preserve plain retry commands.

**Tech Stack:** Node.js 24, ECMAScript modules, `node:test`, ANSI terminal output, existing SKM command and selector abstractions.

## Global Constraints

- Render every scoped skill name in bright green (`\u001b[92m`) and reset foreground color immediately after the name.
- Insert exactly one framed blank line (`│`) between adjacent skill entries; do not create a separator solely after the final entry.
- Existing enclosing section spacers may remain after the final skill.
- Preserve semantic marker/suffix colors, source redaction, headings, summaries, empty states, public UI methods, command grammar, exit codes, configuration schemas, and lifecycle behavior.
- Keep lists whose items are profiles, sources, projects, or dashboard actions outside semantic skill formatting, along with help, warning/error prose, retry commands, and machine-readable output.
- Retry commands stay plain and appear once per unique failed batch.
- Add no dependencies and no new command flags.
- Follow strict RED → GREEN TDD for every implementation task and commit each completed task separately.

## File Structure

- Modify `mac_scripts/functions/skills-manager/ui.mjs`: own semantic skill rows, list spacing, selector presentation, lifecycle presentation, and execution-summary presentation.
- Modify `mac_scripts/functions/skills-manager/manage-commands.mjs`: mark source add/edit selector records as skill items; do not change selection values.
- Modify `mac_scripts/functions/skills-manager/lifecycle-commands.mjs`: mark install selector records as skill items; leave profile selector records unmarked.
- Modify `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs`: assert exact ANSI boundaries, framed spacing, non-skill compactness, and summary retry behavior.
- Modify `mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs`: assert source add/edit selectors expose semantic skill metadata.
- Modify `mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs`: assert install selectors expose semantic skill metadata while profile behavior stays unchanged.

---

### Task 1: Semantic Static Skill Rows for Management Output

**Files:**
- Modify: `mac_scripts/functions/skills-manager/ui.mjs:23-194`
- Test: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs:5-100`

**Interfaces:**
- Consumes: existing `out(line)`, `pipe`, `fg(color, text)`, `C.brightGreen`, and renderer input objects.
- Produces: closure-local `skillItem({ name, suffix, marker, markerColor, suffixColor, indent }) -> void` and `skillList(values, renderItem) -> void`; later tasks reuse these exact helpers.

- [ ] **Step 1: Add ANSI and plain-output test helpers**

Add these helpers below `makeUi()` in `ui.test.mjs`:

```js
const paint = (code, text) => `\u001b[${code}m${text}\u001b[39m`;
const stripAnsi = (text) => text.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, "");
const occurrences = (text, fragment) => text.split(fragment).length - 1;
```

- [ ] **Step 2: Write failing management-renderer tests**

Add focused tests that exercise names with and without suffixes:

```js
test("available skill rows highlight only names and separate adjacent records", () => {
  const { stdout, ui } = makeUi();
  ui.sourceChanged({
    action: "shown",
    profile: null,
    source: "obra/superpowers",
    skills: ["brainstorming", "test-driven-development"],
    available: [
      { name: "brainstorming", description: "Explore requirements" },
      { name: "test-driven-development", description: "Test first" },
    ],
  });

  const rendered = stdout.read();
  assert.ok(rendered.includes(`${paint("92", "brainstorming")} ${paint("90", "— Explore requirements")}`));
  assert.equal(occurrences(rendered, "\u001b[92mbrainstorming\u001b[39m"), 1);
  assert.match(
    stripAnsi(rendered),
    /│  ■ brainstorming — Explore requirements\n│\n│  ■ test-driven-development — Test first\n└/,
  );
});

test("profile and skill-change lists highlight and separate name-only skills", () => {
  const profile = makeUi();
  profile.ui.profileShow({
    profile: {
      name: "quality",
      sources: [{ source: "obra/superpowers", skills: ["brainstorming", "testing"] }],
    },
    projects: [],
  });
  assert.match(
    stripAnsi(profile.stdout.read()),
    /│      • brainstorming\n│\n│      • testing/,
  );
  assert.ok(profile.stdout.read().includes(paint("92", "brainstorming")));

  const changed = makeUi();
  changed.ui.skillChanged({
    action: "added",
    profile: "quality",
    source: "obra/superpowers",
    skills: ["brainstorming", "testing"],
    missing: [],
  });
  assert.match(stripAnsi(changed.stdout.read()), /■ brainstorming\n│\n│  ■ testing/);
  assert.ok(changed.stdout.read().includes(paint("92", "testing")));
});
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
node --test --test-name-pattern='available skill rows|profile and skill-change lists' mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: FAIL because names are not wrapped in `\u001b[92m...\u001b[39m` and adjacent records have no framed blank line.

- [ ] **Step 4: Add the semantic renderer primitives**

Add these closure-local helpers immediately after `item` in `createUi()`:

```js
  const skillItem = ({
    name,
    suffix = "",
    marker = "■",
    markerColor = C.green,
    suffixColor = C.gray,
    indent = "  ",
  }) => out(
    `${pipe}${indent}${fg(markerColor, marker)} ${fg(C.brightGreen, name)}`
      + `${suffix ? ` ${fg(suffixColor, suffix)}` : ""}`,
  );
  const skillList = (values, renderItem) => {
    values.forEach((value, index) => {
      if (index > 0) out(pipe);
      renderItem(value);
    });
  };
```

Do not export either helper; `createUi()` remains the public boundary.

- [ ] **Step 5: Convert management skill lists to structured rows**

Replace the plain skill loops in `profileShow`, `sourceChanged`, and `skillChanged` with:

```js
      skillList(source.skills, (name) => skillItem({
        name,
        marker: "•",
        markerColor: C.gray,
        indent: "      ",
      }));
```

```js
      skillList(change.available, (record) => skillItem({
        name: record.name,
        suffix: record.description ? `— ${record.description}` : "",
      }));
```

```js
    skillList(change.skills ?? [], (name) => skillItem({ name }));
```

Leave the yellow `Not selected: ...` warning as prose and leave profile/source/project lists on `item()`.

- [ ] **Step 6: Run focused and complete UI tests and verify GREEN**

Run:

```bash
node --test --test-name-pattern='available skill rows|profile and skill-change lists' mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
node --test mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: both commands PASS; the second reports every UI test passing.

- [ ] **Step 7: Review Task 1 against its boundary**

Inspect:

```bash
git diff -- mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Confirm that only `profileShow`, `sourceChanged`, and `skillChanged` use the new helpers so far; `profileList`, `projectShow`, `projectList`, help, warnings, and errors must remain compact/plain.

- [ ] **Step 8: Commit Task 1**

```bash
git add mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git commit -m "feat(skm): highlight management skill lists"
```

---

### Task 2: Semantic Lifecycle Status and Plan Rows

**Files:**
- Modify: `mac_scripts/functions/skills-manager/ui.mjs:230-311`
- Test: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs:155-273`

**Interfaces:**
- Consumes: Task 1 `skillItem(...)` and `skillList(values, renderItem)` helpers.
- Produces: lifecycle status/install/uninstall renderers where every actual skill record has a bright-green name and status-colored marker/suffix.

- [ ] **Step 1: Write failing lifecycle formatting tests**

Extend the existing status test with two records in one section, then add exact styling/spacing assertions:

```js
const missingA = requirement("a/repo", "missing-a", ["quality"]);
const missingB = requirement("b/repo", "missing-b", ["frontend"]);
```

Use `[missingA, missingB]` for `status.missing`, then assert:

```js
assert.ok(rendered.includes(`\u001b[33m■\u001b[39m ${paint("92", "missing-a")}`));
assert.ok(rendered.includes(`${paint("92", "missing-a")} ${paint("33", "— a/repo — required by quality")}`));
assert.match(
  stripAnsi(rendered),
  /│  ■ missing-a — a\/repo — required by quality\n│\n│  ■ missing-b — b\/repo — required by frontend/,
);
for (const name of ["ready", "missing-a", "wrong-source", "unknown", "other", "ambiguous"]) {
  assert.ok(rendered.includes(paint("92", name)), `${name} is a highlighted skill`);
}
```

Change the install fixture's `install` array and the uninstall fixture's `remove` array to:

```js
install: [
  requirement("a/repo", "missing"),
  requirement("b/repo", "also-missing", ["quality"]),
],
```

```js
remove: [
  requirement("a/repo", "remove-me"),
  requirement("b/repo", "also-remove", ["quality"]),
],
```

Then assert the framed blank-line patterns and bright-green wrapping. Also assert the unlink profile name is not bright green:

```js
assert.ok(rendered.includes(paint("92", "remove-me")));
assert.ok(rendered.includes(paint("92", "shared")));
assert.ok(!rendered.includes(paint("92", "frontend")));
assert.match(stripAnsi(rendered), /■ remove-me[^\n]*\n│\n│  ■ also-remove/);
```

- [ ] **Step 2: Run lifecycle UI tests and verify RED**

Run:

```bash
node --test --test-name-pattern='status renders|install plan labels|uninstall plan renders' mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: FAIL because lifecycle rows still pass complete plain strings to `item()`.

- [ ] **Step 3: Replace `requirementText` with a structured suffix helper**

Use this exact helper:

```js
  const requirementSuffix = ({ source, profiles = [] }) => (
    `— ${redactSource(source)}`
      + `${profiles.length > 0 ? ` — required by ${profiles.join(", ")}` : ""}`
  );
```

Update `requirementSection` to retain empty states and section spacers:

```js
  function requirementSection(label, values, color = C.green) {
    active(label);
    if (values.length === 0) item("None", C.gray);
    skillList(values, (value) => skillItem({
      name: value.skill,
      suffix: requirementSuffix(value),
      markerColor: color,
      suffixColor: color,
    }));
    out(pipe);
  }
```

- [ ] **Step 4: Convert lifecycle extras and desired-source conflicts**

In `status`, render extras and desired conflicts as:

```js
    skillList(result.extras, (extra) => skillItem({
      name: extra.name,
      suffix: extra.source ? `— ${redactSource(extra.source)}` : "",
      markerColor: C.yellow,
      suffixColor: C.yellow,
    }));
```

```js
    skillList(result.desiredConflicts, (conflict) => skillItem({
      name: conflict.skill,
      suffix: `— ${conflict.sources.map(redactSource).join(" vs ")}`
        + ` — required by ${conflict.profiles.join(", ")}`,
      markerColor: C.red,
      suffixColor: C.red,
    }));
```

In `installPlan`, replace the extra and desired-conflict loops with:

```js
    skillList(plan.extras, (extra) => skillItem({
      name: extra.name,
      suffix: extra.source ? `— ${redactSource(extra.source)}` : "",
      markerColor: C.gray,
      suffixColor: C.gray,
    }));
```

```js
    skillList(plan.desiredConflicts, (conflict) => skillItem({
      name: conflict.skill,
      suffix: `— ${conflict.sources.map(redactSource).join(" vs ")}`,
      markerColor: C.red,
      suffixColor: C.red,
    }));
```

`uninstallPlan` receives requirement formatting through `requirementSection`; keep `unlinkProfiles` on ordinary `item()` because those values are profiles, not skills.

- [ ] **Step 5: Run lifecycle and complete UI tests and verify GREEN**

Run:

```bash
node --test --test-name-pattern='status renders|install plan labels|uninstall plan renders' mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
node --test mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: both commands PASS; markers keep yellow/red ANSI codes, names use bright green, and suffixes use their section color.

- [ ] **Step 6: Review Task 2 source-redaction and non-skill boundaries**

Run:

```bash
node --test --test-name-pattern='redact|unsafe|project renderers|profile list' mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git diff --check
```

Expected: tests PASS and `git diff --check` prints no output. Confirm every displayed source still passes through `redactSource()`.

- [ ] **Step 7: Commit Task 2**

```bash
git add mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git commit -m "feat(skm): highlight lifecycle skill rows"
```

---

### Task 3: Semantic Interactive Skill Selectors

**Files:**
- Modify: `mac_scripts/functions/skills-manager/ui.mjs:382-402`
- Modify: `mac_scripts/functions/skills-manager/manage-commands.mjs:288-358`
- Modify: `mac_scripts/functions/skills-manager/lifecycle-commands.mjs:62-124`
- Test: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs:436-449`
- Test: `mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs:183-275`
- Test: `mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs:418-437`

**Interfaces:**
- Consumes: existing selector records `{ value, label, hint? }` and Task 1 bright-green/pipe primitives.
- Produces: presentation-only selector metadata `{ kind: "skill", value, label, hint? }`; selector values, keys, selection state, and returned selections remain unchanged.

- [ ] **Step 1: Write failing selector-renderer tests**

Add a skill-specific selector test:

```js
test("skill selectors highlight names and separate rows while profile selectors stay compact", () => {
  const skills = makeUi();
  skills.ui.selector("Choose skills", {
    items: [
      { kind: "skill", value: "brainstorming", label: "brainstorming", hint: "Explore" },
      { kind: "skill", value: "testing", label: "testing", hint: "Verify" },
    ],
    cursor: 1,
    selected: new Set([0]),
  }, { mode: "install" });
  const skillOutput = skills.stdout.read();
  assert.ok(skillOutput.includes(paint("92", "brainstorming")));
  assert.ok(skillOutput.includes(paint("92", "testing")));
  assert.match(stripAnsi(skillOutput), /■ brainstorming Explore\n│\n│  □ testing Verify/);

  const profiles = makeUi();
  profiles.ui.selector("Choose profiles", {
    items: [
      { value: "frontend", label: "Frontend" },
      { value: "review", label: "Review" },
    ],
    cursor: 1,
    selected: new Set([0]),
  }, { mode: "install" });
  const profileOutput = profiles.stdout.read();
  assert.doesNotMatch(stripAnsi(profileOutput), /Frontend\n│\n│  □ Review/);
  assert.ok(!profileOutput.includes(paint("92", "Frontend")));
});
```

- [ ] **Step 2: Write failing producer-metadata tests**

In `manage-commands.test.mjs`, extend source add and source edit tests:

```js
assert.ok(harness.selectionCalls[0][0].items.every((item) => item.kind === "skill"));
assert.deepEqual(
  harness.selectionCalls[0][0].items.map(({ value, label, hint }) => ({ value, label, hint })),
  [
    { value: "a", label: "a", hint: "A" },
    { value: "b", label: "b", hint: "B" },
  ],
);
```

For source edit, use the existing `old` and `current` fixture and assert both the unavailable saved record and discovered record:

```js
const editItems = harness.selectionCalls[0][0].items;
assert.deepEqual(editItems.map(({ kind, value, hint }) => ({ kind, value, hint })), [
  { kind: "skill", value: "old", hint: "saved; unavailable upstream" },
  { kind: "skill", value: "current", hint: "Current" },
]);
```

In `lifecycle-commands.test.mjs`, extend `install selector hints redact unsafe persisted source text`:

```js
assert.equal(items[0].kind, "skill");
assert.equal(items[0].value, items[0].key);
assert.equal(items[0].label, "review");
```

- [ ] **Step 3: Run selector and producer tests and verify RED**

Run:

```bash
node --test --test-name-pattern='skill selectors|source add snapshots|source edit retains|install selector hints' \
  mac_scripts/functions/skills-manager/__tests__/ui.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
```

Expected: FAIL because producers omit `kind` and selectors use cursor-dependent white/gray labels with no framed spacing.

- [ ] **Step 4: Mark only skill selector producers**

Change the source-add mapping to include `kind: "skill"`:

```js
available.map(({ name, description }) => ({
  kind: "skill", value: name, label: name, hint: description,
}))
```

```js
.map((name) => ({
  kind: "skill", value: name, label: name, hint: "saved; unavailable upstream",
}))
```

Replace the complete source-edit `items` construction with:

```js
  const items = [
    ...entry.skills
      .filter((name) => !found.has(name))
      .map((name) => ({
        kind: "skill",
        value: name,
        label: name,
        hint: "saved; unavailable upstream",
      })),
    ...available.map(({ name, description }) => ({
      kind: "skill",
      value: name,
      label: name,
      hint: description,
    })),
  ];
```

Change `selectableInstallItems()` records to:

```js
  return [...plan.install, ...plan.replace].map((item) => ({
    kind: "skill",
    key: item.key,
    value: item.key,
    label: item.skill,
    hint: `${redactSource(item.source)} — ${item.profiles.join(", ")}`,
  }));
```

Do not add `kind` to `profileSelectorItems()`, project unlink items, dashboard items, confirmation items, or profile-management items.

- [ ] **Step 5: Make selector rendering honor semantic metadata**

Inside `renderSelector`, before rendering a skill entry, insert a separator only when both current and prior entries are skills:

```js
      const skillEntry = typeof entry !== "string" && entry.kind === "skill";
      const previous = values[index - 1];
      const previousSkill = typeof previous !== "string" && previous?.kind === "skill";
      if (index > 0 && skillEntry && previousSkill) out(pipe);
```

Keep box/cursor logic unchanged and select label color semantically:

```js
      const labelColor = skillEntry
        ? C.brightGreen
        : index === state.cursor ? C.white : C.gray;
```

Continue rendering `hint` with `C.gray` and resetting the label before the hint.

- [ ] **Step 6: Run focused and related selector tests and verify GREEN**

Run:

```bash
node --test --test-name-pattern='skill selectors|selector keeps ANSI|source add snapshots|source edit retains|install selector hints' \
  mac_scripts/functions/skills-manager/__tests__/ui.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
node --test mac_scripts/functions/skills-manager/__tests__/selector.test.mjs
```

Expected: all tests PASS; selector state and keyboard behavior are unchanged.

- [ ] **Step 7: Review Task 3 metadata coverage**

Run:

```bash
rg -n 'kind: "skill"|selectSkills|Select skills|Edit skills' \
  mac_scripts/functions/skills-manager/manage-commands.mjs \
  mac_scripts/functions/skills-manager/lifecycle-commands.mjs \
  mac_scripts/functions/skills-manager/ui.mjs
```

Expected: `kind: "skill"` appears only in source add, source edit, and lifecycle install selector producers; no profile/project/dashboard producer is marked.

- [ ] **Step 8: Commit Task 3**

```bash
git add \
  mac_scripts/functions/skills-manager/ui.mjs \
  mac_scripts/functions/skills-manager/manage-commands.mjs \
  mac_scripts/functions/skills-manager/lifecycle-commands.mjs \
  mac_scripts/functions/skills-manager/__tests__/ui.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
git commit -m "feat(skm): clarify interactive skill selection"
```

---

### Task 4: Readable Execution Summaries with Deduplicated Retry Commands

**Files:**
- Modify: `mac_scripts/functions/skills-manager/ui.mjs:313-368`
- Test: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs:275-336`

**Interfaces:**
- Consumes: operation records `{ action, source, skills, status }`, replacement records `{ source, skill, removeStatus, installStatus }`, Task 1 skill helpers, and existing `retryCommand(record)`.
- Produces: individual semantic skill rows for succeeded/failed/replacement-warning results plus one plain `Retry commands` entry per unique command.

- [ ] **Step 1: Strengthen execution-summary tests for batched skills**

Extend the aggregate test to require individual highlighted rows and spacing:

```js
for (const name of ["three", "one", "two", "blocked"]) {
  assert.ok(rendered.includes(paint("92", name)), `${name} is highlighted`);
}
assert.match(stripAnsi(rendered), /■ one — install failed \(status 2\)\n│\n│  ■ two — install failed \(status 2\)/);
assert.equal(occurrences(rendered, "npx skills add a/repo --skill one --skill two"), 1);
assert.equal(occurrences(rendered, "npx skills remove blocked --yes"), 1);
```

The existing incomplete-replacement fixture already exposes the same retry through both `replacements` and `failed`. Add these assertions for global deduplication:

```js
assert.ok(rendered.includes(paint("92", "review")));
assert.match(stripAnsi(rendered), /review — old version removed; replacement from a\/repo failed \(status 7\)/i);
assert.equal(occurrences(rendered, "npx skills add a/repo --skill review"), 1);
```

Keep the existing safe-source redaction and uninstall retry assertions.

- [ ] **Step 2: Run execution-summary tests and verify RED**

Run:

```bash
node --test --test-name-pattern='execution summary|execution retry|uninstall execution' mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: FAIL because current summaries render operation batches as complete plain strings and may repeat retry guidance.

- [ ] **Step 3: Add record-flattening and retry-deduplication helpers**

Add closure-local helpers next to `retryCommand()`:

```js
  const operationSkillRows = (records) => records.flatMap((record) => (
    record.skills.map((name) => ({ name, record }))
  ));

  const uniqueRetryCommands = (records) => [
    ...new Set(records.map((record) => retryCommand(record))),
  ];
```

These helpers affect presentation only; do not mutate result records or recompute success/failure counts.

- [ ] **Step 4: Render replacement warnings and operation outcomes as skill rows**

Use individual rows:

```js
    if (incompleteReplacements.length > 0) {
      active("Replacement warning");
      skillList(incompleteReplacements, (record) => skillItem({
        name: record.skill,
        suffix: `— old version removed; replacement from ${redactSource(record.source)}`
          + ` failed (status ${record.installStatus})`,
        markerColor: C.red,
        suffixColor: C.red,
      }));
      out(pipe);
    }
```

```js
    if (result.succeeded.length > 0) {
      active("Succeeded");
      skillList(operationSkillRows(result.succeeded), ({ name, record }) => skillItem({
        name,
        suffix: `— ${record.action}`,
      }));
      out(pipe);
    }
```

```js
    if (result.failed.length > 0) {
      active("Failed");
      skillList(operationSkillRows(result.failed), ({ name, record }) => skillItem({
        name,
        suffix: `— ${record.action} failed (status ${record.status})`,
        markerColor: C.red,
        suffixColor: C.red,
      }));
      out(pipe);
    }
```

- [ ] **Step 5: Render each retry command exactly once**

Build replacement retry records in the same shape expected by `retryCommand()`:

```js
    const replacementRetryRecords = incompleteReplacements.map((record) => ({
      action: "install",
      source: record.source,
      skills: [record.skill],
    }));
    const retryCommands = uniqueRetryCommands([
      ...replacementRetryRecords,
      ...result.failed,
    ]);
    if (retryCommands.length > 0) {
      active("Retry commands");
      for (const command of retryCommands) item(command, C.red);
    }
```

Remove retry text from replacement-warning skill suffixes and remove the old `Failed — retry these commands` command loop. Keep retry commands as ordinary `item()` strings so no command token is semantically recolored as a skill.

- [ ] **Step 6: Run execution-summary and complete UI tests and verify GREEN**

Run:

```bash
node --test --test-name-pattern='execution summary|execution retry|uninstall execution' mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
node --test mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: all tests PASS; each skill is a separate bright-green row, every unique retry command occurs once, and unsafe source material remains redacted.

- [ ] **Step 7: Review Task 4 behavioral compatibility**

Confirm with:

```bash
git diff -- mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
node --test mac_scripts/functions/skills-manager/__tests__/operations.test.mjs
```

Expected: operation tests PASS; `executionSummary()` still reports the original record counts and never changes execution result objects.

- [ ] **Step 8: Commit Task 4**

```bash
git add mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git commit -m "feat(skm): clarify skill execution summaries"
```

---

### Task 5: Cross-Command Audit, Self-Review, and Verification

**Files:**
- Review: `mac_scripts/functions/skills-manager/ui.mjs`
- Review: `mac_scripts/functions/skills-manager/manage-commands.mjs`
- Review: `mac_scripts/functions/skills-manager/lifecycle-commands.mjs`
- Review: `mac_scripts/functions/skills-manager/__tests__/*.test.mjs`
- Reference: `docs/superpowers/specs/2026-07-20-skm-skill-list-readability-design.md`

**Interfaces:**
- Consumes: completed Tasks 1-4 and the acceptance criteria in the design spec.
- Produces: review evidence that every semantic skill list is covered, excluded lists remain compact, and all SKM behavior passes regression and live smoke checks.

- [ ] **Step 1: Audit every UI path that emits skill data**

Run:

```bash
rg -n 'skill|skills|available|requirements|extras|conflict|succeeded|failed|selectSkills' \
  mac_scripts/functions/skills-manager/ui.mjs \
  mac_scripts/functions/skills-manager/manage-commands.mjs \
  mac_scripts/functions/skills-manager/lifecycle-commands.mjs
```

Manually map each occurrence to one of these outcomes:

- Semantic list using `skillList`/`skillItem`.
- Interactive semantic list carrying `kind: "skill"`.
- Explicitly excluded count, heading, help syntax, warning/error sentence, retry command, or non-skill list.

Outside the explicitly excluded prose and retry-command cases, no actual skill enumeration may remain as a complete string passed to `item()`, a comma-joined name list, or an unmarked selector label.

- [ ] **Step 2: Run a self-review against every acceptance criterion**

Inspect:

```bash
git diff 630cdc4..HEAD -- \
  mac_scripts/functions/skills-manager/ui.mjs \
  mac_scripts/functions/skills-manager/manage-commands.mjs \
  mac_scripts/functions/skills-manager/lifecycle-commands.mjs \
  mac_scripts/functions/skills-manager/__tests__
```

Verify all of the following directly in the diff:

- Bright green applies only to semantic skill names.
- Separators occur only between adjacent skill rows.
- Yellow/red markers and suffixes remain visible.
- `redactSource()` still protects every displayed persisted source.
- Profile/project/dashboard/help/retry output is not treated as a skill list.
- Selector `value` and `key` fields are unchanged.
- Execution counts, command exit codes, config writes, and lifecycle plans are untouched.

If any check fails, add a focused failing test first, implement the smallest correction, rerun its test, and commit only the affected code/test files with:

```bash
git add \
  mac_scripts/functions/skills-manager/ui.mjs \
  mac_scripts/functions/skills-manager/manage-commands.mjs \
  mac_scripts/functions/skills-manager/lifecycle-commands.mjs \
  mac_scripts/functions/skills-manager/__tests__/ui.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
git commit -m "fix(skm): complete skill list formatting"
```

- [ ] **Step 3: Run focused suites**

Run:

```bash
node --test \
  mac_scripts/functions/skills-manager/__tests__/ui.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/selector.test.mjs \
  mac_scripts/functions/skills-manager/__tests__/operations.test.mjs
```

Expected: every focused test passes with zero failures.

- [ ] **Step 4: Run the full SKM regression suite**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
```

Expected: all tests pass with zero failures, cancellations, or skipped failures.

- [ ] **Step 5: Perform isolated live command smoke checks**

Open one PTY shell, run the following block line-by-line, and keep that same shell open for Step 6 so `SKM_SMOKE_ROOT` remains defined:

```bash
SKM_SMOKE_ROOT="$(mktemp -d /private/tmp/skm-skill-list-smoke.XXXXXX)"
echo "$SKM_SMOKE_ROOT"
env CLOUD_UTILS_CONFIG_DIR="$SKM_SMOKE_ROOT" npm_config_cache=/private/tmp/skm-npx-cache \
  node mac_scripts/functions/skills-manager/cli.mjs source show obra/superpowers
env CLOUD_UTILS_CONFIG_DIR="$SKM_SMOKE_ROOT" npm_config_cache=/private/tmp/skm-npx-cache \
  node mac_scripts/functions/skills-manager/cli.mjs source add obra/superpowers -p default -a
env CLOUD_UTILS_CONFIG_DIR="$SKM_SMOKE_ROOT" \
  node mac_scripts/functions/skills-manager/cli.mjs profile show default
env CLOUD_UTILS_CONFIG_DIR="$SKM_SMOKE_ROOT" \
  node mac_scripts/functions/skills-manager/cli.mjs project link default
env CLOUD_UTILS_CONFIG_DIR="$SKM_SMOKE_ROOT" npm_config_cache=/private/tmp/skm-npx-cache \
  node mac_scripts/functions/skills-manager/cli.mjs status default
env CLOUD_UTILS_CONFIG_DIR="$SKM_SMOKE_ROOT" npm_config_cache=/private/tmp/skm-npx-cache \
  node mac_scripts/functions/skills-manager/cli.mjs install default --yes --dry-run
env CLOUD_UTILS_CONFIG_DIR="$SKM_SMOKE_ROOT" npm_config_cache=/private/tmp/skm-npx-cache \
  node mac_scripts/functions/skills-manager/cli.mjs uninstall default --yes --dry-run
```

Expected: `source show`, `profile show`, `status`, install dry-run, and uninstall dry-run visibly highlight skill names in bright green and separate adjacent skills with one `│` line. Non-zero `status`/dry-run exits are acceptable only when the displayed plan reports missing/conflicting state that explains the exit. Do not delete the temporary directory automatically; report its path so cleanup remains explicit and recoverable.

- [ ] **Step 6: Perform an interactive selector smoke check**

In the same PTY shell from Step 5, launch:

```bash
env CLOUD_UTILS_CONFIG_DIR="$SKM_SMOKE_ROOT" npm_config_cache=/private/tmp/skm-npx-cache \
  node mac_scripts/functions/skills-manager/cli.mjs source edit obra/superpowers -p default
```

Expected: every skill label is bright green, descriptions remain secondary gray, and one framed blank line separates adjacent skills. Press `q` to cancel and confirm the config is not rewritten.

- [ ] **Step 7: Run final hygiene checks**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` prints no output. `git status --short` contains no unexpected files and preserves unrelated user-owned `.agents/` and `skills-lock.json` state.

- [ ] **Step 8: Request final two-stage review**

Use `superpowers:requesting-code-review` to review spec compliance first and code quality second. Any accepted finding must return to RED → GREEN TDD before a correction commit. Finish only after both reviews report no Critical, Important, or Minor issues and the full suite is rerun after the final code change.
