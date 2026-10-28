# dbt

Database tools — `$CLOUD_UTILS_CONFIG_DIR/dbt/secrets.toml`.

Config paths: [setup.md — Local config](setup.md#local-config).

```bash
dbt --help           # every subcommand has its own --help / -h
dbt completion zsh   # eval "$(dbt completion zsh)" in ~/.zshrc: completes commands and row names
dbt config           # print where the secrets file is
dbt config edit      # open it in $EDITOR
dbt connect
dbt list             # tunnels (UP/DOWN/BUSY, no network) and every sync row with its index
dbt status           # same, plus an SSH check on DOWN tunnels and a 3s probe of every database
dbt sync postgres --from prod --to staging   # rows by name or index from `dbt list`; alias: pg
dbt sync mongodb --from prod --to local --db app   # alias: mongo; --db by name or index
dbt sync postgres    # in a terminal: pick the rows with the arrow keys
dbt clean            # delete backup DBs on every target (dry-run: -n)
dbt clean postgres   # only the PostgreSQL targets (or: mongodb)
```

- **Before a sync** dbt prints the plan (target database, what the backup is); `-n` stops after the plan.
- **`dbt clean`** — removes `<database>_backup_YYYYMMDD_HHMMSS`: PostgreSQL targets first, then MongoDB. No configured target is a notice, not an error (exit 0).
- **MongoDB sync** — backs up target before restore; rolls back on failure. Needs `mongosh` (the legacy `mongo` shell is not supported).
- **Ctrl-C during a sync** — the running dump/restore is killed first, then the backup is restored; exit code 130.
- **`--verbose`** — streams `pg_dump`/`pg_restore`/`mongodump`/`mongorestore` output live; without it a failure still prints the first error lines.
- **PostgreSQL** — Supabase/`postgres` targets may use a temp dump file; `dbt clean` does not remove those files. Clients run with `-w` (a URL without a password fails instead of prompting). Restores onto PostgreSQL ≤ 16 stop at the first SQL error and roll back.
- **Status probes** — sources are probed on their own database, targets on `postgres` (the database the sync maintenance runs in).
- **Mongo URIs** — a path database (`.../admin`) must match the database being synced, since `mongodump` rejects a `--db` that differs; use `?authSource=admin` instead.
- **Where a Postgres sync lands** — in a target database named after the source row; the plan printed before the sync says which. The URL path is only the maintenance database (`postgres` when the URL ends at the port). A target whose URL names `postgres`, or any Supabase host, is restored into `postgres` after its `public` schema is wiped. There is no `--db` for Postgres.
- **Connection failures through a tunnel** — when a sync cannot reach `localhost:<port>` and a tunnel row uses that port, dbt says whether the tunnel is DOWN (`dbt connect <service>`), UP but refused on the server side, or the port is taken by another process.
- **Timeouts** — Postgres clients use `PGCONNECT_TIMEOUT=10` unless the environment sets its own; `mongosh` gets `serverSelectionTimeoutMS=5000` unless the URI sets one. `status` probes with 3 seconds instead.
- **SSH preflight** runs with `StrictHostKeyChecking=accept-new`: unknown hosts are recorded, a changed key still fails.
- **Errors never quote credentials** — a bad `url`/`uri` is reported with `user:password@` masked.
- **Secrets file** — TOML; an invalid entry rejects the whole file with its location (`postgres.sources[2] (name): ...`). Tunnels of one VPS share a `name`; two tunnels may not share a `local_port`. The file is created on first run and dbt prints where it put it.

Secrets sections: `[[tunnels]]`, `[[postgres.sources]]`, `[[postgres.targets]]`, `[[mongo.sources]]`, `[[mongo.targets]]` (see [`secrets.example.toml`](../mac_scripts/functions/db_tools/secrets.example.toml)). A Postgres source needs `database` only when it differs from the URL path.
