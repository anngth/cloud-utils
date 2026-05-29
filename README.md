# cloud-utils

Utility scripts for VPS setup, a Docker stack, macOS helpers, MTProto proxy, and Rclone transfers.

## Installation

```bash
git clone https://github.com/nguyenthanhan/cloud-utils.git
cd cloud-utils
```

### New Mac setup

On a new Mac, install Apple's command line tools first:

```bash
xcode-select --install
```

Install Homebrew if it is not available yet:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Clone this repo to `~/Documents/cloud-utils`. The bundled `.zshrc` expects this exact path.

```bash
mkdir -p ~/Documents
git clone https://github.com/nguyenthanhan/cloud-utils.git ~/Documents/cloud-utils
```

Install the common CLI dependencies used by `.zshrc`, aliases, and scripts:

```bash
brew install eza bat zoxide fnm rbenv libpq
brew tap mongodb/brew
brew install mongosh mongodb-database-tools
```

`libpq` provides PostgreSQL client tools such as `psql`, `pg_dump`, and `pg_restore` without running a local PostgreSQL server. `dbt sync postgres` can also use `postgresql@18` if you prefer the full Homebrew formula.

`mongosh` and `mongodb-database-tools` provide the MongoDB client, `mongodump`, and `mongorestore` used by `dbt sync mongodb`.

Create the `~/.zshrc` hard link from this repo, then restart the terminal:

```bash
zsh ~/Documents/cloud-utils/mac_scripts/create_link_zshrc
exec zsh
```

If `~/.zshrc` already exists, the script backs it up automatically to `~/.zshrc.backup.<timestamp>` before creating the hard link.

After restarting the shell, verify the repo scripts are on `PATH`:

```bash
gt fetch
bud list
dbt --help
2fa
```

## Usage

### VPS setup

```bash
./setup.sh
```

```bash
./setup.sh -apt-update -basic-tools -firewall -ssh-security -fail2ban
./setup.sh -docker -fnm -python -uv
./setup.sh -zsh -zimfw -bash-it -zoxide -eza -fastfetch
./setup.sh -proxy -proxy-port=8080
./setup.sh -xrdp -xrdp-port=33899 -verify-xrdp
```

Common flags:

- `-apt-update`, `-basic-tools`, `-set-password`
- `-firewall`, `-ssh-security`, `-fail2ban`
- `-docker`, `-rclone`, `-proxy`, `-proxy-port=PORT`
- `-fnm`, `-python`, `-uv`
- `-zsh`, `-zimfw`, `-bash-it`, `-zoxide`, `-eza`, `-fastfetch`
- `-qbittorrent`, `-xrdp`, `-xrdp-port=PORT`, `-firefox`, `-verify-xrdp`

### Docker stack

```bash
cp .env.example .env
docker network create traefik_network
touch acme.json && chmod 600 acme.json
docker compose up -d
```

### macOS scripts

```bash
dbt --help
dbt connect
dbt list --status
dbt sync postgres -s 1 -d 1
dbt sync -c -n
dbt sync -c
dbt sync postgres -c -n
dbt sync postgres -c
dbt sync mongodb -s 1 -d 1
```

`dbt sync --clear-backups` (`dbt sync -c`) removes both PostgreSQL and MongoDB backup databases that match the backup naming pattern left by interrupted syncs, such as `<database>_backup_YYYYMMDD_HHMMSS`. Use `--dry-run` (`-n`) to preview without dropping databases.

`dbt sync postgres --clear-backups` (`dbt sync postgres -c`) removes PostgreSQL databases that match the backup naming pattern left by interrupted syncs, such as `<database>_backup_YYYYMMDD_HHMMSS`. By default it checks all PostgreSQL targets. Add `-d <target>` to limit cleanup to one target, or `-s <source>` to limit cleanup to backups for one source database.

MongoDB sync backs up the target database to `<database>_backup_YYYYMMDD_HHMMSS` before restoring. The backup is removed after a successful sync. If sync fails, the tool attempts to restore the backup automatically.

PostgreSQL targets that sync into the `postgres` database, including Supabase targets, use a temporary backup file instead of a backup database. If the sync completes with warnings or rollback fails, the file path is printed in the command output. `dbt sync -c` only removes backup databases, so inspect and remove those temporary files manually after verifying the target data.

```bash
zsh ~/Documents/cloud-utils/mac_scripts/create_link_zshrc
```

```bash
bud
bud list
bud add <name>
bud remove <name>
```

```bash
skm
skm ls
skm list
skm add vercel-labs/agent-skills
skm add vercel-labs/agent-skills ./my-local-skills
skm show
skm show vercel-labs/agent-skills
skm remove vercel-labs/agent-skills
skm remove vercel-labs/agent-skills ./my-local-skills
```

`skm` opens an interactive UI to select and install skills from saved sources in `mac_scripts/functions/skills-manager/list.json`. Press Space to toggle selection, Enter to install selected sources, or `q` to cancel.

`skm list` or `skm ls` displays all saved sources.

`skm add <source...>` adds one or more sources to the saved list. If a source already exists, it is skipped.

`skm remove <source...>` removes one or more sources from the saved list. If a source is not found, it is skipped.

`skm show` opens a selector for saved sources and lists available skills for the selected source. `skm show <source>` lists available skills for a source directly.

```bash
gt fetch
gt push
```

```bash
2fa
```

`2fa` prompts for the Base32 secret without echoing it, copies the current 6-digit TOTP code to the macOS clipboard, and prints `Code copied: <code>`.

### MTProto

```bash
cd mtproto
bash save_random_hex.sh
docker compose up -d
```

### Rclone

```bash
cd rclone
export PROXY_URL="http://user:pass@host:port"
bash transfer.sh
```

Windows:

```powershell
cd rclone
.\transfer.ps1
```

## Main Features

- Ubuntu VPS provisioning with one script
- Traefik + Portainer + Stirling PDF + DB services stack
- macOS helpers for DB tunnel/sync, git, and Homebrew casks
- MTProto proxy deployment
- Rclone transfer helpers

## Technology Stack

- Bash and Zsh
- Docker and Docker Compose
- Traefik, Portainer, Stirling PDF
- PostgreSQL, MongoDB, Redis

## Contribution Guidelines

- Keep changes small and focused
- Follow existing script style (Bash vs Zsh)
- Include brief testing notes in PRs

## License

Provided as-is for personal use. Add an explicit license if you plan to redistribute.
