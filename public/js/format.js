// Formatting and small pure helpers.

/** "2d 3h", "4h 10m", "5m 2s" or "9s". */
export function fmtDur(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (d) return `${d}d ${hh}h`;
  if (hh) return `${hh}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

/** Time of day, with the date in front unless it's today. `ts` is epoch seconds. */
export function fmtTime(ts) {
  const d = new Date(ts * 1000);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (new Date().toDateString() === d.toDateString()) return time;
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`;
}

export const pad2 = (n) => String(n).padStart(2, '0');

/** 1st, 2nd, 3rd, 4th, 11th, 21st, ... */
export const dayOrdinal = (d) => `${d}${(d % 100 >= 11 && d % 100 <= 13) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[d % 10] || 'th')}`;

export const initials = (name) => name.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || '?';

/** Step distance (movement): diagonal steps count the same as straight ones. */
export const dist = (a, b) => Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y));

/** Range distance: straight-line distance between tile centres. */
export const rangeDist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

/** Whether b is within range r of a (exact, in integers, like the server). */
export const inRange = (a, b, r) => (a.x - b.x) ** 2 + (a.y - b.y) ** 2 <= r * r;

/** A range distance for display: whole numbers as is, others to one decimal. */
export const fmtDist = (d) => (Number.isInteger(d) ? String(d) : d.toFixed(1));

/** The tiles around (x, y) that are on an N×N board. */
export function neighbours({ x, y }, N) {
  const out = [];
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      const nx = x + dx, ny = y + dy;
      if ((dx || dy) && nx >= 0 && ny >= 0 && nx < N && ny < N) out.push({ x: nx, y: ny });
    }
  }
  return out;
}

/** The last element of a list, or undefined. */
export const last = (list) => list[list.length - 1];

/** {key: number of items with that key}. */
export function countBy(list, key) {
  const out = {};
  for (const item of list) out[key(item)] = (out[key(item)] || 0) + 1;
  return out;
}

export const sum = (nums) => nums.reduce((a, b) => a + b, 0);
