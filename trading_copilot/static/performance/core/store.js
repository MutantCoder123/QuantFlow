// A tiny observable store: the tab's one source of truth for the range,
// the live paper block and the fetched figures. Components subscribe and
// re-render; they never read each other's DOM.

export function createStore(initial = {}) {
  let state = { ...initial };
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      const next = { ...state, ...patch };
      const changed = Object.keys(patch).filter((k) => state[k] !== next[k]);
      state = next;
      if (changed.length) subs.forEach((fn) => fn(state, changed));
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}
