# gt

Git workflow helpers (`push`, `fetch`, `backup`). Implemented in Python 3.14
under `mac_scripts/functions/git_tools/` and run from the repository's locked
`uv` environment.

Config paths: [setup.md — Local config](setup.md#local-config).

```bash
gt fetch
gt push
gt backup add git@github.com:org/my-app.git
gt backup add git@github.com:org/a.git git@github.com:org/b.git
gt backup                          # interactive multi-select
gt backup --dry-run                # preview without mirroring or config writes
gt backup -f                       # force re-mirror (skip fingerprint check)
gt backup --all                    # backup every listed repo
gt backup --all --dry-run          # preview every listed repo
gt backup --all --force            # force all listed repos
gt backup stale                    # interactive select among stale repos (default 7 days)
gt backup stale --all              # backup every stale repo (no TTY)
gt backup stale --days 14          # custom staleness threshold
gt backup stale --all --dry-run    # preview stale repos only
gt backup stale --all --force      # force every stale repo
gt backup remove 1
gt backup remove git@github.com:org/my-app.git
```

- **backup** — manages a list of source SSH URLs and mirrors selected repos to private `anngth-dev/backups/<owner>-<repo>` on GitLab.
  - `gt backup` — interactive multi-select (TTY required); space toggles, a selects all, c clears, enter starts, q cancels; pre-checks repos from the last successful submit.
  - `gt backup --dry-run` — same as interactive, but preview only: no clone/push/create, no timestamp or `selectedLast` writes; summary shows `→ would mirror` / `→ would skip (unchanged)`.
  - `gt backup -f` / `gt backup --force` — skip the equal-fingerprint short-circuit; always mirror live repos (still creates missing / recreates inactive). Cannot combine with `--dry-run`.
  - `gt backup --all` — backup every listed repo (no TTY required); does not read or update `selectedLast`.
  - `gt backup --all --dry-run` — preview every listed repo without mirroring or timestamp writes.
  - `gt backup --all -f` / `--force` — same as `--all`, but force every live repo to mirror.
  - `gt backup stale` — interactive multi-select among **stale** repos only (TTY required); stale ⇔ `lastCheckedAt` is null or older than 7 days; same selector UX (`a`/`c`/remember); `--days <n>` overrides threshold (positive integer).
  - `gt backup stale --dry-run` / `-f` / `--force` — same meanings as plain interactive backup, scoped to stale repos.
  - `gt backup stale --all` — backup every stale repo in list order (no TTY); does not read or update `selectedLast`. Combine with `--days`, `--dry-run`, or `--force` as needed. Empty stale set → `No stale repos`, exit `0`.
  - `gt backup add <ssh-url> [<ssh-url> ...]` / `gt backup remove <index|ssh-url>` — maintain the list (`index` is 1-based). Multi-add processes URLs in order, continues after per-URL failures (invalid or duplicate), writes once if any succeed; exit `0` only when every URL succeeds.
- **Config:** `$CLOUD_UTILS_CONFIG_DIR/gt/backups.json` (same config root as `skm`; default under iCloud Backups when unset).
  - List file schema version 4: each repo is `{ url, lastBackupAt, lastCheckedAt, selectedLast }` (`lastBackupAt` / `lastCheckedAt` must be UTC ISO-8601 with `Z`, e.g. `2026-08-08T09:30:00.000Z`, or null; `selectedLast` boolean).
  - `lastBackupAt` — set only after a successful mirror push; unchanged on skip.
  - `lastCheckedAt` — set after a successful check (skip or mirror).
  - `selectedLast` — per-repo flag for the last interactive submit selection; updated on Enter with ≥1 repo selected (whole list rewritten); cancel / empty submit leave flags unchanged; `add` sets `false`.
  - Interactive selector is compact: checkbox + URL only (no Last backup / Last checked lines). Timestamps remain in `backups.json` for skip / stale.
  - v1 string arrays, v2 `{ url, lastBackupAt }`, and v3 lists migrate to v4 on load (`lastCheckedAt: null` for v2; `selectedLast: false` for older schemas).
  - The file is created by `gt backup add`; no checked-in user list is copied.
- **Migration:** old one-shot `gt backup <ssh-url>` / `-n` / `--new` are removed. Use `gt backup add <ssh-url> [<ssh-url> ...]`, then `gt backup` or `gt backup --all`.
- Per URL: missing project → create; live → compare `git ls-remote` fingerprints (heads + tags only) — equal → skip mirror, update `lastCheckedAt` only (`skip` in summary, `→ unchanged`); differ → full mirror (all branches + tags); inactive/soft-deleted → recreate at the base name (never skip). Creates the private `anngth-dev/backups` subgroup when missing (parent `anngth-dev` must already exist). After push, sets the GitLab default branch to `main` if present, otherwise `develop`. Protects `main` and/or `develop` when those branches exist (force-push allowed for later mirror updates).
- Batch summary lists `ok`, `skip`, and `fail` per URL; exit `0` only when there are no `fail` entries (`ok` and `skip` both succeed).
- Requires `git`, `glab` (logged in), and SSH access to both the source and GitLab.
- Backup pushes all branches and tags (not GitLab hidden refs like `refs/environments/*`). `--prune` can delete remote branches/tags that no longer exist on the source.
