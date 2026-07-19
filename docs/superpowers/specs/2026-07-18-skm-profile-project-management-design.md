# SKM Profile and Project Management Design

## 1. Summary

Refactor `skm` from a saved-source launcher into a profile-based skill manager.
Profiles are reusable installation recipes. Each profile contains one or more
sources and an explicit snapshot of the skills selected from each source.
Projects can be linked to zero, one, or multiple profiles through central SKM
configuration stored outside the project.

The refactored CLI must:

- create and manage reusable profiles;
- discover and save selected skills when a source is added to a profile;
- link profiles to the current project;
- compare profile requirements with skills installed in the current project;
- install only missing skills and skip skills already installed from the
  expected source;
- safely handle source mismatches and skills with unknown provenance;
- uninstall profiles without removing skills still required by other linked
  profiles; and
- provide an interactive dashboard when `skm` is run without arguments.

This is an intentional command and data-model redesign. Legacy command aliases
are not retained.

## 2. Goals

1. Make a profile the only persisted unit of desired installation.
2. Let one profile contain multiple sources and multiple skills per source.
3. Let the same source appear in multiple profiles with different skill sets.
4. Let a source be saved with no selected skills.
5. Require every persisted skill selection to belong to a profile.
6. Let a project link to multiple reusable profiles without adding SKM config
   files to that project.
7. Make install, status, and uninstall operations idempotent and safe to rerun.
8. Treat a skill's logical identity as the canonical source plus skill name.
9. Avoid installing newly published upstream skills unless the user explicitly
   adds them to a profile.
10. Keep storage human-readable and preserve atomic single-file writes.
11. Keep upstream CLI integration isolated behind a dedicated adapter.

## 3. Non-goals

- Installing or managing global skills.
- Pinning source revisions, tags, hashes, or skill versions.
- Updating already installed skills to newer upstream revisions.
- Committing project-local SKM configuration.
- Automatically installing every profile.
- Providing an `--all-profiles` install mode.
- Automatically removing installed skills that are not required by the
  selected profiles.
- Treating the upstream lock file as SKM's desired-state manifest.
- Reimplementing the upstream source resolver or skill installer.
- Retaining `skm ls`, `skm list`, `skm add`, `skm remove`, `skm rm`, or the old
  `skm show` interface as aliases.
- Supporting concurrent SKM configuration writers. One mutating SKM process is
  expected at a time.
- Adding production dependencies; the implementation continues to use Node.js
  24 and built-in modules.

## 4. Terminology

- **Profile**: a reusable installation recipe containing sources and selected
  skills.
- **Source**: an upstream repository, URL, or local path understood by
  `npx skills add`.
- **Project**: the canonical Git root of the current working directory, or the
  canonical current directory when outside Git.
- **Linked profile**: a profile associated with a project in `projects.json`.
- **Required skill**: a `{source, skill}` pair selected by one or more profiles
  participating in the current operation.
- **Actual skill**: a project skill reported as installed by the upstream CLI.
- **Provenance**: the source recorded for an installed skill in a usable
  upstream lock entry.

## 5. Storage

### 5.1 Paths

SKM stores its new data under the existing configuration root:

```text
<config-directory>/skm/profiles.json
<config-directory>/skm/projects.json
```

The configuration directory continues to be resolved from a non-empty
`CLOUD_UTILS_CONFIG_DIR`, falling back to the existing cloud-utils default.

The legacy source file remains at:

```text
<config-directory>/skm/list.json
```

It is used only for first-run migration and is never modified or deleted by the
new implementation.

### 5.2 Profile schema

```json
{
  "version": 1,
  "profiles": [
    {
      "name": "frontend-project",
      "sources": [
        {
          "source": "vercel-labs/agent-skills",
          "skills": [
            "frontend-design",
            "react-best-practices"
          ]
        }
      ]
    },
    {
      "name": "code-review",
      "sources": [
        {
          "source": "another/repository",
          "skills": []
        }
      ]
    }
  ]
}
```

