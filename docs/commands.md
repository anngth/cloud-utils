# Commands

Config paths: [setup.md — Local config](setup.md#local-config).

Also see: [bud.md](bud.md) · [skm.md](skm.md)

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

## gt

Git workflow helpers (`push`, `fetch`, `backup`). Implemented in Node under `mac_scripts/functions/git-tools/`.

```bash
gt fetch
gt push
gt backup git@github.com:org/my-app.git
```

- **backup** — mirrors a repo to private `anngth-dev/backups/<owner>-<repo>` on GitLab. If the project already exists, prompts to update, create a new suffixed project (`-2`, `-3`, …), or cancel. Creates the private `anngth-dev/backups` subgroup when it is missing (parent `anngth-dev` must already exist).
- Requires `git`, `glab` (logged in), and SSH access to both the source and GitLab.
- Backup pushes all branches and tags (not GitLab hidden refs like `refs/environments/*`). `--prune` can delete remote branches/tags that no longer exist on the source.

## 2fa

```bash
2fa
```

Prompts for Base32 secret (hidden), copies 6-digit TOTP to clipboard, prints `Code copied: <code>`.
