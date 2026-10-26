# dbt

Database tools — `$CLOUD_UTILS_CONFIG_DIR/dbt/secrets`.

Config paths: [setup.md — Local config](setup.md#local-config).

```bash
dbt --help
dbt connect
dbt list             # tunnels: UP / DOWN / BUSY
dbt sync list --status
dbt sync postgres -s 1 -d 1
dbt sync mongodb -s 1 -d 1
dbt sync -c          # clear backup DBs (dry-run: -n)
dbt sync postgres -c  # postgres only
```

- **Clear backups** — removes `<database>_backup_YYYYMMDD_HHMMSS`. Postgres: optional `-d <target>`, `-s <source>`.
- **MongoDB sync** — backs up target before restore; rolls back on failure. Needs `mongosh` (the legacy `mongo` shell is not supported).
- **Ctrl-C during a sync** — the running dump/restore is killed first, then the backup is restored; exit code 130.
- **`--verbose`** — streams `pg_dump`/`pg_restore`/`mongodump`/`mongorestore` output live; without it a failure still prints the first error lines.
- **PostgreSQL** — Supabase/`postgres` targets may use a temp dump file; `dbt sync -c` does not remove those files.
- **Target URLs must name a database** (`.../mydb`); a target whose URL ends at the port is rejected. A target named `postgres` (or any Supabase host) is restored into `postgres` after its `public` schema is wiped.
- **Secrets file** — only double-quoted rows; `$` must be written `\$`. The file is bootstrapped on first run and dbt prints where it put it.

Secrets: `VPS_CONFIGS`, `POSTGRES_SOURCES`, `POSTGRES_TARGETS`, `MONGO_SOURCES`, `MONGO_TARGETS` (see [`secrets.example`](../mac_scripts/functions/db_tools/secrets.example)).