Profile invariants:

- `version` is exactly `1`.
- `profiles` is a non-empty array.
- Profile names are non-empty and unique by exact string comparison.
- `sources` is an array and may be empty.
- A canonical source appears at most once in one profile.
- The same canonical source may appear in multiple profiles.
- `skills` is an array of non-empty strings.
- Skill names are unique within one source entry.
- Within one profile, the same skill name cannot be selected from two different
  canonical sources because the project installation namespace is keyed by
  skill name.
- `skills: []` saves the source without contributing anything to install,
  status, or uninstall plans.
- Wildcards are never persisted. A select-all operation expands to the exact
  skill names discovered at that time.
- A profile may contain no selected skills. An install selection whose merged
  requirement set is empty fails clearly. Uninstall may still unlink an empty
  linked profile without removing project files.

Profiles and sources are sorted by name for stable diffs. Skill arrays preserve
upstream display order when first saved; subsequent explicit additions append
in request order without duplicating an existing name.

### 5.3 Project schema

```json
{
  "version": 1,
  "projects": [
    {
      "root": "/Users/example/Documents/my-app",
      "profiles": [
        "frontend-project",
        "code-review"
      ]
    }
  ]
}
```

Project invariants:

- `version` is exactly `1`.
- `projects` is an array and may be empty.
- Every `root` is an absolute canonical path and is unique.
- Every profile name in a project exists in `profiles.json`.
- A profile name appears at most once in one project entry.
- A project with no linked profiles has no entry in `projects.json`.
- Projects are sorted by root. Profile names inside a project preserve link
  order.

### 5.4 Bootstrap and migration

On a fresh configuration with neither new file present, SKM creates:

```json
{
  "version": 1,
  "profiles": [
    {
      "name": "default",
      "sources": []
    }
  ]
}
```

and:

```json
{
  "version": 1,
  "projects": []
}
```

The name `default` has no reserved behavior. It can be renamed, and it can be
removed after another profile exists.

If `profiles.json` is absent and the legacy `list.json` contains either the
canonical source array or the accepted legacy `{ "presets": [...] }` shape,
SKM creates one profile named `default`. Every legacy source is copied into that
profile with `skills: []`. Migration does not contact a source, select skills,
or link the current project.

If `profiles.json` exists and `projects.json` is absent, SKM validates the
profile file and creates an empty project registry. If `projects.json` exists
without `profiles.json`, SKM cannot validate its references and exits with a
recovery message instead of inventing profiles. Existing invalid data is never
silently reset. A read or validation failure identifies the file and prevents
all dependent work.

### 5.5 Writes and cross-file transactions

A mutation affecting one file is written atomically through a same-directory
temporary file followed by rename.

Renaming a linked profile or force-removing a linked profile affects both new
files. SKM treats this as one logical transaction:

1. Read and validate both current documents.
2. Compute and validate both next documents in memory.
3. Create verified backups of both current files and write both
   same-directory next-state temporary files.
4. Write a small transaction journal containing target, backup, and temporary
   paths; checksums; and transaction phase.
5. Rename both next-state temporary files to their targets.
6. Remove the journal and backups after both renames succeed.

At startup, SKM resolves an existing journal before dispatching a command. It
either completes verified pending renames or restores the verified previous
pair. It never invents missing profile or project data. Failure injection tests
must cover every transaction phase.

## 6. Project identity

SKM resolves the current project as follows:

1. Run `git rev-parse --show-toplevel` from the current working directory.
2. If it succeeds, canonicalize that Git root through `realpath` semantics.
3. Otherwise canonicalize the current working directory.

Different checkout or worktree paths are different projects. Moving a project
therefore requires linking its new path and optionally removing the stale old
entry.

Project discovery is read-only. A command does not create a `projects.json`
entry until a link is explicitly saved.

## 7. Source identity

SKM canonicalizes a source before persisting or comparing it:

- GitHub shorthand and equivalent HTTPS or SSH repository-root URLs normalize
  to `owner/repository`.
