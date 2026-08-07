# skm

Profile-based skill manager for the current project. SKM does not manage global
skills. Current state lives in `$CLOUD_UTILS_CONFIG_DIR/skm/profiles.json` and
`projects.json`; an existing `list.json` is one-time migration input only and is
retained unchanged. See [setup.md — Local config](setup.md#local-config).

```bash
skm
skm profile list
skm profile add frontend-project
skm profile add code-review
skm source add vercel-labs/agent-skills -p frontend-project
skm skill add code-review -s vercel-labs/agent-skills -p frontend-project
skm project link frontend-project code-review
skm status
skm install
skm install frontend-project code-review -y
skm uninstall frontend-project
skm uninstall frontend-project -l
```

With no arguments, `skm` opens the project-aware dashboard. Its actions install
linked profiles, link or unlink profiles, install other profiles once, show
status, manage profiles, or exit.

Profile, source, skill, and project commands only read or change saved
configuration; they never install or uninstall project skills. Use the
lifecycle commands for project files.

For `source add`, `source edit`, and `source remove`, omitting `-p` / `--profile`
opens the interactive profile selector. `skill add` and `skill remove` still
require an explicit profile.

| Family | Supported commands |
| --- | --- |
| Profile | `profile list`, `show`, `add`, `rename`, `remove [--force]` |
| Source | `source add`, `edit`, `remove`, `show` |
| Skill | `skill add <skill...> --source <source> --profile <profile>`, `skill remove <skill...> --source <source> --profile <profile>` |
| Project | `project link`, `unlink`, `show`, `list`, `remove` |
| Lifecycle | `status`, `install`, `uninstall` |

| Flag | Meaning |
| --- | --- |
| `-h`, `--help` | Show top-level SKM help |
| `-p`, `--profile` | Select the profile changed by a source or skill command |
| `-s`, `--source` | Select the saved profile source changed by `skill add` or `skill remove` |
| `-k`, `--skill` | Select an explicit skill for `source add`; repeat for multiple skills |
| `-a`, `--all` | Snapshot every currently discovered skill into one profile source |
| `-n`, `--no-skills` | Save a source with an empty skill selection |
| `-y`, `--yes` | Skip SKM and upstream confirmation prompts |
| `-f`, `--force` | Permit linked-profile removal or lifecycle mismatch/untracked replacement/removal |
| `-d`, `--dry-run` | Render a lifecycle plan without mutation |
| `-l`, `--keep-link` | Uninstall files without unlinking the selected profile |

`status`, `install`, and `uninstall` accept one or multiple explicit profile
names. Without profile names they use the current project's linked profiles;
there is no all-profiles mode or `--all-profiles` flag. If an interactive
install has no links, SKM can instead select profiles for a one-time install
and optionally save links after success.

Lifecycle safety rules:

- Correctly installed skills are skipped. Source-mismatch or untracked
  same-name skills require `--force` before replacement or removal.
- Uninstall retains the exact source-and-skill requirements still contributed
  by remaining linked profiles.
- Links offered by the interactive install flow are saved only after the whole
  external install succeeds; uninstall removes links only after all required
  external removals succeed.
- `uninstall --keep-link` removes eligible files but preserves the selected
  links, so a later status reports their required skills as missing.
