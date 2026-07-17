# SKM Profile and Project Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SKM's saved-source launcher with a profile-based manager that links reusable profiles to projects and provides safe status, install, and uninstall workflows.

**Architecture:** Store reusable profile recipes and project links in separate versioned JSON documents, with pure domain modules validating and planning all changes. Keep upstream `npx skills` behavior behind an adapter, reconcile actual installed state with lock provenance, and leave `cli.mjs` responsible only for parsing and dispatch into focused command modules.

**Tech Stack:** Node.js 24+, ECMAScript modules (`.mjs`), Node built-ins only, `node:test`, Zsh compatibility wrapper, macOS PTY smoke tests, JSON configuration, Git.

## Global Constraints

- The approved design is `docs/superpowers/specs/2026-07-18-skm-profile-project-management-design.md`.
- Production code uses Node.js 24 and built-in modules only; do not add a package manifest, runtime dependency, transpiler, or bundler.
- The supported scope is the current project only; do not add global skill management.
- `profiles.json` always contains at least one profile; `projects.json` stores only projects with one or more linked profiles.
- A persisted skill belongs to a profile source entry, and a source may have `skills: []`.
- Never persist `"*"`; select-all stores an explicit snapshot of discovered skill names.
- Canonical source plus exact skill name is the logical identity, but one merged operation may not require the same skill name from different sources.
- No command installs all profiles. Do not implement `--all-profiles`.
- Source mismatch and untracked same-name skills require explicit `--force` before replacement or removal.
- `skm uninstall` removes selected profile links by default and preserves skills required by remaining linked profiles.
- Removed commands `skm ls`, `skm list`, `skm add`, `skm remove`, `skm rm`, and the old `skm show` are not aliases; they fail as unknown commands.
- Preserve the existing `mac_scripts/skm` wrapper boundary and exact argument forwarding.
- Preserve terminal restoration on cancel, Ctrl+C, suspend, resume, spawn, and signal paths.
- Use TDD for every behavior change: write the focused failing test, observe the expected failure, implement the minimum behavior, then rerun the focused and related suites.
- Preserve unrelated working-tree changes in `mac_init/alias`, `mac_scripts/functions/skills-manager/list.json.example`, `.agents/`, and `skills-lock.json`.

---

## File Structure

### New production files

- `mac_scripts/functions/skills-manager/source-id.mjs` — source canonicalization and credential-safe display.
- `mac_scripts/functions/skills-manager/profiles.mjs` — profile document validation and immutable profile/source/skill mutations.
- `mac_scripts/functions/skills-manager/projects.mjs` — project document validation, root resolution, and immutable link mutations.
- `mac_scripts/functions/skills-manager/installed-state.mjs` — actual installed-skill and lock-provenance reconciliation.
- `mac_scripts/functions/skills-manager/planner.mjs` — pure profile merge, status, install, and uninstall plans.
- `mac_scripts/functions/skills-manager/operations.mjs` — sequential upstream install/uninstall execution and aggregate results.
- `mac_scripts/functions/skills-manager/manage-commands.mjs` — profile, source, skill, and project command handlers.
- `mac_scripts/functions/skills-manager/lifecycle-commands.mjs` — status, install, and uninstall handlers.
- `mac_scripts/functions/skills-manager/dashboard.mjs` — no-argument interactive dashboard orchestration.

### Modified production files

- `mac_scripts/functions/skills-manager/config.mjs` — bootstrap `profiles.json` and `projects.json`, migrate legacy `list.json`, perform atomic writes, and recover cross-file transactions.
- `mac_scripts/functions/skills-manager/skills-cli.mjs` — add captured upstream execution, installed JSON parsing, discovery parsing, and explicit mutation helpers.
- `mac_scripts/functions/skills-manager/selector.mjs` — generalize source-only selector state to arbitrary labeled values and confirmation stages.
- `mac_scripts/functions/skills-manager/ui.mjs` — replace saved-source UI with profile/project/status/plan/dashboard renderers.
- `mac_scripts/functions/skills-manager/cli.mjs` — replace old routes with the new command surface and dependency wiring.
- `mac_scripts/skm` — update the missing-Node message to reference the SKM config directory rather than `list.json`.

### New or rewritten tests

- `mac_scripts/functions/skills-manager/__tests__/source-id.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/projects.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/config.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/installed-state.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/planner.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/operations.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/dashboard.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/cli.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/selector.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/pty.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/wrapper.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/helpers.mjs`

### Removed obsolete implementation and tests

- `mac_scripts/functions/skills-manager/sources.mjs`
- `mac_scripts/functions/skills-manager/__tests__/sources.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/parity.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs`
- `mac_scripts/functions/skills-manager/__tests__/fixtures/golden.json`

Keep `mac_scripts/functions/skills-manager/list.json.example` untouched as a legacy/example artifact because it currently has unrelated user changes. The new runtime neither copies nor writes it.

### Documentation updates

- `docs/commands.md` — document profiles, project links, status, install, uninstall, and the dashboard.
- `docs/setup.md` — document `profiles.json` and `projects.json` paths.
- `docs/skm-js-migration-spec.md` — add a historical notice pointing to the new design; do not rewrite the historical migration contract.

---

### Task 1: Canonical Source Identity

**Files:**

- Create: `mac_scripts/functions/skills-manager/source-id.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/source-id.test.mjs`

**Interfaces:**

- Consumes: raw source strings, a current directory, and injectable `realpathSync`.
- Produces:
  - `canonicalizeSource(source, { cwd, realpath } = {}) -> string`
  - `redactSource(source) -> string`
  - `SourceIdentityError extends Error`

- [ ] **Step 1: Write failing canonicalization and redaction tests**

```js
import assert from "node:assert/strict";
import test from "node:test";
import {
  SourceIdentityError,
  canonicalizeSource,
  redactSource,
} from "../source-id.mjs";

test("canonicalizes equivalent GitHub repository roots", () => {
  for (const value of [
    "vercel-labs/agent-skills",
    "https://github.com/vercel-labs/agent-skills",
    "https://github.com/vercel-labs/agent-skills.git",
    "git@github.com:vercel-labs/agent-skills.git",
    "ssh://git@github.com/vercel-labs/agent-skills.git",
  ]) {
    assert.equal(canonicalizeSource(value), "vercel-labs/agent-skills");
  }
});

test("preserves a GitHub ref and subpath", () => {
  assert.equal(
    canonicalizeSource("https://github.com/acme/skills/tree/v2/skills/review"),
    "https://github.com/acme/skills/tree/v2/skills/review",
  );
});

test("canonicalizes local paths through injected realpath", () => {
  assert.equal(
    canonicalizeSource("../skills", {
      cwd: "/repo/app",
      realpath: (value) => `/real${value}`,
    }),
    "/real/repo/skills",
  );
});

test("removes credentials from stored and displayed URLs", () => {
  const input = "https://user:secret@git.example.com/acme/skills.git?token=query-secret";
  assert.doesNotMatch(canonicalizeSource(input), /user|secret/);
  assert.equal(redactSource(input), "https://git.example.com/acme/skills.git");
});

test("rejects empty source strings", () => {
  assert.throws(() => canonicalizeSource("   "), SourceIdentityError);
});
```

- [ ] **Step 2: Run the focused test and verify the module is missing**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/source-id.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `source-id.mjs`.

- [ ] **Step 3: Implement canonicalization and safe display**

Create `source-id.mjs` with this public structure and provider ordering:

```js
import { realpathSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";

export class SourceIdentityError extends Error {}

const SHORTHAND = /^[^\s/:]+\/[^\s/]+(?:\.git)?$/;
const GITHUB_SSH = /^(?:git@github\.com:|ssh:\/\/git@github\.com\/)([^/]+)\/([^/]+?)(?:\.git)?$/i;

export function redactSource(source) {
  const value = String(source);
  try {
    const url = new URL(value);
    url.username = "";
    url.password = "";
    for (const key of ["token", "access_token", "auth", "key"]) url.searchParams.delete(key);
    url.search = url.searchParams.toString();
    return url.toString().replace(/\/$/, "");
  } catch {
    return value.replace(/\/\/[^/@]+@/, "//");
  }
}

export function canonicalizeSource(source, {
  cwd = process.cwd(),
  realpath = realpathSync,
} = {}) {
  const value = String(source).trim();
  if (!value) throw new SourceIdentityError("Source must not be empty");

  if (value.startsWith("./") || value.startsWith("../") || isAbsolute(value)) {
    return realpath(resolve(cwd, value));
  }

  const ssh = value.match(GITHUB_SSH);
  if (ssh) return `${ssh[1]}/${ssh[2].replace(/\.git$/, "")}`;

  if (SHORTHAND.test(value)) return value.replace(/\.git$/, "");

  try {
    const url = new URL(value);
    url.username = "";
    url.password = "";
    for (const key of ["token", "access_token", "auth", "key"]) url.searchParams.delete(key);
    url.search = url.searchParams.toString();
    url.hash = "";
    url.hostname = url.hostname.toLowerCase();
    if (url.hostname === "github.com") {
      const parts = url.pathname.replace(/^\//, "").split("/").filter(Boolean);
      if (parts.length === 2) return `${parts[0]}/${parts[1].replace(/\.git$/, "")}`;
    }
    url.pathname = url.pathname.replace(/\.git\/?$/, "").replace(/\/$/, "");
    return url.toString().replace(/\/$/, "");
  } catch {
    return value;
  }
}
```

The generic URL branch intentionally preserves GitHub `/tree/<ref>/<subpath>`
and provider-specific path components after clearing credentials and fragments.

- [ ] **Step 4: Run the focused test and the existing source tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/source-id.test.mjs mac_scripts/functions/skills-manager/__tests__/sources.test.mjs
```

Expected: all tests pass.

- [ ] **Step 5: Commit the source identity boundary**

```bash
git add mac_scripts/functions/skills-manager/source-id.mjs mac_scripts/functions/skills-manager/__tests__/source-id.test.mjs
git commit -m "feat(skm): canonicalize skill sources"
```

---

### Task 2: Profile and Project Domain Documents

**Files:**

- Create: `mac_scripts/functions/skills-manager/profiles.mjs`
- Create: `mac_scripts/functions/skills-manager/projects.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/projects.test.mjs`

**Interfaces:**

- Consumes: canonical sources from Task 1 and plain parsed JSON values.
- Produces:
  - `validateProfilesDocument(value) -> ProfilesDocument`
  - `createProfile`, `renameProfile`, `removeProfile`
  - `addProfileSource`, `replaceProfileSourceSkills`, `removeProfileSource`
  - `addProfileSkills`, `removeProfileSkills`, `getProfile`
  - `validateProjectsDocument(value, profileNames) -> ProjectsDocument`
  - `linkProjectProfiles`, `unlinkProjectProfiles`, `removeProject`
  - `renameProjectProfileRefs`, `removeProjectProfileRefs`
  - `resolveProjectRoot({ cwd, execFile, realpath } = {}) -> string`
  - `ProfileConfigError` and `ProjectConfigError`

- [ ] **Step 1: Write failing profile invariant and mutation tests**

```js
import assert from "node:assert/strict";
import test from "node:test";
import {
  ProfileConfigError,
  addProfileSource,
  createProfile,
  removeProfile,
  validateProfilesDocument,
} from "../profiles.mjs";

const base = { version: 1, profiles: [{ name: "default", sources: [] }] };

test("requires at least one profile", () => {
  assert.throws(
    () => validateProfilesDocument({ version: 1, profiles: [] }),
    ProfileConfigError,
  );
  assert.throws(() => removeProfile(base, "default"), /final profile/i);
});

test("sorts profiles and sources without mutating input", () => {
  const created = createProfile(base, "alpha");
  const withSource = addProfileSource(created, "alpha", {
    source: "z/repo",
    skills: ["review"],
  });
  assert.deepEqual(withSource.profiles.map((item) => item.name), ["alpha", "default"]);
  assert.deepEqual(base, { version: 1, profiles: [{ name: "default", sources: [] }] });
});

test("rejects one skill name from two sources in one profile", () => {
  const first = addProfileSource(base, "default", {
    source: "a/repo",
    skills: ["review"],
  });
  assert.throws(
    () => addProfileSource(first, "default", {
      source: "b/repo",
      skills: ["review"],
    }),
    /review.*different source/i,
  );
});
```

- [ ] **Step 2: Write failing project link and root-resolution tests**

```js
import assert from "node:assert/strict";
import test from "node:test";
import {
  linkProjectProfiles,
  resolveProjectRoot,
  unlinkProjectProfiles,
  validateProjectsDocument,
} from "../projects.mjs";

const profiles = new Set(["frontend", "review"]);
const empty = { version: 1, projects: [] };

test("links profiles once and removes an entry after the last unlink", () => {
  const linked = linkProjectProfiles(empty, "/repo", ["frontend", "review"], profiles);
  assert.deepEqual(linked.projects[0], {
    root: "/repo",
    profiles: ["frontend", "review"],
  });
  assert.deepEqual(
    unlinkProjectProfiles(linked, "/repo", ["frontend", "review"]).projects,
    [],
  );
});

