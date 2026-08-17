# Design: `budj` TTY brew progress

**Date:** 2026-08-17  
**Status:** Approved for planning  
**Extends:** `mac_scripts/functions/brew-desired-update-js/brew.mjs` (`runBrew`, `createBrewRunner`)  
**Does not change:** zsh `bud` (`brew-desired-update-zsh/`)

## Goal

When `budj` runs long Homebrew commands (`update`, `upgrade`, `cleanup`, `tap <name>`, …), brew must see a real TTY so download `%` / progress bars appear live — same as zsh `bud`. Today `stdio: pipe` makes brew treat output as non-interactive, so the process looks frozen aside from `$ brew …` and step chrome.

## Decisions

| Topic | Choice |
| --- | --- |
| Interactive output | Inherit the parent TTY (no `│` prefix on brew process output) |
| Interactive stdin | Inherit (sudo / cask password / confirm work) |
| Probe commands | Log `$ brew …`, capture stdout, do **not** print package dumps |
| Probe set | Existing `isBrewProbe`: `list`, `info`, bare `tap`, `trust --help`, `--help` |
| Spawn split | Inside `runBrew` / `createBrewRunner`; callers unchanged |
| Non-TTY `budj` | Still inherit; no pipe fallback |
| PTY / `node-pty` | Out of scope |
| `HOMEBREW_COLOR` | Out of scope (TTY inherit restores color and progress) |
| zsh `bud` | Unchanged |

## Behavior

Two modes, selected by `isBrewProbe(args)` (overridable in tests via an explicit `mode` option on `runBrew`).

### Probe

Commands: `list`, `info`, `tap` with no extra args, `trust --help`, `--help`.

- Print `$ brew …` via `ui.command` (or default stdout), same as today.
- `stdio: ["ignore", "pipe", "pipe"]`.
- Do not write child stdout/stderr to the terminal.
- Return buffered `{ code, stdout, stderr }` for parsers (`loadBrewState`, `brewInfoToken`, …).

### Interactive

Everything else (`update`, `upgrade`, `tap <name>`, `tap --repair`, `cleanup`, `trust --tap`, …).

- Print `$ brew …` via `ui.command`.
- `stdio: "inherit"` (stdin, stdout, stderr).
- Brew draws progress on the real terminal. No `linePrefix` / `createLineFramer`.
- Return `{ code, stdout: "", stderr: "" }`. Callers already use `code` only (`update.mjs`).

UI chrome (`title`, `◇`/`◆` steps, `ui.command` with `│`) stays. Only the child brew process output is unprefixed.

## Architecture

Keep a single `runBrew` entry. `createBrewRunner` keeps logging through `ui.command` and does not pass `streamPrefix`.

- Default mode = `isBrewProbe(args) ? "probe" : "interactive"`.
- Optional `mode: "probe" | "interactive"` on `runBrew` for tests.
- Remove live chunk forwarding and `linePrefix` from the interactive path.
- Remove `createLineFramer` if nothing else uses it.

`update.mjs`, `list.mjs`, `manage.mjs` keep calling `runner([...])`. No per-call flags.

## Error handling

- Interactive failure: brew already printed to the TTY; `runBrew` returns non-zero `code`; existing `ui.error("brew update failed")` (and siblings) stay.
- Probe failure: unchanged (`code !== 0` → `null` / tap fail).
- Spawn error: `code` 1, as today.
- If `budj` itself is not a TTY, inherit still attaches to the same fds; brew will not show `%`. No extra fallback.

## Testing

Unit tests with mock `spawn` in `brew.test.mjs`. No real brew / PTY integration test.

- Probe: assert `stdio` is `["ignore", "pipe", "pipe"]`; `$ brew …` logged; terminal writers get no child output; `result.stdout` populated.
- Interactive: assert `stdio` is `"inherit"`; `$ brew …` logged; no chunk forwarding; `stdout`/`stderr` empty; `code` from `close`.
- Keep `isBrewProbe` coverage (`tap --repair` and `tap <name>` are interactive).
- Drop tests for `createLineFramer` and “frames live upgrade output” if that code is removed.
- Direct `runBrew(["--version"])` is interactive (`--version` is not a probe). Capture tests must use a probe argv or `mode: "probe"`.

`update.test.mjs` / `cli.test.mjs` mock `runBrew` and should not need behavior changes.

## Non-goals

- Matching zsh step rewrite (`◇` → `◆` in place).
- Changing `ui.command` chrome (still `│` + current colors).
- Quiet-hiding probe `$ brew …` lines.
- Dumping `brew list` output to the terminal.
- Sharing this runner with zsh `bud`.
