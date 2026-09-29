// The live paper block, from the shell's store (the app's one /ws socket):
// the Performance tab never opens a second connection.

export function connectLive(store, appStore, clock = () => Date.now() / 1000) {
  const take = (s) => {
    const paper = s && s.live ? s.live.paper : undefined;
    if (paper) store.set({ live: paper, liveAt: clock() });
  };
  take(appStore.get());
  return appStore.subscribe((s, changed) => { if (changed.includes('live')) take(s); });
}
