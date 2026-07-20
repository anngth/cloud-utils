# SKM Skill List Readability Design

**Date:** 2026-07-20

## Goal

Make skill-oriented output easy to scan across every `skm` command. Whenever a renderer presents skills as a list, each skill name must stand out from its description, source, profiles, status, or other trailing context. Adjacent skill entries must have one blank framed line between them.

The design follows the useful distinction in `npx skills` 1.5.19: skill names receive their own visual emphasis, and detailed available-skill output uses whitespace to separate records. SKM keeps its existing green visual identity instead of copying the upstream cyan palette.

## Rendering Rules

1. Render every skill name in bright green.
2. Keep the list marker and trailing context in their semantic colors. For example, a mismatch keeps its red marker while the skill name remains bright green and the source/profile suffix remains visually secondary.
3. Put exactly one framed blank line (`│`) between adjacent skill entries.
4. Do not add a separator solely because a skill is the final item. An enclosing section may retain its existing trailing spacer before the next heading.
5. Treat an entry with only a skill name the same as an entry with trailing context: it is still highlighted and separated from adjacent skills.
6. Preserve the current heading, summary, warning, and empty-state layout.

Conceptual output:

```text
◆  Missing
│  ■ brainstorming — obra/superpowers — required by backend
│
│  ■ test-driven-development — obra/superpowers — required by backend
```

Color is not shown in the example: each skill name is bright green, while markers and suffixes retain the section's status styling.

## Scope

Apply the rules to every UI surface that is semantically a skill list:

- `skm profile show`: selected skills nested below each source.
- `skm source show`: available skill names followed by descriptions.
- `skm source add` and `skm source edit`: interactive available-skill selectors.
- `skm skill add` and `skm skill remove`: changed-skill result lists.
- `skm status`: installed, missing, source-mismatch, untracked, extra, and desired-source-conflict skill lists.
- `skm install`: interactive skill selection and install-plan sections.
- `skm uninstall`: uninstall-plan sections.
- Install and uninstall execution summaries when they enumerate succeeded, failed, or incomplete-replacement skills.

Do not apply skill-list formatting to:

- Profile, source, project, or dashboard action lists.
- Profile selectors used by install, project linking, or the dashboard.
- Help syntax such as `<skill>` and prose that only mentions the word “skill”.
- Compact warnings and errors that mention skill names within a sentence rather than enumerate records.
- Retry commands. They remain plain, copy-friendly command text; the related skill entry carries the visual emphasis.
- JSON or other machine-readable upstream output.

## UI Architecture

Keep the change inside the presentation boundary.

### Static output

Add a semantic skill-row renderer in `ui.mjs`. It accepts a skill name, optional suffix text, marker/status color, and optional suffix color. A companion list renderer writes multiple rows and inserts the framed separator only between rows.

Existing renderers provide structured names and suffixes instead of assembling a complete plain string before rendering. This avoids guessing that the first token is a skill name and prevents accidental highlighting of profiles, sources, paths, actions, or shell commands.

Execution summaries may receive batched operation records. They should enumerate the affected skill names as individual semantic skill rows while keeping a batch retry command as separate plain guidance. The retry command must not be duplicated once per skill.

### Interactive output

Skill selector producers mark their entries with `kind: "skill"`. The generic selector checks this metadata to use the skill-name treatment and insert separators. Profile and other selector entries omit the marker and retain their current compact rendering.

The selector's cursor and selected state continue to be conveyed by the existing marker. Skill-name color therefore does not need to change as the cursor moves.

## Data and Behavior

No command grammar, config schema, lifecycle planning, discovery, installation, or uninstallation behavior changes. No new flags are introduced.

The new metadata is presentation-only and must not alter selector values, initial selections, cursor movement, selection submission, or cancellation.

Source redaction remains mandatory. Renderers must continue passing source values through the existing redaction boundary before displaying suffixes or retry guidance.

## Error Handling and Compatibility

- Empty sections continue to show `None` or the existing empty-state text without skill formatting.
- A single skill does not create a skill-to-skill separator; an existing section spacer may still follow it.
- Multiple skill lists in one screen manage spacing independently; separators never bleed into the following section.
- ANSI styling must reset immediately after each styled fragment so suffix colors and later lines cannot inherit bright green.
- Existing public UI methods and command exit codes remain unchanged.

## Testing

Use renderer-level tests to verify both ANSI boundaries and whitespace structure:

1. A skill name is wrapped in bright-green ANSI codes while its suffix is outside that color span.
2. Two adjacent skills contain exactly one framed blank line between them.
3. A single skill creates no skill-to-skill separator, independent of any existing section spacer.
4. Status and plan markers retain their semantic colors.
5. Source descriptions, source identities, profile requirements, and retry commands remain present and correctly redacted.
6. Skill selectors highlight and separate entries, while profile selectors remain compact.
7. Execution summaries enumerate batched skills without duplicating the batch retry command.

Run the full SKM test suite after the focused UI tests, then perform live smoke checks for `source show`, `profile show`, `status`, install dry-run, uninstall dry-run, and an interactive skill selector. Strip ANSI in an additional assertion or inspection when checking exact line spacing.

## Acceptance Criteria

- Every scoped skill list highlights only skill names in bright green.
- Every pair of adjacent skill entries has exactly one blank framed line between them.
- Non-skill lists retain their current compact layout.
- Retry commands remain plain and appear once per failed batch.
- All existing SKM behavior and tests remain valid, with new coverage for the formatting contract.
