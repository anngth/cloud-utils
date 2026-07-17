# SKM JavaScript Migration Specification

> **Historical migration contract:** This document describes the completed
> Zsh-to-JavaScript compatibility migration. The current profile/project
> redesign is specified in
> [`2026-07-18-skm-profile-project-management-design.md`](superpowers/specs/2026-07-18-skm-profile-project-management-design.md).

## 1. Summary

Migrate the `skm` command from `mac_scripts/functions/skills-manager/main.zsh`
to a Node.js CLI while preserving its public commands, persisted data, terminal
UI, and observable behavior.

After the migration:

- `mac_scripts/skm` is a small Zsh compatibility wrapper;
- all command, persistence, rendering, selector, and child-process logic is
  implemented in ECMAScript modules;
- `main.zsh` and all inline `node -e` scripts are removed from `skm`;
- the CLI is testable with Node's built-in test runner.

This is a language migration, not a redesign.

## 2. Scope

### 2.1 Goals

1. Preserve every public command, alias, argument shape, data path, bootstrap
   rule, prerequisite check, exit code, and user-visible message defined here.
2. Preserve the current interactive single-select and multi-select workflows.
3. Continue invoking the upstream operation as `npx skills add`.
4. Replace embedded JavaScript and Zsh business logic with cohesive,
   independently testable JavaScript modules.
5. Keep Node.js 20 as the implementation and CI compatibility baseline.
6. Use only Node.js built-ins in production unless a dependency is separately
   approved.

### 2.2 Non-goals

- Redesigning commands, output, colors, or keyboard controls.
- Changing the upstream `skills` CLI.
- Validating source names or contacting the network during `skm add`.
- Installing a source automatically when it is saved.
- Managing individual skills after a source is handed to `npx skills add`.
- Adding TypeScript, a transpiler, or a bundler.
- Adding runtime color suppression or `NO_COLOR` behavior.
- Serializing concurrent writers. The last successful atomic rename wins.

## 3. Compatibility policy

`main.zsh` is the reference for behavior not explicitly covered by this
specification. The JavaScript implementation should preserve useful public
behavior, but it does not need to reproduce accidental behavior caused by
shell interpolation or ad-hoc delimiter parsing.

The approved migration differences are:

1. The wrapper checks for `node` before JavaScript bootstrap can run. It still
   creates the configuration directory first.
2. Invalid JSON and invalid source-list structures produce one deterministic
   error instead of leaking a JavaScript stack trace or being treated as an
   empty list.
3. Source values are passed directly as JavaScript strings and child-process
   arguments. They are not split on delimiters and are not interpreted by
   Zsh `print -P`.
4. The JavaScript CLI restores terminal state and cleans up its own temporary
   files on more failure and signal paths than the Zsh implementation.

No other product change is part of this migration.

## 4. Runtime and entrypoint

### 4.1 Runtime

- Node.js 20 or newer.
- ECMAScript modules using `.mjs` files.
- The JavaScript CLI does not implement a separate Node version gate.
- Supported platform: macOS terminal with Zsh, matching current usage.

### 4.2 Wrapper

`mac_scripts/skm` remains executable and performs only the preflight that
cannot be performed after Node starts:

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

The wrapper must preserve argument boundaries, including arguments containing
spaces. `exec` makes the JavaScript CLI's status and signal behavior observable
at the wrapper boundary.

## 5. Commands

The following invocations remain supported:

```text
skm
skm ls
skm list
skm show
skm show <source>
skm add <source...>
skm remove <source...>
skm rm <source...>
skm help
skm -h
skm --help
```

`list-available` remains an undocumented alias for `show`.

An unknown command writes the following error and exits `1`:

```text
❌ Unknown command: <action>
Use 'skm --help' for usage information
```

## 6. Configuration and bootstrap

### 6.1 Configuration directory

Resolve the configuration directory with the same semantics as
`cloud_utils_config_dir()`:

1. Use a non-empty `CLOUD_UTILS_CONFIG_DIR`.
2. Otherwise use:

   ```text
   ${HOME}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils
   ```

3. Do not substitute `os.homedir()` when `HOME` is empty or absent. The path
   then begins with `/Library/...`, matching the current interpolation.
4. Create the directory recursively. Failure writes the following to stderr
   and exits `1`:

   ```text
   ❌ Could not create config directory.
   ```

### 6.2 Data path

The primary data file remains:

```text
<config-directory>/skm/list.json
```

### 6.3 Bootstrap order

JavaScript bootstrap runs before command dispatch, including for help and
unknown commands. It performs these attempts in order:

