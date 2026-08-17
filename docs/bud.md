# bud

JS variant of [`budz`](budz.md) — same `$CLOUD_UTILS_CONFIG_DIR/bud/desired.json`.

`bud` prints each `brew` command. Probe commands (`list`, `info`, …) show the command only and capture output internally. `update` / `upgrade` / `cleanup` inherit the terminal so Homebrew progress (`%`, download bars) shows live.

```bash
bud              # brew update/upgrade + sync desired casks
bud --exclude postman
bud -e postman spotify
bud list
bud add <name>
bud remove <name>
```

Config and list layout: [budz.md](budz.md).