- A trailing `.git` is removed where it does not alter meaning.
- GitHub URLs containing a ref or subpath preserve that ref and subpath so a
  repository root and a direct skill path are not conflated.
- Generic Git URLs normalize scheme, host, path, and trailing `.git` when this
  can be done without changing the target.
- Local sources normalize to an absolute real path at the time they are added.
- Embedded URL credentials and tokens are never persisted or printed.
- A source that cannot be normalized safely remains an opaque string and uses
  exact comparison.

A source mismatch exists only when reliable provenance canonicalizes to a
different source. Equivalent source spellings do not create a mismatch.

## 8. Command surface

### 8.1 Help

```bash
skm help
skm -h
skm --help
```

Help documents only the new command surface. Removed legacy commands are
reported as unknown commands with a pointer to `skm --help`; they are not
silently reinterpreted.

### 8.2 Profile commands

```bash
skm profile list
skm profile show <profile>
skm profile create <profile>
skm profile rename <old> <new>
skm profile remove <profile>
skm profile remove <profile> --force
```

Behavior:

- `list` shows every profile, source count, selected skill count, and linked
  project count.
- `show` shows sources, selected skills, and linked projects.
- `create` creates an empty profile.
- `rename` updates every project reference in the same logical transaction.
- `remove` refuses to remove the last profile.
- `remove` refuses to remove a linked profile and lists the linked projects.
- `remove --force` unlinks the profile from every project, removes project
  entries that become empty, and then removes the profile. It still cannot
  remove the final profile.
- Removing a profile changes configuration only. It does not uninstall skills
  from any project.

### 8.3 Source commands

```bash
skm source add <source> --profile <profile>
skm source add <source> -p <profile> --skill <skill> [--skill <skill>...]
skm source add <source> -p <profile> --all
skm source add <source> -p <profile> --no-skills
skm source edit <source> --profile <profile>
skm source remove <source> --profile <profile>
skm source show <source>
```

Behavior:

- `source add` without a selection flag discovers available skills and opens a
  multi-select UI. Selecting zero skills is valid and saves the source with an
  empty array.
- `--skill`, `--all`, and `--no-skills` are mutually exclusive selection modes.
- `--no-skills` canonicalizes and saves the source without contacting it.
- Every explicitly named skill is verified against the source before the
  profile is changed.
- `--all` saves the exact discovered names; it never persists a wildcard.
- Adding a canonical source already present in the profile is an error with a
  hint to use `source edit` or `skill add`; `source add` never performs an
  implicit merge.
- `source edit` rediscovers available skills, preselects currently saved names,
  marks saved names no longer found upstream, and lets the user confirm the
  next snapshot. Missing upstream names are retained unless explicitly
  deselected.
- `source remove` removes the complete source entry from one profile only.
- `source show` displays upstream skills without changing configuration and
  does not require the source to be saved.
- If `--profile` is omitted in a TTY, SKM opens a profile selector. In a
  non-interactive environment, omission is a usage error.

### 8.4 Skill commands

```bash
skm skill add <skill...> --source <source> --profile <profile>
skm skill remove <skill...> --source <source> --profile <profile>
```

Behavior:

- These commands modify a profile definition only. They never install or
  uninstall project skills.
- `skill add` requires the source to exist in the profile and verifies every
  requested name upstream before one atomic write.
- `skill remove` requires the source to exist and reports requested names not
  present in the profile.
- A successful remove may leave `skills: []`; the source remains saved.
- Help and result output explicitly state that these commands do not change
  installed project files.

### 8.5 Project commands

```bash
skm project link <profile...>
skm project unlink <profile...>
skm project unlink
skm project show
skm project list
skm project remove [project-path]
```

Behavior:

- `link` associates one or more existing profiles with the current project and
  skips links already present.
- `link` changes configuration only; it does not install profile skills.
- `unlink <profile...>` removes those links from the current project.
- `unlink` without names opens a selector in a TTY and is a usage error without
  a TTY.
