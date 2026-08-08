import assert from "node:assert/strict";
import test from "node:test";
import {
  formatLastBackupLabel,
  formatLastCheckedLabel,
} from "../last-backup.mjs";

test("formatLastBackupLabel uses relative + local datetime", () => {
  const now = new Date("2026-08-08T10:00:00.000Z");
  const iso = "2026-08-08T08:00:00.000Z"; // 2 hours earlier
  const label = formatLastBackupLabel(iso, now);
  assert.match(label, /^Last backup: 2 hours ago \(/);
  assert.match(label, /\)$/);
  // Local clock dependent: assert HH:mm pattern inside parens
  assert.match(label, /\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}\)/);
});

test("formatLastBackupLabel just now under one minute", () => {
  const now = new Date("2026-08-08T10:00:30.000Z");
  assert.equal(
    formatLastBackupLabel("2026-08-08T10:00:00.000Z", now).startsWith(
      "Last backup: just now (",
    ),
    true,
  );
});

test("formatLastBackupLabel singular minute and day", () => {
  const now = new Date("2026-08-08T10:00:00.000Z");
  assert.match(
    formatLastBackupLabel("2026-08-08T09:59:00.000Z", now),
    /1 minute ago/,
  );
  assert.match(
    formatLastBackupLabel("2026-08-07T10:00:00.000Z", now),
    /1 day ago/,
  );
});

test("formatLastCheckedLabel uses Last checked prefix", () => {
  const now = new Date("2026-08-08T10:00:00.000Z");
  const label = formatLastCheckedLabel("2026-08-08T08:00:00.000Z", now);
  assert.match(label, /^Last checked: 2 hours ago \(/);
});

test("formatLastBackupLabel still works", () => {
  const now = new Date("2026-08-08T10:00:00.000Z");
  assert.match(
    formatLastBackupLabel("2026-08-08T08:00:00.000Z", now),
    /^Last backup: 2 hours ago \(/,
  );
});
