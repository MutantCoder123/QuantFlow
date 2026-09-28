// Formatting shared by the question answers.
export { inr, pct, plural, r } from '../../core/format.js';

// "longs in trend expansion" -> "Longs in trend expansion"
export const sentenceText = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
