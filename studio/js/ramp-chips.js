// Ramp chips: a ramp (colours, darkest first) drawn as a strip of swatches or as one gradient.

const hex = (c) => typeof c === "string" ? c : "#" + c.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");

/** <span class="strip"> of one <i> swatch per colour (hex strings or [r, g, b]). */
export function stripHtml(cols) { return `<span class="strip">${(cols || []).map(c => `<i style="background:${hex(c)}"></i>`).join("")}</span>`; }

/** A CSS linear-gradient with one hard band per colour ([r, g, b]); "" for no colours. */
export function gradient(cols) {
  if (!cols || !cols.length) return ""; const n = cols.length;
  return "linear-gradient(90deg," + cols.map((c, i) => `rgb(${c[0]},${c[1]},${c[2]}) ${i * 100 / n}% ${(i + 1) * 100 / n}%`).join(",") + ")";
}