- Removing the final link removes the project entry.
- `unlink` changes configuration only; it does not uninstall project skills.
- `show` displays the resolved current project and linked profiles.
- `list` displays all registered project roots and profile links, marking roots
  that no longer exist.
- `remove [project-path]` deletes a stale or unwanted project entry only. When
  the path is omitted, it resolves and removes the current project root, using
  the same project-root resolution as `link`, `unlink`, and `show`. It does not
  modify the project filesystem or uninstall skills.

### 8.6 Status

```bash
skm status
skm status <profile...>
```

- With profile arguments, status compares exactly those profiles.
- Without arguments, status uses the current project's linked profiles.
- Without arguments and without linked profiles, status exits `1` with a hint
  to link or name a profile.
- Status is read-only and does not alter links, profile data, installed skills,
  or lock files.

### 8.7 Install

```bash
skm install
skm install <profile...>
skm install <profile...> --yes
skm install <profile...> --force
skm install <profile...> --dry-run
```

- With profile arguments, install uses exactly those profiles and does not
  change project links.
- Without arguments, install uses linked profiles.
- If no profile is linked, no profile is named, the terminal is interactive,
  and `--yes` is absent, install opens a profile selector and asks whether to
  save the selected links or install once.
- If no profile is linked and either the terminal is non-interactive or
  `--yes` is present, install exits `1` with a hint to run
  `skm project link <profile...>` or name profiles explicitly.
- Interactive install presents the merged skill list, allows temporary
  deselection, displays the final plan, and asks for confirmation.
- `--yes` selects every eligible missing skill and skips SKM and upstream
  confirmation prompts.
- `--force` permits replacement of source-mismatch and untracked same-name
  skills after showing them as destructive replacements.
- `--dry-run` computes and renders the plan without an upstream mutation or a
  `projects.json` write.
- A link selected from the no-profile interactive flow is saved only after the
  complete install succeeds.

### 8.8 Uninstall

```bash
skm uninstall
skm uninstall <profile...>
skm uninstall <profile...> --yes
skm uninstall <profile...> --force
skm uninstall <profile...> --dry-run
skm uninstall <profile...> --keep-link
```

- With profile arguments, uninstall uses exactly those profiles.
- Without arguments, uninstall uses all profiles linked to the current
  project.
- Without arguments and without linked profiles, uninstall exits `1`.
- Uninstall removes only skills contributed by the selected profiles and not
  still required by another linked profile.
- Skills retained for another linked profile are shown with the profiles that
  still require them.
- By default, successfully uninstalled profiles are also unlinked from the
  current project.
- An explicitly selected profile that was not linked can be uninstalled, but
  there is no link to remove.
- `--keep-link` leaves project links unchanged, so a following status reports
  removed required skills as missing.
- `--force` permits removal of source-mismatch and untracked same-name skills.
- `--dry-run` computes and renders the removal, retention, conflict, and unlink
  plan without mutation.
- Links are removed only after all required upstream removals succeed.
- An empty linked profile can be uninstalled to remove its link without any
  upstream removal. Explicitly uninstalling an empty unlinked profile is a
  successful no-op with a warning.

No install or uninstall command accepts `--all-profiles`.

## 9. Interactive dashboard

Running `skm` without arguments opens a dashboard rather than the old source
selector.

For a linked project:

```text
Current project: /path/to/project
Linked profiles: frontend-project, code-review

Install linked profiles
Link or unlink profiles
Install other profiles once
View status
Manage profiles
Exit
```

For an unlinked project, the dashboard leads with selecting one or more
profiles. It then asks whether to save those links or install once, allows
temporary skill deselection, shows the plan, and asks for confirmation.

Every dashboard cancellation restores terminal state and exits without
configuration or project mutations.

## 10. Upstream integration

### 10.1 Installed skills

SKM runs:

```bash
npx skills list --json
```

to obtain actual project skill names, canonical paths, scope, and linked agents.
This structured output is implemented by the upstream list command even though
it is not prominently documented in the README.

