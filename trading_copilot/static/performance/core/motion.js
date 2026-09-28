// Motion is reserved for one moment (a trade closing: its tick lands, the
// statement's figures roll). Everything respects prefers-reduced-motion.

export function reducedMotion() {
  return !!(globalThis.matchMedia && globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

// Roll a number from `from` to `to`, calling step(value) each frame.
export function roll(from, to, step, ms = 400) {
  if (reducedMotion() || !globalThis.requestAnimationFrame || from === to) {
    step(to);
    return;
  }
  const t0 = performance.now();
  const frame = (t) => {
    const k = Math.min(1, (t - t0) / ms);
    const eased = 1 - (1 - k) ** 3;
    step(from + (to - from) * eased);
    if (k < 1) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}