test("uses canonical Git root and falls back to cwd", () => {
  assert.equal(resolveProjectRoot({
    cwd: "/repo/subdir",
    execFile: () => "/repo\n",
    realpath: (value) => `/real${value}`,
  }), "/real/repo");
  assert.equal(resolveProjectRoot({
    cwd: "/plain",
    execFile: () => { throw new Error("not git"); },
    realpath: (value) => `/real${value}`,
  }), "/real/plain");
});

test("rejects references to missing profiles", () => {
  assert.throws(
    () => validateProjectsDocument({
      version: 1,
      projects: [{ root: "/repo", profiles: ["missing"] }],
    }, profiles),
    /missing profile/i,
  );
});
```

- [ ] **Step 3: Run both tests and verify missing modules**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs mac_scripts/functions/skills-manager/__tests__/projects.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for the two new modules.

- [ ] **Step 4: Implement immutable profile operations**

Use this exported document shape and clone-before-change pattern in
`profiles.mjs`:

```js
export class ProfileConfigError extends Error {}

const clone = (value) => structuredClone(value);

export function getProfile(document, name) {
  const profile = document.profiles.find((item) => item.name === name);
  if (!profile) throw new ProfileConfigError(`Profile not found: ${name}`);
  return profile;
}

export function createProfile(document, name) {
  validateProfileName(name);
  if (document.profiles.some((item) => item.name === name)) {
    throw new ProfileConfigError(`Profile already exists: ${name}`);
  }
  const next = clone(document);
  next.profiles.push({ name, sources: [] });
  next.profiles.sort((a, b) => a.name.localeCompare(b.name));
  return validateProfilesDocument(next);
}

export function addProfileSource(document, profileName, entry) {
  const next = clone(document);
  const profile = getProfile(next, profileName);
  if (profile.sources.some((item) => item.source === entry.source)) {
    throw new ProfileConfigError(`Source already exists in ${profileName}: ${entry.source}`);
  }
  profile.sources.push({ source: entry.source, skills: [...entry.skills] });
  profile.sources.sort((a, b) => a.source.localeCompare(b.source));
  return validateProfilesDocument(next);
}
```

Add the remaining profile functions with these exact signatures and mutation
rules:

```js
function validateProfileName(name) {
  if (typeof name !== "string" || name.trim() === "") {
    throw new ProfileConfigError("Profile name must not be empty");
  }
}

export function validateProfilesDocument(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.version !== 1) {
    throw new ProfileConfigError("profiles.json must have version 1");
  }
  if (!Array.isArray(value.profiles) || value.profiles.length === 0) {
    throw new ProfileConfigError("profiles.json must contain at least one profile");
  }
  const next = clone(value);
  const names = new Set();
  for (const profile of next.profiles) {
    validateProfileName(profile?.name);
    if (names.has(profile.name)) throw new ProfileConfigError(`Duplicate profile: ${profile.name}`);
    names.add(profile.name);
    if (!Array.isArray(profile.sources)) throw new ProfileConfigError(`Invalid sources: ${profile.name}`);
    const sources = new Set();
    const skillOwners = new Map();
    for (const entry of profile.sources) {
      if (!entry || typeof entry.source !== "string" || !Array.isArray(entry.skills)) {
        throw new ProfileConfigError(`Invalid source in profile: ${profile.name}`);
      }
      if (sources.has(entry.source)) throw new ProfileConfigError(`Duplicate source: ${entry.source}`);
      sources.add(entry.source);
      const skills = new Set();
      for (const skill of entry.skills) {
        if (typeof skill !== "string" || skill.trim() === "") {
          throw new ProfileConfigError(`Invalid skill in source: ${entry.source}`);
        }
        if (skills.has(skill)) throw new ProfileConfigError(`Duplicate skill: ${skill}`);
        skills.add(skill);
        const owner = skillOwners.get(skill);
        if (owner && owner !== entry.source) {
          throw new ProfileConfigError(`${skill} is selected from a different source: ${owner}`);
        }
        skillOwners.set(skill, entry.source);
      }
    }
  }
  next.profiles.sort((a, b) => a.name.localeCompare(b.name));
  for (const profile of next.profiles) {
    profile.sources.sort((a, b) => a.source.localeCompare(b.source));
  }
  return next;
}

export function renameProfile(document, oldName, newName) {
  validateProfileName(newName);
  if (document.profiles.some((item) => item.name === newName)) {
    throw new ProfileConfigError(`Profile already exists: ${newName}`);
  }
  const next = clone(document);
  getProfile(next, oldName).name = newName;
  return validateProfilesDocument(next);
}

export function removeProfile(document, name) {
  if (document.profiles.length === 1) throw new ProfileConfigError("Cannot remove the final profile");
  getProfile(document, name);
  return validateProfilesDocument({
    ...clone(document),
    profiles: document.profiles.filter((item) => item.name !== name),
  });
}

export function replaceProfileSourceSkills(document, profileName, source, skills) {
  const next = clone(document);
  const entry = getProfile(next, profileName).sources.find((item) => item.source === source);
  if (!entry) throw new ProfileConfigError(`Source not found in ${profileName}: ${source}`);
  entry.skills = [...skills];
  return validateProfilesDocument(next);
}

export function removeProfileSource(document, profileName, source) {
  const next = clone(document);
  const profile = getProfile(next, profileName);
  if (!profile.sources.some((item) => item.source === source)) {
    throw new ProfileConfigError(`Source not found in ${profileName}: ${source}`);
  }
  profile.sources = profile.sources.filter((item) => item.source !== source);
  return validateProfilesDocument(next);
}

export function addProfileSkills(document, profileName, source, requested) {
  const current = getProfile(document, profileName).sources.find((item) => item.source === source);
  if (!current) throw new ProfileConfigError(`Source not found in ${profileName}: ${source}`);
  return replaceProfileSourceSkills(document, profileName, source, [
    ...current.skills,
    ...requested.filter((skill) => !current.skills.includes(skill)),
  ]);
}

export function removeProfileSkills(document, profileName, source, requested) {
  const current = getProfile(document, profileName).sources.find((item) => item.source === source);
  if (!current) throw new ProfileConfigError(`Source not found in ${profileName}: ${source}`);
  const remove = new Set(requested);
  return replaceProfileSourceSkills(
    document,
    profileName,
    source,
    current.skills.filter((skill) => !remove.has(skill)),
  );
}
```

- [ ] **Step 5: Implement project operations and root resolution**

Use this structure in `projects.mjs`:

```js
import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";

export class ProjectConfigError extends Error {}

export function resolveProjectRoot({
  cwd = process.cwd(),
  execFile = (file, args, options) => execFileSync(file, args, options),
  realpath = realpathSync,
} = {}) {
  try {
    const root = execFile("git", ["rev-parse", "--show-toplevel"], {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    return realpath(root);
  } catch {
    return realpath(cwd);
  }
}

export function linkProjectProfiles(document, root, requested, profileNames) {
  for (const name of requested) {
    if (!profileNames.has(name)) throw new ProjectConfigError(`Profile not found: ${name}`);
  }
  const next = structuredClone(document);
  let project = next.projects.find((item) => item.root === root);
  if (!project) {
    project = { root, profiles: [] };
    next.projects.push(project);
  }
  for (const name of requested) {
    if (!project.profiles.includes(name)) project.profiles.push(name);
  }
  next.projects.sort((a, b) => a.root.localeCompare(b.root));
  return validateProjectsDocument(next, profileNames);
}
```

Add the remaining project functions:

```js
export function validateProjectsDocument(value, profileNames) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.version !== 1) {
    throw new ProjectConfigError("projects.json must have version 1");
  }
  if (!Array.isArray(value.projects)) throw new ProjectConfigError("projects must be an array");
  const next = structuredClone(value);
  const roots = new Set();
  for (const project of next.projects) {
    if (!project || typeof project.root !== "string" || !project.root.startsWith("/")) {
      throw new ProjectConfigError("Project root must be an absolute path");
    }
    if (roots.has(project.root)) throw new ProjectConfigError(`Duplicate project: ${project.root}`);
    roots.add(project.root);
    if (!Array.isArray(project.profiles) || project.profiles.length === 0) {
      throw new ProjectConfigError(`Project must link at least one profile: ${project.root}`);
    }
    if (new Set(project.profiles).size !== project.profiles.length) {
      throw new ProjectConfigError(`Duplicate profile link: ${project.root}`);
    }
    for (const name of project.profiles) {
      if (!profileNames.has(name)) throw new ProjectConfigError(`Missing profile: ${name}`);
    }
  }
  next.projects.sort((a, b) => a.root.localeCompare(b.root));
  return next;
}

export function unlinkProjectProfiles(document, root, requested) {
  const next = structuredClone(document);
  const project = next.projects.find((item) => item.root === root);
  if (!project) return next;
  const remove = new Set(requested);
  project.profiles = project.profiles.filter((name) => !remove.has(name));
  next.projects = next.projects.filter((item) => item.profiles.length > 0);
  return next;
}

export function removeProject(document, root) {
  return { ...structuredClone(document), projects: document.projects.filter((item) => item.root !== root) };
}

export function renameProjectProfileRefs(document, oldName, newName) {
  const next = structuredClone(document);
  for (const project of next.projects) {
    project.profiles = project.profiles.map((name) => name === oldName ? newName : name);
  }
  return next;
}

export function removeProjectProfileRefs(document, name) {
  const next = structuredClone(document);
  for (const project of next.projects) {
    project.profiles = project.profiles.filter((item) => item !== name);
  }
  next.projects = next.projects.filter((project) => project.profiles.length > 0);
  return next;
}
```

- [ ] **Step 6: Run focused and existing unit tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs mac_scripts/functions/skills-manager/__tests__/projects.test.mjs mac_scripts/functions/skills-manager/__tests__/sources.test.mjs
```

Expected: all tests pass.

- [ ] **Step 7: Commit the domain documents**

```bash
git add mac_scripts/functions/skills-manager/profiles.mjs mac_scripts/functions/skills-manager/projects.mjs mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs mac_scripts/functions/skills-manager/__tests__/projects.test.mjs
git commit -m "feat(skm): add profile and project models"
```

---

### Task 3: Config Bootstrap, Migration, and Atomic Writes

**Files:**

- Modify: `mac_scripts/functions/skills-manager/config.mjs`
- Rewrite: `mac_scripts/functions/skills-manager/__tests__/config.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/helpers.mjs`

**Interfaces:**

- Consumes: validators from Task 2 and the legacy `list.json` source shapes.
- Produces:
  - `initializeConfig({ env, fs, pid } = {}) -> ConfigPaths`
  - `readConfig(paths, { fs } = {}) -> { profiles, projects }`
  - `writeProfiles(paths, document, options) -> void`
  - `writeProjects(paths, profilesDocument, projectsDocument, options) -> void`
  - `writeJsonAtomic(filePath, value, options) -> void`
  - `ConfigFileError extends Error`
- Updates the sandbox helper to expose `profilesFile`, `projectsFile`,
  `legacyFile`, `transactionFile`, and configurable initial documents.

- [ ] **Step 1: Rewrite sandbox setup for both new files**

Replace the `makeSandbox` signature with:

```js
export function makeSandbox(t, {
  profiles = { version: 1, profiles: [{ name: "default", sources: [] }] },
  projects = { version: 1, projects: [] },
  legacyList,
  list,
  createProfiles = true,
  createProjects = true,
} = {}) {
  const root = mkdtempSync(join(tmpdir(), "skm-test-"));
  const configDir = join(root, "config");
  const skmDir = join(configDir, "skm");
  const profilesFile = join(skmDir, "profiles.json");
  const projectsFile = join(skmDir, "projects.json");
  const legacyFile = join(skmDir, "list.json");
  const transactionFile = join(skmDir, ".transaction.json");
  mkdirSync(skmDir, { recursive: true });
  if (createProfiles) writeFileSync(profilesFile, `${JSON.stringify(profiles, null, 2)}\n`);
  if (createProjects) writeFileSync(projectsFile, `${JSON.stringify(projects, null, 2)}\n`);
  const selectedLegacy = legacyList ?? list;
  if (selectedLegacy !== undefined) {
    writeFileSync(legacyFile, `${JSON.stringify(selectedLegacy, null, 2)}\n`);
  }
  // Reuse the current executable node/npx stubs and env construction below,
  // replacing only the returned config path fields with the fields listed next.
}
```

The returned object must include:

```js
{
  root,
  configDir,
  skmDir,
  profilesFile,
  projectsFile,
  legacyFile,
  transactionFile,
  binDir,
  argvLog,
  env,
}
```

Keep `skillsFile` temporarily as an alias of `legacyFile` until obsolete tests
are removed in Task 11.

- [ ] **Step 2: Write failing bootstrap and migration tests**