1. If the primary file exists, stop.
2. Try repository-local legacy `functions/skills-manager/list.json`.
3. Otherwise copy `functions/skills-manager/list.json.example`.
4. If the primary file is still absent, try legacy
   `<config-directory>/skills/list.json`.
5. Otherwise try `list.json.example` again.

Copying a legacy file writes this cyan migration message to stderr:

```text
◇ Migrated list.json → <destination>
```

Copying the example is silent. Bootstrap never overwrites the primary file and
never deletes a legacy file. Individual bootstrap failures are ignored; a
command that requires the list normalizes a still-missing file later.

## 7. Source-list model

### 7.1 Accepted input

The canonical form is an array:

```json
[
  {
    "source": "owner/repository"
  }
]
```

The legacy object form is also accepted:

```json
{
  "presets": [
    {
      "source": "owner/repository"
    }
  ]
}
```

Every item must be a non-null object with a string-valued `source`. Additional
item properties are accepted and preserved. A modifying command normalizes the
top level to the canonical array and discards additional legacy top-level
properties.

### 7.2 Source values

A source is an opaque JavaScript string:

- comparison uses exact `===` semantics;
- no repository-name validation or normalization is added;
- spaces, Unicode, `|`, `%`, and other printable characters round-trip without
  shell parsing;
- NUL is unsupported because it cannot occur in an operating-system argument;
- existing terminal control characters are outside the supported input domain.

When a command needs saved sources but the valid list is empty, it writes the
following warning to stderr and exits `1`:

```text
⚠️  No sources saved. Add one with: skm add <source>
```

### 7.3 Missing and invalid files

Commands that need the list first create a missing file as `[]` followed by a
newline. An existing invalid file is never reset or repaired automatically.

The list is invalid when:

- it is not valid JSON;
- its top level is neither an array nor an object with an array-valued
  `presets`; or
- an item does not satisfy the item contract in Section 7.1.

A command that needs invalid data writes the following to stderr and exits `1`:

```text
❌ Could not read source list: <skills-file-path>
```

It must not print a stack trace, rewrite the file, or invoke `npx`. A modifying
command leaves the invalid file byte-for-byte unchanged. Help, unknown commands,
and `show <source>` do not read the list.

### 7.4 Writes

`add` and `remove` write atomically:

1. Serialize the complete next array with
   `JSON.stringify(items, null, 2) + "\n"`.
2. Write `<skills-file>.<pid>.tmp` in the same directory.
3. Rename the temporary file over the primary file.

On serialization, write, or rename failure, exit `1`, leave the original usable,
and make a best-effort attempt to remove only the current process's temporary
file.

## 8. Tool checks and startup order

The wrapper requires `node`. Commands that currently call
`_skm_require_tools` require `npx`, even when they only manipulate or display
the local list.

Missing `npx` writes this message to stderr and exits `1`:

```text
❌ npx is required to run 'npx skills add'
```

After configuration and bootstrap, processing order is:

| Invocation | Required order |
| --- | --- |
| `skm` | `npx` check → normalize → TTY check → read list → selector |
| `skm ls`, `skm list` | `npx` check → normalize → read list → render |
| `skm show` | `npx` check → normalize → TTY check → read list → selector |
| `skm show <source>` | `npx` check → spawn; do not read or normalize the list |
| `skm show <a> <b...>` | usage error only |
| `skm add` | usage error only |
| `skm add <source...>` | `npx` check → normalize → read → modify → render |
| `skm remove`, `skm rm` | usage error only |
| `skm remove <source...>`, `skm rm <source...>` | `npx` check → normalize → read → modify → render |
| Help aliases | render help only |
| Unknown command | render error only |

Usage validation therefore precedes the `npx` check for invalid `show`, `add`,
and `remove` invocations. A failure stops subsequent work unless multi-source
installation explicitly aggregates failures.

## 9. Command behavior

### 9.1 `skm ls` and `skm list`

Render the saved-source list defined in Section 11.2. These commands do not
spawn `npx`, but they retain the `npx` prerequisite for compatibility.

### 9.2 `skm add <source...>`

Without a source, write the following and exit `1`:

```text
❌ At least one source is required
Usage: skm add <source...>
```

For each argument in order:

- report `Already exists` if the array at that point contains an exact matching
  source, including a value added by an earlier argument in the same command;
- otherwise append `{ "source": <source> }` and report `Added`.

Sort the resulting array with:

```js
items.sort((a, b) => a.source.localeCompare(b.source));
```

