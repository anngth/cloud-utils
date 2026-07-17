# SKM Node.js 24 Baseline Design

## Goal

Change the SKM JavaScript migration compatibility baseline from Node.js 20 to Node.js 24 without changing runtime behavior or adding a version gate.

## Scope

- Update every Node.js 20 compatibility reference in `docs/skm-js-migration-spec.md` and `docs/skm-js-migration-plan.md` to Node.js 24.
- State the supported runtime as Node.js 24 or newer.
- Update the implementation-plan verification command to run the complete SKM test suite with Node.js 24.
- Verify that no Node.js 20 baseline references remain and that the complete suite passes on Node.js 24.

## Non-goals

- No runtime Node version check in `mac_scripts/skm` or `cli.mjs`.
- No package-manager metadata, CI configuration, dependencies, or production-code changes.
- No changes to commands, data, terminal UI, streams, exit statuses, or compatibility behavior.

## Verification

Run the full built-in Node test suite through a Node.js 24 runtime, scan the repository documentation for stale Node.js 20 baseline references, and run `git diff --check`.

## Expected Files

- `docs/skm-js-migration-spec.md`
- `docs/skm-js-migration-plan.md`