SKM separately reads usable upstream lock data for source provenance. The
adapter supports the project-local `skills-lock.json` layout already present in
existing installations and known newer upstream lock locations. A lock entry
is used only when it can be associated with the current project skill without
ambiguity. Missing, invalid, stale, or ambiguous provenance produces an
untracked state rather than a guessed source.

### 10.2 Available skills

SKM runs:

```bash
npx skills add <source> --list
```

to discover available skill names and descriptions.

The current upstream command emits formatted text rather than a documented JSON
contract. `skills-cli.mjs` therefore owns a version-tolerant parser that strips
ANSI codes and returns structured discovery records. Parser fixtures cover
known upstream layouts.

If a future upstream version provides structured discovery output, the adapter
uses it first. If output cannot be parsed confidently, SKM leaves configuration
unchanged, prints the original output, and instructs the user to retry with
explicit `--skill` arguments. It never guesses a skill name.

### 10.3 Installation batching

Missing skills are grouped by canonical source. One upstream add command is run
per source:

```bash
npx skills add <source> \
  --skill <skill-a> \
  --skill <skill-b>
```

`--yes` is forwarded when SKM receives `--yes`. Without it, upstream remains
responsible for agent and install-method prompts.

Source batches execute sequentially. A failed batch does not prevent later
batches from being attempted, and the final result aggregates every success and
failure.

### 10.4 Uninstall delegation

After SKM filters shared, mismatched, and untracked skills, it delegates the
eligible names to the upstream project-scope remove command. SKM never uses the
upstream remove-all mode.

## 11. Status model

For each required `{canonical source, skill name}` pair, SKM computes one state:

| State | Actual presence | Reliable provenance | Meaning |
| --- | --- | --- | --- |
| `installed` | present | expected source | Correctly installed |
| `missing` | absent | any or none | Required skill is not on disk |
| `source mismatch` | present | different source | Same name came from another source |
| `untracked` | present | unavailable | Same name exists but source is unknown |

A stale lock entry without an actual skill is `missing`. Separately, every
actual installed skill name not required by the selected profiles is classified
as `extra`. Extra skills are informational and never cause install or uninstall
mutation.

Status output groups results by state and includes the profiles requiring each
pair. `status` exits `0` only when every required skill is installed from the
expected source. Missing, mismatch, untracked, state-discovery failure, or
invalid configuration exits `1`. Extra skills alone do not fail status.

## 12. Planning and execution

### 12.1 Merge

Selected profiles are expanded to required pairs and deduplicated by canonical
source plus exact skill name. The planner also retains the contributing profile
names for display and shared-skill uninstall decisions.

If selected profiles require the same skill name from different canonical
sources, the merged request has a desired-source conflict. Status, install, and
uninstall report the conflicting profiles and sources and exit `1` without an
upstream mutation. `--force` does not resolve an ambiguous desired state.

### 12.2 Install plan

- `installed`: skip.
- `missing`: eligible to install.
- `source mismatch`: conflict unless `--force`.
- `untracked`: conflict unless `--force`.
- `extra`: display only.

Forced replacement removes the same-name existing skill before adding it from
the expected source. Because external filesystem changes are not transactional,
a successful removal followed by failed installation can leave the skill
missing. The final report must call this out explicitly and provide the exact
retry command.

### 12.3 Uninstall plan

The planner computes requirements from linked profiles that will remain after
the operation. A selected profile's skill is:

- retained when any remaining linked profile requires the same pair;
- eligible for removal when no remaining linked profile requires it and the
  installed source matches;
- a conflict when the actual same-name skill is mismatched or untracked and
  `--force` is absent; or
- ignored when it is already absent.

An explicitly selected unlinked profile does not change the remaining-link set.
It can remove eligible skills, but it cannot remove requirements belonging to
currently linked profiles.

### 12.4 Partial failure

Install and uninstall changes cannot be rolled back reliably across upstream
processes. SKM therefore:

- continues independent source batches after a failure;
- records per-skill and per-source success, failure, skip, retention, and
  conflict results;