Persist once, then render the result summary. `add` never invokes
`npx skills add`.

### 9.3 `skm remove <source...>` and `skm rm <source...>`

Without a source, write the following and exit `1`:

```text
❌ At least one source is required
Usage: skm remove <source...>
```

Classify every argument against the list as it existed before the operation.
Report `Removed` or `Not found` for each argument. Repeated arguments that
matched the original list are each counted as removed, while matching persisted
items are removed once. A missing requested source does not make the command
fail.

Persist once, then render the result summary. `remove` never invokes
`npx skills add`.

### 9.4 `skm show <source>`

The source does not need to be saved. Invoke without a shell:

```js
spawn("npx", ["skills", "add", source, "--list"], {
  stdio: "inherit",
});
```

Propagate a normal numeric child exit status exactly. A spawn failure or a
child completion without a numeric status maps to exit `1`.

More than one source writes the following and exits `1`:

```text
❌ show accepts at most one source
Usage: skm show [source]
```

### 9.5 `skm show`

Without an argument, open a single-select source picker.

- Non-interactive stdin or stdout writes the following and exits `1`:

  ```text
  ❌ skm show requires an interactive terminal when no source is provided
  Usage: skm show <source>
  ```

- An empty list writes the standard no-sources warning and exits `1`.
- Up arrow or `k` moves up; down arrow or `j` moves down.
- Enter invokes `npx skills add <source> --list`.
- `q`, Ctrl+C, or input EOF renders cancellation and exits `0`.

### 9.6 `skm`

Without arguments, open a multi-select install picker.

- Non-interactive stdin or stdout writes
  `❌ skm requires an interactive terminal` and exits `1`.
- An empty list writes the standard no-sources warning and exits `1`.
- Up arrow or `k` moves up; down arrow or `j` moves down.
- Space toggles the current source.
- `q`, Ctrl+C, or input EOF redraws the current selection, renders
  cancellation, and exits `0`.
- Enter with no selection renders `⚠️  No sources selected` and exits `1`.
- Enter with selected sources installs them sequentially in displayed order.

Before each installation, verify that the source still exists in the saved
list. A missing source writes `❌ Source not found: <source>`. Otherwise print:

```text
▶ Installing source: <source>
```

Then invoke without a shell:

```js
spawn("npx", ["skills", "add", source], {
  stdio: "inherit",
});
```

Continue after a lookup, spawn, or child failure. Exit `1` if any selected
source failed; otherwise exit `0`.

### 9.7 Help

`help`, `-h`, and `--help` render Section 11.1 and exit `0` without checking
`npx` or reading the list.

## 10. Terminal and process lifecycle

Interactive code has one idempotent cleanup path that removes listeners and
restores the exact previous TTY mode.

1. Raw mode is active only while reading selector input.
2. Raw byte `0x03` and `SIGINT` during selection follow the documented Ctrl+C
   cancellation path.
3. `SIGTERM` and `SIGHUP` restore the terminal and then preserve normal signal
   termination semantics.
4. `SIGTSTP` restores the terminal before suspension. `SIGCONT` re-enters input
   mode and redraws only while the selector remains active.
5. Input EOF restores the terminal and follows the cancellation path without
   spinning.
6. Selection listeners are removed before a child inherits stdio.
7. Cleanup also runs after Enter, `q`, child completion, and synchronous or
   asynchronous exceptions.

## 11. Rendering contract

Output streams are significant: errors and warnings use stderr; normal command
and interactive output use stdout. ANSI color remains present when
non-interactive output is redirected.

### 11.1 Help

```text

    SKILLS MANAGER
│
◇  Usage: skm <command> [args]
│
◆  Available commands

│  ls, list
│      Show saved sources from skm/list.json
│
│  show [source]
│      Show available skills; opens a single-select UI without source
│
│  add <source...>
│      Add or update saved sources
│
│  remove <source...>
│      Remove saved sources
└
```

The title uses a cyan background with black text. Command names are green;
structural lines and markers are cyan.

### 11.2 List

```text

    SKILLS MANAGER
│
◇  Source list: <skills-file-path>
│
◇  Found <N> sources
│
◆  Saved sources
│  ■ <source>
└
```

Each populated row has a green `■`. The empty row is a yellow `■` followed by
`No sources saved`.

### 11.3 Add

```text

    SKILLS MANAGER
│
◇  Source list: <path>
│
◇  Add request: <N> sources
│
◆  Add sources
│  ■ Added: <source>
│  ■ Already exists: <source>
└  Added <added> sources, skipped <skipped> existing
```

