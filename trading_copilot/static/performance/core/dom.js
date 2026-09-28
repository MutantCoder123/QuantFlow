// Safe HTML for the Performance tab. Every value interpolated into html``
// is escaped unless it is itself the result of html`` (or raw()). Symbols,
// AI rationales and error text all come from outside the page, so nothing
// reaches innerHTML unescaped.

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' };

export function escape(v) {
  return String(v).replace(/[&<>"'`]/g, (c) => ESC[c]);
}

class Safe {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}

// Trusted markup, e.g. an icon string written in this codebase. Never pass data.
export const raw = (s) => new Safe(String(s));

function piece(v) {
  if (v === null || v === undefined || v === false) return '';
  if (v instanceof Safe) return v.s;
  if (Array.isArray(v)) return v.map(piece).join('');
  return escape(v);
}

export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((v, i) => { out += piece(v) + strings[i + 1]; });
  return new Safe(out);
}

export const isSafe = (v) => v instanceof Safe;

export function render(el, markup) {
  if (!(markup instanceof Safe)) throw new TypeError('render() takes html`` output only');
  el.innerHTML = markup.s;
}

export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
