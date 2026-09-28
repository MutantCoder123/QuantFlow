// Hand-built SVG helpers (Design.md §0 ruling 1: no chart library). Pure.

export const f1 = (v) => (Math.round(v * 10) / 10).toString();

export function linear(d0, d1, r0, r1) {
  const span = d1 - d0 || 1;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

// A step line through [x, y] points: hold each value until the next point.
export function stepPath(points) {
  if (!points.length) return '';
  return points.map(([x, y], i) => (i ? `H${f1(x)}V${f1(y)}` : `M${f1(x)} ${f1(y)}`)).join('');
}

export const hline = (y, x0, x1) => `M${f1(x0)} ${f1(y)}H${f1(x1)}`;
export const vline = (x, y0, y1) => `M${f1(x)} ${f1(y0)}V${f1(y1)}`;

// A filled circle as a path, so many dots fit in one <path>.
export const dot = (x, y, r = 3.5) => `M${f1(x - r)} ${f1(y)}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
