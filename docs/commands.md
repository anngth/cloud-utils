# Commands

Config paths: [setup.md — Local config](setup.md#local-config).

## bud

Homebrew desired lists — `$CLOUD_UTILS_CONFIG_DIR/bud/`. File changes are local only (not in git).

```bash
bud              # brew update/upgrade + sync desired casks
bud --exclude postman        # exclude cask(s) from upgrade this run
bud -e postman spotify       # short form
bud list         # desired vs installed (alias: bud ls)
bud add <name>   # auto-detect cask or formula
bud remove <name>
bud add --cask <name>
bud add --formula <name>
bud add <user/repo>          # tap (e.g. mongodb/brew)
bud add --tap <user/repo>
bud remove <user/repo>       # remove from taps list
```

Desired taps live in `bud/taps.txt` (one `user/repo` per line). On first run, if the file is missing, `bud` seeds it from `brew tap`.

### `bud list`

Sections (in order):

1. Formulae · in list, installed / not installed / installed but not in list  
2. Taps · in list, tapped / not tapped / tapped but not in list  
3. Casks · in list, installed / not installed / installed but not in list  

Running `bud` (no subcommand) runs `brew tap` for any taps in the list that are not yet tapped.

Layout: top-to-bottom in each column, then next column. Column count: `(terminal width − 2) ÷ (longest name + 2 spaces)`.

## dbt

Database tools — `$CLOUD_UTILS_CONFIG_DIR/dbt/secrets`.

```bash
dbt --help
dbt connect
dbt list --status
dbt sync postgres -s 1 -d 1
dbt sync mongodb -s 1 -d 1
dbt sync -c          # clear backup DBs (dry-run: -n)
dbt sync postgres -c  # postgres only
```

- **Clear backups** — removes `<database>_backup_YYYYMMDD_HHMMSS`. Postgres: optional `-d <target>`, `-s <source>`.
- **MongoDB sync** — backs up target before restore; rolls back on failure.
- **PostgreSQL** — Supabase/`postgres` targets may use a temp dump file; `dbt sync -c` does not remove those files.

Secrets: `VPS_CONFIGS`, `POSTGRES_SOURCES`, `POSTGRES_TARGETS`, `MONGO_SOURCES`, `MONGO_TARGETS` (see `secrets.example` in repo).

## skm

Skills manager — `$CLOUD_UTILS_CONFIG_DIR/skm/list.json`.

```bash
skm
skm ls
skm list
skm add <source...>
skm remove <source...>
skm show
skm show <source>
```

| Command | Description |
|---------|-------------|
| `skm` | Interactive: Space toggle, Enter install, `q` cancel |
| `skm ls` / `list` | Saved sources |
| `skm add` | Add sources (skips duplicates) |
| `skm remove` | Remove sources |
| `skm show` | Pick source or pass name directly |

## gt

```bash
gt fetch
gt push
```

## 2fa

```bash
2fa
```

Prompts for Base32 secret (hidden), copies 6-digit TOTP to clipboard, prints `Code copied: <code>`.
