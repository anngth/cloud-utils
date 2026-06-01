# Setup (macOS)

## Clone

```bash
mkdir -p ~/Documents
git clone https://github.com/nguyenthanhan/cloud-utils.git ~/Documents/cloud-utils
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

## Verify

```bash
exec zsh
gt fetch
bud list
dbt --help
2fa
```

## Local config

Personal data for `bud`, `skm`, and `dbt` lives outside the repo (iCloud by default).

In `~/.zshrc`:

```zsh
export CLOUD_UTILS_CONFIG_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
```

Layout:

```text
~/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils/
├── bud/
│   ├── casks.txt
│   ├── formulas.txt
│   └── taps.txt
├── dbt/
│   └── secrets
└── skm/
    └── list.json
```

| Tool  | Config                              | Template in repo                                         |
| ----- | ----------------------------------- | -------------------------------------------------------- |
| `bud` | `bud/casks.txt`, `bud/formulas.txt`, `bud/taps.txt` | `mac_scripts/functions/brew-desired-update/*.example`    |
| `skm` | `skm/list.json`                     | `mac_scripts/functions/skills-manager/list.json.example` |
| `dbt` | `dbt/secrets`                       | `mac_scripts/functions/db-tools/secrets.example`         |

On first run, tools bootstrap missing files from legacy paths or `*.example`.

Optional:

```zsh
export DBT_SECRETS_FILE="/absolute/path/to/secrets"
```
