// Character Workshop, Template tab: the clean puppet template (the rig), edited on two canvases
// (specs/puppet_template.json).
// ------------------------------------------------------------------ template editor (clean puppet template = the rig)
import { get, post } from "../api.js";
import { zoomAt, wheelFactor } from "../canvas.js";
// <imports>
import { $, RIG, RIG_PAD, err, guard, img, jobs, pollJobs, queue } from "./common.js";
// </imports>
const TP = { st: null, spec: null, sel: null, undo: [], art: {}, dirty: false, inited: false, drag: null, pan: null, space: false,
  tplDone: -1, vw: { front: { z: 1, ox: 0, oy: 0 }, back: { z: 1, ox: 0, oy: 0 } }, hidden: new Set() };
try { TP.hidden = new Set(JSON.parse(localStorage.getItem("tpHidden") || "[]")); } catch (e) { }
const TP_LIMBS = ["armA", "armB", "legA", "legB"];
const TPC = { pivot: "#2a50ff", mid: "#ff2a2a", end: "#ff9a00", tip: "#22b14c", body: "#a24bd6", width: "#ffd23f", shape: "#20c0c0", seam: "#e1288f", face: "#ff8c00" };
function tpFacePts(V) {   // the front head's face guide handles: [key, index, point]
  const f = V.shapes.head.face; if (!f) return [];
  return [["eyes", 0, f.eyes[0]], ["eyes", 1, f.eyes[1]], ["mouth", -1, f.mouth], ...f.center.map((q, i) => ["center", i, q])];
}
const tpJ = (V, name) => { const [k, j] = name.split("."); return j ? V.joints[k][j] : V.joints[name]; };
const tpShown = (name) => !TP.hidden.has(name);
function tpCatmull(P, n = 10) {   // closed Catmull-Rom through the landmarks (same as the Python renderer)
  const out = [], L = P.length;
  for (let i = 0; i < L; i++) {
    const p0 = P[(i - 1 + L) % L], p1 = P[i], p2 = P[(i + 1) % L], p3 = P[(i + 2) % L];
    for (let s = 0; s < n; s++) { const t = s / n, t2 = t * t, t3 = t2 * t;
      out.push([0, 1].map(c => 0.5 * (2 * p1[c] + (-p0[c] + p2[c]) * t + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2 + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t3))); }
  }
  return out;
}
function tpFrame(V, L) {   // a limb bone's frame: start joint, bone vector, unit normal
  const a = tpJ(V, L.from), b = tpJ(V, L.to), d = [b[0] - a[0], b[1] - a[1]], len = Math.hypot(...d) || 1;
  return { a, d, n: [-d[1] / len, d[0] / len], len2: d[0] * d[0] + d[1] * d[1] || 1 };
}
const tpLimbWorld = (V, L) => { const f = tpFrame(V, L); return L.points.map(([t, q]) => [f.a[0] + f.d[0] * t + f.n[0] * q, f.a[1] + f.d[1] * t + f.n[1] * q]); };
function tpToLocal(V, L, p) {   // rig-space point -> [t along the bone, px to the side]
  const f = tpFrame(V, L), r = [p[0] - f.a[0], p[1] - f.a[1]];
  return [Math.round((r[0] * f.d[0] + r[1] * f.d[1]) / f.len2 * 1000) / 1000, Math.round((r[0] * f.n[0] + r[1] * f.n[1]) * 10) / 10];
}
function tpSelPts(V, name) {   // a part's outline points in rig space (limb points converted from their bone frame)
  return V.limbs[name] ? tpLimbWorld(V, V.limbs[name]) : V.shapes[name] ? V.shapes[name].points : null;
}
function tpSetPt(V, name, i, p) {
  if (V.limbs[name]) V.limbs[name].points[i] = tpToLocal(V, V.limbs[name], p); else V.shapes[name].points[i] = p;
}
function tpInsertPt(V, name, i, p) {
  if (V.limbs[name]) V.limbs[name].points.splice(i, 0, tpToLocal(V, V.limbs[name], p)); else V.shapes[name].points.splice(i, 0, p);
}
const tpRawPts = (V, name) => V.limbs[name] ? V.limbs[name].points : V.shapes[name].points;
function tpPoly(pts) { const p = new Path2D(); p.moveTo(...pts[0]); pts.slice(1).forEach(q => p.lineTo(...q)); p.closePath(); return p; }
function tpPartPath(V, name) {
  return tpPoly(tpCatmull(tpSelPts(V, name)));
}
function tpWaist(V) {   // the waistband handle sits on the torso's centre line
  const n = V.joints.neck, c = V.joints.crotch, y = V.seams.waist.y, t = (y - n[1]) / ((c[1] - n[1]) || 1);
  return [n[0] + (c[0] - n[0]) * t, y];
}
function tpSize(v) {   // canvas pixels = CSS size x device pixels, so zoomed-in lines stay sharp
  const cv = $(`tpCv_${v}`), css = +$("tpZoom").value, px = Math.round(css * (window.devicePixelRatio || 1));
  cv.style.width = css + "px"; if (cv.width !== px) { cv.width = px; cv.height = px; }
}
function tpDraw(v) {
  const cv = $(`tpCv_${v}`), x = cv.getContext("2d"), V = TP.spec.views[v], vw = TP.vw[v], k = cv.width / RIG, z = vw.z;
  const T = (c) => c.setTransform(k * z, 0, 0, k * z, k * vw.ox, k * vw.oy);
  x.setTransform(1, 0, 0, 1, 0, 0); x.fillStyle = "#c4c6cc"; x.fillRect(0, 0, cv.width, cv.height);
  T(x);
  const aop = +$("tpArt").value, pop = +$("tpParts").value;
  if (TP.art[v] && aop > 0 && tpShown("art")) { x.globalAlpha = aop; x.imageSmoothingEnabled = z < 3; if (TP.art[v].naturalWidth === RIG) x.drawImage(TP.art[v], 0, 0, RIG, RIG); else x.drawImage(TP.art[v], RIG_PAD[0], RIG_PAD[1], 512, 512); x.globalAlpha = 1; }   // a 512 px mannequin sits at PAD
  if (pop > 0) {   // parts drawn opaque off-screen in draw order (each one fill + its own outline), then laid over the art
    const off = document.createElement("canvas"); off.width = cv.width; off.height = cv.height; const o = off.getContext("2d"); T(o);
    for (const name of V.order) { if (!tpShown(name)) continue;
      const p = tpPartPath(V, name); o.fillStyle = name === TP.sel ? "#e7dcc0" : "#d2d0cb"; o.fill(p);
      o.strokeStyle = "#3a3432"; o.lineWidth = 1.8; o.lineJoin = "round"; o.stroke(p); }
    x.setTransform(1, 0, 0, 1, 0, 0); x.globalAlpha = pop; x.drawImage(off, 0, 0); x.globalAlpha = 1; T(x);
  }
  const px = 1 / z;   // one screen pixel (CSS-ish) in rig units: handles keep their size at any zoom
  const dot = (p, col, r = 5) => { x.fillStyle = col; x.beginPath(); x.arc(p[0], p[1], r * px, 0, 7); x.fill(); x.strokeStyle = "#000"; x.lineWidth = px; x.stroke(); };
  if (tpShown("guides")) {
    x.lineWidth = 1.5 * px; x.strokeStyle = "rgba(40,150,230,.9)";
    for (const kk of TP_LIMBS) { const j = V.joints[kk]; x.beginPath(); x.moveTo(...j.pivot); x.lineTo(...j.mid); x.lineTo(...j.end); x.lineTo(...j.tip); x.stroke(); }
    x.beginPath(); x.moveTo(...V.joints.neck); x.lineTo(...V.joints.pelvis); x.stroke();
    const w = tpWaist(V), tp = V.shapes.torso.points, xs = tp.map(p => p[0]), hw = (Math.max(...xs) - Math.min(...xs)) * 0.42;
    x.strokeStyle = TPC.seam; x.lineWidth = 2 * px; x.beginPath();
    for (let u = -1; u <= 1.001; u += 0.1) { const qx = w[0] + hw * u, qy = w[1] + V.seams.waist.bow * hw * (1 - u * u); u === -1 ? x.moveTo(qx, qy) : x.lineTo(qx, qy); }
    x.stroke(); dot(w, TPC.seam, 5);
    const f = V.shapes.head.face;
    if (f && tpShown("head")) {   // face guides: eye line, eyes, mouth, centre line
      const [e0, e1] = f.eyes, ex = [(e1[0] - e0[0]) * 0.35, (e1[1] - e0[1]) * 0.35], r = 0.09 * Math.hypot(e1[0] - e0[0], e1[1] - e0[1]);
      x.strokeStyle = TPC.face; x.lineWidth = 1.5 * px;
      x.beginPath(); x.moveTo(e0[0] - ex[0], e0[1] - ex[1]); x.lineTo(e1[0] + ex[0], e1[1] + ex[1]); x.stroke();
      for (const e of f.eyes) { x.beginPath(); x.ellipse(e[0], e[1], r, 1.3 * r, 0, 0, 7); x.stroke(); }
      x.lineWidth = 2.5 * px; x.beginPath(); x.moveTo(f.mouth[0] - r, f.mouth[1]); x.lineTo(f.mouth[0] + r, f.mouth[1]); x.stroke();
      x.lineWidth = 1.5 * px; x.beginPath(); f.center.forEach((q, i) => i ? x.lineTo(...q) : x.moveTo(...q)); x.stroke();
      tpFacePts(V).forEach(([, , q]) => { const s = 3.5 * px; x.fillStyle = TPC.face; x.fillRect(q[0] - s, q[1] - s, 2 * s, 2 * s); });
    }
  }
  if (tpShown("joints")) {
    for (const kk of TP_LIMBS) for (const n of ["pivot", "mid", "end", "tip"]) dot(V.joints[kk][n], TPC[n]);
    for (const n of ["neck", "pelvis", "crotch"]) dot(V.joints[n], TPC.body);
  }
  if (TP.sel && tpShown(TP.sel)) (tpSelPts(V, TP.sel) || []).forEach(p => dot(p, TPC.shape, 4.5));
  x.setTransform(1, 0, 0, 1, 0, 0);
  if (z > 1.01) { x.fillStyle = "rgba(0,0,0,.55)"; x.fillRect(6, 6, 54 * (window.devicePixelRatio || 1), 20 * (window.devicePixelRatio || 1));
    x.fillStyle = "#fff"; x.font = `${12 * (window.devicePixelRatio || 1)}px system-ui`; x.fillText(`${z.toFixed(1)}×`, 12, 21 * (window.devicePixelRatio || 1)); }
}
function tpLayers() {   // top of the draw order first, like a paint program's layer list
  const V = TP.spec.views.front, rows = ["joints", "guides", ...V.order.slice().reverse(), "art"];
  const label = { joints: "joints (handles)", guides: "guides (bones, waistband)", art: "art (approved unisex)" };
  $("tpLayers").innerHTML = rows.map(n => `<div class="tpl ${n === TP.sel ? "on" : ""}" data-lsel="${n}">
    <input type="checkbox" data-ly="${n}" ${tpShown(n) ? "checked" : ""} style="width:auto;margin:0 6px 0 0">${label[n] || n}</div>`).join("");
  $("tpLayers").querySelectorAll("[data-ly]").forEach(c => c.onchange = () => {
    c.checked ? TP.hidden.delete(c.dataset.ly) : TP.hidden.add(c.dataset.ly); tpSaveHidden(); tpDrawAll(); });
  $("tpLayers").querySelectorAll("[data-lsel]").forEach(r => r.onclick = (e) => {
    if (e.target.tagName === "INPUT") return; const n = r.dataset.lsel;
    if (V.limbs[n] || V.shapes[n]) { TP.sel = TP.sel === n ? null : n; $("tpPart").value = TP.sel || ""; tpDrawAll(); } });
}
function tpSaveHidden() { try { localStorage.setItem("tpHidden", JSON.stringify([...TP.hidden])); } catch (e) { } }
function tpDrawAll() {
  if (!TP.spec) return;
  ["front", "back"].forEach(tpDraw); tpLayers();
  $("tpStatus").textContent = TP.dirty ? " unsaved changes" : "";
  const info = [];
  for (const v of ["front", "back"]) { const V = TP.spec.views[v];
    if (TP.sel && (V.limbs[TP.sel] || V.shapes[TP.sel])) info.push(`${v}: ${tpRawPts(V, TP.sel).length} outline points`); }
  $("tpInfo").textContent = TP.sel ? `${TP.sel}: ${info.join("   |   ")}` : "No part selected.";
}
function tpPushUndo() { TP.undo.push(JSON.stringify(TP.spec.views)); if (TP.undo.length > 80) TP.undo.shift(); }
function tpUndo() { if (!TP.undo.length) return; TP.spec.views = JSON.parse(TP.undo.pop()); TP.dirty = true; tpDrawAll(); }
function tpHit(v, V, p) {   // the handle under the cursor (selected part's handles first), else the visible part under it
  const z = TP.vw[v].z, near = (q, r = 9) => Math.hypot(q[0] - p[0], q[1] - p[1]) < r / z;
  if (TP.sel && tpShown(TP.sel)) { const pts = tpSelPts(V, TP.sel) || []; for (let i = 0; i < pts.length; i++) if (near(pts[i], 8)) return { type: "pt", part: TP.sel, i }; }
  if (tpShown("joints")) {
    for (const k of TP_LIMBS) for (const n of ["tip", "end", "mid", "pivot"]) if (near(V.joints[k][n])) return { type: "joint", name: `${k}.${n}` };
    for (const n of ["neck", "pelvis", "crotch"]) if (near(V.joints[n])) return { type: "joint", name: n };
  }
  if (tpShown("guides") && near(tpWaist(V))) return { type: "seam" };
  if (tpShown("guides") && tpShown("head")) for (const [key, i, q] of tpFacePts(V)) if (near(q, 7)) return { type: "face", key, i };
  const x = $("tpCv_front").getContext("2d"); x.setTransform(1, 0, 0, 1, 0, 0);
  for (const name of [...V.order].reverse()) if (tpShown(name) && x.isPointInPath(tpPartPath(V, name), p[0], p[1])) return { type: "part", name };
  return null;
}
function tpWire(v) {
  const cv = $(`tpCv_${v}`);
  const scr = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * RIG / r.width, (e.clientY - r.top) * RIG / r.height]; };
  const pos = e => { const s = scr(e), vw = TP.vw[v]; return [(s[0] - vw.ox) / vw.z, (s[1] - vw.oy) / vw.z]; };
  cv.oncontextmenu = e => e.preventDefault();
  cv.onwheel = e => { e.preventDefault(); const vw = TP.vw[v], s = scr(e);
    zoomAt(vw, s, wheelFactor(e)); tpDraw(v); };
  cv.onmousedown = e => { if (!TP.spec) return; e.preventDefault();
    if (e.button === 1 || e.button === 2 || TP.space) { const vw = TP.vw[v]; TP.pan = { v, s: scr(e), ox: vw.ox, oy: vw.oy }; cv.style.cursor = "grabbing"; return; }
    const V = TP.spec.views[v], p = pos(e), h = tpHit(v, V, p);
    if (!h) { TP.sel = null; $("tpPart").value = ""; tpDrawAll(); return; }
    if (h.type === "part") { TP.sel = h.name; $("tpPart").value = h.name; tpDrawAll(); return; }
    if (h.type === "pt" && e.altKey) { const pts = tpRawPts(V, h.part);
      if (pts.length > 6) { tpPushUndo(); pts.splice(h.i, 1); TP.dirty = true; tpDrawAll(); } return; }
    tpPushUndo(); TP.drag = { v, ...h };
    if (h.type === "joint" && $("tpKeep").checked !== e.shiftKey)   // keep shapes: remember the outlines of the limbs on this joint in rig space
      TP.drag.keep = Object.fromEntries(Object.entries(V.limbs).filter(([, L]) => L.from === h.name || L.to === h.name).map(([n, L]) => [n, tpLimbWorld(V, L)])); };
  cv.ondblclick = e => {   // add an outline point on the selected part, between the two nearest neighbours
    const V = TP.spec.views[v]; if (!TP.sel || !(V.limbs[TP.sel] || V.shapes[TP.sel])) return;
    const p = pos(e), pts = tpSelPts(V, TP.sel); let best = 0, bd = 1e9;
    for (let i = 0; i < pts.length; i++) { const a = pts[i], b = pts[(i + 1) % pts.length], m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const d = Math.hypot(m[0] - p[0], m[1] - p[1]); if (d < bd) { bd = d; best = i; } }
    tpPushUndo(); tpInsertPt(V, TP.sel, best + 1, p.map(c => Math.round(c * 10) / 10)); TP.dirty = true; tpDrawAll(); };
  cv.onmousemove = e => {
    if (TP.pan && TP.pan.v === v) { const vw = TP.vw[v], s = scr(e); vw.ox = TP.pan.ox + s[0] - TP.pan.s[0]; vw.oy = TP.pan.oy + s[1] - TP.pan.s[1]; tpDraw(v); return; }
    const d = TP.drag; if (!d || d.v !== v) return;
    const V = TP.spec.views[v], p = pos(e).map(c => Math.round(c * 10) / 10);
    if (d.type === "joint") { const [k, j] = d.name.split("."); if (j) V.joints[k][j] = p; else V.joints[k] = p;
      if (d.keep) for (const [n, w] of Object.entries(d.keep)) V.limbs[n].points = w.map(q => tpToLocal(V, V.limbs[n], q)); }   // same outline, new bone frame
    else if (d.type === "pt") tpSetPt(V, d.part, d.i, p);
    else if (d.type === "seam") V.seams.waist.y = p[1];
    else if (d.type === "face") { const f = V.shapes.head.face; if (d.key === "mouth") f.mouth = p; else f[d.key][d.i] = p; }
    TP.dirty = true; tpDrawAll(); };
}
window.addEventListener("mouseup", () => { TP.drag = null; if (TP.pan) { ["front", "back"].forEach(v => $(`tpCv_${v}`).style.cursor = "crosshair"); TP.pan = null; } });
const tpOn = () => document.querySelector('.tab[data-t="template"]').classList.contains("on");
const tpTyping = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
window.addEventListener("keydown", e => {
  if (!tpOn() || tpTyping()) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); tpUndo(); }
  if (e.code === "Space") { e.preventDefault(); TP.space = true; }
});
window.addEventListener("keyup", e => { if (e.code === "Space") TP.space = false; });
function tpPreview() {
  const st = TP.st, t = `&_t=${st.preview_made}`;
  const im = (name, w) => st.files.includes(name) ? `<a href="${img(st.dir + "/" + name)}${t}" target="_blank"><img src="${img(st.dir + "/" + name)}${t}" style="width:${w}px;background:#c4c6cc;border-radius:4px"></a>` : "";
  $("tpPreview").innerHTML = st.files.length ? `${im("template_sheet.png", 900)}<div>${im("walk_front.gif", 260)}<br>${im("walk_back.gif", 260)}</div>
    <details style="width:100%"><summary class="hint">parts (front / back)</summary>${im("parts_front.png", 1100)}<br>${im("parts_back.png", 1100)}</details>`
    : `<p class="hint">No preview yet: Save &amp; render preview.</p>`;
}
async function tpLoad(keepSpec) {
  TP.st = await get("character.template");
  if (!keepSpec || !TP.spec) { TP.spec = TP.st.spec; TP.dirty = false; TP.undo = []; }
  for (const v of ["front", "back"]) if (TP.st.art[v] && !TP.art[v]) {
    const a = new Image(); a.src = img(TP.st.art[v]); await new Promise(ok => { a.onload = ok; a.onerror = ok; }); TP.art[v] = a; }
  const V = TP.spec.views.front;
  $("tpPart").innerHTML = `<option value="">(none)</option>` + V.order.slice().sort().map(n => `<option ${n === TP.sel ? "selected" : ""}>${n}</option>`).join("");
  tpPreview(); tpDrawAll();
}
async function tpSave(render) {
  TP.st = await post("character.save_template", { spec: TP.spec, render });
  TP.spec = TP.st.spec; TP.dirty = false; tpDrawAll();
  $("tpStatus").textContent = render ? " saved; rendering the preview (queue)" : " saved"; if (render) pollJobs();
}
async function initTemplate() {
  if (TP.inited) return; TP.inited = true;
  ["front", "back"].forEach(v => { tpSize(v); tpWire(v); });
  ["tpArt", "tpParts"].forEach(id => $(id).oninput = tpDrawAll);
  $("tpKeep").checked = localStorage.getItem("tpKeep") === "1"; $("tpKeep").onchange = () => localStorage.setItem("tpKeep", $("tpKeep").checked ? "1" : "0");
  $("tpZoom").onchange = () => { ["front", "back"].forEach(tpSize); tpDrawAll(); };
  $("tpReset").onclick = () => { ["front", "back"].forEach(v => TP.vw[v] = { z: 1, ox: 0, oy: 0 }); tpDrawAll(); };
  $("tpAll").onclick = () => { TP.hidden.clear(); tpSaveHidden(); tpDrawAll(); };
  $("tpNone").onclick = () => { TP.spec.views.front.order.forEach(n => TP.hidden.add(n)); tpSaveHidden(); tpDrawAll(); };
  $("tpPart").onchange = () => { TP.sel = $("tpPart").value || null; tpDrawAll(); };
  $("tpUndo").onclick = tpUndo;
  $("tpSave").onclick = guard(() => tpSave(false));
  $("tpRender").onclick = guard(() => tpSave(true));
  $("tpMeasure").onclick = guard(async () => {
    if (!confirm("Throw away the template edits and measure it again from the approved human.unisex rig?")) return;
    await queue([{ kind: "tpl_measure", build: "human.unisex", label: "template · re-measure" }]); TP.measuring = true; });
  await tpLoad(false);
}
document.querySelector('.tab[data-t="template"]').addEventListener("click", guard(initTemplate));
setInterval(() => {   // when a template job finishes: new preview (and, after a re-measure, the new spec)
  if (!TP.inited) return;
  const done = jobs.filter(j => (j.kind === "tpl" || j.kind === "tpl_measure") && (j.status === "done" || j.status === "failed")).length;
  if (TP.tplDone === -1) { TP.tplDone = done; return; }
  if (done !== TP.tplDone) { TP.tplDone = done; const m = TP.measuring; TP.measuring = false; tpLoad(!m).catch(e => err(e.message)); }
}, 2000);

// <exports>
export { TP, TPC, TP_LIMBS, initTemplate, tpCatmull, tpDraw, tpDrawAll, tpFacePts, tpFrame, tpHit, tpInsertPt, tpJ, tpLayers, tpLimbWorld, tpLoad, tpOn, tpPartPath, tpPoly, tpPreview, tpPushUndo, tpRawPts, tpSave, tpSaveHidden, tpSelPts, tpSetPt, tpShown, tpSize, tpToLocal, tpTyping, tpUndo, tpWaist, tpWire };
// </exports>
