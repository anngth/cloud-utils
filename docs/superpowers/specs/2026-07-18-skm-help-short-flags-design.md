# SKM Help and Short Flags Design

## Goal

Make `skm -h` complete enough to use without opening separate documentation,
while keeping the output compact. Every supported subcommand must show its exact
syntax. Every existing long flag must have a short alias displayed beside it and
accepted by the parser.

## Help structure

The help output is grouped by purpose in this order:

1. Entry points
2. Lifecycle commands
3. Profile commands
4. Source commands
5. Skill commands
6. Project commands
7. Three concise behavioral notes

Each subcommand occupies one line where practical. A command may wrap onto one
continuation line when keeping all alternatives on one line would reduce
readability. Each signature has a short action description; descriptions do not
repeat details already expressed by the syntax.

The notation is:

- `[]` means optional.
- `...` means repeatable.
- `(short | long)` means either alias is accepted.

## Complete command surface

```text
Usage: skm [command]
       skm                                      Open interactive dashboard
       skm (help | -h | --help)                Show this help

Lifecycle
  skm status [profile...]                      Compare desired and installed skills
  skm install [profile...] [(-y | --yes)] [(-f | --force)] [(-d | --dry-run)]
                                               Install selected profile skills
  skm uninstall [profile...] [(-y | --yes)] [(-f | --force)]
      [(-d | --dry-run)] [(-l | --keep-link)]  Uninstall selected profile skills

Profiles
  skm profile list                             List profiles
  skm profile show <profile>                   Show one profile
  skm profile create <profile>                 Create a profile
  skm profile rename <old> <new>               Rename a profile
  skm profile remove <profile> [(-f | --force)]
                                               Remove a profile

Sources
  skm source add <source> (-p | --profile) <profile>
      [[(-k | --skill) <skill>]... | (-a | --all) | (-n | --no-skills)]
                                               Add a source and select skills
  skm source edit <source> (-p | --profile) <profile>
                                               Edit selected source skills
  skm source remove <source> (-p | --profile) <profile>
                                               Remove a source from a profile
  skm source show <source>                     Show available source skills

Skills
  skm skill add <skill...> (-s | --source) <source>
      (-p | --profile) <profile>               Add skills to a profile source
  skm skill remove <skill...> (-s | --source) <source>
      (-p | --profile) <profile>               Remove skills from a profile source

Projects
  skm project link <profile...>                Link profiles to the current project
  skm project unlink [profile...]              Unlink profiles from the current project
  skm project show                             Show the current project
  skm project list                             List registered projects
  skm project remove <project-path>            Remove a project registration

Notes
  Profile names omitted from lifecycle commands use current project links.
  Profile, source, skill, and project commands change configuration only.
  --force permits linked-profile removal or mismatch/untracked skill changes.
```

Color and the existing SKM renderer style remain unchanged. Alignment may be
implemented with existing renderer primitives rather than hard-coded terminal
width assumptions.

## Short flag mapping

| Short | Long | Scope |
|---|---|---|
| `-h` | `--help` | Top-level help |
| `-p` | `--profile` | Source and skill management |
| `-s` | `--source` | Skill management |
| `-k` | `--skill` | Source add; repeatable |
| `-a` | `--all` | Source add |
| `-n` | `--no-skills` | Source add |
| `-f` | `--force` | Profile removal and lifecycle commands |
| `-y` | `--yes` | Install and uninstall |
| `-d` | `--dry-run` | Install and uninstall |
| `-l` | `--keep-link` | Uninstall only |

Short and long forms are semantically identical. Existing validation remains in
force: unknown flags are rejected, value flags require a value, repeated
non-repeatable value flags are rejected, and source selection modes remain
mutually exclusive even when short and long forms are mixed.

## Implementation boundaries

- Update the management option parser with `-s`, `-k`, `-a`, `-n`, and `-f`.
- Update the lifecycle option parser with `-y`, `-f`, `-d`, and `-l`.
- Keep the existing top-level `-h`, `--help`, and `help` routing.
- Replace the current abbreviated usage renderer with the complete grouped help.
- Do not add per-subcommand help, new commands, or new long flags.
- Update command documentation only where it describes available aliases.

## Testing

Tests must be written before production changes and must cover:

- The help output contains all 19 business subcommands, dashboard entry point,
  help aliases, exact argument forms, short/long flag pairs, and behavioral notes.
- Every short flag produces the same parsed behavior as its long form.
- Repeatable `-k` works and mixed source-selection modes remain rejected.
- Unsupported short flags remain rejected.
- Existing long-flag behavior and the full SKM suite do not regress.
