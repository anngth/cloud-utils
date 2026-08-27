# bud

Python 3 CLI for Homebrew desired lists. Config: `$CLOUD_UTILS_CONFIG_DIR/bud/desired.json` (file changes are local only, not in git).

Requires Homebrew. The `bud` wrapper uses the same locked repository
`.venv/bin/python` environment as `2fa` and `gt`; create it explicitly with
`uv sync --locked`. Spec: [bud CLI (Python)](superpowers/specs/2026-08-25-bud-budz-cli-parity-design.md).

`bud` prints each `brew` command. Probe commands (`list`, `info`, …) show the command only and capture output internally. `update` / `upgrade` / `cleanup` inherit the terminal so Homebrew progress (`%`, download bars) shows live.

```bash
bud                            # brew update/upgrade + sync desired casks
bud --exclude postman          # exclude cask(s) from upgrade this run
bud -e postman spotify         # short form
bud list                       # desired vs installed (alias: bud ls)
bud add <name>                 # auto-detect cask or formula (tap formulae by short name when tap is installed)
bud remove <name>
bud add --cask <name>
bud add --formula <name>
bud add <user/repo>            # tap (e.g. mongodb/brew)
bud add <user/repo>/<formula>  # tap formula (adds tap + formula, e.g. atlassian/acli/acli)
bud add --tap <user/repo>
bud remove <user/repo>         # remove from taps list
bud help
```

Config paths: [setup.md — Local config](setup.md#local-config).

Example template: `mac_scripts/functions/brew-desired-update/desired.example.json`.

## Config (`desired.json`)

Single JSON document with `version`, `formulas`, `casks`, and `taps` arrays. On first run, if `desired.json` is missing, `bud` bootstraps from `desired.example.json`. If the taps list is still empty after bootstrap, `bud` seeds taps from `brew tap`.

## `bud list`

Sections (in order):

1. Formulae · in list, installed / not installed / installed but not in list
2. Taps · in list, tapped / not tapped / tapped but not in list
3. Casks · in list, installed / not installed / installed but not in list

Running `bud` (no subcommand) runs `brew tap` for any taps in the list that are not yet tapped.

Layout: top-to-bottom in each column, then next column. Column count: `(terminal width − 2) ÷ (longest name + 2 spaces)`.