- never writes a newly chosen link after partial install failure;
- never removes selected links after partial uninstall failure;
- never modifies `profiles.json` during install or uninstall; and
- prints a final retry-oriented summary.

Rerunning the same command is safe because correctly installed skills are
skipped and already absent uninstall targets are ignored.

### 12.5 Dry run

Dry run performs configuration validation, project resolution, profile merge,
actual-state discovery, provenance lookup, planning, and rendering. It performs
no upstream mutation and no SKM write.

A dry run exits `0` when it produces a valid conflict-free plan, even when the
plan contains changes. It exits `1` for conflicts, invalid input, invalid
configuration, or state-discovery failure.

### 12.6 Operation exit status

Install and uninstall exit `0` when every selected eligible operation succeeds,
including an idempotent run with nothing left to change. They exit `1` for a
desired-source conflict, unresolved mismatch or untracked conflict, upstream
failure, invalid input, invalid configuration, or state-discovery failure.
Interactive cancellation is a successful no-op and exits `0`.

## 13. Error handling and safety

- Unknown commands and invalid argument combinations exit `1` before reading
  state or invoking upstream mutation.
- Mutually exclusive flags produce a usage error.
- Invalid profile or project JSON is never overwritten automatically.
- A failed source discovery or explicit skill verification leaves the complete
  profile file byte-for-byte unchanged.
- A profile rename collision is rejected before either file is written.
- Removing the last profile is always rejected, including with `--force`.
- Conflicting desired sources for one skill name are rejected before upstream
  mutation, including with `--force`.
- Profile removal never implies project uninstall.
- Project removal never touches the project filesystem.
- Mismatch and untracked states are never overwritten or removed without
  `--force`.
- Configuration output redacts credentials found in source input.
- Cancellation is a successful no-op and restores terminal state.
- Spawn errors, signals, and non-zero upstream statuses are converted into
  deterministic SKM results without JavaScript stack traces.

## 14. Implementation structure

```text
mac_scripts/functions/skills-manager/
├── cli.mjs
├── config.mjs
├── profiles.mjs
├── projects.mjs
├── source-id.mjs
├── skills-cli.mjs
├── installed-state.mjs
├── planner.mjs
├── operations.mjs
├── selector.mjs
├── ui.mjs
└── __tests__/
```

- `cli.mjs`: argument parsing and command dispatch only.
- `config.mjs`: path resolution, bootstrap, migration, atomic writes, and
  transaction recovery.
- `profiles.mjs`: profile/source/skill validation and mutations.
- `projects.mjs`: project-root resolution and link mutations.
- `source-id.mjs`: source canonicalization and credential redaction.
- `skills-cli.mjs`: upstream process execution and output adapters.
- `installed-state.mjs`: actual-skill and provenance reconciliation.
- `planner.mjs`: pure merge, status, install, and uninstall planning.
- `operations.mjs`: sequential execution and partial-failure aggregation.
- `selector.mjs`: generic single- and multi-select state and terminal lifecycle.
- `ui.mjs`: help, dashboard, selectors, plans, status, and summaries.

No domain module writes directly to streams or spawns a process. Filesystem,
process, and UI boundaries remain injectable for deterministic tests.

## 15. Implementation sequence

1. **Storage and migration**
   - Add both schemas, validation, bootstrap, migration, atomic writes, and
     transaction recovery.
   - Add canonical project-root resolution.
2. **Profile and project commands**
   - Add profile CRUD, source and skill editing, project links, and source
     identity handling.
3. **State and planning**
   - Add upstream JSON list integration, lock provenance adapters, state
     classification, profile merge, and pure plans.
4. **Execution**
   - Add batched install, safe uninstall, confirmation, `--yes`, `--force`,
     `--dry-run`, `--keep-link`, and partial-failure reporting.
5. **Dashboard and documentation**
   - Generalize the selector, implement dashboard flows, replace help and
     command docs, and remove old command handlers and alias tests.

