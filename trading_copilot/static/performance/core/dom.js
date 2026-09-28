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

// Replace el's content. Focus survives a re-render: an element carrying
// data-key gets focus back if it (by key) had it before.
export function render(el, markup) {
  if (!(markup instanceof Safe)) throw new TypeError('render() takes html`` output only');
  const doc = el.ownerDocument;
  const active = doc && doc.activeElement;
  const key = active && el.contains && el.contains(active) && active.getAttribute ? active.getAttribute('data-key') : null;
  const caret = key && typeof active.selectionStart === 'number' ? [active.selectionStart, active.selectionEnd] : null;
  el.innerHTML = markup.s;
  if (key) {
    const again = el.querySelector(`[data-key="${globalThis.CSS && CSS.escape ? CSS.escape(key) : key}"]`);
    if (again) {
      again.focus({ preventScroll: true });
      if (caret && typeof again.setSelectionRange === 'function') again.setSelectionRange(caret[0], caret[1]);
    }
  }
}

export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
