# SKM Profiles Example Design

**Date:** 2026-07-18

## Goal

Replace the obsolete `list.json.example` artifact with a human-readable example
of the current `profiles.json` schema without changing SKM bootstrap or migration
behavior.

## File layout

Rename:

```text
mac_scripts/functions/skills-manager/list.json.example
```

to:

```text
mac_scripts/functions/skills-manager/profiles.json.example
```

The new example contains schema version `1`, one profile named `default`, and
the two sources already present in the user-edited legacy example. Each source
has an empty `skills` array, which saves the source without selecting any skill.
The example does not snapshot upstream skill names that may change over time.

```json
{
  "version": 1,
  "profiles": [
    {
      "name": "default",
      "sources": [
        {
          "source": "anthropics/skills",
          "skills": []
        },
        {
          "source": "vercel-labs/agent-skills",
          "skills": []
        }
      ]
    }
  ]
}
```

## Runtime behavior

The example is documentation only. SKM continues to create `profiles.json` and
`projects.json` automatically. Existing `<config>/skm/list.json` files remain
accepted as one-time legacy migration input; the runtime does not read either
repository example file.

Removing the misleading `list.json.example` name ensures a current-schema file
cannot be mistaken for valid legacy migration input.

## Documentation

Update the local configuration table in `docs/setup.md` to list
`profiles.json.example` as an optional reference template while preserving the
statement that SKM creates its live configuration automatically.

No historical migration document is rewritten because those documents describe
the former implementation and legacy `list.json` contract.

## Validation

- Parse `profiles.json.example` as JSON.
- Validate its value with `validateProfilesDocument`.
- Confirm `list.json.example` no longer exists and `profiles.json.example` does.
- Run the SKM test suite to ensure bootstrap and legacy migration behavior remain
  unchanged.

## Out of scope

- Copying the example into the user's config directory.
- Changing profile bootstrap defaults.
- Changing legacy `list.json` migration behavior.
- Adding or selecting individual skills for either source; users select them
  through `skm source edit` or `skm skill add`.
