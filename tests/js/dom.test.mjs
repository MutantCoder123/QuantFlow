import { test } from 'node:test';
import assert from 'node:assert/strict';
import { escape, html, isSafe, raw, render } from '../../trading_copilot/static/shared/dom.js';

test('every interpolated value is escaped', () => {
  const evil = '<img src=x onerror="alert(1)">`\'&';
  const out = String(html`<p>${evil}</p>`);
  assert.equal(out, '<p>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&#96;&#39;&amp;</p>');
  assert.ok(!out.includes('<img'));
});

test('attribute values cannot break out', () => {
  const out = String(html`<span data-x="${'" onclick="x()'}"></span>`);
  assert.equal(out, '<span data-x="&quot; onclick=&quot;x()"></span>');
});

test('nested html and arrays compose without double-escaping', () => {
  const items = ['a<b', 'c'].map((t) => html`<li>${t}</li>`);
  assert.equal(String(html`<ul>${items}</ul>`), '<ul><li>a&lt;b</li><li>c</li></ul>');
});

test('null, undefined and false render as nothing; 0 renders', () => {
  assert.equal(String(html`${null}${undefined}${false}${0}`), '0');
});

test('raw is trusted markup, and render refuses plain strings', () => {
  assert.equal(String(html`${raw('<b>x</b>')}`), '<b>x</b>');
  assert.ok(isSafe(html`x`));
  assert.throws(() => render({}, '<b>x</b>'), TypeError);
  const el = {};
  render(el, html`<i>${'<'}</i>`);
  assert.equal(el.innerHTML, '<i>&lt;</i>');
  assert.equal(escape(5), '5');
});
