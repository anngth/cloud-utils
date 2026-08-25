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
what is installed on disk in the current project. Submit opens a full-screen
Install/Remove preview with Yes/No. Choosing No or pressing `q` prints nothing.
Choosing Yes runs the changes and prints one combined Changes summary.

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
| `-d`, `--dry-run` | Render a lifecycle plan without mutation |

Source and index tokens follow the same rule as `gt backup remove`:

- A token that is entirely an integer is a **1-based index** into the catalog.
- Otherwise the token is a source id (canonicalized before lookup).

Catalog uniqueness:

- `source add` and `source edit` require skill names to be unique across all
  catalog sources. The whole command fails and lists the skill plus each owning
  source.

Lifecycle safety rules:

- Correctly installed skills are skipped on `add`.
- Source-mismatch or untracked same-name skills **block install** with an error;
  SKM does not replace skills in place.
- `remove` (and interactive deselection) uninstalls by catalog skill name even
  when the installed copy has mismatched provenance or is untracked.
- `remove` uninstalls only skills from the selected sources that are not still
  required by other catalog sources.

`status` output:

- Numbered catalog sources with indented skills.
- `■` installed, `□` missing, `▲` source mismatch or untracked.
- Overflow sections (Source mismatch, Untracked, Extra, Desired-source conflict)
  appear only when non-empty.
