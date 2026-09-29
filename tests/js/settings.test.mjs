// The Settings sheet's pure parts: watchlist edits, the news form, the sync list.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { addItem, diff, parseNews, removeItem, sectionOf, syncSymbols } from '../../trading_copilot/static/settings/model.js';

const WL = [{ token: '1', symbol: 'SAIL-EQ', exchange: 'NSE' }, { token: '2', symbol: 'IDEA', exchange: 'NSE' }];

test('adding a stock is once only, by token or by symbol', () => {
  assert.equal(addItem(WL, { token: '9', symbol: 'SAIL', exchange: 'NSE' }).length, 2);   // SAIL-EQ is SAIL
  assert.equal(addItem(WL, { token: '2', symbol: 'X' }).length, 2);
  const out = addItem(WL, { token: 3, symbol: 'NMDC' });
  assert.deepEqual(out[2], { token: '3', symbol: 'NMDC', exchange: 'NSE' });
  assert.equal(addItem(WL, null), WL);
  assert.deepEqual(removeItem(WL, 1).map((x) => x.token), ['2']);
});

test('the diff counts additions and removals', () => {
  assert.deepEqual(diff(WL, [WL[0], { token: '3' }]), { added: 1, removed: 1 });
  assert.deepEqual(diff(WL, WL), { added: 0, removed: 0 });
});

test('the news form: at most every 30 seconds', () => {
  assert.deepEqual(parseNews({ interval: '120', model: 'ollama:qwen2.5:7b' }), { ok: true, interval: 120, model: 'ollama:qwen2.5:7b' });
  assert.equal(parseNews({ interval: '10' }).ok, false);
  assert.equal(parseNews({ interval: 'soon' }).error, 'Fetch at most every 30 seconds.');
});

test('sections and the sync list', () => {
  assert.equal(sectionOf('news'), 'news');
  assert.equal(sectionOf('nonsense'), 'watchlist');
  assert.deepEqual(syncSymbols([...WL, { symbol: 'Nifty 50' }, { symbol: 'SAIL' }]), ['SAIL', 'IDEA']);
});
