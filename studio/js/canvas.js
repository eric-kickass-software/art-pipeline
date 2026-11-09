// Canvas editor building blocks shared by the paint tools (Template, Workshop Editor, zone editor, grip stage).

/** A blank canvas of w x h pixels. */
export function makeCanvas(w, h) { const c = document.createElement("canvas"); c.width = w; c.height = h; return c; }

/** An image, loaded (rejects when it fails). */
export function loadImg(src) { return new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = src; }); }

/** A round brush from a to b: stamps of radius r along the segment (fills with ctx's current fillStyle). */
export function stamp(ctx, a, b, r) {
  const d = Math.hypot(b[0] - a[0], b[1] - a[1]), n = Math.max(1, Math.ceil(d / Math.max(1, r * 0.35)));
  for (let i = 0; i <= n; i++) { const t = i / n; ctx.beginPath(); ctx.arc(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, r, 0, 7); ctx.fill(); }
}

/** Zoom a view {z, ox, oy} by `factor` around the point s (canvas units), between 1x (whole canvas, no offset) and 8x. */
export function zoomAt(vw, s, factor) {
  const z = Math.min(8, Math.max(1, vw.z * factor));
  if (z === 1) { vw.ox = vw.oy = 0; } else { vw.ox = s[0] - (s[0] - vw.ox) * z / vw.z; vw.oy = s[1] - (s[1] - vw.oy) * z / vw.z; }
  vw.z = z;
}
/** The zoom factor of one mouse-wheel event. */
export const wheelFactor = (e) => Math.exp(-e.deltaY * 0.0015);
