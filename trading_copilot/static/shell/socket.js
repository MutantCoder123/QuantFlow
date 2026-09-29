// The app's one /ws connection. Every payload goes into the store as `live`;
// every tab reads it from there.

const RECONNECT_MS = 3000;

export function connectSocket(store, { target = globalThis, WebSocketImpl = globalThis.WebSocket,
  clock = () => Date.now() / 1000, url } = {}) {
  let socket = null;
  let stopped = false;
  let timer = null;
  const loc = target.location || {};
  const wsUrl = url || `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/ws`;

  const open = () => {
    if (stopped) return;
    try {
      socket = new WebSocketImpl(wsUrl);
    } catch {
      store.set({ connected: false });
      timer = setTimeout(open, RECONNECT_MS);
      return;
    }
    socket.onopen = () => store.set({ connected: true });
    socket.onmessage = (event) => {
      let payload;
      try { payload = JSON.parse(event.data); } catch { return; }
      store.set({ live: payload, liveAt: clock(), connected: true });
    };
    socket.onclose = () => {
      store.set({ connected: false });
      if (!stopped) timer = setTimeout(open, RECONNECT_MS);
    };
    socket.onerror = () => { try { socket.close(); } catch { /* already closing */ } };
  };

  open();
  return () => {
    stopped = true;
    clearTimeout(timer);
    if (socket) socket.close();
  };
}
