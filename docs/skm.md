# skm

Source-catalog skill manager for the current project. SKM does not manage global
skills. The catalog lives in `$CLOUD_UTILS_CONFIG_DIR/skm/sources.json`; legacy
`profiles.json`, `projects.json`, and `list.json` are migration input only and
are retained unchanged after migration. See [setup.md — Local config](setup.md#local-config).

```bash
skm
skm source add vercel-labs/agent-skills -a
skm source add anthropics/skills -n
skm add 1
skm add --all -y
skm status
skm remove 1
skm remove --all
```

With no arguments, `skm` opens an interactive catalog selector. Checkboxes reflect
what is installed on disk in the current project. Submit installs newly selected
skills and uninstalls deselected catalog skills.

`source add` and `source remove` change the global catalog only. Use `add` and
`remove` to install or uninstall catalog skills in the current project.

| Family | Supported commands |
| --- | --- |
| Lifecycle | `add <source\|index...> [--all]`, `remove <source\|index...> [--all]`, `status` |
| Catalog | `source add <source>`, `source remove <source\|index>` |

| Flag | Meaning |
| --- | --- |
| `-h`, `--help` | Show top-level SKM help |
| `-k`, `--skill` | Select explicit skills for `source add`; repeat for multiple skills |
| `-a`, `--all` | Install/uninstall every catalog source (`add`/`remove`), or snapshot every discovered skill (`source add`) |
| `-n`, `--no-skills` | Save a catalog source with an empty skill selection |
| `-y`, `--yes` | Skip SKM and upstream confirmation prompts |
| `-f`, `--force` | Permit mismatch/untracked skill replacement or removal |
| `-d`, `--dry-run` | Render a lifecycle plan without mutation |

Source and index tokens follow the same rule as `gt backup remove`:

- A token that is entirely an integer is a **1-based index** into the catalog.
- Otherwise the token is a source id (canonicalized before lookup).

Lifecycle safety rules:

- Correctly installed skills are skipped. Source-mismatch or untracked
  same-name skills require `--force` before replacement or removal.
- `remove` uninstalls only skills from the selected sources that are not still
  required by other catalog sources.
- Interactive `skm` blocks on planner conflicts without `--force`.
