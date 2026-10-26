# dbt

Database tools — `$CLOUD_UTILS_CONFIG_DIR/dbt/secrets.toml`.

Config paths: [setup.md — Local config](setup.md#local-config).

```bash
dbt --help           # every subcommand has its own --help / -h
dbt config           # show the secrets file, credentials masked
dbt config path      # only its location (e.g. $EDITOR "$(dbt config path)")
dbt connect
dbt list             # tunnels: UP / DOWN / BUSY
dbt status           # tunnels (DOWN rows get an SSH check) plus a probe of every database
dbt sync list --status
dbt sync postgres -s 1 -d 1   # aliases: postgresql, pg
dbt sync mongodb -s 1 -d 1    # alias: mongo
dbt sync -c          # clear backup DBs (dry-run: -n)
dbt sync postgres -c  # postgres only
```

- **Clear backups** — removes `<database>_backup_YYYYMMDD_HHMMSS`. Postgres: optional `-d <target>`, `-s <source>`. No configured target is a notice, not an error (exit 0).
- **MongoDB sync** — backs up target before restore; rolls back on failure. Needs `mongosh` (the legacy `mongo` shell is not supported).
- **Ctrl-C during a sync** — the running dump/restore is killed first, then the backup is restored; exit code 130.
- **`--verbose`** — streams `pg_dump`/`pg_restore`/`mongodump`/`mongorestore` output live; without it a failure still prints the first error lines.
- **PostgreSQL** — Supabase/`postgres` targets may use a temp dump file; `dbt sync -c` does not remove those files. Clients run with `-w` (a URL without a password fails instead of prompting). Restores onto PostgreSQL ≤ 16 stop at the first SQL error and roll back.
- **Status probes** — sources are probed on their own database, targets on `postgres` (the database the sync maintenance runs in).
- **Mongo URIs** — a path database (`.../admin`) must match the database being synced, since `mongodump` rejects a `--db` that differs; use `?authSource=admin` instead.
- **Where a Postgres sync lands** — in a target database named after the source row; the sync prints `Target database:` so there is no guessing. The URL path is only the maintenance database (`postgres` when the URL ends at the port). A target whose URL names `postgres`, or any Supabase host, is restored into `postgres` after its `public` schema is wiped. `-db` is accepted and ignored for Postgres.
- **Connection failures through a tunnel** — when a sync cannot reach `localhost:<port>` and a tunnel row uses that port, dbt says whether the tunnel is DOWN (`dbt connect <service>`), UP but refused on the server side, or the port is taken by another process.
- **Timeouts** — Postgres clients use `PGCONNECT_TIMEOUT=10` unless the environment sets its own; `mongosh` probes get `serverSelectionTimeoutMS=5000` unless the URI sets one.
- **SSH preflight** runs with `StrictHostKeyChecking=accept-new`: unknown hosts are recorded, a changed key still fails.
- **Errors never quote credentials** — a bad `url`/`uri` is reported with `user:password@` masked, and `dbt config` masks them too.
- **Secrets file** — TOML; an invalid entry rejects the whole file with its location (`postgres.sources[2] (name): ...`). Tunnels of one VPS share a `name`; two tunnels may not share a `local_port`. The file is created on first run and dbt prints where it put it.

Secrets sections: `[[tunnels]]`, `[[postgres.sources]]`, `[[postgres.targets]]`, `[[mongo.sources]]`, `[[mongo.targets]]` (see [`secrets.example.toml`](../mac_scripts/functions/db_tools/secrets.example.toml)). A Postgres source needs `database` only when it differs from the URL path.