```js
test("creates one empty profile and an empty project registry when fresh", (t) => {
  const sandbox = makeSandbox(t, { createProfiles: false, createProjects: false });
  initializeConfig({ env: sandbox.env });
  assert.deepEqual(JSON.parse(readFileSync(sandbox.profilesFile, "utf8")), {
    version: 1,
    profiles: [{ name: "default", sources: [] }],
  });
  assert.deepEqual(JSON.parse(readFileSync(sandbox.projectsFile, "utf8")), {
    version: 1,
    projects: [],
  });
});

test("migrates legacy sources without selecting skills or linking a project", (t) => {
  const sandbox = makeSandbox(t, {
    createProfiles: false,
    createProjects: false,
    legacyList: [{ source: "b/repo" }, { source: "a/repo" }],
  });
  initializeConfig({ env: sandbox.env });
  const data = JSON.parse(readFileSync(sandbox.profilesFile, "utf8"));
  assert.deepEqual(data.profiles[0].sources, [
    { source: "a/repo", skills: [] },
    { source: "b/repo", skills: [] },
  ]);
  assert.equal(existsSync(sandbox.legacyFile), true);
  assert.deepEqual(JSON.parse(readFileSync(sandbox.projectsFile, "utf8")), {
    version: 1,
    projects: [],
  });
});

test("refuses projects.json without profiles.json", (t) => {
  const sandbox = makeSandbox(t, { createProfiles: false });
  assert.throws(() => initializeConfig({ env: sandbox.env }), /profiles\.json.*missing/i);
});

test("invalid JSON is byte preserving", (t) => {
  const sandbox = makeSandbox(t);
  writeFileSync(sandbox.profilesFile, "{broken", "utf8");
  assert.throws(() => readConfig(initializeConfig({ env: sandbox.env })), ConfigFileError);
  assert.equal(readFileSync(sandbox.profilesFile, "utf8"), "{broken");
});
```

- [ ] **Step 3: Run config tests and verify the old API fails expectations**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/config.test.mjs
```

Expected: FAIL because `initializeConfig` still returns `skillsFile` and does
not create or migrate the two new documents.

- [ ] **Step 4: Implement new bootstrap and read APIs**

Use these canonical constants and return shape:

```js
export const EMPTY_PROFILES = Object.freeze({
  version: 1,
  profiles: [{ name: "default", sources: [] }],
});
export const EMPTY_PROJECTS = Object.freeze({ version: 1, projects: [] });

export function initializeConfig({ env = process.env, fs = defaultFs, pid = process.pid } = {}) {
  const configDir = env.CLOUD_UTILS_CONFIG_DIR || defaultConfigDir(env);
  const skmDir = join(configDir, "skm");
  const paths = {
    configDir,
    skmDir,
    profilesFile: join(skmDir, "profiles.json"),
    projectsFile: join(skmDir, "projects.json"),
    legacyFile: join(skmDir, "list.json"),
    transactionFile: join(skmDir, ".transaction.json"),
  };
  fs.mkdirSync(skmDir, { recursive: true });
  recoverConfigTransaction(paths, { fs, pid });
  bootstrapDocuments(paths, { fs, pid });
  return { ...paths, skillsFile: paths.legacyFile };
}

