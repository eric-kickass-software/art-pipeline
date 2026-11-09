// Item Workshop: the zone editor (where a fitted item sits on the puppet, plus its details sketch).
// ---- zone editor
import { get, post } from "../api.js";
import { loadImg, stamp } from "../canvas.js";
// <imports>
import { $, CANVAS, guard } from "./common.js";
import { IT, itGet, itHeld } from "./items.js";
// </imports>
const ZE = { view: "front", tool: "brush", mask: null, sketch: null, puppet: null, undo: [], redo: [], lasso: null, down: false, last: null, cursor: null, dirty: false, loaded: null,
  layer: "zone", ink: null, mats: [], line: "#221a18", size: { zone: 40, sketch: 10 } };
const zeLayer = () => ZE.layer === "sketch" ? ZE.sketch : ZE.mask;
const zeInkIsMat = () => ZE.ink && ZE.ink !== ZE.line;
const zeCanvas = () => { const c = document.createElement("canvas"); c.width = CANVAS; c.height = CANVAS; return c; };
async function zeLoad() {
  if (!IT.sel || itHeld() || !$("zeBuild").value) return;
  const key = `${IT.sel}|${$("zeBuild").value}|${ZE.view}`; if (ZE.loaded === key) return;
  $("zeStatus").textContent = "loading…";
  const d = await itGet("items.zone", { item: IT.sel, build: $("zeBuild").value, view: ZE.view });
  const [pi, mi, si] = await Promise.all([loadImg(d.puppet), loadImg(d.mask), d.sketch ? loadImg(d.sketch) : null]);
  ZE.sketch = zeCanvas(); if (si) ZE.sketch.getContext("2d").drawImage(si, 0, 0);
  ZE.mats = d.materials || []; ZE.line = d.line || ZE.line;
  if (!ZE.ink || ![ZE.line, ...ZE.mats.map(m => m.key)].includes(ZE.ink)) ZE.ink = ZE.mats.length ? ZE.mats[0].key : ZE.line;
  zePalette();
  ZE.puppet = pi; ZE.mask = zeCanvas(); const mx = ZE.mask.getContext("2d"); mx.drawImage(mi, 0, 0);
  const id = mx.getImageData(0, 0, CANVAS, CANVAS), p = id.data;
  for (let i = 0; i < p.length; i += 4) { const on = p[i] > 127; p[i] = p[i + 1] = p[i + 2] = 255; p[i + 3] = on ? 255 : 0; }
  mx.putImageData(id, 0, 0);
  ZE.undo = []; ZE.redo = []; ZE.dirty = false; ZE.loaded = key;
  $("zeSource").textContent = `zone from: ${d.source}`; $("zeStatus").textContent = ""; zeDraw();
}
function zeDraw() {
  const cv = $("zeCv"), x = cv.getContext("2d"); x.clearRect(0, 0, CANVAS, CANVAS);
  if (ZE.puppet) x.drawImage(ZE.puppet, 0, 0);
  if (ZE.mask) { const t = zeCanvas(), tx = t.getContext("2d"); tx.drawImage(ZE.mask, 0, 0);
    tx.globalCompositeOperation = "source-in"; tx.fillStyle = "#18c4d4"; tx.fillRect(0, 0, CANVAS, CANVAS);
    x.globalAlpha = 0.5; x.drawImage(t, 0, 0); x.globalAlpha = 1; }
  if (ZE.sketch && (ZE.layer === "sketch" || $("zeShow").checked)) { x.globalAlpha = ZE.layer === "sketch" ? 1 : 0.8; x.drawImage(ZE.sketch, 0, 0); x.globalAlpha = 1; }
  if (ZE.lasso && ZE.lasso.length > 1) { x.strokeStyle = ZE.tool === "lasso" ? (ZE.layer === "sketch" ? ZE.ink : "#18c4d4") : "#e8786a"; x.lineWidth = 2; x.beginPath();
    ZE.lasso.forEach((p, i) => i ? x.lineTo(p[0], p[1]) : x.moveTo(p[0], p[1])); x.stroke(); }
  if (ZE.cursor && (ZE.tool === "brush" || ZE.tool === "erase")) { x.strokeStyle = "#000"; x.lineWidth = 1.5; x.beginPath();
    x.arc(ZE.cursor[0], ZE.cursor[1], +$("zeSize").value / 2, 0, 7); x.stroke(); }
}
function zePos(e) { const r = $("zeCv").getBoundingClientRect(); return [(e.clientX - r.left) * CANVAS / r.width, (e.clientY - r.top) * CANVAS / r.height]; }
const zeSnap = () => [ZE.mask, ZE.sketch].map(c => c.getContext("2d").getImageData(0, 0, CANVAS, CANVAS));   // undo holds both layers
const zeRestore = (st) => [ZE.mask, ZE.sketch].forEach((c, i) => c.getContext("2d").putImageData(st[i], 0, 0));
function zePush() { ZE.undo.push(zeSnap()); if (ZE.undo.length > 25) ZE.undo.shift(); ZE.redo = []; }
function zeSetDirty() { ZE.dirty = true; $("zeStatus").textContent = "unsaved changes"; }
function zeStamp(a, b) {
  const r = +$("zeSize").value / 2, erase = ZE.tool === "erase";
  const m = zeLayer().getContext("2d");
  m.globalCompositeOperation = erase ? "destination-out" : "source-over"; m.fillStyle = ZE.layer === "sketch" ? ZE.ink : "#fff";
  stamp(m, a, b, r); m.globalCompositeOperation = "source-over";
  if (ZE.layer === "sketch" && !erase && zeInkIsMat()) { const z = ZE.mask.getContext("2d"); z.fillStyle = "#fff"; stamp(z, a, b, r); }   // a material stroke is zone too
}
function zeFillPath(ctx, pts, erase, fill) {
  ctx.globalCompositeOperation = erase ? "destination-out" : "source-over"; ctx.fillStyle = fill; ctx.beginPath();
  pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); ctx.fill(); ctx.globalCompositeOperation = "source-over";
}
function zePalette() {
  const sw = (key, name) => `<button data-zi="${key}" class="${ZE.ink === key ? "on" : ""}" title="${name}"><span style="display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:-1px;background:${key}"></span> ${name}</button>`;
  $("zePal").innerHTML = ZE.mats.map(m => sw(m.key, m.name)).join("") + sw(ZE.line, "line");
  $("zePal").querySelectorAll("[data-zi]").forEach(b => b.onclick = () => { ZE.ink = b.dataset.zi; zePalette(); zeDraw(); });
}
function zeSetLayer(l) {
  ZE.size[ZE.layer] = +$("zeSize").value; ZE.layer = l; $("zeSize").value = ZE.size[l];
  document.querySelectorAll("[data-zl]").forEach(b => b.classList.toggle("on", b.dataset.zl === l));
  $("zePal").style.display = l === "sketch" ? "" : "none"; $("zeShowL").style.display = l === "sketch" ? "none" : "";
  zeDraw();
}
document.querySelectorAll("[data-zl]").forEach(b => b.onclick = () => zeSetLayer(b.dataset.zl));
$("zeShow").onchange = () => zeDraw();
$("zeCv").onpointerdown = (e) => { if (!ZE.mask) return; $("zeCv").setPointerCapture(e.pointerId); const p = zePos(e); ZE.down = true; zePush();
  if (ZE.tool.startsWith("lasso")) ZE.lasso = [p]; else { zeStamp(p, p); ZE.last = p; } zeSetDirty(); zeDraw(); };
