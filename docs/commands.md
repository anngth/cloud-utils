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
