# bud

Homebrew desired lists — `$CLOUD_UTILS_CONFIG_DIR/bud/desired.json`. File changes are local only (not in git).

Config paths: [setup.md — Local config](setup.md#local-config).

Example template: `mac_scripts/functions/brew-desired-update/desired.json.example`.

```bash
bud              # brew update/upgrade + sync desired casks
bud --exclude postman        # exclude cask(s) from upgrade this run
bud -e postman spotify       # short form
bud list         # desired vs installed (alias: bud ls)
bud add <name>   # auto-detect cask or formula (tap formulae by short name when tap is installed)
bud remove <name>
bud add --cask <name>
bud add --formula <name>
bud add <user/repo>          # tap (e.g. mongodb/brew)
bud add <user/repo>/<formula> # tap formula (adds tap + formula, e.g. atlassian/acli/acli)
bud add --tap <user/repo>
bud remove <user/repo>       # remove from taps list
```

## Config (`desired.json`)

Single JSON document with `version`, `formulas`, `casks`, and `taps` arrays. On first run, if `desired.json` is missing, `bud` migrates from legacy `bud/*.txt` (or older `brew/*.txt`) once, writes JSON, then deletes the `bud/*.txt` files. Legacy `brew/*.txt` is never deleted. If the taps list is still empty after migration, `bud` seeds taps from `brew tap`.

## `bud list`

Sections (in order):

1. Formulae · in list, installed / not installed / installed but not in list  
2. Taps · in list, tapped / not tapped / tapped but not in list  
3. Casks · in list, installed / not installed / installed but not in list  

Running `bud` (no subcommand) runs `brew tap` for any taps in the list that are not yet tapped.

Layout: top-to-bottom in each column, then next column. Column count: `(terminal width − 2) ÷ (longest name + 2 spaces)`.