$("zeCv").onpointermove = (e) => { const p = zePos(e); ZE.cursor = p;
  if (ZE.down) { if (ZE.lasso) ZE.lasso.push(p); else { zeStamp(ZE.last, p); ZE.last = p; } } zeDraw(); };
$("zeCv").onpointerup = () => { if (!ZE.down) return; ZE.down = false;
  if (ZE.lasso && ZE.lasso.length > 2) { const erase = ZE.tool === "lassox", sk = ZE.layer === "sketch";
    zeFillPath(zeLayer().getContext("2d"), ZE.lasso, erase, sk ? ZE.ink : "#fff");
    if (sk && !erase && zeInkIsMat()) zeFillPath(ZE.mask.getContext("2d"), ZE.lasso, false, "#fff"); }
  ZE.lasso = null; zeDraw(); };
$("zeCv").onpointerleave = () => { ZE.cursor = null; zeDraw(); };
function zeTool(t) { ZE.tool = t; document.querySelectorAll("[data-zt]").forEach(b => b.classList.toggle("on", b.dataset.zt === t)); }
document.querySelectorAll("[data-zt]").forEach(b => b.onclick = () => zeTool(b.dataset.zt));
document.querySelectorAll("[data-zv]").forEach(b => b.onclick = guard(async () => {
  if (ZE.dirty && !confirmLeave()) return;
  ZE.view = b.dataset.zv; document.querySelectorAll("[data-zv]").forEach(x => x.classList.toggle("on", x === b)); await zeLoad(); }));
