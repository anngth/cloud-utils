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
gt backup --help                   # same usage screen as gt --help; no list read
gt backup -h
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
  - `gt backup --help` and `gt backup -h` print the gt usage screen and exit 0. They do not read the backups file. Any other argument list, including `gt backup --all --help`, does not take this path. `gt backup add --help` remains an invalid SSH URL. `gt backup remove --help` remains a remove error.
  - `gt backup add` rejects a later URL whose GitLab project slug (`slugify(owner)-slugify(repo)`, host ignored) is already on the list or earlier in the same add. The error is `Duplicate backup project <slug> (already listed): <earliest stored ssh url>`. Canonical duplicates still use `Duplicate repo (already listed): ...`. The earlier URL stays. If every URL in the invocation fails, the file is not rewritten.
  - During `gt backup` and `gt backup --all`, including `--dry-run` and `--force`, a project slug that appears more than once in that run fails every URL in the group with `Duplicate backup project <slug> (also selected): <other selected ssh urls>`. Those URLs are not created, cloned, pushed, or previewed as mirrors, and their timestamps stay unchanged. Slugs that appear once proceed. Selecting only one URL from a stored pair still backs up that URL. Exit 1 when any result is `fail`. If every selected repo collides, the command does not call `git` or `glab`. Interactive submit still saves `selectedLast` for the whole submission.
  - `gt backup add <ssh-url> [<ssh-url> ...]` / `gt backup remove <index|ssh-url>` — maintain the list (`index` is 1-based). Multi-add processes URLs in order, continues after per-URL failures (invalid or duplicate), writes once if any succeed; exit `0` only when every URL succeeds.
- **Config:** `$CLOUD_UTILS_CONFIG_DIR/gt/backups.json` (same config root as `skm`; default under iCloud Backups when unset).
  - List file schema version 4: each repo is `{ url, lastBackupAt, lastCheckedAt, selectedLast }` (`lastBackupAt` / `lastCheckedAt` must be UTC ISO-8601 with `Z`, e.g. `2026-08-08T09:30:00.000Z`, or null; `selectedLast` boolean).
  - `lastBackupAt` — set only after a successful mirror push; unchanged on skip.
  - `lastCheckedAt` — set after a successful check (skip or mirror); still written on skip or mirror and shown in the selector.
  - `selectedLast` — per-repo flag for the last interactive submit selection; updated on Enter with ≥1 repo selected (whole list rewritten); cancel / empty submit leave flags unchanged; `add` sets `false`.
  - Interactive selector: each repo is a checkbox and SSH URL, then muted `Last backup` and `Last checked` lines, with a blank line between repos. `null` displays as `Last backup: never` and `Last checked: never`. A stored timestamp displays as a relative age plus the local clock. A broken string that reaches the renderer displays `Invalid timestamp`. A non-ISO value in `backups.json` still fails at load with `Invalid lastBackupAt for ...` and does not open the selector.
  - v1 string arrays, v2 `{ url, lastBackupAt }`, and v3 lists migrate to v4 on load (`lastCheckedAt: null` for v2; `selectedLast: false` for older schemas).
  - The file is created by `gt backup add`; no checked-in user list is copied.
- **Migration:** old one-shot `gt backup <ssh-url>` / `-n` / `--new` are removed. Use `gt backup add <ssh-url> [<ssh-url> ...]`, then `gt backup` or `gt backup --all`.
- Per URL: missing project → create; live → compare `git ls-remote` fingerprints (heads + tags only) — equal → skip mirror, update `lastCheckedAt` only (`skip` in summary, `→ unchanged`); differ → full mirror (all branches + tags); inactive/soft-deleted → recreate at the base name (never skip). Creates the private `anngth-dev/backups` subgroup when missing (parent `anngth-dev` must already exist). After push, sets the GitLab default branch to `main` if present, otherwise `develop`. Protects `main` and/or `develop` when those branches exist (force-push allowed for later mirror updates).
- Batch summary lists `ok`, `skip`, and `fail` per URL; exit `0` only when there are no `fail` entries (`ok` and `skip` both succeed).
- Requires `git`, `glab` (logged in to **gitlab.com**), and SSH access to both the source and GitLab. If `glab auth status` fails, `gt backup` exits before mirroring and prints re-login steps (`glab auth logout` / `glab auth login` for `gitlab.com`).
- Backup pushes all branches and tags (not GitLab hidden refs like `refs/environments/*`). `--prune` can delete remote branches/tags that no longer exist on the source.
