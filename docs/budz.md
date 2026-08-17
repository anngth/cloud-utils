# budz

Homebrew desired lists — `$CLOUD_UTILS_CONFIG_DIR/bud/desired.json`. File changes are local only (not in git).

`budz` is the zsh CLI. JS variant (same JSON): [`bud`](bud.md).

Config paths: [setup.md — Local config](setup.md#local-config).

Example template: `mac_scripts/functions/brew-desired-update-js/desired.json.example`.

```bash
budz              # brew update/upgrade + sync desired casks
budz --exclude postman        # exclude cask(s) from upgrade this run
budz -e postman spotify       # short form
budz list         # desired vs installed (alias: budz ls)
budz add <name>   # auto-detect cask or formula (tap formulae by short name when tap is installed)
budz remove <name>
budz add --cask <name>
budz add --formula <name>
budz add <user/repo>          # tap (e.g. mongodb/brew)
budz add <user/repo>/<formula> # tap formula (adds tap + formula, e.g. atlassian/acli/acli)
budz add --tap <user/repo>
budz remove <user/repo>       # remove from taps list
```

## Config (`desired.json`)

Single JSON document with `version`, `formulas`, `casks`, and `taps` arrays. On first run, if `desired.json` is missing, `budz` bootstraps from `desired.json.example`. If the taps list is still empty after bootstrap, `budz` seeds taps from `brew tap`.

## `budz list`

Sections (in order):

1. Formulae · in list, installed / not installed / installed but not in list  
2. Taps · in list, tapped / not tapped / tapped but not in list  
3. Casks · in list, installed / not installed / installed but not in list  

Running `budz` (no subcommand) runs `brew tap` for any taps in the list that are not yet tapped.

Layout: top-to-bottom in each column, then next column. Column count: `(terminal width − 2) ÷ (longest name + 2 spaces)`.
