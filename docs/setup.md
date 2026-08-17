# Setup (macOS)

## Clone

```bash
mkdir -p ~/Documents
git clone https://github.com/anngth/cloud-utils.git ~/Documents/cloud-utils
```

## Command line tools (first-time Mac)

Only if `brew install` asks for Command Line Tools:

```bash
xcode-select --install
```

## Shell

Add to `~/.zshrc`:

```zsh
source "$HOME/Documents/cloud-utils/mac_init/load.zsh"
```

## CLI dependencies

Requires [Homebrew](https://brew.sh) (`brew` on your PATH). Install it first if you do not have it yet.

```bash
brew install eza bat zoxide fnm rbenv libpq
brew tap mongodb/brew
brew install mongosh mongodb-database-tools
```

- `libpq` — PostgreSQL clients (`psql`, `pg_dump`, `pg_restore`). `dbt` can also use `postgresql@18`.
- `mongosh` / `mongodb-database-tools` — used by `dbt sync mongodb`.
- `python3` — used by `bud` to read/write `desired.json`. Install with `brew install python` if `python3` is missing.

## Verify

```bash
exec zsh
gt fetch
bud list
dbt --help
2fa
```

## Commands

| Command | Docs |
| ------- | ---- |
| `bud` | [bud.md](bud.md) |
| `budj` | [budj.md](budj.md) — JS variant, same `bud/desired.json` |
| `gt` | [gt.md](gt.md) |
| `skm` | [skm.md](skm.md) |
| `dbt` | [dbt.md](dbt.md) |
| `2fa` | [2fa.md](2fa.md) |

## Local config

Personal data for `bud`, `gt`, `skm`, and `dbt` lives outside the repo (iCloud by default).

In `~/.zshrc`:

```zsh
export CLOUD_UTILS_CONFIG_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
```

Layout:

```text
~/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils/
├── bud/
│   └── desired.json
├── dbt/
│   └── secrets
├── gt/
│   └── backups.json
└── skm/
    └── sources.json
```

| Tool  | Config                                              | Template/reference in repo                                                    |
| ----- | --------------------------------------------------- | ----------------------------------------------------------------------------- |
| `bud` | `bud/desired.json`                                  | `mac_scripts/functions/brew-desired-update-js/desired.json.example`           |
| `gt`  | `gt/backups.json`                                   | `mac_scripts/functions/git-tools/backups.json.example` (reference only)       |
| `skm` | `skm/sources.json`                                  | `mac_scripts/functions/skills-manager/sources.json.example` (reference only) |
| `dbt` | `dbt/secrets`                                       | `mac_scripts/functions/db-tools/secrets.example`                              |

On first run, `bud` bootstraps missing `desired.json` from
`desired.json.example`. `dbt` bootstraps missing files from
legacy paths or `*.example`. SKM creates `sources.json` automatically; legacy `profiles.json`,
`projects.json`, and `list.json` are migrated once and retained unchanged.
`gt backup` creates `backups.json` on first list use (and migrates older schemas).

Optional:

```zsh
export DBT_SECRETS_FILE="/absolute/path/to/secrets"
```