function confirmLeave() { $("zeStatus").textContent = "unsaved changes: save or undo them first"; return false; }
$("zeBuild").onchange = guard(async () => { ZE.loaded = null; await zeLoad(); });
$("zeZoom").oninput = () => $("zeCv").style.width = $("zeZoom").value + "px";
$("zeUndo").onclick = () => { if (!ZE.undo.length) return; ZE.redo.push(zeSnap()); zeRestore(ZE.undo.pop());
  if (ZE.undo.length) zeSetDirty(); else { ZE.dirty = false; $("zeStatus").textContent = ""; } zeDraw(); };   // back to what was loaded
$("zeRedo").onclick = () => { if (!ZE.redo.length) return; ZE.undo.push(zeSnap()); zeRestore(ZE.redo.pop()); zeSetDirty(); zeDraw(); };
$("zeClear").onclick = () => { if (!ZE.mask) return; zePush(); zeLayer().getContext("2d").clearRect(0, 0, CANVAS, CANVAS); zeSetDirty(); zeDraw(); };   // clears the layer being painted
$("zeSave").onclick = guard(async () => {
  const out = zeCanvas(), x = out.getContext("2d"); x.fillStyle = "#000"; x.fillRect(0, 0, CANVAS, CANVAS); x.drawImage(ZE.mask, 0, 0);
  const sex = $("zeSexOnly").checked ? $("zeBuild").value.split(".")[1] : null;
  const r = await post("items.save_zone", { item: IT.sel, view: ZE.view, png: out.toDataURL("image/png"), sketch: ZE.sketch.toDataURL("image/png"), sex });
  const msg = `saved (${r.px} px zone, ${r.sketch_px || 0} px details)${sex ? " for " + sex : ""}`;
  ZE.dirty = false; ZE.loaded = null; $("zeStatus").textContent = msg; await zeLoad();
  $("zeStatus").textContent = `${msg}: generate to use the details (re-extract keeps the old paint)`;
});
$("zeReset").onclick = guard(async () => {
  const sex = $("zeSexOnly").checked ? $("zeBuild").value.split(".")[1] : null;
  await post("items.reset_zone", { item: IT.sel, view: ZE.view, sex }); ZE.dirty = false; ZE.loaded = null; await zeLoad(); });
document.addEventListener("keydown", (e) => {
  if (!$("items").classList.contains("on") || e.target.closest("input, textarea, select")) return;
  const k = e.key.toLowerCase();
  if (k === "d" && !e.ctrlKey) zeSetLayer(ZE.layer === "sketch" ? "zone" : "sketch");
  else if (k === "b") zeTool("brush"); else if (k === "e") zeTool("erase"); else if (k === "l") zeTool("lasso"); else if (k === "k") zeTool("lassox");
  else if (e.ctrlKey && k === "z") { e.preventDefault(); e.shiftKey ? $("zeRedo").click() : $("zeUndo").click(); }
  else if (e.ctrlKey && k === "y") { e.preventDefault(); $("zeRedo").click(); }
});

// <exports>
export { ZE, confirmLeave, zeCanvas, zeDraw, zeFillPath, zeInkIsMat, zeLayer, zeLoad, zePalette, zePos, zePush, zeRestore, zeSetDirty, zeSetLayer, zeSnap, zeStamp, zeTool };
// </exports>
