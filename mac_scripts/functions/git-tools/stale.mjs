/**
 * Whether a backup list entry has not been checked within the threshold.
 *
 * @param {{ lastCheckedAt?: string | null }} entry
 * @param {{ now?: Date, days?: number }} [options]
 * @returns {boolean}
 */
export function isStaleRepo(entry, { now = new Date(), days = 7 } = {}) {
  if (entry.lastCheckedAt == null) return true;
  const then = new Date(entry.lastCheckedAt);
  if (Number.isNaN(then.getTime())) return true;
  const ms = days * 24 * 60 * 60 * 1000;
  return now.getTime() - then.getTime() > ms;
}
