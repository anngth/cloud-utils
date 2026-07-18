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

Profile-based skill manager for the current project. SKM does not manage global
skills. Current state lives in `$CLOUD_UTILS_CONFIG_DIR/skm/profiles.json` and
`projects.json`; an existing `list.json` is one-time migration input only and is
retained unchanged. See [setup.md — Local config](setup.md#local-config).

```bash
skm
skm profile list
skm profile create frontend-project
skm profile create code-review
skm source add vercel-labs/agent-skills -p frontend-project
skm skill add code-review -s vercel-labs/agent-skills -p frontend-project
skm project link frontend-project code-review
skm status
skm install
skm install frontend-project code-review -y
skm uninstall frontend-project
skm uninstall frontend-project -l
```

With no arguments, `skm` opens the project-aware dashboard. Its actions install
linked profiles, link or unlink profiles, install other profiles once, show
status, manage profiles, or exit.

Profile, source, skill, and project commands only read or change saved
configuration; they never install or uninstall project skills. Use the
lifecycle commands for project files.

For `source add`, `source edit`, and `source remove`, omitting `-p` / `--profile`
opens the interactive profile selector. `skill add` and `skill remove` still
require an explicit profile.

| Family | Supported commands |
| --- | --- |
| Profile | `profile list`, `show`, `create`, `rename`, `remove [--force]` |
| Source | `source add`, `edit`, `remove`, `show` |
| Skill | `skill add <skill...> --source <source> --profile <profile>`, `skill remove <skill...> --source <source> --profile <profile>` |
| Project | `project link`, `unlink`, `show`, `list`, `remove` |
| Lifecycle | `status`, `install`, `uninstall` |

| Flag | Meaning |
| --- | --- |
| `-h`, `--help` | Show top-level SKM help |
| `-p`, `--profile` | Select the profile changed by a source or skill command |
| `-s`, `--source` | Select the saved profile source changed by `skill add` or `skill remove` |
| `-k`, `--skill` | Select an explicit skill for `source add`; repeat for multiple skills |
| `-a`, `--all` | Snapshot every currently discovered skill into one profile source |
| `-n`, `--no-skills` | Save a source with an empty skill selection |
| `-y`, `--yes` | Skip SKM and upstream confirmation prompts |
| `-f`, `--force` | Permit linked-profile removal or lifecycle mismatch/untracked replacement/removal |
| `-d`, `--dry-run` | Render a lifecycle plan without mutation |
| `-l`, `--keep-link` | Uninstall files without unlinking the selected profile |

`status`, `install`, and `uninstall` accept one or multiple explicit profile
names. Without profile names they use the current project's linked profiles;
there is no all-profiles mode or `--all-profiles` flag. If an interactive
install has no links, SKM can instead select profiles for a one-time install
and optionally save links after success.

Lifecycle safety rules:

- Correctly installed skills are skipped. Source-mismatch or untracked
  same-name skills require `--force` before replacement or removal.
- Uninstall retains the exact source-and-skill requirements still contributed
  by remaining linked profiles.
- Links offered by the interactive install flow are saved only after the whole
  external install succeeds; uninstall removes links only after all required
  external removals succeed.
- `uninstall --keep-link` removes eligible files but preserves the selected
  links, so a later status reports their required skills as missing.

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
