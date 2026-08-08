export function formatLastBackupLabel(isoString, now = new Date()) {
  const then = new Date(isoString);
  if (Number.isNaN(then.getTime())) {
    throw new TypeError(`Invalid lastBackupAt: ${isoString}`);
  }
  const ms = Math.max(0, now.getTime() - then.getTime());
  const sec = Math.floor(ms / 1000);
  let relative;
  if (sec < 60) relative = "just now";
  else if (sec < 3600) {
    const n = Math.floor(sec / 60);
    relative = n === 1 ? "1 minute ago" : `${n} minutes ago`;
  } else if (sec < 86400) {
    const n = Math.floor(sec / 3600);
    relative = n === 1 ? "1 hour ago" : `${n} hours ago`;
  } else {
    const n = Math.floor(sec / 86400);
    relative = n === 1 ? "1 day ago" : `${n} days ago`;
  }
  const y = then.getFullYear();
  const mo = String(then.getMonth() + 1).padStart(2, "0");
  const d = String(then.getDate()).padStart(2, "0");
  const h = String(then.getHours()).padStart(2, "0");
  const mi = String(then.getMinutes()).padStart(2, "0");
  return `Last backup: ${relative} (${y}-${mo}-${d} ${h}:${mi})`;
}
