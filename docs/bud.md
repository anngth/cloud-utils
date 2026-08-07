# bud

Homebrew desired lists — `$CLOUD_UTILS_CONFIG_DIR/bud/`. File changes are local only (not in git).

Config paths: [setup.md — Local config](setup.md#local-config).

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

Desired taps live in `bud/taps.txt` (one `user/repo` per line). On first run, if the file is missing, `bud` seeds it from `brew tap`.

## `bud list`

Sections (in order):

1. Formulae · in list, installed / not installed / installed but not in list  
2. Taps · in list, tapped / not tapped / tapped but not in list  
3. Casks · in list, installed / not installed / installed but not in list  

Running `bud` (no subcommand) runs `brew tap` for any taps in the list that are not yet tapped.

Layout: top-to-bottom in each column, then next column. Column count: `(terminal width − 2) ÷ (longest name + 2 spaces)`.