export function readConfig(paths, { fs = defaultFs } = {}) {
  const profiles = validateProfilesDocument(readJson(paths.profilesFile, fs));
  const names = new Set(profiles.profiles.map((profile) => profile.name));
  const projects = validateProjectsDocument(readJson(paths.projectsFile, fs), names);
  return { profiles, projects };
}
```

Implement legacy array and `{ presets: [...] }` parsing locally in `config.mjs`.
Do not import `sources.mjs`; Task 12 removes it. Canonicalize and sort legacy
source strings before saving them with `skills: []`.

The temporary `skillsFile` return property keeps the old `cli.mjs` executable
during Tasks 3–10. Task 11 removes it when the router is replaced; it is not a
supported public command contract.

Until Task 4 adds journal recovery, export this fail-closed implementation so a
leftover transaction can never be ignored:

```js
export function recoverConfigTransaction(paths, { fs = defaultFs } = {}) {
  if (fs.existsSync(paths.transactionFile)) {
    throw new ConfigFileError(`Pending SKM transaction requires recovery: ${paths.transactionFile}`);
  }
}
```

- [ ] **Step 5: Implement atomic single-file writes**

```js
export function writeJsonAtomic(filePath, value, {
  fs = defaultFs,
  pid = process.pid,
} = {}) {
  const tempPath = `${filePath}.${pid}.tmp`;
  try {
    fs.writeFileSync(tempPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
    fs.renameSync(tempPath, filePath);
  } catch (error) {
    try { fs.rmSync(tempPath, { force: true }); } catch {}
    throw error;
  }
}

export function writeProfiles(paths, document, options) {
  writeJsonAtomic(paths.profilesFile, validateProfilesDocument(document), options);
}

export function writeProjects(paths, profilesDocument, projectsDocument, options) {
  const names = new Set(profilesDocument.profiles.map((profile) => profile.name));
  writeJsonAtomic(
    paths.projectsFile,
    validateProjectsDocument(projectsDocument, names),
    options,
  );
}
```

- [ ] **Step 6: Run config, profile, and project tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/config.test.mjs mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs mac_scripts/functions/skills-manager/__tests__/projects.test.mjs
```

Expected: all tests pass.

- [ ] **Step 7: Commit bootstrap and migration**

```bash
git add mac_scripts/functions/skills-manager/config.mjs mac_scripts/functions/skills-manager/__tests__/config.test.mjs mac_scripts/functions/skills-manager/__tests__/helpers.mjs
git commit -m "feat(skm): migrate profile configuration"
```

---

### Task 4: Recoverable Cross-File Transactions

**Files:**

- Modify: `mac_scripts/functions/skills-manager/config.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/config.test.mjs`

**Interfaces:**

- Consumes: validated documents and paths from Task 3.
- Produces:
  - `writeConfigTransaction(paths, { profiles, projects }, options) -> void`
  - `recoverConfigTransaction(paths, options) -> void`
- Transaction journal schema:
  `{ version: 1, phase, files: [{ target, backup, next, beforeHash, nextHash }] }`.

- [ ] **Step 1: Write failure-injection and recovery tests**

```js
import { createHash } from "node:crypto";
import * as realFs from "node:fs";

test("recovers the old pair when the second target rename fails", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const before = readConfig(paths);
  const profiles = createProfile(before.profiles, "frontend");
  const projects = linkProjectProfiles(
    before.projects,
    sandbox.root,
    ["frontend"],
    new Set(["default", "frontend"]),
  );
  let targetRenames = 0;
  const fs = {
    ...realFs,
    renameSync(from, to) {
      if ([paths.profilesFile, paths.projectsFile].includes(to) && ++targetRenames === 2) {
        throw new Error("second rename failed");
      }
      realFs.renameSync(from, to);
    },
  };
  assert.throws(
    () => writeConfigTransaction(paths, { profiles, projects }, { fs, pid: 4242 }),
    /second rename failed/,
  );
  recoverConfigTransaction(paths);
  assert.deepEqual(readConfig(paths), before);
  assert.equal(existsSync(paths.transactionFile), false);
});

test("completes a verified next pair after both targets were renamed", (t) => {
  const sandbox = makeSandbox(t);
  const paths = initializeConfig({ env: sandbox.env });
  const beforeProfiles = readFileSync(paths.profilesFile, "utf8");
  const beforeProjects = readFileSync(paths.projectsFile, "utf8");
  const nextProfiles = `${JSON.stringify({
    version: 1,
    profiles: [
      { name: "default", sources: [] },
      { name: "frontend", sources: [] },
    ],
  }, null, 2)}\n`;
  const nextProjects = `${JSON.stringify({
    version: 1,
    projects: [{ root: sandbox.root, profiles: ["frontend"] }],
  }, null, 2)}\n`;
  const hash = (value) => createHash("sha256").update(value).digest("hex");
  const files = [
    {
      target: paths.profilesFile,
      backup: `${paths.profilesFile}.777.bak`,
      next: `${paths.profilesFile}.777.next`,
      beforeHash: hash(beforeProfiles),
      nextHash: hash(nextProfiles),
      before: beforeProfiles,
      after: nextProfiles,
    },
    {
      target: paths.projectsFile,
      backup: `${paths.projectsFile}.777.bak`,
      next: `${paths.projectsFile}.777.next`,
      beforeHash: hash(beforeProjects),
      nextHash: hash(nextProjects),
      before: beforeProjects,
      after: nextProjects,
    },
  ];
  for (const item of files) {
    writeFileSync(item.backup, item.before, "utf8");
    writeFileSync(item.next, item.after, "utf8");
    writeFileSync(item.target, item.after, "utf8");
  }
  writeFileSync(paths.transactionFile, `${JSON.stringify({
    version: 1,
    phase: "targets-written",
    files: files.map(({ target, backup, next, beforeHash, nextHash }) => ({
      target, backup, next, beforeHash, nextHash,
    })),
  }, null, 2)}\n`, "utf8");

  recoverConfigTransaction(paths);

  assert.deepEqual(readConfig(paths), {
    profiles: JSON.parse(nextProfiles),
    projects: JSON.parse(nextProjects),
  });
  assert.equal(existsSync(paths.transactionFile), false);
  for (const item of files) {
    assert.equal(existsSync(item.backup), false);
    assert.equal(existsSync(item.next), false);
  }
});
```

- [ ] **Step 2: Run the focused recovery tests and observe missing exports**

Run:

```bash
node --test --test-name-pattern='recovers|completes a verified' mac_scripts/functions/skills-manager/__tests__/config.test.mjs
```

Expected: FAIL because `writeConfigTransaction` and complete recovery do not
yet exist.

- [ ] **Step 3: Implement journal hashing and transaction phases**

Add private helpers using `node:crypto`:

```js
import { createHash } from "node:crypto";

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

function transactionEntry(target, pid) {
  return {
    target,
    backup: `${target}.${pid}.bak`,
    next: `${target}.${pid}.next`,
    beforeHash: "",
    nextHash: "",
  };
}
```

`writeConfigTransaction` must execute these exact phases, rewriting the journal
atomically after each phase:

```text
prepared        backups and next files exist and hashes match
profiles-written
targets-written both target files contain nextHash
```

On an in-process failure, call recovery once and rethrow the original error.

Implement the writer with this control flow:

```js
export function writeConfigTransaction(paths, { profiles, projects }, {
  fs = defaultFs,
  pid = process.pid,
} = {}) {
  const validProfiles = validateProfilesDocument(profiles);
  const profileNames = new Set(validProfiles.profiles.map((item) => item.name));
  const validProjects = validateProjectsDocument(projects, profileNames);
  const documents = [
    [paths.profilesFile, validProfiles],
    [paths.projectsFile, validProjects],
  ];
  const files = documents.map(([target, document]) => {
    const entry = transactionEntry(target, pid);
    const before = fs.readFileSync(target);
    const next = Buffer.from(`${JSON.stringify(document, null, 2)}\n`);
    fs.writeFileSync(entry.backup, before);
    fs.writeFileSync(entry.next, next);
    entry.beforeHash = sha256(before);
    entry.nextHash = sha256(next);
    return entry;
  });
  const journal = { version: 1, phase: "prepared", files };
  const saveJournal = () => writeJsonAtomic(paths.transactionFile, journal, { fs, pid });
  saveJournal();
  try {
    fs.renameSync(files[0].next, files[0].target);
    journal.phase = "profiles-written";
    saveJournal();
    fs.renameSync(files[1].next, files[1].target);
    journal.phase = "targets-written";
    saveJournal();
    cleanupTransaction(journal, paths.transactionFile, fs);
  } catch (error) {
    try { recoverConfigTransaction(paths, { fs }); } catch {}
    throw error;
  }
}
```

Use these private helpers so recovery validates every path and checksum:

```js
function hashFile(filePath, fs) {
  return sha256(fs.readFileSync(filePath));
}

function readAndValidateJournal(filePath, fs) {
  let value;
  try {
    value = JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    throw new ConfigFileError(`Could not read transaction journal: ${filePath}`, { cause: error });
  }
  if (
    value?.version !== 1 ||
    !["prepared", "profiles-written", "targets-written"].includes(value.phase) ||
    !Array.isArray(value.files) ||
    value.files.length !== 2
  ) {
    throw new ConfigFileError(`Invalid transaction journal: ${filePath}`);
  }
  for (const item of value.files) {
    for (const key of ["target", "backup", "next", "beforeHash", "nextHash"]) {
      if (typeof item[key] !== "string" || item[key] === "") {
        throw new ConfigFileError(`Invalid transaction journal field: ${key}`);
      }
    }
  }
  return value;
}

function cleanupTransaction(journal, journalPath, fs) {
  for (const item of journal.files) {
    fs.rmSync(item.backup, { force: true });
    fs.rmSync(item.next, { force: true });
  }
  fs.rmSync(journalPath, { force: true });
}
```

- [ ] **Step 4: Implement deterministic recovery**

```js
export function recoverConfigTransaction(paths, { fs = defaultFs } = {}) {
  if (!fs.existsSync(paths.transactionFile)) return;
  const journal = readAndValidateJournal(paths.transactionFile, fs);
  const bothNext = journal.files.every((item) => hashFile(item.target, fs) === item.nextHash);
  if (journal.phase === "targets-written" && bothNext) {
    cleanupTransaction(journal, paths.transactionFile, fs);
    return;
  }
  for (const item of journal.files) {
    if (!fs.existsSync(item.backup) || hashFile(item.backup, fs) !== item.beforeHash) {
      throw new ConfigFileError(`Cannot recover transaction: ${item.target}`);
    }
  }
  for (const item of journal.files) fs.copyFileSync(item.backup, item.target);
  cleanupTransaction(journal, paths.transactionFile, fs);
}
```

`cleanupTransaction` removes only paths named in the validated journal. Never
glob or recursively remove the SKM directory.

- [ ] **Step 5: Run all storage tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/config.test.mjs mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs mac_scripts/functions/skills-manager/__tests__/projects.test.mjs
```

Expected: all tests pass, including every failure-injection phase.

- [ ] **Step 6: Commit recoverable transactions**

```bash
git add mac_scripts/functions/skills-manager/config.mjs mac_scripts/functions/skills-manager/__tests__/config.test.mjs
git commit -m "feat(skm): recover profile config transactions"
```

---

### Task 5: Structured Upstream CLI Adapter

**Files:**

- Modify: `mac_scripts/functions/skills-manager/skills-cli.mjs`
- Rewrite: `mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/fixtures/discovery-flat.txt`
- Create: `mac_scripts/functions/skills-manager/__tests__/fixtures/discovery-grouped.txt`

**Interfaces:**

- Consumes: canonical source strings and process dependencies.
- Produces:
  - existing `hasCommand` and inherited-stdio `runNpx`
  - `runNpxCapture(args, options) -> Promise<{ status, stdout, stderr }>`
  - `listInstalledSkills({ cwd, runner } = {}) -> Promise<InstalledSkill[]>`
  - `discoverAvailableSkills(source, { cwd, runner } = {}) -> Promise<AvailableSkill[]>`
  - `parseAvailableSkills(output) -> AvailableSkill[]`
  - `DiscoveryParseError extends Error`
  - `runSkillsMutation(args, { runner } = {}) -> Promise<number>`

`InstalledSkill` is `{ name, path, scope, agents }`; `AvailableSkill` is
`{ name, description }`.

- [ ] **Step 1: Add exact upstream output fixtures**

`discovery-flat.txt`:

```text
◇  Available Skills
│
│    frontend-design
│      Build distinctive frontend interfaces.
│
│    react-best-practices
│      Review React code for performance.
│
└  Use --skill <name> to install specific skills
```

`discovery-grouped.txt`:

```text
◇  Available Skills

React
│    react-best-practices
│      Review React code for performance.

General
│    docs
│      Create concise documentation.

└  Use --skill <name> to install specific skills
```

- [ ] **Step 2: Write failing capture, JSON-list, and discovery tests**

```js
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const FLAT = join(TEST_DIR, "fixtures/discovery-flat.txt");
const GROUPED = join(TEST_DIR, "fixtures/discovery-grouped.txt");

test("parses installed JSON and validates its shape", async () => {
  const result = await listInstalledSkills({
    cwd: "/repo",
    runner: async () => ({
      status: 0,
      stdout: '[{"name":"review","path":"/repo/.agents/skills/review","scope":"project","agents":["Codex"]}]',
      stderr: "",
    }),
  });
  assert.deepEqual(result, [{
    name: "review",
    path: "/repo/.agents/skills/review",
    scope: "project",
    agents: ["Codex"],
  }]);
});

test("parses flat and grouped available-skill output", () => {
  assert.deepEqual(parseAvailableSkills(readFileSync(FLAT, "utf8")), [
    { name: "frontend-design", description: "Build distinctive frontend interfaces." },
    { name: "react-best-practices", description: "Review React code for performance." },
  ]);
  assert.deepEqual(parseAvailableSkills(readFileSync(GROUPED, "utf8")), [
    { name: "react-best-practices", description: "Review React code for performance." },
    { name: "docs", description: "Create concise documentation." },
  ]);
});

test("rejects uncertain discovery output", () => {
  assert.throws(() => parseAvailableSkills("Available Skills\nmaybe"), DiscoveryParseError);
});
```

- [ ] **Step 3: Run adapter tests and observe missing APIs**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs
```

Expected: FAIL because the captured runner, parsers, and typed helpers are not
implemented.

- [ ] **Step 4: Implement captured process execution**

```js
export function runNpxCapture(args, {
  spawnImpl = spawn,
  cwd = process.cwd(),
} = {}) {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (status) => {
      if (settled) return;
      settled = true;
      resolve({ status, stdout, stderr });
    };
    let child;
    try {
      child = spawnImpl("npx", args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    } catch {
      finish(1);
      return;
    }
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", () => finish(1));
    child.once("close", (status) => finish(Number.isInteger(status) ? status : 1));
  });
}
```

Retain the existing settle-once behavior of inherited `runNpx`.

- [ ] **Step 5: Implement typed list and discovery parsers**

```js
const ANSI = /\u001b\[[0-?]*[ -/]*[@-~]/g;

export function parseAvailableSkills(output) {
  const lines = output.replace(ANSI, "").split(/\r?\n/);
  const start = lines.findIndex((line) => line.includes("Available Skills"));
  const end = lines.findIndex((line, index) => index > start && line.includes("Use --skill"));
  if (start < 0 || end < 0) throw new DiscoveryParseError("Could not parse available skills");
  const result = [];
  for (let index = start + 1; index < end; index += 1) {
    const name = lines[index].match(/^│\s{4}(\S.*)$/)?.[1]?.trim();
    if (!name) continue;
    const description = lines[index + 1]?.match(/^│\s{6}(\S.*)$/)?.[1]?.trim();
    if (!description) throw new DiscoveryParseError(`Missing description for skill: ${name}`);
    result.push({ name, description });
    index += 1;
  }
  if (result.length === 0) throw new DiscoveryParseError("No skill records found");
  if (new Set(result.map((item) => item.name)).size !== result.length) {
    throw new DiscoveryParseError("Duplicate skill names in discovery output");
  }
  return result;
}
```

`listInstalledSkills` must call `runner(["skills", "list", "--json"], { cwd })`,
require status `0`, JSON-parse stdout, and reject any record missing the four
documented fields. `discoverAvailableSkills` calls
`runner(["skills", "add", source, "--list"], { cwd })` and parses stdout only
on status `0`.

Mutation delegation remains intentionally thin:

```js
export function runSkillsMutation(args, { runner = runNpx } = {}) {
  return runner(args);
}
```

- [ ] **Step 6: Run adapter and existing child-process tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs
```

Expected: all tests pass, including synchronous throw, error-plus-close, and
signal-only settle-once cases.

- [ ] **Step 7: Commit the upstream adapter**

```bash
git add mac_scripts/functions/skills-manager/skills-cli.mjs mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs mac_scripts/functions/skills-manager/__tests__/fixtures/discovery-flat.txt mac_scripts/functions/skills-manager/__tests__/fixtures/discovery-grouped.txt
git commit -m "feat(skm): add structured skills CLI adapter"
```

---

### Task 6: Installed State and Provenance Reconciliation

**Files:**

- Create: `mac_scripts/functions/skills-manager/installed-state.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/installed-state.test.mjs`

**Interfaces:**

- Consumes: `listInstalledSkills`, current project root, environment, and known
  upstream lock layouts.
- Produces:
  - `readProjectProvenance({ projectRoot, env, fs } = {}) -> Map<string, Provenance>`
  - `loadInstalledState({ projectRoot, env, listInstalled, fs } = {}) -> Promise<Map<string, ActualSkill>>`
  - `InstalledStateError extends Error`

`Provenance` is `{ source, sourceType, origin }`. `ActualSkill` is
`{ name, path, agents, source, provenance }`, where `source` is canonical or
`null`, and `provenance` is `"tracked"` or `"untracked"`.

- [ ] **Step 1: Write failing project-local, newer-lock, stale, and invalid tests**

```js
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function makeRoot(t) {
  const root = mkdtempSync(join(tmpdir(), "skm-state-test-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

function writeLock(root, skills) {
  writeFileSync(join(root, "skills-lock.json"), `${JSON.stringify({
    version: 1,
    skills,
  }, null, 2)}\n`, "utf8");
}

test("combines actual presence with project-local provenance", async (t) => {
  const root = makeRoot(t);
  writeFileSync(join(root, "skills-lock.json"), JSON.stringify({
    version: 1,
    skills: {
      review: { source: "https://github.com/acme/skills.git", sourceType: "github" },
    },
  }));
  const state = await loadInstalledState({
    projectRoot: root,
    listInstalled: async () => [{
      name: "review",
      path: join(root, ".agents/skills/review"),
      scope: "project",
      agents: ["Codex"],
    }],
  });
  assert.deepEqual(state.get("review"), {
    name: "review",
    path: join(root, ".agents/skills/review"),
    agents: ["Codex"],
    source: "acme/skills",
    provenance: "tracked",
  });
});

test("marks actual skills untracked when lock data is absent or invalid", async (t) => {
  const root = makeRoot(t);
  writeFileSync(join(root, "skills-lock.json"), "{broken");
  const state = await loadInstalledState({
    projectRoot: root,
    listInstalled: async () => [{
      name: "review", path: `${root}/review`, scope: "project", agents: [],
    }],
  });
  assert.equal(state.get("review").source, null);
  assert.equal(state.get("review").provenance, "untracked");
});

test("does not turn a stale lock-only entry into actual presence", async (t) => {
  const root = makeRoot(t);
  writeLock(root, { review: { source: "acme/skills", sourceType: "github" } });
  const state = await loadInstalledState({ projectRoot: root, listInstalled: async () => [] });
  assert.equal(state.has("review"), false);
});
```

- [ ] **Step 2: Run the focused test and verify the module is missing**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/installed-state.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement lock candidate parsing and reconciliation**

```js
export async function loadInstalledState({
  projectRoot,
  env = process.env,
  listInstalled = ({ cwd }) => listInstalledSkills({ cwd }),
  fs = defaultFs,
} = {}) {
  let actual;
  try {
    actual = await listInstalled({ cwd: projectRoot });
  } catch (error) {
    throw new InstalledStateError(`Could not list installed skills in ${projectRoot}`, { cause: error });
  }
  const provenance = readProjectProvenance({ projectRoot, env, fs });
  return new Map(actual.map((skill) => {
    const entry = provenance.get(skill.name);
    return [skill.name, {
      name: skill.name,
      path: skill.path,
      agents: [...skill.agents],
      source: entry?.source ?? null,
      provenance: entry ? "tracked" : "untracked",
    }];
  }));
}
```

`readProjectProvenance` checks, in order:

1. `<projectRoot>/skills-lock.json`.
2. `<projectRoot>/.agents/.skill-lock.json`.
3. `${XDG_STATE_HOME}/skills/.skill-lock.json` when set.
4. `${HOME}/.agents/.skill-lock.json`.

Accept a top-level `{ skills: Record<string, entry> }` with string `source`.
Canonicalize sources through Task 1. An invalid candidate is ignored for
provenance but never rewritten. A project-local valid entry wins over a newer
global entry. Return no provenance when two same-priority usable candidates
disagree.

- [ ] **Step 4: Run installed-state and adapter tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/installed-state.test.mjs mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs
```

Expected: all tests pass.

- [ ] **Step 5: Commit installed-state reconciliation**

```bash
git add mac_scripts/functions/skills-manager/installed-state.mjs mac_scripts/functions/skills-manager/__tests__/installed-state.test.mjs
git commit -m "feat(skm): reconcile installed skill state"
```

---

### Task 7: Pure Status, Install, and Uninstall Planner

**Files:**

- Create: `mac_scripts/functions/skills-manager/planner.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/planner.test.mjs`

**Interfaces:**

- Consumes: validated profiles and the installed-state map from Tasks 2 and 6.
- Produces:
  - `mergeProfileRequirements(profilesDocument, names) -> MergeResult`
  - `classifyStatus(mergeResult, installedState) -> StatusResult`
  - `createInstallPlan(statusResult, { force, selectedKeys } = {}) -> InstallPlan`
  - `createUninstallPlan({ selected, remaining, installedState, force, linkedSelected }) -> UninstallPlan`
  - `PlannerError extends Error`

`Requirement` is `{ key, source, skill, profiles }`, where
`key = JSON.stringify([source, skill])`. `MergeResult` is
`{ requirements, desiredConflicts }`.

- [ ] **Step 1: Write failing merge and status classification tests**

```js
const PROFILES = {
  version: 1,
  profiles: [
    {
      name: "frontend",
      sources: [{ source: "a/repo", skills: ["frontend-design", "code-review"] }],
    },
    {
      name: "quality",
      sources: [{ source: "a/repo", skills: ["code-review", "testing"] }],
    },
    {
      name: "review",
      sources: [{ source: "a/repo", skills: ["code-review"] }],
    },
  ],
};

const CONFLICTING_PROFILES = {
  version: 1,
  profiles: [
    { name: "a", sources: [{ source: "a/repo", skills: ["review"] }] },
    { name: "b", sources: [{ source: "b/repo", skills: ["review"] }] },
  ],
};

const name = (item) => item.skill;
const actual = (skill, source) => ({
  name: skill,
  path: `/repo/.agents/skills/${skill}`,
  agents: ["Codex"],
  source,
  provenance: source ? "tracked" : "untracked",
});

const MERGED = {
  requirements: [
    { key: '["a/repo","ok"]', source: "a/repo", skill: "ok", profiles: ["p"] },
    { key: '["a/repo","missing"]', source: "a/repo", skill: "missing", profiles: ["p"] },
    { key: '["a/repo","wrong"]', source: "a/repo", skill: "wrong", profiles: ["p"] },
    { key: '["a/repo","unknown"]', source: "a/repo", skill: "unknown", profiles: ["p"] },
  ],
  desiredConflicts: [],
};

test("deduplicates the same pair and records contributing profiles", () => {
  const merged = mergeProfileRequirements(PROFILES, ["frontend", "review"]);
  const shared = merged.requirements.find((item) => item.skill === "code-review");
  assert.deepEqual(shared.profiles, ["frontend", "review"]);
});

test("reports one name required from different sources", () => {
  const merged = mergeProfileRequirements(CONFLICTING_PROFILES, ["a", "b"]);
  assert.deepEqual(merged.desiredConflicts, [{
    skill: "review",
    sources: ["a/repo", "b/repo"],
    profiles: ["a", "b"],
  }]);
});

test("classifies installed missing mismatch untracked and extra", () => {
  const result = classifyStatus(MERGED, new Map([
    ["ok", actual("ok", "a/repo")],
    ["wrong", actual("wrong", "other/repo")],
    ["unknown", actual("unknown", null)],
    ["extra", actual("extra", "x/repo")],
  ]));
  assert.deepEqual(result.installed.map(name), ["ok"]);
  assert.deepEqual(result.missing.map(name), ["missing"]);
  assert.deepEqual(result.mismatches.map(name), ["wrong"]);
  assert.deepEqual(result.untracked.map(name), ["unknown"]);
  assert.deepEqual(result.extras.map((item) => item.name), ["extra"]);
});
```

- [ ] **Step 2: Write failing install and shared-uninstall tests**

```js
const STATUS = classifyStatus(MERGED, new Map([
  ["ok", actual("ok", "a/repo")],
  ["wrong", actual("wrong", "other/repo")],
  ["unknown", actual("unknown", null)],
]));

const INSTALLED = new Map([
  ["frontend-design", actual("frontend-design", "a/repo")],
  ["code-review", actual("code-review", "a/repo")],
]);

test("install selects missing and blocks conflicts without force", () => {
  const plan = createInstallPlan(STATUS, { force: false });
  assert.deepEqual(plan.install.map(name), ["missing"]);
  assert.deepEqual(plan.replace, []);
  assert.deepEqual(plan.conflicts.map(name), ["wrong", "unknown"]);
});

test("install force moves mismatch and untracked entries into replacement", () => {
  const plan = createInstallPlan(STATUS, { force: true });
  assert.deepEqual(plan.replace.map(name), ["wrong", "unknown"]);
  assert.deepEqual(plan.conflicts, []);
});

test("temporary selected keys filter install candidates without changing status", () => {
  const missing = STATUS.missing[0];
  const plan = createInstallPlan(STATUS, {
    force: false,
    selectedKeys: new Set([missing.key]),
  });
  assert.deepEqual(plan.install.map(name), ["missing"]);
  assert.equal(STATUS.missing.length, 1);
});

test("uninstall retains requirements from remaining linked profiles", () => {
  const plan = createUninstallPlan({
    selected: mergeProfileRequirements(PROFILES, ["frontend"]),
    remaining: mergeProfileRequirements(PROFILES, ["quality"]),
    installedState: INSTALLED,
    force: false,
    linkedSelected: ["frontend"],
  });
  assert.deepEqual(plan.remove.map(name), ["frontend-design"]);
  assert.deepEqual(plan.retain.map(name), ["code-review"]);
  assert.deepEqual(plan.unlinkProfiles, ["frontend"]);
});

test("force never resolves two desired sources for one skill name", () => {
  const selected = mergeProfileRequirements(CONFLICTING_PROFILES, ["a", "b"]);
  const plan = createUninstallPlan({
    selected,
    remaining: { requirements: [], desiredConflicts: [] },
    installedState: new Map(),
    force: true,
    linkedSelected: ["a", "b"],
  });
  assert.equal(plan.desiredConflicts.length, 1);
  assert.deepEqual(plan.remove, []);
});
```

- [ ] **Step 3: Run planner tests and verify the module is missing**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/planner.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 4: Implement deterministic merge and classification**

```js
export function requirementKey(source, skill) {
  return JSON.stringify([source, skill]);
}

export function mergeProfileRequirements(document, names) {
  const byKey = new Map();
  const bySkill = new Map();
  for (const profileName of names) {
    const profile = getProfile(document, profileName);
    for (const entry of profile.sources) {
      for (const skill of entry.skills) {
        const key = requirementKey(entry.source, skill);
        const item = byKey.get(key) ?? { key, source: entry.source, skill, profiles: [] };
        if (!item.profiles.includes(profileName)) item.profiles.push(profileName);
        byKey.set(key, item);
        const sources = bySkill.get(skill) ?? new Map();
        const owners = sources.get(entry.source) ?? [];
        if (!owners.includes(profileName)) owners.push(profileName);
        sources.set(entry.source, owners);
        bySkill.set(skill, sources);
      }
    }
  }
  const desiredConflicts = [...bySkill]
    .filter(([, sources]) => sources.size > 1)
    .map(([skill, sources]) => ({
      skill,
      sources: [...sources.keys()].sort(),
      profiles: [...new Set([...sources.values()].flat())].sort(),
    }));
  return {
    requirements: [...byKey.values()].sort((a, b) => a.skill.localeCompare(b.skill)),
    desiredConflicts,
  };
}
```

`classifyStatus` must return the non-empty `desiredConflicts` unchanged and skip
actual-state classification for the ambiguous names. Otherwise it classifies
required pairs by actual name and canonical source and computes extras from
actual names absent from the required skill-name set.

- [ ] **Step 5: Implement install and uninstall plan construction**

Plans use arrays named exactly:

```js
// InstallPlan
{ install, replace, skip, conflicts, extras, desiredConflicts }

// UninstallPlan
{ remove, retain, absent, conflicts, unlinkProfiles, desiredConflicts }
```

`force` moves mismatch and untracked entries from `conflicts` to `replace` for
install and to `remove` for uninstall. `selectedKeys`, when provided, filters
only install/replace candidates and never changes the persisted profile.
Uninstall retention compares pair keys, not only names, after desired-source
conflicts have been rejected.

- [ ] **Step 6: Run planner and domain tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/planner.test.mjs mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs mac_scripts/functions/skills-manager/__tests__/projects.test.mjs
```

Expected: all tests pass.

- [ ] **Step 7: Commit the pure planner**

```bash
git add mac_scripts/functions/skills-manager/planner.mjs mac_scripts/functions/skills-manager/__tests__/planner.test.mjs
git commit -m "feat(skm): plan profile lifecycle changes"
```

---

### Task 8: Profile, Source, Skill, and Project Commands

**Files:**

- Create: `mac_scripts/functions/skills-manager/manage-commands.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/ui.mjs`
- Rewrite: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs`

**Interfaces:**

- Consumes: config paths/read/write APIs, profile/project mutations, source
  identity, discovery adapter, selector runner, and UI.
- Produces:
  - `runProfileCommand(args, context) -> Promise<number>`
  - `runSourceCommand(args, context) -> Promise<number>`
  - `runSkillCommand(args, context) -> Promise<number>`
  - `runProjectCommand(args, context) -> Promise<number>`
- `context` contains `{ paths, config, cwd, stdin, stdout, stderr, ui,
  discoverAvailableSkills, selectItems }` and injected write functions.

- [ ] **Step 1: Write failing profile and project command tests**

```js
const profiles = (...names) => ({
  version: 1,
  profiles: names.map((name) => ({ name, sources: [] })),
});

const projects = (...entries) => ({ version: 1, projects: entries });

const profileWithSource = (name, source, skills) => ({
  version: 1,
  profiles: [{ name, sources: [{ source, skills }] }],
});

function makeManagementHarness(t, {
  profiles: profilesDocument = profiles("default"),
  projects: projectsDocument = projects(),
  discover = [],
  selected = [],
  projectRoot = "/repo",
} = {}) {
  const transactions = [];
  const upstreamCalls = [];
  let writtenProfiles;
  let writtenProjects;
  let stdout = "";
  let stderr = "";
  const context = {
    paths: { profilesFile: "/config/profiles.json", projectsFile: "/config/projects.json" },
    config: { profiles: profilesDocument, projects: projectsDocument },
    cwd: projectRoot,
    stdin: { isTTY: true },
    stdout: { isTTY: true, write: (value) => { stdout += value; } },
    stderr: { write: (value) => { stderr += value; } },
    ui: {
      error: (message) => { stderr += `${message}\n`; },
      warn: (message) => { stderr += `${message}\n`; },
      profileList() {}, profileShow() {}, profileChanged() {}, sourceChanged() {},
      skillChanged() {}, projectShow() {}, projectList() {}, projectChanged() {},
    },
    discoverAvailableSkills: async (source) => {
      upstreamCalls.push(["discover", source]);
      return discover;
    },
    selectItems: async () => ({ type: "submit", selected }),
    resolveProjectRoot: () => projectRoot,
    writeProfiles: (_paths, document) => { writtenProfiles = document; },
    writeProjects: (_paths, _profiles, document) => { writtenProjects = document; },
    writeConfigTransaction: (_paths, documents) => { transactions.push(documents); },
  };
  return {
    context,
    transactions,
    upstreamCalls,
    stdout: () => stdout,
    stderr: () => stderr,
    get writtenProfiles() { return writtenProfiles; },
    get writtenProjects() { return writtenProjects; },
  };
}

test("profile rename updates linked projects through one transaction", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profiles("frontend"),
    projects: projects({ root: "/repo", profiles: ["frontend"] }),
  });
  assert.equal(await runProfileCommand(["rename", "frontend", "web"], harness.context), 0);
  assert.equal(harness.transactions.length, 1);
  assert.deepEqual(harness.transactions[0].profiles.profiles.map((item) => item.name), ["web"]);
  assert.deepEqual(harness.transactions[0].projects.projects[0].profiles, ["web"]);
});

test("project link changes config without invoking upstream", async (t) => {
  const harness = makeManagementHarness(t, { profiles: profiles("frontend") });
  assert.equal(await runProjectCommand(["link", "frontend"], harness.context), 0);
  assert.deepEqual(harness.writtenProjects.projects[0].profiles, ["frontend"]);
  assert.equal(harness.upstreamCalls.length, 0);
});

test("profile remove blocks linked use and force unlinks in one transaction", async (t) => {
  const blocked = makeManagementHarness(t, {
    profiles: profiles("default", "frontend"),
    projects: projects({ root: "/repo", profiles: ["frontend"] }),
  });
  assert.equal(await runProfileCommand(["remove", "frontend"], blocked.context), 1);
  assert.equal(blocked.transactions.length, 0);

  const forced = makeManagementHarness(t, {
    profiles: profiles("default", "frontend"),
    projects: projects({ root: "/repo", profiles: ["frontend"] }),
  });
  assert.equal(await runProfileCommand(["remove", "frontend", "--force"], forced.context), 0);
  assert.deepEqual(forced.transactions[0], {
    profiles: profiles("default"),
    projects: projects(),
  });
});

test("profile remove never deletes the final profile", async (t) => {
  const harness = makeManagementHarness(t, { profiles: profiles("only") });
  assert.equal(await runProfileCommand(["remove", "only", "--force"], harness.context), 1);
  assert.equal(harness.transactions.length, 0);
});
```

- [ ] **Step 2: Write failing source and skill command tests**

```js
test("source add snapshots selected discovery records", async (t) => {
  const harness = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
    selected: ["b"],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "--profile", "default",
  ], harness.context), 0);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources, [{
    source: "acme/skills",
    skills: ["b"],
  }]);
});

test("source add --no-skills never contacts upstream", async (t) => {
  const harness = makeManagementHarness(t);
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "--no-skills",
  ], harness.context), 0);
  assert.equal(harness.upstreamCalls.length, 0);
});

test("source add --all snapshots every discovered name without a wildcard", async (t) => {
  const harness = makeManagementHarness(t, {
    discover: [
      { name: "a", description: "A" },
      { name: "b", description: "B" },
    ],
  });
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default", "--all",
  ], harness.context), 0);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources[0].skills, ["a", "b"]);
  assert.doesNotMatch(JSON.stringify(harness.writtenProfiles), /"\*"/);
});

test("skill add verifies every requested name before writing once", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profileWithSource("default", "acme/skills", []),
    discover: [{ name: "a", description: "A" }],
  });
  assert.equal(await runSkillCommand([
    "add", "a", "--source", "acme/skills", "--profile", "default",
  ], harness.context), 0);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources[0].skills, ["a"]);
});

test("source edit retains an unavailable saved skill unless deselected", async (t) => {
  const harness = makeManagementHarness(t, {
    profiles: profileWithSource("default", "acme/skills", ["old", "current"]),
    discover: [{ name: "current", description: "Current" }],
    selected: ["old", "current"],
  });
  assert.equal(await runSourceCommand([
    "edit", "acme/skills", "-p", "default",
  ], harness.context), 0);
  assert.deepEqual(harness.writtenProfiles.profiles[0].sources[0].skills, ["old", "current"]);
});

test("discovery failure leaves the complete profile document unwritten", async (t) => {
  const harness = makeManagementHarness(t);
  harness.context.discoverAvailableSkills = async () => { throw new Error("unparseable output"); };
  assert.equal(await runSourceCommand([
    "add", "acme/skills", "-p", "default",
  ], harness.context), 1);
  assert.equal(harness.writtenProfiles, undefined);
});
```

- [ ] **Step 3: Run command tests and verify handlers are missing**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 4: Implement strict option parsing for management commands**

Add a private parser that returns this stable shape:

```js
{
  positionals: [],
  profile: null,
  source: null,
  skills: [],
  all: false,
  noSkills: false,
  force: false,
}
```

Recognize `-p`/`--profile`, `--source`, repeatable `--skill`, `--all`,
`--no-skills`, and `--force`. Reject missing flag values, unknown flags, and
mutually exclusive source-selection modes before discovery or writes.

- [ ] **Step 5: Implement command dispatch and exact write boundaries**

Use this pattern for every handler:

```js
export async function runSourceCommand(args, context) {
  const [action, ...rest] = args;
  if (action === "add") return runSourceAdd(rest, context);
  if (action === "edit") return runSourceEdit(rest, context);
  if (action === "remove") return runSourceRemove(rest, context);
  if (action === "show") return runSourceShow(rest, context);
  context.ui.error(`Unknown source command: ${action ?? ""}`);
  return 1;
}
```

Profile rename and force-remove call `writeConfigTransaction` exactly once.
Other profile/source/skill changes call `writeProfiles` once. Project commands
call `writeProjects` once. No management handler invokes install or uninstall.

- [ ] **Step 6: Replace UI methods for management output**

`createUi` must expose these management renderers with no domain mutation:

```js
{
  usage,
  profileList,
  profileShow,
  profileChanged,
  sourceChanged,
  skillChanged,
  projectShow,
  projectList,
  projectChanged,
  selector,
  cancelledSelector,
  error,
  warn,
  info,
}
```

Add UI tests asserting profile/source/skill counts, linked-project display,
stale-root marking, and the explicit phrase `does not change installed project
skills` for skill mutations.

- [ ] **Step 7: Run management, UI, domain, and config tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs mac_scripts/functions/skills-manager/__tests__/profiles.test.mjs mac_scripts/functions/skills-manager/__tests__/projects.test.mjs mac_scripts/functions/skills-manager/__tests__/config.test.mjs
```

Expected: all tests pass.

- [ ] **Step 8: Commit management commands**

```bash
git add mac_scripts/functions/skills-manager/manage-commands.mjs mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/manage-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git commit -m "feat(skm): manage profiles and project links"
```

---

### Task 9: Lifecycle Execution and Status/Install Commands

**Files:**

- Create: `mac_scripts/functions/skills-manager/operations.mjs`
- Create: `mac_scripts/functions/skills-manager/lifecycle-commands.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/operations.test.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/ui.mjs`

**Interfaces:**

- Consumes: installed state, planner results, `runSkillsMutation`, project
  links, and UI.
- Produces:
  - `executeInstallPlan(plan, { yes, runMutation, onEvent }) -> Promise<ExecutionResult>`
  - `executeUninstallPlan(plan, { yes, runMutation, onEvent }) -> Promise<ExecutionResult>`
  - `runStatusCommand(args, context) -> Promise<number>`
  - `runInstallCommand(args, context) -> Promise<number>`
  - `runUninstallCommand(args, context) -> Promise<number>`

`ExecutionResult` is `{ ok, succeeded, failed }`, with `succeeded` and `failed`
arrays of `{ action, source, skills, status }`.

- [ ] **Step 1: Write failing batched install execution tests**

```js
const req = (source, skill) => ({
  key: JSON.stringify([source, skill]),
  source,
  skill,
  profiles: ["frontend"],
});

test("batches missing skills by source and continues after failure", async () => {
  const calls = [];
  const result = await executeInstallPlan({
    install: [req("a/repo", "one"), req("a/repo", "two"), req("b/repo", "three")],
    replace: [], conflicts: [], skip: [], extras: [], desiredConflicts: [],
  }, {
    yes: true,
    runMutation: async (args) => {
      calls.push(args);
      return args.includes("a/repo") ? 2 : 0;
    },
    onEvent() {},
  });
  assert.deepEqual(calls, [
    ["skills", "add", "a/repo", "--skill", "one", "--skill", "two", "--yes"],
    ["skills", "add", "b/repo", "--skill", "three", "--yes"],
  ]);
  assert.equal(result.ok, false);
  assert.equal(result.failed.length, 1);
  assert.equal(result.succeeded.length, 1);
});

test("installs independent missing skills while leaving actual conflicts unresolved", async () => {
  const calls = [];
  const result = await executeInstallPlan({
    install: [req("a/repo", "missing")],
    replace: [],
    conflicts: [req("a/repo", "mismatch")],
    skip: [], extras: [], desiredConflicts: [],
  }, {
    yes: true,
    runMutation: async (args) => { calls.push(args); return 0; },
    onEvent() {},
  });
  assert.equal(calls.length, 1);
  assert.equal(result.succeeded.length, 1);
  assert.equal(result.ok, false);
});
```

- [ ] **Step 2: Write failing status and install orchestration tests**

```js
const FRONTEND_PROFILES = {
  version: 1,
  profiles: [{
    name: "frontend",
    sources: [{ source: "a/repo", skills: ["frontend-design"] }],
  }],
};

const actualSkill = (name, source) => ({
  name,
  path: `/repo/.agents/skills/${name}`,
  agents: ["Codex"],
  source,
  provenance: source ? "tracked" : "untracked",
});

function lifecycleHarness({
  linkedProfiles = ["frontend"],
  installed = new Map(),
  selectedProfiles = [],
  saveLinks = false,
  execution = { ok: true, succeeded: [], failed: [] },
} = {}) {
  const root = "/repo";
  let stateCalls = 0;
  let writtenProjects;
  let stderr = "";
  const projects = linkedProfiles.length
    ? { version: 1, projects: [{ root, profiles: linkedProfiles }] }
    : { version: 1, projects: [] };
  const context = {
    paths: { projectsFile: "/config/projects.json" },
    config: { profiles: FRONTEND_PROFILES, projects },
    stdin: { isTTY: true },
    stdout: { isTTY: true, write() {} },
    stderr: { write: (value) => { stderr += value; } },
    ui: {
      error: (message) => { stderr += `${message}\n`; },
      warn() {}, status() {}, installPlan() {}, executionSummary() {},
    },
    resolveProjectRoot: () => root,
    loadInstalledState: async () => { stateCalls += 1; return installed; },
    selectProfiles: async () => ({ type: "submit", selected: selectedProfiles }),
    selectSkills: async (items) => ({ type: "submit", selected: items.map((item) => item.key) }),
    confirmSaveLinks: async () => saveLinks,
    confirm: async () => true,
    executeInstallPlan: async () => execution,
    writeProjects: (_paths, _profiles, document) => { writtenProjects = document; },
  };
  return {
    context,
    stderr: () => stderr,
    get stateCalls() { return stateCalls; },
    get writtenProjects() { return writtenProjects; },
  };
}

test("status exits one for missing and zero for extras alone", async () => {
  const missing = lifecycleHarness({ installed: new Map() });
  assert.equal(await runStatusCommand([], missing.context), 1);
  const extras = lifecycleHarness({ installed: new Map([
    ["frontend-design", actualSkill("frontend-design", "a/repo")],
    ["other", actualSkill("other", "x/repo")],
  ]) });
  assert.equal(await runStatusCommand(["frontend"], extras.context), 0);
});

test("install --yes without links or explicit profiles fails before state discovery", async () => {
  const harness = lifecycleHarness({ linkedProfiles: [] });
  assert.equal(await runInstallCommand(["--yes"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
  assert.match(harness.stderr(), /project link|name profiles/i);
});

test("install rejects the unsupported all-profiles flag before state discovery", async () => {
  const harness = lifecycleHarness();
  assert.equal(await runInstallCommand(["--all-profiles"], harness.context), 1);
  assert.equal(harness.stateCalls, 0);
});

test("interactive one-time install writes links only after complete success", async () => {
  const harness = lifecycleHarness({
    linkedProfiles: [], selectedProfiles: ["frontend"], saveLinks: true,
    execution: { ok: true, succeeded: [], failed: [] },
  });
  assert.equal(await runInstallCommand([], harness.context), 0);
  assert.deepEqual(harness.writtenProjects.projects[0].profiles, ["frontend"]);
});

test("dry-run renders a plan without execution or link writes", async () => {
  const harness = lifecycleHarness({ linkedProfiles: ["frontend"], installed: new Map() });
  let executions = 0;
  harness.context.executeInstallPlan = async () => { executions += 1; throw new Error("must not execute"); };
  assert.equal(await runInstallCommand(["--dry-run"], harness.context), 0);
  assert.equal(executions, 0);
  assert.equal(harness.writtenProjects, undefined);
});
```

- [ ] **Step 3: Run operation and lifecycle tests and verify missing modules**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/operations.test.mjs mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 4: Implement install execution**

```js
function groupBySource(requirements) {
  const groups = new Map();
  for (const item of requirements) {
    const skills = groups.get(item.source) ?? [];
    skills.push(item.skill);
    groups.set(item.source, skills);
  }
  return [...groups].map(([source, skills]) => ({ source, skills }));
}

export async function executeInstallPlan(plan, {
  yes = false,
  runMutation = (args) => runSkillsMutation(args),
  onEvent = () => {},
} = {}) {
  const succeeded = [];
  const failed = [];
  for (const replacement of plan.replace) {
    const removeArgs = ["skills", "remove", replacement.skill, "--yes"];
    const removeStatus = await runMutation(removeArgs);
    onEvent({ action: "remove-for-replace", requirement: replacement, status: removeStatus });
    if (removeStatus !== 0) {
      failed.push({ action: "replace", source: replacement.source, skills: [replacement.skill], status: removeStatus });
    }
  }
  const eligible = [
    ...plan.install,
    ...plan.replace.filter((item) => !failed.some((failure) => failure.skills.includes(item.skill))),
  ];
  for (const batch of groupBySource(eligible)) {
    const args = ["skills", "add", batch.source];
    for (const skill of batch.skills) args.push("--skill", skill);
    if (yes) args.push("--yes");
    const status = await runMutation(args);
    const record = { action: "install", source: batch.source, skills: batch.skills, status };
    (status === 0 ? succeeded : failed).push(record);
    onEvent(record);
  }
  return {
    ok: failed.length === 0 && plan.conflicts.length === 0,
    succeeded,
    failed,
  };
}
```

Keep grouping deterministic by first requirement appearance. Never invoke a
mutation when the plan has desired-source conflicts. Actual mismatch or
untracked conflicts remain skipped while independent missing skills are still
installed; they make `result.ok` false so links are not added and the command
exits `1`.

- [ ] **Step 5: Implement status and install command orchestration**

Use one `resolveLifecycleProfiles(args, context)` helper that returns explicit
profile names when present or current linked names otherwise. For install only,
the helper may prompt in a TTY when no links exist and `--yes` is absent.

`runInstallCommand` order must be:

```text
parse/validate flags
resolve project root and profiles
merge requirements and reject empty/conflicting desired state
load actual state
classify status and build plan
optional temporary skill selector
render plan and confirm unless --yes/--dry-run
execute unless --dry-run
write newly chosen links only after result.ok
render aggregate result and return 0/1
```

`runStatusCommand` stops after rendering classification and returns the status
exit contract from the design.

- [ ] **Step 6: Add status/install UI renderers**

Add methods with these inputs:

```js
ui.status({ projectRoot, profileNames, status });
ui.installPlan({ projectRoot, profileNames, plan, dryRun });
ui.executionSummary(result);
ui.confirm(message); // injected prompt boundary, not raw stdin parsing in UI
```

Tests must assert source mismatch, untracked, extra, contributing profiles,
dry-run label, and exact retry batches after failure.

- [ ] **Step 7: Run lifecycle, planner, adapter, and state tests**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/operations.test.mjs mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/planner.test.mjs mac_scripts/functions/skills-manager/__tests__/installed-state.test.mjs mac_scripts/functions/skills-manager/__tests__/skills-cli.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: all tests pass.

- [ ] **Step 8: Commit status and install**

```bash
git add mac_scripts/functions/skills-manager/operations.mjs mac_scripts/functions/skills-manager/lifecycle-commands.mjs mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/operations.test.mjs mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git commit -m "feat(skm): add profile status and install"
```

---

### Task 10: Safe Profile Uninstall

**Files:**

- Modify: `mac_scripts/functions/skills-manager/operations.mjs`
- Modify: `mac_scripts/functions/skills-manager/lifecycle-commands.mjs`
- Modify: `mac_scripts/functions/skills-manager/ui.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/operations.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/ui.test.mjs`

**Interfaces:**

- Consumes: uninstall plans from Task 7 and link writes from Task 3.
- Produces the already declared `executeUninstallPlan` and
  `runUninstallCommand` interfaces from Task 9.

- [ ] **Step 1: Write failing execution tests for remove, retain, and failure**

```js
test("uninstall removes only eligible names in one upstream call", async () => {
  const calls = [];
  const result = await executeUninstallPlan({
    remove: [req("a/repo", "one"), req("b/repo", "two")],
    retain: [req("a/repo", "shared")],
    absent: [], conflicts: [], unlinkProfiles: ["frontend"], desiredConflicts: [],
  }, {
    yes: true,
    runMutation: async (args) => { calls.push(args); return 0; },
    onEvent() {},
  });
  assert.deepEqual(calls, [["skills", "remove", "one", "two", "--yes"]]);
  assert.equal(result.ok, true);
});

test("uninstall removes eligible names but remains unsuccessful with skipped conflicts", async () => {
  const calls = [];
  const result = await executeUninstallPlan({
    remove: [req("a/repo", "one")],
    retain: [], absent: [],
    conflicts: [req("a/repo", "unknown")],
    unlinkProfiles: ["frontend"], desiredConflicts: [],
  }, {
    yes: true,
    runMutation: async (args) => { calls.push(args); return 0; },
    onEvent() {},
  });
  assert.equal(calls.length, 1);
  assert.equal(result.ok, false);
});

test("uninstall failure prevents link removal", async () => {
  const harness = makeUninstallHarness({ executionOk: false, linkedProfiles: ["frontend"] });
  assert.equal(await runUninstallCommand(["frontend", "--yes"], harness.context), 1);
  assert.equal(harness.projectWrites, 0);
});
```

- [ ] **Step 2: Write failing shared, empty, one-time, and keep-link tests**

```js
const UNINSTALL_PROFILES = {
  version: 1,
  profiles: [
    {
      name: "frontend",
      sources: [{ source: "a/repo", skills: ["frontend-design", "code-review"] }],
    },
    {
      name: "quality",
      sources: [{ source: "a/repo", skills: ["code-review"] }],
    },
    { name: "empty", sources: [{ source: "a/repo", skills: [] }] },
  ],
};

function makeUninstallHarness({
  linkedProfiles = ["frontend", "quality"],
  executionOk = true,
} = {}) {
  const root = "/repo";
  const mutationCalls = [];
  let capturedPlan;
  let writtenProjects;
  let projectWrites = 0;
  let output = "";
  const context = {
    paths: { projectsFile: "/config/projects.json" },
    config: {
      profiles: UNINSTALL_PROFILES,
      projects: linkedProfiles.length
        ? { version: 1, projects: [{ root, profiles: linkedProfiles }] }
        : { version: 1, projects: [] },
    },
    stdin: { isTTY: true },
    stdout: { isTTY: true, write: (value) => { output += value; } },
    stderr: { write() {} },
    ui: {
      error() {}, warn() {}, uninstallPlan: (plan) => { capturedPlan = plan; },
      executionSummary() {},
    },
    resolveProjectRoot: () => root,
    loadInstalledState: async () => new Map([
      ["frontend-design", actualSkill("frontend-design", "a/repo")],
      ["code-review", actualSkill("code-review", "a/repo")],
    ]),
    confirm: async () => true,
    executeUninstallPlan: async (plan) => {
      capturedPlan = plan;
      mutationCalls.push(plan.remove.map((item) => item.skill));
      return executionOk
        ? { ok: true, succeeded: [], failed: [] }
        : { ok: false, succeeded: [], failed: [{ action: "uninstall", status: 4 }] };
    },
    writeProjects: (_paths, _profiles, document) => {
      projectWrites += 1;
      writtenProjects = document;
    },
  };
  return {
    context,
    mutationCalls,
    stdout: () => output,
    get capturedPlan() { return capturedPlan; },
    get writtenProjects() { return writtenProjects; },
    get projectWrites() { return projectWrites; },
    get removedNames() { return capturedPlan?.remove.map((item) => item.skill) ?? []; },
  };
}

test("retains a skill required by a remaining linked profile", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["frontend", "quality"] });
  await runUninstallCommand(["frontend", "--yes"], harness.context);
  assert.deepEqual(harness.removedNames, ["frontend-design"]);
  assert.deepEqual(harness.writtenProjects.projects[0].profiles, ["quality"]);
  assert.deepEqual(harness.capturedPlan.retain[0].profiles, ["quality"]);
});

test("empty linked profile unlinks without upstream removal", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["empty"] });
  assert.equal(await runUninstallCommand(["empty", "--yes"], harness.context), 0);
  assert.deepEqual(harness.mutationCalls, [[]]);
  assert.deepEqual(harness.writtenProjects.projects, []);
});

test("keep-link leaves the selected profile linked", async () => {
  const harness = makeUninstallHarness({ linkedProfiles: ["frontend"] });
  assert.equal(await runUninstallCommand(["frontend", "--yes", "--keep-link"], harness.context), 0);
  assert.equal(harness.projectWrites, 0);
});
```

- [ ] **Step 3: Run focused uninstall tests and observe failures**

Run:

```bash
node --test --test-name-pattern='uninstall|retains|empty linked|keep-link' mac_scripts/functions/skills-manager/__tests__/operations.test.mjs mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs
```

Expected: FAIL because uninstall execution and link sequencing are incomplete.

- [ ] **Step 4: Implement uninstall execution**

```js
export async function executeUninstallPlan(plan, {
  yes = false,
  runMutation = (args) => runSkillsMutation(args),
  onEvent = () => {},
} = {}) {
  if (plan.desiredConflicts.length) {
    return { ok: false, succeeded: [], failed: [] };
  }
  const names = plan.remove.map((item) => item.skill);
  if (names.length === 0) {
    return { ok: plan.conflicts.length === 0, succeeded: [], failed: [] };
  }
  const args = ["skills", "remove", ...names];
  if (yes) args.push("--yes");
  const status = await runMutation(args);
  const record = { action: "uninstall", source: null, skills: names, status };
  onEvent(record);
  return {
    ok: status === 0 && plan.conflicts.length === 0,
    succeeded: status === 0 ? [record] : [],
    failed: status === 0 ? [] : [record],
  };
}
```

This executes eligible removals even when unrelated mismatch or untracked
conflicts are skipped. Remaining conflicts keep `ok` false, so project links
are not removed and the command exits `1`.

- [ ] **Step 5: Implement uninstall command ordering**

`runUninstallCommand` must:

```text
parse --yes/--force/--dry-run/--keep-link
resolve explicit profiles or all linked profiles
compute selected and remaining linked requirements
reject desired-source conflict
load actual state and build uninstall plan
render retain/remove/absent/conflict/unlink sections
confirm unless --yes/--dry-run
execute unless --dry-run
unlink only plan.unlinkProfiles and only when result.ok and !keepLink
return 0 for an empty linked-profile unlink or empty unlinked no-op
```

An explicitly named unlinked profile is included in `selected` but never in
`unlinkProfiles`. Remaining requirements always include every currently linked
profile not selected for unlink.

- [ ] **Step 6: Add uninstall UI coverage**

`ui.uninstallPlan` must display:

```text
Remove
Keep — required by quality
Already absent
Conflict
Unlink
```

Assert `--force` conflict warnings and `--keep-link` omission of the Unlink
section.

- [ ] **Step 7: Run the complete lifecycle slice**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/operations.test.mjs mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/planner.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
```

Expected: all tests pass.

- [ ] **Step 8: Commit safe uninstall**

```bash
git add mac_scripts/functions/skills-manager/operations.mjs mac_scripts/functions/skills-manager/lifecycle-commands.mjs mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/operations.test.mjs mac_scripts/functions/skills-manager/__tests__/lifecycle-commands.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs
git commit -m "feat(skm): safely uninstall profiles"
```

---

### Task 11: New CLI Router and Interactive Dashboard

**Files:**

- Create: `mac_scripts/functions/skills-manager/dashboard.mjs`
- Create: `mac_scripts/functions/skills-manager/__tests__/dashboard.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/selector.mjs`
- Rewrite: `mac_scripts/functions/skills-manager/__tests__/selector.test.mjs`
- Rewrite: `mac_scripts/functions/skills-manager/cli.mjs`
- Modify: `mac_scripts/functions/skills-manager/config.mjs`
- Rewrite: `mac_scripts/functions/skills-manager/__tests__/cli.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/ui.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/pty.test.mjs`
- Modify: `mac_scripts/functions/skills-manager/__tests__/helpers.mjs`
- Delete: `mac_scripts/functions/skills-manager/__tests__/parity.test.mjs`
- Delete: `mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs`
- Delete: `mac_scripts/functions/skills-manager/__tests__/fixtures/golden.json`

**Interfaces:**

- Consumes: all command handlers from Tasks 8–10.
- Produces:
  - generic `runSelector({ items, multiple, input, render })`
  - `runDashboard(context) -> Promise<number>`
  - `runCli(argv, dependencies) -> Promise<number>` with only new routes.

- [ ] **Step 1: Rewrite selector tests around generic items**

```js
test("generic selector returns item values rather than source strings", () => {
  const state = createSelectorState([
    { value: "frontend", label: "Frontend" },
    { value: "review", label: "Review" },
  ]);
  const toggled = reduceSelector(state, "toggle", { multiple: true });
  const submitted = reduceSelector(toggled.state, "submit", { multiple: true });
  assert.deepEqual(submitted.selected, ["frontend"]);
});
```

Keep the existing key decoder, raw-mode cleanup, signal, suspend, and resume
tests. Rename `state.sources` to `state.items` everywhere and render labels
while returning values.

- [ ] **Step 2: Write failing CLI route and removed-command tests**

```js
function cliHarness() {
  const calls = [];
  let stderr = "";
  const handler = (name) => async () => { calls.push(name); return 0; };
  return {
    calls,
    stderr: () => stderr,
    dependencies: {
      stdin: { isTTY: true },
      stdout: { isTTY: true, write() {} },
      stderr: { write: (value) => { stderr += value; } },
      initializeConfig: () => ({ profilesFile: "/profiles", projectsFile: "/projects" }),
      readConfig: () => ({
        profiles: { version: 1, profiles: [{ name: "default", sources: [] }] },
        projects: { version: 1, projects: [] },
      }),
      runProfileCommand: handler("profile"),
      runSourceCommand: handler("source"),
      runSkillCommand: handler("skill"),
      runProjectCommand: handler("project"),
      runStatusCommand: handler("status"),
      runInstallCommand: handler("install"),
      runUninstallCommand: handler("uninstall"),
      runDashboard: handler("dashboard"),
      ui: {
        usage() {},
        error: (message) => { stderr += `${message}\n`; },
      },
    },
  };
}

test("dispatches only the new top-level commands", async () => {
  for (const [argv, handler] of [
    [["profile", "list"], "profile"],
    [["source", "show", "a/repo"], "source"],
    [["skill", "remove", "a", "--source", "a/repo", "-p", "default"], "skill"],
    [["project", "show"], "project"],
    [["status"], "status"],
    [["install", "frontend"], "install"],
    [["uninstall", "frontend"], "uninstall"],
  ]) {
    const harness = cliHarness();
    assert.equal(await runCli(argv, harness.dependencies), 0);
    assert.deepEqual(harness.calls, [handler]);
  }
});

test("rejects removed legacy commands", async () => {
  for (const command of ["ls", "list", "add", "remove", "rm", "show"] ) {
    const harness = cliHarness();
    assert.equal(await runCli([command], harness.dependencies), 1);
    assert.match(harness.stderr(), new RegExp(`Unknown command: ${command}`));
    assert.deepEqual(harness.calls, []);
  }
});
```

- [ ] **Step 3: Write failing linked and unlinked dashboard tests**

```js
function dashboardHarness({
  linkedProfiles = ["frontend"],
  selectedAction = "exit",
  selectedProfiles = [],
  saveLinks = false,
  installStatus = 0,
} = {}) {
  const root = "/repo";
  const renderedActions = [];
  let writtenProjects;
  const context = {
    config: {
      profiles: {
        version: 1,
        profiles: [{
          name: "frontend",
          sources: [{ source: "a/repo", skills: ["frontend-design"] }],
        }],
      },
      projects: linkedProfiles.length
        ? { version: 1, projects: [{ root, profiles: linkedProfiles }] }
        : { version: 1, projects: [] },
    },
    paths: { projectsFile: "/config/projects.json" },
    resolveProjectRoot: () => root,
    ui: {
      dashboard: ({ actions }) => { renderedActions.push(...actions.map((item) => item.value)); },
      error() {}, warn() {},
    },
    selectAction: async () => ({ type: "submit", selected: [selectedAction] }),
    selectProfiles: async () => ({ type: "submit", selected: selectedProfiles }),
    confirmSaveLinks: async () => saveLinks,
    runInstallCommand: async () => installStatus,
    runStatusCommand: async () => 0,
    runProjectCommand: async () => 0,
    runProfileCommand: async () => 0,
    writeProjects: (_paths, _profiles, document) => { writtenProjects = document; },
  };
  return {
    context,
    renderedActions,
    get writtenProjects() { return writtenProjects; },
  };
}

test("linked dashboard offers install status links and management", async () => {
  const harness = dashboardHarness({ linkedProfiles: ["frontend"] });
  await runDashboard(harness.context);
  assert.deepEqual(harness.renderedActions, [
    "install-linked", "edit-links", "install-once", "status", "manage", "exit",
  ]);
});

test("unlinked dashboard can save links only after successful install", async () => {
  const harness = dashboardHarness({
    linkedProfiles: [],
    selectedAction: "install-linked",
    selectedProfiles: ["frontend"],
    saveLinks: true,
    installStatus: 0,
  });
  assert.equal(await runDashboard(harness.context), 0);
  assert.deepEqual(harness.writtenProjects.projects[0].profiles, ["frontend"]);
});
```

- [ ] **Step 4: Run selector, CLI, and dashboard tests and observe failures**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/selector.test.mjs mac_scripts/functions/skills-manager/__tests__/cli.test.mjs mac_scripts/functions/skills-manager/__tests__/dashboard.test.mjs
```

Expected: FAIL because selector state and routes still implement saved sources.

- [ ] **Step 5: Generalize the selector without changing terminal lifecycle**

Use this state shape:

```js
export function createSelectorState(items) {
  return { items: items.map((item) => ({ ...item })), cursor: 0, selected: new Set() };
}
```

`reduceSelector` returns `state.items[index].value`; UI renderers read
`state.items[index].label` and optional `hint`. Do not change raw mode, decoder,
signal, suspension keepalive, or listener cleanup logic except replacing the
old `sources` property.

- [ ] **Step 6: Implement dashboard orchestration**

`runDashboard` resolves the project and config, renders the linked/unlinked
header, and dispatches one of these stable action values:

```js
[
  { value: "install-linked", label: "Install linked profiles" },
  { value: "edit-links", label: "Link or unlink profiles" },
  { value: "install-once", label: "Install other profiles once" },
  { value: "status", label: "View status" },
  { value: "manage", label: "Manage profiles" },
  { value: "exit", label: "Exit" },
]
```

For an unlinked project, `install-linked` first selects profiles and asks
whether to save links after successful install. Cancellation at any stage
returns `0` without writes.

- [ ] **Step 7: Rewrite `cli.mjs` as new-route dispatch**

The dispatch table is exactly:

```js
const routes = {
  profile: runProfileCommand,
  source: runSourceCommand,
  skill: runSkillCommand,
  project: runProjectCommand,
  status: runStatusCommand,
  install: runInstallCommand,
  uninstall: runUninstallCommand,
};
```

Help aliases remain `help`, `-h`, and `--help`. An absent action requires a TTY
and calls `runDashboard`. All other old top-level actions use the unknown-command
path. Bootstrap and `npx` checks happen only for routes that need them; help and
unknown commands never require `npx`.

- [ ] **Step 8: Rewrite PTY scenarios for the dashboard**

Replace old source-selector expectations with:

```text
q cancels dashboard and restores terminal state
raw Ctrl+C cancels a profile selector and restores terminal state
Enter restores canonical/echo mode before an upstream child inherits stdio
SIGTERM and SIGHUP restore terminal state before termination
Ctrl+Z then fg rerenders the same dashboard and accepts raw q
```

Keep the current `script`, `expect`, `stty -g`, and `stty -a` assertions. Update
the held `npx` expected argv to a new source-show or install command supported
by the new router.

- [ ] **Step 9: Run CLI, dashboard, selector, UI, and PTY tests**

Before running the new-surface suite, delete
`__tests__/parity.test.mjs`, `__tests__/legacy-contract.test.mjs`, and
`__tests__/fixtures/golden.json`; those fixtures assert the intentionally
removed command contract. Remove the transitional `list` option and
`skillsFile` alias from `__tests__/helpers.mjs` and the transitional
`skillsFile` return property from `config.mjs`, then update every remaining test
to use `profiles`, `projects`, and `legacyList` explicitly.

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/cli.test.mjs mac_scripts/functions/skills-manager/__tests__/dashboard.test.mjs mac_scripts/functions/skills-manager/__tests__/selector.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs mac_scripts/functions/skills-manager/__tests__/pty.test.mjs
```

Expected: all tests pass; on supported macOS, PTY tests run without skips.

- [ ] **Step 10: Commit the new command surface and dashboard**

```bash
git add mac_scripts/functions/skills-manager/dashboard.mjs mac_scripts/functions/skills-manager/selector.mjs mac_scripts/functions/skills-manager/cli.mjs mac_scripts/functions/skills-manager/config.mjs mac_scripts/functions/skills-manager/ui.mjs mac_scripts/functions/skills-manager/__tests__/dashboard.test.mjs mac_scripts/functions/skills-manager/__tests__/selector.test.mjs mac_scripts/functions/skills-manager/__tests__/cli.test.mjs mac_scripts/functions/skills-manager/__tests__/ui.test.mjs mac_scripts/functions/skills-manager/__tests__/pty.test.mjs mac_scripts/functions/skills-manager/__tests__/helpers.mjs
git add -u -- mac_scripts/functions/skills-manager/__tests__/parity.test.mjs mac_scripts/functions/skills-manager/__tests__/legacy-contract.test.mjs mac_scripts/functions/skills-manager/__tests__/fixtures/golden.json
git commit -m "feat(skm): add project-aware dashboard"
```

---

### Task 12: Wrapper, Obsolete-Code Removal, Documentation, and Final Verification

**Files:**

- Modify: `mac_scripts/skm`
- Modify: `mac_scripts/functions/skills-manager/__tests__/wrapper.test.mjs`
- Delete: `mac_scripts/functions/skills-manager/sources.mjs`
- Delete: `mac_scripts/functions/skills-manager/__tests__/sources.test.mjs`
- Modify: `docs/commands.md`
- Modify: `docs/setup.md`
- Modify: `docs/skm-js-migration-spec.md`

**Interfaces:**

- Consumes: the complete new CLI.
- Produces: a consistent supported surface with no source-list runtime or legacy
  aliases, updated documentation, and a fully verified Node.js 24 suite.

- [ ] **Step 1: Write failing wrapper expectations**

Update `wrapper.test.mjs` so opaque forwarding uses a supported command:

```js
test("wrapper forwards opaque source-show arguments and child status", (t) => {
  const sandbox = makeSandbox(t);
  const result = spawnSync(WRAPPER, ["source", "show", "owner/repo with space"], {
    env: { ...sandbox.env, SKM_NPX_STATUS: "7" },
    encoding: "utf8",
  });
  assert.equal(result.status, 7);
  assert.equal(
    readFileSync(sandbox.argvLog, "utf8"),
    '["skills","add","owner/repo with space","--list"]\n',
  );
});

test("missing-node error names the SKM config directory", (t) => {
  const sandbox = makeSandbox(t);
  rmSync(sandbox.configDir, { recursive: true });
  const noNodeBin = join(sandbox.root, "no-node-bin");
  mkdirSync(noNodeBin);
  symlinkSync("/usr/bin/dirname", join(noNodeBin, "dirname"));
  symlinkSync("/bin/mkdir", join(noNodeBin, "mkdir"));
  const result = spawnSync("/bin/zsh", [WRAPPER, "--help"], {
    env: { ...sandbox.env, PATH: noNodeBin },
    encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /node is required to manage .*\/skm/);
  assert.doesNotMatch(result.stderr, /list\.json/);
  assert.equal(existsSync(sandbox.configDir), true);
});
```

- [ ] **Step 2: Run wrapper tests and observe the stale message/route failure**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/wrapper.test.mjs
```

Expected: FAIL until the wrapper message and new CLI source-show route align.

- [ ] **Step 3: Update the wrapper message without changing forwarding**

Replace only the stale file-specific variable/message:

```zsh
SKM_CONFIG_DIR="$CONFIG_DIR/skm"
if ! command -v node >/dev/null 2>&1; then
  print -P "%F{red}❌ node is required to manage ${SKM_CONFIG_DIR}%f" >&2
  exit 1
fi
```

Keep `exec node "$SCRIPT_DIR/functions/skills-manager/cli.mjs" "$@"` unchanged.

- [ ] **Step 4: Remove obsolete source-list code and tests**

Delete `sources.mjs` and `__tests__/sources.test.mjs`. Then run:

```bash
rg -n 'from "\.\/sources\.mjs"|from "\.\.\/sources\.mjs"|list-available|JavaScript matches legacy golden' mac_scripts/functions/skills-manager
```

Expected: no output.

Do not delete or modify `list.json.example`; migration reads only a user's
existing `<config>/skm/list.json`.

- [ ] **Step 5: Replace the SKM command documentation**

In `docs/commands.md`, replace the SKM section with this supported-command
outline and examples:

````markdown
## skm

Profile-based project skill manager. Configuration lives in
`$CLOUD_UTILS_CONFIG_DIR/skm/profiles.json` and `projects.json`.

```bash
skm
skm profile list
skm profile create frontend-project
skm source add vercel-labs/agent-skills --profile frontend-project
skm project link frontend-project code-review
skm status
skm install
skm install frontend-project --yes
skm uninstall frontend-project
skm uninstall frontend-project --keep-link
```

| Family | Supported commands |
| --- | --- |
| Profile | `profile list`, `show`, `create`, `rename`, `remove [--force]` |
| Source | `source add`, `edit`, `remove`, `show` |
| Skill | `skill add`, `remove` |
| Project | `project link`, `unlink`, `show`, `list`, `remove` |
| Lifecycle | `status`, `install`, `uninstall` |

| Flag | Meaning |
| --- | --- |
| `-p`, `--profile` | Select the profile changed by a source or skill command |
| `--skill` | Select an explicit source skill; repeat for multiple skills |
| `--all` | Snapshot every currently discovered skill into one profile source |
| `--no-skills` | Save a source with an empty skill selection |
| `--yes` | Skip SKM and upstream confirmation prompts |
| `--force` | Permit explicit mismatch or untracked replacement/removal |
| `--dry-run` | Render a lifecycle plan without mutation |
| `--keep-link` | Uninstall files without unlinking the selected profile |
````

- [ ] **Step 6: Update setup paths and mark historical migration docs**

In `docs/setup.md`, change the SKM layout to:

```text
└── skm/
    ├── profiles.json
    └── projects.json
```

Update the config table to list both files and state that existing `list.json`
is migrated once and retained unchanged.

At the top of `docs/skm-js-migration-spec.md`, add:

```markdown
> **Historical migration contract:** This document describes the completed
> Zsh-to-JavaScript compatibility migration. The current profile/project
> redesign is specified in
> [`2026-07-18-skm-profile-project-management-design.md`](superpowers/specs/2026-07-18-skm-profile-project-management-design.md).
```

- [ ] **Step 7: Run the complete suite on the default Node runtime**

Run:

```bash
node --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
```

Expected: exit `0`; every supported test passes and macOS PTY tests are not
skipped on the supported environment.

- [ ] **Step 8: Run the complete suite on Node.js 24**

Run:

```bash
env npm_config_cache=/private/tmp/cloud-utils-skm-node24-npm-cache npm_config_update_notifier=false npx -y node@24 --version
env npm_config_cache=/private/tmp/cloud-utils-skm-node24-npm-cache npm_config_update_notifier=false npx -y node@24 --test mac_scripts/functions/skills-manager/__tests__/*.test.mjs
```

Expected: version begins with `v24.` and the full suite exits `0` with no test
failures.

- [ ] **Step 9: Audit the supported surface and task diff**

Run:

```bash
! rg -n 'skm (ls|list|add|remove|rm)( |$)|list-available' docs/commands.md docs/setup.md mac_scripts/functions/skills-manager/ui.mjs
rg -n 'skm (profile|source|skill|project|status|install|uninstall)' docs/commands.md
git diff --check -- mac_scripts/skm mac_scripts/functions/skills-manager docs/commands.md docs/setup.md docs/skm-js-migration-spec.md
git status --short
```

Expected:

- no removed alias is documented or rendered as supported;
- every new command family appears in `docs/commands.md`;
- no whitespace errors;
- only intended SKM implementation, test, wrapper, and documentation paths are
  part of this implementation, while the unrelated working-tree changes named
  in Global Constraints remain untouched.

- [ ] **Step 10: Commit cleanup and documentation**

```bash
git add mac_scripts/skm mac_scripts/functions/skills-manager/__tests__/wrapper.test.mjs docs/commands.md docs/setup.md docs/skm-js-migration-spec.md
git add -u -- mac_scripts/functions/skills-manager/sources.mjs mac_scripts/functions/skills-manager/__tests__/sources.test.mjs
git diff --cached --name-only
git commit -m "docs(skm): document profile workflows"
```

Before committing, confirm the staged file list does not contain
`mac_init/alias`, `mac_scripts/functions/skills-manager/list.json.example`,
`.agents/`, or `skills-lock.json`.

---

## Final Implementation Review Gate

After Task 12 and before merging or handing off the branch:

- [ ] Compare every acceptance criterion in
  `docs/superpowers/specs/2026-07-18-skm-profile-project-management-design.md`
  against at least one named automated test. Add a missing test before
  continuing.
- [ ] Run `git log --format='%s' 7a14a2f..HEAD` and confirm it contains one
  reviewable commit subject for every completed task in this plan.
- [ ] Inspect `git diff 7a14a2f...HEAD --stat` and
  `git diff 7a14a2f...HEAD` for
  accidental changes, credential leakage, stale aliases, and duplicated domain
  logic.
- [ ] Run the Node.js 24 full-suite command from Task 12 again after the final
  review, not from cached output.
- [ ] Run `git diff --check 7a14a2f...HEAD` and confirm no whitespace errors.
- [ ] Use `superpowers:requesting-code-review` for a fresh spec-compliance and
  code-quality review before integration.
