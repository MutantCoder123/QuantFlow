// The app bar's two status readings, in words. Pure: no DOM, no clock of its
// own, so both run under node --test.
//
// marketClock: "11:42 · closes in 3 h 48 m". The session is 09:15-15:30 IST on
// weekdays (pipeline_guard.is_market_open). When the socket says whether the
// market is live, that wins: the server also knows about holidays we don't.
//
// feedHealth: "Live", "Live · 3 stale", "Prices stopped", "Reconnecting". A
// price is stale past the paper engine's stale limit (15 s by default), the
// same limit the old red banner used.

import { hm } from '../shared/format.js';

const OPEN_MIN = 9 * 60 + 15;
const CLOSE_MIN = 15 * 60 + 30;
const IST_OFFSET_S = 19800;

function istParts(ts) {
  const d = new Date((ts + IST_OFFSET_S) * 1000);
  return { weekday: d.getUTCDay(), minutes: d.getUTCHours() * 60 + d.getUTCMinutes(), seconds: d.getUTCSeconds() };
}

/** "3 h 48 m", "17 min", "under a minute". */
export function span(minutes) {
  const m = Math.max(0, Math.floor(minutes));
  if (m < 1) return 'under a minute';
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} m` : `${h} h`;
}

/** Is the market open by the clock alone (weekday, 09:15-15:30 IST)? */
export function openByClock(ts) {
  const { weekday, minutes } = istParts(ts);
  return weekday >= 1 && weekday <= 5 && minutes >= OPEN_MIN && minutes < CLOSE_MIN;
}

/**
 * {time, words, open}. `live` is true/false when the socket has said, else
 * undefined (then the clock decides).
 */
export function marketClock(ts, live) {
  const open = typeof live === 'boolean' ? live : openByClock(ts);
  const { weekday, minutes } = istParts(ts);
  const time = hm(ts);
  if (open) return { time, open, words: `closes in ${span(CLOSE_MIN - minutes)}` };
  const weekdayMorning = weekday >= 1 && weekday <= 5 && minutes < OPEN_MIN;
  if (weekdayMorning) return { time, open, words: `opens in ${span(OPEN_MIN - minutes)}` };
  return { time, open, words: 'closed' };
}

/** Whether the socket's stocks report a live market: true, false, or undefined when it hasn't said. */
export function marketLive(live) {
  const states = Object.values((live && live.global_state) || {});
  if (!states.length) return undefined;
  return states.some((s) => s && s.market_state === 'LIVE');
}

function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/**
 * {tone: ok|amber|down|idle, words, stale, total, stopped}.
 * `connected` is the socket's state; `staleAfter` the stale limit in seconds.
 */
export function feedHealth(live, { connected = true, staleAfter = 15 } = {}) {
  if (!connected) return { tone: 'down', words: 'Reconnecting', stale: 0, total: 0, stopped: false };
  if (!live) return { tone: 'idle', words: 'Connecting', stale: 0, total: 0, stopped: false };
  const states = Object.values(live.global_state || {}).filter((s) => s && s.market_state === 'LIVE');
  if (!states.length) {
    const any = Object.keys(live.global_state || {}).length;
    return { tone: 'idle', words: any ? 'Idle' : 'Waiting for prices', stale: 0, total: 0, stopped: false };
  }
  const ages = states.map((s) => Number(s.data_age_s)).filter((a) => Number.isFinite(a));
  const stale = ages.filter((a) => a > staleAfter).length;
  const total = states.length;
  if (ages.length && stale === ages.length) {
    return { tone: 'down', words: 'Prices stopped', stale, total, stopped: true, age: median(ages) };
  }
  if (stale) return { tone: 'amber', words: `Live · ${stale} stale`, stale, total, stopped: false };
  return { tone: 'ok', words: 'Live', stale: 0, total, stopped: false };
}
