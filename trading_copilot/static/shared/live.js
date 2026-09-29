// The live paper block, taken from the page's existing dashboard socket.
// index.html re-broadcasts each socket message as a `qf:ws` event, so this
// tab never opens a second connection.

export function connectLive(store, target = globalThis, clock = () => Date.now() / 1000) {
  const on = (e) => {
    const paper = e && e.detail ? e.detail.paper : undefined;
    if (paper) store.set({ live: paper, liveAt: clock() });
  };
  target.addEventListener('qf:ws', on);
  return () => target.removeEventListener('qf:ws', on);
}
