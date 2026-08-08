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
gt backup add git@github.com:org/my-app.git
gt backup                          # interactive multi-select
gt backup --all                    # backup every listed repo
gt backup remove 1
gt backup remove git@github.com:org/my-app.git
```

- **backup** — manages a list of source SSH URLs and mirrors selected repos to private `anngth-dev/backups/<owner>-<repo>` on GitLab.
  - `gt backup` — interactive multi-select (TTY required); space toggles, enter starts, q cancels.
  - `gt backup --all` — backup every listed repo (no TTY required).
  - `gt backup add <ssh-url>` / `gt backup remove <index|ssh-url>` — maintain the list (`index` is 1-based).
- **Config:** `$CLOUD_UTILS_CONFIG_DIR/gt/backups.json` (same config root as `skm`; default under iCloud Backups when unset).
  - List file schema version 3: each repo is `{ url, lastBackupAt, lastCheckedAt }` (`lastBackupAt` / `lastCheckedAt` ISO UTC or null).
  - `lastBackupAt` — set only after a successful mirror push; unchanged on skip.
  - `lastCheckedAt` — set after a successful check (skip or mirror).
  - Interactive selector shows `Last backup: <relative> (<local datetime>)` and `Last checked: …` under repos when those timestamps are set (omit line when null).
  - v1 string arrays and v2 `{ url, lastBackupAt }` lists migrate to v3 on load (adds `lastCheckedAt: null` for v2).
- **Migration:** old one-shot `gt backup <ssh-url>` / `-n` / `--new` are removed. Use `gt backup add <ssh-url>`, then `gt backup` or `gt backup --all`.
- Per URL: missing project → create; live → compare `git ls-remote` fingerprints (heads + tags only) — equal → skip mirror, update `lastCheckedAt` only (`skip` in summary, `→ unchanged`); differ → full mirror (all branches + tags); inactive/soft-deleted → recreate at the base name (never skip). Creates the private `anngth-dev/backups` subgroup when missing (parent `anngth-dev` must already exist). After push, sets the GitLab default branch to `main` if present, otherwise `develop`. Protects `main` and/or `develop` when those branches exist (force-push allowed for later mirror updates).
- Batch summary lists `ok`, `skip`, and `fail` per URL; exit `0` only when there are no `fail` entries (`ok` and `skip` both succeed).
- Requires `git`, `glab` (logged in), and SSH access to both the source and GitLab.
- Backup pushes all branches and tags (not GitLab hidden refs like `refs/environments/*`). `--prune` can delete remote branches/tags that no longer exist on the source.

## 2fa

```bash
2fa
```

Prompts for Base32 secret (hidden), copies 6-digit TOTP to clipboard, prints `Code copied: <code>`.