Each phase must leave the complete supported test suite passing. The command
redesign becomes the supported interface when all phases are complete; there is
no compatibility period for the removed aliases.

## 16. Test strategy

### 16.1 Unit tests

- Profile and project schema validation.
- The invariant that at least one profile always exists.
- Source uniqueness within a profile and allowed duplication across profiles.
- Source canonicalization across shorthand, HTTPS, SSH, `.git`, direct paths,
  generic Git URLs, and local paths.
- Credential redaction.
- Git-root and non-Git project identity.
- Multi-profile merge, pair deduplication, and desired-source conflict
  detection.
- All five status classifications.
- Shared-skill uninstall retention.
- Forced and non-forced mismatch and untracked behavior.
- Empty-profile install rejection and empty-profile unlink through uninstall.
- Profile rename and force-remove project updates.
- Legacy array and legacy `presets` migration.
- Transaction recovery from every journal phase.

### 16.2 Integration tests

- Profile, source, skill, and project commands with temporary config roots.
- Available-skill parsing from captured upstream fixtures.
- Parser uncertainty leaves profiles unchanged.
- Installed-state reconciliation from JSON list and supported lock fixtures.
- Install invokes only eligible missing skills and batches by source.
- `--yes` is forwarded and interactive confirmation is skipped.
- Conflict never invokes destructive upstream work without `--force`.
- Uninstall retains skills required by remaining linked profiles.
- Install links and uninstall unlinks only after complete success.
- Partial upstream failures preserve project links and produce aggregate status.
- Dry run performs no write or upstream mutation.
- Removed legacy commands fail as unknown commands.

### 16.3 UI and PTY tests

- Dashboard for linked and unlinked projects.
- Multi-profile selection and temporary skill deselection.
- Link-on-success prompt for an unlinked project.
- Install and uninstall plan rendering.
- Cancellation, Ctrl+C, suspend, resume, and terminal-state restoration.
- Help, usage errors, colors, and exit statuses.

### 16.4 Verification

- Run the full SKM suite with Node.js 24.
- Run supported macOS PTY tests without skips.
- Run `git diff --check`.
- Scan documentation and tests to ensure removed aliases are not presented as
  supported behavior.
- Verify no production dependency or package-manager metadata was added.

## 17. Acceptance criteria

1. A fresh SKM configuration contains exactly one empty, renameable profile and
   an empty project registry.
2. Existing saved sources migrate into one `default` profile with no selected
   skills and no project links.
3. A user can add a source to a profile and select zero, some, or all currently
   available skills.
4. Select-all persists an explicit snapshot and never auto-adopts later skills.
5. A project can link, unlink, and display multiple profiles.
6. Status distinguishes installed, missing, mismatch, untracked, and extra
   skills using actual presence plus reliable provenance.
7. Install skips correct existing skills, installs only missing selections, and
   requires force for mismatch or untracked replacement.
8. Install supports one or multiple named profiles but no all-profiles mode.
9. Interactive install permits temporary skill deselection without changing a
   profile.
10. Uninstall removes profile links by default, retains shared requirements,
    and requires force for mismatch or untracked removal.
11. Selecting profiles that require the same skill name from different sources
    reports a desired-source conflict and performs no mutation.
12. Failed or partial external operations do not produce incorrect project
    links.
13. Profile rename and force-removal keep `profiles.json` and `projects.json`
    consistent across recoverable write failures.
14. Running `skm` opens the new project-aware dashboard.
15. Removed legacy aliases are rejected rather than mapped to new commands.
16. The full Node.js 24 test suite and supported PTY tests pass.

## 18. Upstream references

- [`vercel-labs/skills` README](https://github.com/vercel-labs/skills/blob/main/README.md)
- [Installed-list JSON implementation](https://github.com/vercel-labs/skills/blob/main/src/list.ts)
- [Available-skill list implementation](https://github.com/vercel-labs/skills/blob/main/src/add.ts)
- [Upstream lock implementation](https://github.com/vercel-labs/skills/blob/main/src/skill-lock.ts)
