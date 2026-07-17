# SKM Node.js 24 Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the documented SKM JavaScript compatibility baseline from Node.js 20 to Node.js 24 and verify the complete suite with a Node.js 24 runtime.

**Architecture:** Keep the wrapper and JavaScript implementation unchanged. Update the migration specification and implementation plan together so the runtime floor, global constraint, acceptance criterion, and executable verification command all describe the same Node.js 24 baseline.

**Tech Stack:** Markdown, Node.js 24, `node:test`, `npx`, Git.

## Global Constraints

- Node.js 24 is the implementation and CI compatibility baseline.
- The supported runtime is Node.js 24 or newer.
- The JavaScript CLI does not implement a separate Node version gate.
- Production code continues to use only Node.js built-ins; no package metadata, CI configuration, dependency, wrapper, or CLI change is added.
- Commands, data, terminal UI, streams, exit statuses, and compatibility behavior remain unchanged.
- Preserve unrelated working-tree changes in `mac_init/alias`, `mac_scripts/functions/skills-manager/list.json.example`, `.agents/`, and `skills-lock.json`.

---

### Task 1: Raise the SKM Compatibility Baseline

**Files:**

- Modify: `docs/skm-js-migration-spec.md`
- Modify: `docs/skm-js-migration-plan.md`

**Interfaces:**

- Consumes: the approved design in `docs/superpowers/specs/2026-07-17-skm-node-24-baseline-design.md` and the existing SKM test suite.
- Produces: a consistent Node.js 24 runtime contract and a self-contained Node.js 24 verification command.

- [ ] **Step 1: Confirm the stale Node.js 20 baseline is present**

  Run:

  ```bash
  rg -n "Node\.js 20|Node 20|node@20|using=20|v20\." docs/skm-js-migration-spec.md docs/skm-js-migration-plan.md
  ```

  Expected: six matches—three in the specification and three in the implementation plan. This is the failing baseline condition that the documentation change removes.

- [ ] **Step 2: Update the specification contract**

  In `docs/skm-js-migration-spec.md`, replace the three Node.js 20 statements with these exact lines:

  ```markdown
  5. Keep Node.js 24 as the implementation and CI compatibility baseline.
  ```

  ```markdown
  - Node.js 24 or newer.
  ```

  ```markdown
  7. Unit, command, golden, differential, wrapper, and supported PTY tests pass on
     Node.js 24.
  ```

- [ ] **Step 3: Update the implementation-plan baseline and executable verification**

  In `docs/skm-js-migration-plan.md`, replace the technology and global-constraint lines with:

  ```markdown
  **Tech Stack:** Node.js 24+, ECMAScript modules (`.mjs`), `node:test`, Node built-ins only, Zsh wrapper, macOS PTY smoke tests.
  ```

  ```markdown
  - Node.js 24 is the implementation and CI compatibility baseline.
  ```

  Replace the first Final Verification checklist item with this exact block:

  ````markdown
  - [ ] Run the full suite on Node.js 24:

    ```bash
    env npm_config_cache=/private/tmp/cloud-utils-skm-node24-npm-cache npm_config_update_notifier=false npx -y node@24 --version
    env npm_config_cache=/private/tmp/cloud-utils-skm-node24-npm-cache npm_config_update_notifier=false npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
    ```

    Expected: version starts with `v24.` and all supported tests pass.
  ````

- [ ] **Step 4: Verify that no Node.js 20 baseline reference remains**

  Run:

  ```bash
  ! rg -n "Node\.js 20|Node 20|node@20|using=20|v20\." docs/skm-js-migration-spec.md docs/skm-js-migration-plan.md
  ```

  Expected: exit status `0` with no output.

  Run:

  ```bash
  rg -n "Node\.js 24|Node 24|node@24|v24\." docs/skm-js-migration-spec.md docs/skm-js-migration-plan.md
  ```

  Expected: the specification goal, runtime floor, acceptance criterion, plan technology line, global constraint, Final Verification heading, both `node@24` commands, and the `v24.` expectation are present.

- [ ] **Step 5: Run the complete suite with Node.js 24**

  Run:

  ```bash
  env npm_config_cache=/private/tmp/cloud-utils-skm-node24-npm-cache npm_config_update_notifier=false npx -y node@24 --version
  env npm_config_cache=/private/tmp/cloud-utils-skm-node24-npm-cache npm_config_update_notifier=false npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
  ```

  Expected: the first command prints a version beginning with `v24.`; the second exits `0` with every supported test passing. On the supported macOS environment, PTY tests run without skips.

- [ ] **Step 6: Verify the exact documentation-only diff**

  Run:

  ```bash
  git diff --check -- docs/skm-js-migration-spec.md docs/skm-js-migration-plan.md
  git diff --stat -- docs/skm-js-migration-spec.md docs/skm-js-migration-plan.md
  ```

  Expected: no whitespace errors; only the two expected Markdown files appear in the task diff.

- [ ] **Step 7: Commit the baseline update with explicit paths**

  Run:

  ```bash
  git add docs/skm-js-migration-spec.md docs/skm-js-migration-plan.md
  git diff --cached --name-only
  ```

  Expected: the staged file list contains exactly:

  ```text
  docs/skm-js-migration-plan.md
  docs/skm-js-migration-spec.md
  ```

  Commit:

  ```bash
  git commit -m "docs(skm): raise Node.js baseline to 24"
  ```
