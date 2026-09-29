import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../../trading_copilot/static/shared/store.js';
import { connectLive } from '../../trading_copilot/static/shared/live.js';
import { request } from '../../trading_copilot/static/shared/api.js';

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

test('the live paper block comes from the shell store, not a second socket', () => {
  const app = createStore({ live: null });
  const s = createStore({});
  const off = connectLive(s, app, () => 100);
  app.set({ live: { global_state: {} } });
  assert.equal(s.get().live, undefined);        // no paper block: nothing changes
  app.set({ live: { paper: { engine_ok: true } } });
  assert.deepEqual(s.get().live, { engine_ok: true });
  assert.equal(s.get().liveAt, 100);
  off();
  app.set({ live: { paper: { engine_ok: false } } });
  assert.deepEqual(s.get().live, { engine_ok: true });   // unsubscribed
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
