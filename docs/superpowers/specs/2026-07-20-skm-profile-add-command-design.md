# SKM Profile Add Command Design

## Goal

Rename the public profile creation command from `skm profile create <profile>`
to `skm profile add <profile>` so profile, source, and skill configuration use
consistent `add` terminology.

## Command behavior

- `skm profile add <profile>` creates and persists a new empty profile using
  the existing profile validation and persistence behavior.
- `skm profile create <profile>` is removed without a compatibility alias and
  returns the existing unknown-profile-command error.
- Missing or extra positional arguments for the new command report
  `Usage: skm profile add <profile>`.
- Internal domain helpers such as `createProfile()` retain their current names;
  only the public CLI action changes.

## Help and documentation

- Top-level `skm -h` lists `skm profile add <profile>` and no longer lists
  `profile create`.
- `docs/commands.md` examples and the supported-command table use `profile add`.
- No legacy `profile create` references remain in user-facing documentation.

## Verification

- A management-command test proves that `profile add` writes the new profile.
- A regression test proves that `profile create` is no longer accepted.
- The exact help-output test expects the new command syntax.
- The complete SKM test suite passes and a repository search finds no stale
  public `profile create` references.