Added rows are green; duplicate rows are yellow.

### 11.4 Remove

```text

    SKILLS MANAGER
│
◇  Source list: <path>
│
◇  Remove request: <N> sources
│
◆  Remove sources
│  ■ Removed: <source>
│  ■ Not found: <source>
└  Removed <removed> sources, skipped <skipped> missing
```

Removed rows are green; missing rows are yellow.

### 11.5 Interactive selectors

Every state change clears and redraws the selector.

The install prompt is:

```text
Select sources to install (space to toggle, enter to start, q to quit)
```

The show prompt is:

```text
Select source to inspect (enter to show, q to quit)
```

Rows use a gray `□` when unselected and green `■` when selected. The current
source is white; other sources are gray. Cancellation labels are respectively
`Select sources to install` and `Select source to inspect`.

### 11.6 Rendering primitives

| Primitive | Required rendering |
| --- | --- |
| Title | blank line, three spaces, cyan background ` SKILLS MANAGER `, cyan `│` |
| Completed step | green `◇`, two spaces, text, then cyan `│` |
| Active step | cyan `◆`, two spaces, text |
| List end | cyan `└` |
| Error | red `❌ <message>` on stderr |
| Warning | yellow `⚠️  <message>` on stderr |
| Cancellation | cyan `└`, two spaces, red `<label> cancelled` |

## 12. Module boundaries

```text
mac_scripts/
├── skm
└── functions/
    └── skills-manager/
        ├── cli.mjs
        ├── config.mjs
        ├── sources.mjs
        ├── selector.mjs
        ├── skills-cli.mjs
        ├── ui.mjs
        ├── list.json.example
        └── __tests__/
```

| Module | Responsibility |
| --- | --- |
| `cli.mjs` | argument validation, dispatch, top-level error boundary, exit status |
| `config.mjs` | configuration resolution and bootstrap |
| `sources.mjs` | list validation, normalization, add/remove, atomic persistence |
| `selector.mjs` | input state machines, redraws, raw mode, signal cleanup |
| `skills-cli.mjs` | prerequisite lookup and shell-free `npx` execution |
| `ui.mjs` | ANSI primitives and command rendering |

Modules may be combined only when the result remains cohesive and independently
testable. The behavioral requirements take precedence over this suggested
layout.

## 13. Exit status contract

| Status | Meaning |
| ---: | --- |
| `0` | success or explicit selector cancellation |
| `1` | wrapper/CLI failure, usage error, invalid data, selector failure, spawn failure, or aggregated install failure |
| `2`–`255` | exact normal child status propagated by `show` |
| signal-derived | termination by the original non-cancellation signal |

## 14. Verification requirements

Use `node:test` with isolated temporary directories. Verification must cover:

- configuration resolution, both legacy bootstrap paths, and example fallback;
- canonical and legacy data shapes, missing files, invalid data, and atomic
  write failures;
- add/remove duplicate semantics and opaque source strings;
- every command, alias, usage error, prerequisite check, and startup-order row;
- shell-free child arguments, `show` status propagation, and aggregated install
  failures;
- selector state transitions, cancellation, EOF, raw-mode restoration, and
  supported signal paths;
- golden stdout/stderr transcripts and resulting file bytes;
- wrapper behavior, including missing Node and arguments containing spaces.

Before removing `main.zsh`, run legacy and JavaScript entrypoints with identical
temporary config directories, deterministic `PATH` stubs, and a fixed `TERM`.
Compare stdout, stderr, exit status, child argv, and persisted bytes. Normalize
only nondeterministic temporary paths and terminal-specific clear sequences.
Approved migration differences follow Section 3 instead of literal legacy
output.

Interactive rendering also requires a manual smoke test in the project's macOS
terminal.

## 15. Acceptance criteria

The migration is complete when:

1. Every command and alias in Section 5 satisfies this specification.
2. Existing canonical and legacy user data works without manual migration.
3. Configuration, bootstrap, normalization, and tool-check order match Sections
   6–8.
4. Terminal rendering and controls match Sections 9–11.
5. Child processes receive exact argument arrays without a shell.
6. Terminal state is restored on every lifecycle path in Section 10.
7. Unit, command, golden, differential, wrapper, and supported PTY tests pass on
   Node.js 20.
8. The manual macOS terminal smoke test passes.
9. `docs/commands.md` accurately describes the JavaScript entrypoint.
10. `main.zsh` and all inline `node -e` scripts are absent from the final `skm`
    implementation.
