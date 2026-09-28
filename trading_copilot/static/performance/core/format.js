// Number and time formatting for the Performance tab. Pure: no DOM, so it
// runs under `node --test`. Unknown values render as an em dash, never as 0.

export const MINUS = '−';
export const DASH = '—';
const IST = 'Asia/Kolkata';

const known = (n) => typeof n === 'number' && Number.isFinite(n);

// Indian digit grouping of |n|: 1012480 -> "10,12,480".
export function group(n, dp = 0) {
  if (!known(n)) return DASH;
  const [int, frac] = Math.abs(n).toFixed(dp).split('.');
  let head = int.slice(0, -3);
  const tail = int.slice(-3);
  head = head.replace(/\B(?=(\d{2})+(?!\d))/g, ',');
  return (head ? `${head},${tail}` : tail) + (frac ? `.${frac}` : '');
}

function signOf(n, dp, signed) {
  const zero = Math.round(Math.abs(n) * 10 ** dp) === 0;
  if (zero) return '';
  if (n < 0) return MINUS;
  return signed ? '+' : '';
}

// Rupees. Signed by default: "+₹2,100", "−₹630", "₹0".
export function inr(n, { signed = true, dp = 0 } = {}) {
  if (!known(n)) return DASH;
  return `${signOf(n, dp, signed)}₹${group(n, dp)}`;
}

// R-multiples: "+0.62 R".
export function r(n) {
  if (!known(n)) return DASH;
  return `${signOf(n, 2, true)}${Math.abs(n).toFixed(2)} R`;
}

export function pct(n, { dp = 1, signed = false } = {}) {
  if (!known(n)) return DASH;
  return `${signOf(n, dp, signed)}${Math.abs(n).toFixed(dp)}%`;
}

export function num(n, dp = 2) {
  if (!known(n)) return DASH;
  return `${signOf(n, dp, false)}${Math.abs(n).toFixed(dp)}`;
}

// Month names are fixed here: locale data differs between
// browsers ("Sep" vs "Sept"), and the page should read the same everywhere.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const IST_PARTS = new Intl.DateTimeFormat('en-US', {
  timeZone: IST, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric',
  weekday: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
});

function parts(ts) {
  const p = Object.fromEntries(IST_PARTS.formatToParts(new Date(ts * 1000)).map((x) => [x.type, x.value]));
  return { ...p, month: MONTHS[Number(p.month) - 1] };
}

// Epoch seconds -> IST, 24-hour.
export const hms = (ts) => (known(ts) ? (({ hour, minute, second }) => `${hour}:${minute}:${second}`)(parts(ts)) : DASH);
export const hm = (ts) => (known(ts) ? (({ hour, minute }) => `${hour}:${minute}`)(parts(ts)) : DASH);
export const dayMonth = (ts) => (known(ts) ? (({ day, month }) => `${day} ${month}`)(parts(ts)) : DASH);
export const weekdayDayMonth = (ts) =>
  (known(ts) ? (({ weekday, day, month }) => `${weekday} ${day} ${month}`)(parts(ts)) : DASH);

// "2026-09-28" -> "28 Sep", read as an IST calendar day.
export function isoDay(day) {
  if (!day) return DASH;
  const [y, m, d] = day.split('-').map(Number);
  return dayMonth(Date.UTC(y, m - 1, d, 6, 30) / 1000);
}

// Seconds -> "2 s", "3 min", "1 h 12 min".
export function duration(s) {
  if (!known(s) || s < 0) return DASH;
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// A gated metric ({value, n, min_n, status}) that has no value yet.
export function notYet(m) {
  if (!m) return 'not yet';
  if (m.status === 'insufficient') return `not yet (${m.n} of ${m.min_n})`;
  if (m.status === 'undefined') return 'no value yet';
  return 'not yet';
}

// "profit" | "loss" | "flat" -- the tone class for a signed figure.
export function tone(n) {
  if (!known(n) || Math.abs(n) < 1e-9) return 'flat';
  return n > 0 ? 'profit' : 'loss';
}
