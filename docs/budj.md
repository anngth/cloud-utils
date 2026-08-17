# budj

JS variant of [`bud`](bud.md) — same `$CLOUD_UTILS_CONFIG_DIR/bud/desired.json`.

`budj` prints each `brew` command. Probe commands (`list`, `info`, …) show the command only and capture output internally. `update` / `upgrade` / `cleanup` inherit the terminal so Homebrew progress (`%`, download bars) shows live.

```bash
budj              # brew update/upgrade + sync desired casks
budj --exclude postman
budj -e postman spotify
budj list
budj add <name>
budj remove <name>
```

Config and list layout: [bud.md](bud.md).
