import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../../trading_copilot/static/performance/core/store.js';
import { connectLive } from '../../trading_copilot/static/performance/core/live.js';
import { request } from '../../trading_copilot/static/performance/core/api.js';

test('store notifies with the changed keys, and only on change', () => {
  const s = createStore({ range: 'all', n: 1 });
  const calls = [];
  const off = s.subscribe((st, changed) => calls.push([st.range, changed]));
  s.set({ range: 'today' });
  s.set({ range: 'today' });                     // no change: no call
  s.set({ n: 2, range: 'today' });
  assert.deepEqual(calls, [['today', ['range']], ['today', ['n']]]);
  off();
  s.set({ range: '5d' });
  assert.equal(calls.length, 2);
});

test('live takes the paper block from the page socket event', () => {
  const s = createStore({});
  const target = new EventTarget();
  const off = connectLive(s, target, () => 100);
  target.dispatchEvent(new CustomEvent('qf:ws', { detail: { global_state: {} } }));
  assert.equal(s.get().live, undefined);        // no paper block: nothing changes
  target.dispatchEvent(new CustomEvent('qf:ws', { detail: { paper: { engine_ok: true } } }));
  assert.deepEqual(s.get().live, { engine_ok: true });
  assert.equal(s.get().liveAt, 100);
  off();
});

test('api calls resolve to ok/error, never throw', async () => {
  const ok = await request('/x', { fetchImpl: async () => ({ ok: true, json: async () => ({ status: 'success', a: 1 }) }) });
  assert.deepEqual(ok, { ok: true, data: { status: 'success', a: 1 } });
  const app = await request('/x', { fetchImpl: async () => ({ ok: true, json: async () => ({ status: 'error', message: 'Unknown range' }) }) });
  assert.equal(app.ok, false);
  assert.equal(app.error, 'Unknown range');
  const http = await request('/x', { fetchImpl: async () => ({ ok: false, status: 500 }) });
  assert.equal(http.error, 'The web process answered 500.');
  const down = await request('/x', { fetchImpl: async () => { throw new Error('refused'); } });
  assert.equal(down.error, 'The web process could not be reached.');
  const junk = await request('/x', { fetchImpl: async () => ({ ok: true, json: async () => { throw new SyntaxError(); } }) });
  assert.equal(junk.ok, false);
});
