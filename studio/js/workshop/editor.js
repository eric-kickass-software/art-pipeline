// The Workshop Editor page (/editor): a painted puppet with the items, hair and face it wears, every layer editable.
// ------------------------------------------------------------------ Workshop Editor (/editor): a painted puppet with the items, hair and
// face it wears, every layer editable. Each of them is a "group" (puppet, item:<slot>, hair, face) with its own doc from the server;
// its canvases are keyed "<view>|<group>|<layer>" (+ "#b" for its ramp bands). The draw order interleaves them like the game does
// (an item's layer right after its body part, hair and face right after the head); the Layers panel lists each group as its own tree.
import { get, post } from "../api.js";
import { makeCanvas, loadImg, stamp, zoomAt, wheelFactor } from "../canvas.js";
import { gradient } from "../ramp-chips.js";
import { rgb2hex } from "../ramps.js";
// <imports>
import { $, CANVAS, SEXSYM, err, guard, img, jobs, pollJobs, queue, startPage, wantedTab } from "./common.js";
// </imports>
const PA = { race: null, sex: null, build: null, id: null, view: "front", groups: [], parts: {}, tmpl: {}, bmask: {}, empty: {}, lv: 2, sel: null,
  hidden: new Set(), ghide: new Set(), fold: new Set(), allParts: new Set(), tool: "brush",
  vw: { z: 1, ox: 0, oy: 0 }, undo: [], redo: [], dirty: {}, mask: null, stroke: null, pan: null, space: false, inited: false, its: null, fcs: null,
  ramps: {}, rampCols: null, chan: {}, flev: {}, rc: {}, frame: 0, playing: false, playT: 0 };
const PA_N = CANVAS, PA_LINE = "#28201e", PA_LEVELS = 5, PA_HELD = ["mainhand", "offhand"];
const PA_BAND = ["#3b2a6b", "#8a3f8f", "#d4606a", "#f2a65a", "#fbeaa0"];   // band display colours, darkest level first
const PA_BAND_RGB = PA_BAND.map(h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)));
const VIEWS_PA = ["front", "back"];
const paCanvas = (w = PA_N, h = PA_N) => makeCanvas(w, h);
const paLoadImg = loadImg;
const paKey = (v, gid, n) => `${v}|${gid}|${n}`;
const paG = gid => PA.groups.find(g => g.gid === gid);
const paSelG = () => PA.sel ? paG(PA.sel.split("|")[0]) : null;
const paSelN = () => PA.sel ? PA.sel.split("|")[1] : null;
const paSelKey = () => PA.sel ? `${PA.view}|${PA.sel}` : null;
const paBandsOn = () => !!PA.bandMode;
const paEK = () => { const k = paSelKey(); return k && paBandsOn() && PA.tool !== "outline" && PA.parts[k + "#b"] ? k + "#b" : k; };   // the canvas the tools edit (the outline is always paint)
const paShort = id => id.replace(/^item\./, "").replace(/_/g, " ");
const paGroupDirty = gid => Object.keys(PA.dirty).some(k => PA.dirty[k] && k.includes(`|${gid}|`));
const paVisible = (gid, n) => !PA.ghide.has(gid) && !PA.hidden.has(`${gid}|${n}`);
function paInfo(g, v) {   // a group's layers in one view: names, image path, bands path, template shape (null = not in this view)
  const vd = g.doc.views[v]; if (!vd) return null;
  if (g.kind === "paint") return { names: vd.order, src: n => vd.parts[n], band: n => vd.bands && vd.bands[n], tmpl: n => vd.template[n] };
  if (g.kind === "item") return { names: vd.order.filter(n => n.endsWith("@item")).map(n => n.slice(0, -5)),
    src: n => vd.parts[n + "@item"], band: n => vd.levels && vd.levels[n + "@item"], tmpl: () => null };
  return { names: ["layer"], src: () => vd.parts.layer, band: () => vd.levels && vd.levels.layer, tmpl: () => null, flev: () => vd.ramp && vd.ramp.layer };
}
function paIsEmpty(c) { const d = c.getContext("2d", { willReadFrequently: true }).getImageData(0, 0, PA_N, PA_N).data;
  for (let i = 3; i < d.length; i += 4) if (d[i]) return false; return true; }
async function paLoadView(v) {   // every group's layer images (editable canvases), bands and template shapes (clamp, ghost) of one view
  await Promise.all(PA.groups.flatMap(g => { const I = paInfo(g, v); if (!I) return []; const t = `&_t=${g.doc.stamp}`;
    return I.names.map(async n => { const k = paKey(v, g.gid, n);
      if (!PA.parts[k]) { const c = paCanvas(); c.getContext("2d").drawImage(await paLoadImg(img(I.src(n)) + t), 0, 0); PA.parts[k] = c;
        if (g.kind === "item") PA.empty[k] = paIsEmpty(c); }
      const tp = I.tmpl(n); if (tp && !PA.tmpl[k]) { const c = paCanvas(); c.getContext("2d").drawImage(await paLoadImg(img(tp)), 0, 0); PA.tmpl[k] = c; }
      const b = I.band(n); if (b && !PA.parts[k + "#b"]) paBandCanvas(k, await paLoadImg(img(b) + t), g.kind === "item" || g.kind === "hair");
      const fl = I.flev && I.flev(n); if (fl && !PA.flev[k]) paFaceLevels(k, await paLoadImg(img(fl) + t)); }); }));
}
function paFaceLevels(k, im) {   // a face's iris levels (R, 254+ = not iris) for its eye-ramp preview
  const c = paCanvas(), x = c.getContext("2d", { willReadFrequently: true }); x.drawImage(im, 0, 0);
  const d = x.getImageData(0, 0, PA_N, PA_N).data, f = new Uint8Array(PA_N * PA_N).fill(255);
  for (let p = 0, i = 0; p < f.length; p++, i += 4) if (d[i + 3] > 127) f[p] = d[i];
  PA.flev[k] = f;
}
function paBandCanvas(k, im, chan) {   // a levels map (R = level, 254 outline, 255 not skin) -> a display canvas (banded pixels only) + its mask
  const src = paCanvas(), sx = src.getContext("2d", { willReadFrequently: true }); sx.drawImage(im, 0, 0);
  const d = sx.getImageData(0, 0, PA_N, PA_N), out = paCanvas(), ox = out.getContext("2d"), o = ox.createImageData(PA_N, PA_N);
  if (chan) { const ch = new Uint8Array(PA_N * PA_N);   // per pixel: an item's material channel; 255 = outline, 254 = no levels (items, hair)
    for (let p = 0; p < ch.length; p++) ch[p] = d.data[p * 4 + 3] <= 127 ? 254 : d.data[p * 4] === 254 ? 255 : d.data[p * 4 + 1]; PA.chan[k] = ch; }
  const mask = paCanvas(), mx = mask.getContext("2d"), m = mx.createImageData(PA_N, PA_N); let any = false;
  for (let i = 0; i < d.data.length; i += 4) if (d.data[i + 3] > 127 && d.data[i] < 254) { any = true;
    const c = PA_BAND_RGB[Math.min(PA_BAND_RGB.length - 1, d.data[i])];
    o.data[i] = c[0]; o.data[i + 1] = c[1]; o.data[i + 2] = c[2]; o.data[i + 3] = 255; m.data[i] = m.data[i + 1] = m.data[i + 2] = m.data[i + 3] = 255; }
  if (!any) return;
  ox.putImageData(o, 0, 0); mx.putImageData(m, 0, 0);
  PA.parts[k + "#b"] = out; PA.bmask[k] = mask;
}
function paBandSnap(key, box) {   // after a heal: band pixels back to the nearest level colour, nothing outside the banded area
  const pad = 8, x0 = Math.max(0, Math.floor(box[0]) - pad), y0 = Math.max(0, Math.floor(box[1]) - pad);
  const W = Math.min(PA_N, Math.ceil(box[2]) + pad) - x0, H = Math.min(PA_N, Math.ceil(box[3]) + pad) - y0; if (W < 1 || H < 1) return;
  const ctx = PA.parts[key].getContext("2d"), im = ctx.getImageData(x0, y0, W, H), d = im.data;
  const m = PA.bmask[key.slice(0, -2)].getContext("2d").getImageData(x0, y0, W, H).data;
  for (let i = 0; i < d.length; i += 4) { if (!m[i + 3]) { d[i + 3] = 0; continue; } if (!d[i + 3]) continue;
    const c = PA_BAND_RGB[paBandLevel(d[i], d[i + 1], d[i + 2])]; d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 255; }
  ctx.putImageData(im, x0, y0);
}
function paBandLevel(r, g, b) { let best = 0, bd = 1e9;
  PA_BAND_RGB.forEach((c, i) => { const dd = (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2; if (dd < bd) { bd = dd; best = i; } }); return best; }
function paBandPng(key) {   // display canvas -> the bands PNG the server wants (R = level, A = painted)
  const d = PA.parts[key].getContext("2d").getImageData(0, 0, PA_N, PA_N), out = paCanvas(), ox = out.getContext("2d"), o = ox.createImageData(PA_N, PA_N);
  for (let i = 0; i < d.data.length; i += 4) if (d.data[i + 3] > 127) { o.data[i] = paBandLevel(d.data[i], d.data[i + 1], d.data[i + 2]); o.data[i + 3] = 255; }
  ox.putImageData(o, 0, 0); return out.toDataURL("image/png");
}
function paLevels() {   // the level picker (the puppet's skin key names the painted colour of each level)
  const g = paSelG(), key = g && g.kind === "paint" ? g.doc.skinKey : null, n = (key && key.length) || PA_LEVELS; PA.lv = Math.min(PA.lv, n - 1);
  $("paLv").innerHTML = PA_BAND.slice(0, n).map((c, i) => `<button data-lv="${i}" class="${i === PA.lv ? "on" : ""}" title="level ${i + 1} (key ${i + 1})${key ? ": painted " + rgb2hex(key[i]) : ""}" style="background:${c};color:${i > 2 ? "#222" : "#fff"}">${i + 1}</button>`).join("");
  $("paLv").querySelectorAll("[data-lv]").forEach(b => b.onclick = () => { PA.lv = +b.dataset.lv; paLevels(); });
  paSwatch();
}
function paSwatch() { $("paSwatch").style.background = paBandsOn() ? PA_BAND[PA.lv] : $("paColor").value;
  $("paSwatch").parentElement.title = paBandsOn() ? `Ramp level ${PA.lv + 1} (keys 1-5)` : "Colour"; }
const PA_TOOLS = { brush: ["Brush", true, "paints the selected layer"], eraser: ["Eraser", true, ""], heal: ["Heal", true, "fills the stroke from the colours around it"],
  outline: ["Outline", false, "the even outline colour and weight"], move: ["Move", false, "drag the selected layer; arrows nudge 1 px, Shift 10"],
  ramp: ["Ramp bands", true, ""], pick: ["Eyedropper", false, "click a colour (Alt-click with any tool)"], mask: ["AI fix area", true, ""] };
function paCtx() {   // the bar along the top of the canvas: what the chosen tool needs
  const t = PA_TOOLS[PA.tool] || ["", false, ""], bands = paBandsOn();
  $("paToolName").textContent = bands && PA.tool !== "ramp" ? `${t[0]} · bands` : t[0];
  $("paSizeGrp").hidden = !t[1]; $("paLvGrp").hidden = !bands; $("paFixGrp").hidden = PA.tool !== "mask";
  $("paToolHint").textContent = bands ? (PA.tool === "ramp" ? "" : PA.tool === "heal" ? "smooths the bands" : "picks a level") : t[2];
  document.querySelectorAll("#paRail [data-tool]").forEach(b => { b.classList.toggle("on", b.dataset.tool === PA.tool); b.classList.toggle("on2", b.dataset.tool === "ramp" && bands && PA.tool !== "ramp"); });
  paSwatch();
}
function paZoomBy(f) { zoomAt(PA.vw, [PA_N / 2, PA_N / 2], f); paDraw(); }
function paSize() { const cv = $("paCv"), css = +$("paZoom").value, px = Math.round(css * (window.devicePixelRatio || 1));
  cv.style.width = css + "px"; if (cv.width !== px) { cv.width = px; cv.height = px; } }
function paOrder(v) {   // the draw order, bottom first: [group, layer]
  const P = paG("puppet"); if (!P || !P.doc.views[v]) return [];
  const items = PA.groups.filter(g => g.kind === "item" && paInfo(g, v)).sort((a, b) => (a.doc.depth || 0) - (b.doc.depth || 0));
  const heads = ["face", "hair"].map(paG).filter(g => g && paInfo(g, v)), out = [];
  for (const n of P.doc.views[v].order) { out.push(["puppet", n]);
    if (n === "head") heads.forEach(g => out.push([g.gid, "layer"]));
    items.forEach(g => { if (paInfo(g, v).names.includes(n)) out.push([g.gid, n]); }); }
  return out;
}
let paRaf = 0;
function paDraw() { if (!paRaf) paRaf = requestAnimationFrame(() => { paRaf = 0; paDrawNow(); }); }
function paDrawNow() {
  paFlush();   // a stroke in progress: its new stamps onto the layer, once per frame
  const cv = $("paCv"), x = cv.getContext("2d"), k = cv.width / PA_N, vw = PA.vw;
  x.setTransform(1, 0, 0, 1, 0, 0); x.fillStyle = "#c4c6cc"; x.fillRect(0, 0, cv.width, cv.height);
  $("paZv").textContent = Math.round(vw.z * 100) + "%";
  if (!paG("puppet")) return;
  x.setTransform(k * vw.z, 0, 0, k * vw.z, k * vw.ox, k * vw.oy); x.imageSmoothingEnabled = vw.z < 2.5;
  const top = $("paTop").checked && PA.sel, bands = paBandsOn();
  const layer = (gid, n) => { const key = paKey(PA.view, gid, n), c = PA.parts[key]; if (!c) return;
    const m = paXf(gid, n); if (m) { x.save(); x.transform(...m); }   // the walk on: the layer in its pose
    const b = bands && PA.parts[key + "#b"];
    if (b) { x.drawImage(c, 0, 0); x.drawImage(b, 0, 0); }
    else { const fn = !bands && paRampFn(paG(gid)); x.drawImage(fn ? paRecolor(key, fn) : c, 0, 0); }
    if (m) x.restore(); };
  for (const [gid, n] of paOrder(PA.view)) if (paVisible(gid, n) && !(top && `${gid}|${n}` === PA.sel)) layer(gid, n);
  if (top) { const [gid, n] = PA.sel.split("|"); if (paVisible(gid, n)) layer(gid, n); }
  const sk = paSelKey();
  if (sk && $("paGhost").checked && PA.tmpl[sk]) {   // the selected part's template shape, as a cyan edge
    if (!PA.ghost || PA.ghost.key !== sk) PA.ghost = { key: sk, c: paGhost(PA.tmpl[sk]) };
    paPosed(x, () => x.drawImage(PA.ghost.c, 0, 0));
  }
  if (PA.mask) paPosed(x, () => { x.globalAlpha = 0.45; x.drawImage(PA.mask, 0, 0); x.globalAlpha = 1; });
  if (PA.cursor) { const r = PA.tool === "outline" ? 2 : +$("paSize").value / 2;
    x.strokeStyle = "#000"; x.lineWidth = 1 / vw.z; x.beginPath(); x.arc(PA.cursor[0], PA.cursor[1], Math.max(r, 1), 0, 7); x.stroke();
    x.strokeStyle = "#fff"; x.beginPath(); x.arc(PA.cursor[0], PA.cursor[1], Math.max(r, 1) + 1 / vw.z, 0, 7); x.stroke(); }
}
// ---- the walk cycle on the canvas: every layer drawn in the pose of one walk frame (the rig's rigid transform of its bone, from
// the doc's pose; hair and face ride the head). The tools still edit the rest-pose layers: pointer positions go back through the
// selected layer's transform, so a stroke on any frame lands where it shows.
function paXf(gid, n) {   // a layer's pose in the current frame as a canvas transform [a, b, c, d, e, f]; null = the rest pose
  if (!$("paWalkOn").checked) return null;
  const P = paG("puppet"), fr = P && P.doc.pose && P.doc.pose[PA.view]; if (!fr || !fr.length) return null;
  const t = fr[PA.frame % fr.length][gid === "hair" || gid === "face" ? "head" : n]; if (!t) return null;
  const [x0, y0, x1, y1, th] = t, c = Math.cos(th), s = Math.sin(th);
  return [c, s, -s, c, x1 - (c * x0 - s * y0), y1 - (s * x0 + c * y0)];
}
const paSelXf = () => PA.sel ? paXf(...PA.sel.split("|")) : null;
const paToLocal = (m, p) => { if (!m) return p; const dx = p[0] - m[4], dy = p[1] - m[5]; return [m[0] * dx + m[1] * dy, m[2] * dx + m[3] * dy]; };
function paPosed(x, fn) { const m = paSelXf(); if (m) { x.save(); x.transform(...m); } fn(); if (m) x.restore(); }   // drawn with the selected layer
const paFrames = () => { const P = paG("puppet"), fr = P && P.doc.pose && P.doc.pose[PA.view]; return fr && fr.length ? fr.length : 8; };
function paSetFrame(f) { const n = paFrames(); PA.frame = ((f % n) + n) % n;
  $("paFrame").max = n - 1; $("paFrame").value = PA.frame; $("paFrameV").textContent = `${PA.frame + 1}/${n}`; paDraw(); }
function paPlay(on) { clearInterval(PA.playT); PA.playT = 0; PA.playing = !!on && $("paWalkOn").checked;
  if (PA.playing) PA.playT = setInterval(() => { if (!PA.stroke) paSetFrame(PA.frame + 1); }, 120);   // the walk GIFs' pace
  $("paPlay").querySelector("use").setAttribute("href", PA.playing ? "#i-pause" : "#i-play");
  $("paPlay").title = PA.playing ? "Pause (P)" : "Play the walk, looping (P)"; }
function paToggle(id, on) {   // a rail toggle (its hidden checkbox + button)
  const box = $(id); box.checked = on === undefined ? !box.checked : on;
  const b = document.querySelector(`#paRail [data-toggle="${id}"]`); if (b) b.classList.toggle("on", box.checked);
  if (id === "paWalkOn") { $("paWalkBar").hidden = !box.checked; if (!box.checked) paPlay(false); paSetFrame(PA.frame); }
  paDraw(); }
// ---- ramp previews: a layer's banded pixels in the chosen ramp (from its bands, so unsaved band edits show too)
const PA_LUT = new Int16Array(256).fill(-1); PA_BAND_RGB.forEach((c, i) => PA_LUT[c[0]] = i);
function paRampFn(g) {   // material channel -> ramp colours (dark first) for a group's preview, or null (as painted)
  const C = PA.rampCols, r = g && PA.ramps[g.gid]; if (!C || !r) return null;
  if (g.kind === "paint") return () => C.skin && C.skin[r];
  if (g.kind === "hair") return () => C.hair && C.hair[r];
  if (g.kind === "face") return () => C.eyes && C.eyes[r];
  if (g.kind !== "item" || !PA.its) return null;
  const mats = PA.its.spec.materials, by = {};
  for (const m of g.doc.materials || []) { const e = mats[m]; if (!e || !C[e.group]) continue;
    by[e.channel] = C[e.group][r[m]] || Object.values(C[e.group])[0]; }   // a material without a choice: its group's first (as the game's default)
  return ch => by[ch];
}
function paTouch(key, box) {   // a layer changed: drop its ramp preview, or (box = [x0, y0, x1, y1]) mark only that region stale
  const k = key.replace(/#b$/, ""), c = PA.rc[k]; if (!c) return;
  if (!box) { delete PA.rc[k]; return; }
  const d = c._dirty; c._dirty = d ? [Math.min(d[0], box[0]), Math.min(d[1], box[1]), Math.max(d[2], box[2]), Math.max(d[3], box[3])] : box.slice();
}
function paRecolor(k, fn) {
  const c = PA.parts[k], b = PA.parts[k + "#b"], fl = PA.flev[k]; if (!b && !fl) return c;
  let out = PA.rc[k];
  if (out && !out._dirty) return out;
  if (out) { const [x0, y0, x1, y1] = out._dirty; out._dirty = null; paRecolorBox(out, k, fn, x0, y0, x1 - x0, y1 - y0); return out; }   // a stroke: only what it touched
  out = paCanvas(); paRecolorBox(out, k, fn, 0, 0, PA_N, PA_N); return PA.rc[k] = out;
}
function paRecolorBox(out, k, fn, x0, y0, W, H) {   // the ramp preview of one region of a layer, into out
  if (W < 1 || H < 1) return;
  const c = PA.parts[k], b = PA.parts[k + "#b"], fl = PA.flev[k], ch = PA.chan[k];
  const ox = out.getContext("2d", { willReadFrequently: true }); ox.clearRect(x0, y0, W, H); ox.drawImage(c, x0, y0, W, H, x0, y0, W, H);
  const im = ox.getImageData(x0, y0, W, H), d = im.data, bd = b ? b.getContext("2d").getImageData(x0, y0, W, H).data : null;
  for (let yy = 0, i = 0; yy < H; yy++) for (let xx = 0; xx < W; xx++, i += 4) { const p = (y0 + yy) * PA_N + x0 + xx;
    if (!d[i + 3]) continue;   // erased / deleted paint: its old bands don't show
    if (ch && ch[p] === 255 && d[i + 3] > 0) { d[i] = 40; d[i + 1] = 32; d[i + 2] = 30; continue; }   // outline: the line colour, as the game draws it
    if (ch && ch[p] === 254 && d[i + 3] > 0 && !(bd && bd[i + 3] > 127)) { d[i + 3] = Math.min(d[i + 3], 80); continue; }   // painted but not keyed: faded (the game won't draw it; save to key new paint)
    let lv = -1;
    if (bd) { if (bd[i + 3] > 127) { lv = PA_LUT[bd[i]]; if (lv < 0) lv = paBandLevel(bd[i], bd[i + 1], bd[i + 2]); } }
    else if (fl[p] < 254 && d[i + 3] > 0) lv = fl[p];
    if (lv < 0) continue;
    const cols = fn(ch ? ch[p] : 0); if (!cols) continue; const col = cols[Math.min(lv, cols.length - 1)];
    d[i] = col[0]; d[i + 1] = col[1]; d[i + 2] = col[2]; d[i + 3] = 255;
  }
  ox.putImageData(im, x0, y0);
}
function paGhost(t) {   // template shape -> a cyan edge (one-time per part)
  const src = t.getContext("2d").getImageData(0, 0, PA_N, PA_N).data, out = paCanvas(), ctx = out.getContext("2d"), im = ctx.createImageData(PA_N, PA_N), d = im.data;
  for (let y = 1; y < PA_N - 1; y++) for (let x = 1; x < PA_N - 1; x++) { const i = (y * PA_N + x) * 4, a = src[i + 3] > 127;
    if (a && (src[i - 4 + 3] <= 127 || src[i + 4 + 3] <= 127 || src[i - PA_N * 4 + 3] <= 127 || src[i + PA_N * 4 + 3] <= 127)) { d[i] = 0; d[i + 1] = 210; d[i + 2] = 235; d[i + 3] = 230; } }
  ctx.putImageData(im, 0, 0); return out;
}
// ---- the Layers panel: one foldable root per group, the rig hierarchy below it (torso > head, arms and legs upper > lower > hand)
const paParent = n => n === "torso" ? null : n === "head" ? "torso" : n.endsWith(".hand") ? n.replace(/hand$/, "lower")
  : n.endsWith(".lower") ? n.replace(/lower$/, "upper") : n.endsWith(".upper") ? "torso" : null;
const PA_TORSO_KIDS = ["head", "armA", "armB", "legA", "legB"];   // the torso's children in Layers (fixed, not the draw order)
function paTree(names) {   // part names (draw order) -> [{n, kids}], the top of the draw order first among siblings
  const set = new Set(names), node = {}, roots = []; names.forEach(n => node[n] = { n, kids: [] });
  names.slice().reverse().forEach(n => { let p = paParent(n); while (p && !set.has(p)) p = paParent(p); (p ? node[p].kids : roots).push(node[n]); });
  if (node.torso) node.torso.kids.sort((a, b) => PA_TORSO_KIDS.indexOf(a.n.split(".")[0]) - PA_TORSO_KIDS.indexOf(b.n.split(".")[0]));
  return roots;
}
function paSaveUi() { try { localStorage.setItem("paUi", JSON.stringify({ fold: [...PA.fold], hidden: [...PA.hidden], ghide: [...PA.ghide], all: [...PA.allParts] })); } catch (e) { } }
function paLoadUi() { try { const u = JSON.parse(localStorage.getItem("paUi") || "{}");
  PA.fold = new Set(u.fold || []); PA.hidden = new Set(u.hidden || []); PA.ghide = new Set(u.ghide || []); PA.allParts = new Set(u.all || []); } catch (e) { } }
function paRootOrder() { const slots = PA.its ? PA.its.vocab.artSlots : [];
  return ["puppet", ...slots.map(s => `item:${s}`), "hair", "face"].map(paG).filter(Boolean); }
function paSlots() {   // the Layers roots: the puppet, hair, face, every worn slot (empty or not), then any other group (held items)
  const S = PA.its, worn = S ? S.vocab.artSlots.filter(s => !PA_HELD.includes(s)) : [];
  const out = [{ gid: "puppet" }, { gid: "hair", kind: "hair" }, { gid: "face", kind: "face" }, ...worn.map(s => ({ gid: `item:${s}`, kind: "item", slot: s }))];
  PA.groups.forEach(g => { if (!out.some(o => o.gid === g.gid)) out.push({ gid: g.gid, slot: g.slot }); });
  return out;
}
// ---- ramp choices as colour: a chip shows the chosen ramp's bands (dark to light); clicking it opens every ramp of its group
const paStrip = gradient;
function paRampNow(gid, m, grp) {   // the ramp a chip shows: null = as painted
  const r = PA.ramps[gid], C = (PA.rampCols || {})[grp] || {};
  if (!m) return r || null;
  if (!r || !Object.keys(r).length) return null;
  return r[m] || Object.keys(C)[0] || null;   // a material without a choice: its group's first (as the preview and the game)
}
function paChip(gid, m, grp, on) {   // gid, material (items) or "", palette group
  const n = on ? paRampNow(gid, m, grp) : null, cols = n && ((PA.rampCols || {})[grp] || {})[n];
  const tip = `${m ? m + ": " : ""}${n || "as painted"}${on ? " (click to change)" : ""}`;
  return `<button class="rchip ${cols ? "" : "none"}" ${on ? "" : "disabled"} data-rchip="${gid}|${m}|${grp}" title="${tip}" ${cols ? `style="background:${paStrip(cols)}"` : ""}></button>`;
}
function paRampPop(btn) {
  const pop = $("paRampPop"), [gid, m, grp] = btn.dataset.rchip.split("|"), C = (PA.rampCols || {})[grp] || {}, cur = paRampNow(gid, m, grp);
  const opt = (n, cols) => `<button class="ropt ${n ? "" : "none"} ${cur === n ? "on" : ""}" data-ropt="${n || ""}"><i ${cols ? `style="background:${paStrip(cols)}"` : ""}></i>${n || "as painted"}</button>`;
  pop.innerHTML = `<h4>${m ? `${m} · ` : ""}${grp} ramps</h4><div class="ropts">${opt(null)}${Object.entries(C).map(([n, c]) => opt(n, c)).join("")}</div>`;
  const r = btn.getBoundingClientRect(); pop.hidden = false;
  const h = pop.offsetHeight; pop.style.left = Math.min(innerWidth - 260, r.left) + "px"; pop.style.top = (r.bottom + h + 8 > innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + "px";
  pop.querySelectorAll("[data-ropt]").forEach(b => b.onclick = () => { const n = b.dataset.ropt;
    if (m) { const o = { ...(PA.ramps[gid] || {}) }; if (n) o[m] = n; else delete o[m]; if (Object.keys(o).length) PA.ramps[gid] = o; else delete PA.ramps[gid]; }
    else if (n) PA.ramps[gid] = n; else delete PA.ramps[gid];
    pop.hidden = true; paRampChanged(gid); paLayers(); });
}
function paPickers(o) {   // a slot header's two dropdowns: what it wears, and the ramp to preview it in
  const S = PA.its, F = PA.fcs, C = PA.rampCols || {}, g = paG(o.gid), sx = b => b ? ({ male: "♂", female: "♀", unisex: "⚥" }[b.split(".")[1]] || b) : "?";
  const cell = chips => `<span class="rcell">${chips}</span>`;
  if (o.kind === "hair" || o.kind === "face") {
    const lst = F ? (F.kinds[o.kind] || []).filter(c => c.files.includes("layer_front.png")).reverse() : [];
    return (lst.length ? `<select data-pfk="${o.kind}" title="the ${o.kind} it wears"><option value="">none</option>${lst.map(c => `<option value="${c.id}" ${g && g.id === c.id ? "selected" : ""}>${c.id}${c.in_set ? " ✓" : ""}${c.meta.edited ? " · edited" : ""}${c.meta.on && c.meta.on !== PA.build ? ` · on ${sx(c.meta.on)}` : ""}</option>`).join("")}</select>`
      : `<select disabled><option>(none made yet)</option></select>`) + cell(paChip(o.kind, "", o.kind === "hair" ? "hair" : "eyes", !!g));
  }
  if (!S || !o.slot || PA_HELD.includes(o.slot)) return "";
  const items = Object.entries(S.spec.items).filter(([, it]) => it.slot === o.slot && it.kind !== "held");
  if (!items.length) return `<select disabled><option>(no items)</option></select>` + cell(paChip("", "", "", false));
  const opts = items.map(([iid]) => { const cs = (S.candidates[iid] || []).slice().sort((a, b) => (b.on === PA.build) - (a.on === PA.build));
    if (!cs.length) return `<option disabled>${paShort(iid)} (no candidates)</option>`;
    return `<optgroup label="${paShort(iid)}">` + cs.map(c => { const ap = Object.entries(S.approved[iid] || {}).filter(([, v]) => v === c.id).map(([k]) => SEXSYM[k] || k).join("");
      return `<option value="${iid}|${c.id}" ${g && g.item === iid && g.id === c.id ? "selected" : ""}>${c.id}${ap ? " " + ap : ""}${c.edited ? " · edited" : ""}${c.on !== PA.build ? ` · on ${sx(c.on)}` : ""}</option>`; }).join("") + `</optgroup>`; }).join("");
  const mats = g ? (S.spec.items[g.item].materials || []).filter(m => S.spec.materials[m] && C[S.spec.materials[m].group]) : [];
  return `<select data-pslot="${o.slot}" title="the item it wears"><option value="">none</option>${opts}</select>`
    + cell(mats.length ? mats.map(m => paChip(o.gid, m, S.spec.materials[m].group, true)).join("") : paChip("", "", "", false));
}
function paLayers() {
  const v = PA.view, html = [];
  for (const o of paSlots()) {
    const g = paG(o.gid), gid = o.gid, folded = PA.fold.has(gid), pick = o.gid === "puppet" ? "" : paPickers(o);
    if (o.gid === "puppet" && !g) continue;
    const I = g && paInfo(g, v), gd = g && paGroupDirty(gid), name = o.slot || o.kind || "";
    if (o.gid === "puppet") html.push(`<div class="lgrp">`); else
    html.push(`<div class="lgrp ${g ? "" : "off"}"><div class="lroot" ${g ? `data-gfold="${gid}"` : ""}><span class="fold">${g ? (folded ? "▸" : "▾") : ""}</span>`
      + (g ? `<input type="checkbox" data-gvis="${gid}" ${PA.ghide.has(gid) ? "" : "checked"} title="show / hide all of it">` : "")
      + (g && g.kind === "paint" ? `<b>${g.label}</b>${gd ? " •" : ""}<span class="hint"> puppet${g.doc.edited ? " · edited" : ""}</span>`
        : `<span class="slot">${name}</span>${g ? `${gd ? " •" : ""}${g.doc.edited ? '<span class="hint"> · edited</span>' : ""}` : ""}`)
      + (g && g.kind === "item" ? `<button class="mini" data-gall="${gid}" title="list the parts it doesn't cover yet too (to paint it onto them)">${PA.allParts.has(gid) ? "covered" : "all parts"}</button>` : "")
      + `</div>` + (pick ? `<div class="lslot">${pick}</div>` : ""));
    if (g && !folded) {
      if (!I) html.push(`<div class="hint" style="padding-left:24px">no ${v} view</div>`);
      else if (g.kind === "paint" || g.kind === "item") {
        let names = I.names;
        if (g.kind === "item" && !PA.allParts.has(gid)) {   // the parts it covers, with their ancestors
          const keep = new Set(names.filter(n => !PA.empty[paKey(v, gid, n)] || PA.dirty[paKey(v, gid, n)] || `${gid}|${n}` === PA.sel));
          [...keep].forEach(n => { let p = paParent(n); while (p) { if (names.includes(p)) keep.add(p); p = paParent(p); } });
          names = names.filter(n => keep.has(n));
          if (!names.length) html.push(`<div class="hint" style="padding-left:24px">nothing in the ${v} view (all parts: paint it on)</div>`);
        }
        const walk = (nodes, depth) => nodes.forEach(nd => { html.push(paRow(g, nd.n, depth, nd.kids.length > 0));
          if (nd.kids.length && !PA.fold.has(`${gid}|${nd.n}`)) walk(nd.kids, depth + 1); });
        walk(paTree(names), 1);
      } else html.push(paRow(g, "layer", 1, false));
    }
    html.push(`</div>`);
  }
  $("paLayers").innerHTML = html.join("") || `<div class="hint">nothing loaded</div>`;
  const L = $("paLayers"), redraw = () => { paSaveUi(); paLayers(); paDraw(); };
  L.querySelectorAll("[data-ply]").forEach(c => c.onchange = () => { c.checked ? PA.hidden.delete(c.dataset.ply) : PA.hidden.add(c.dataset.ply); redraw(); });
  L.querySelectorAll("[data-gvis]").forEach(c => { c.onclick = e => e.stopPropagation(); c.onchange = () => { c.checked ? PA.ghide.delete(c.dataset.gvis) : PA.ghide.add(c.dataset.gvis); redraw(); }; });
  L.querySelectorAll("[data-gfold]").forEach(r => r.onclick = () => { const k = r.dataset.gfold; PA.fold.has(k) ? PA.fold.delete(k) : PA.fold.add(k); redraw(); });
  L.querySelectorAll("[data-nfold]").forEach(f => f.onclick = e => { e.stopPropagation(); const k = f.dataset.nfold; PA.fold.has(k) ? PA.fold.delete(k) : PA.fold.add(k); redraw(); });
  L.querySelectorAll("[data-gall]").forEach(b => b.onclick = e => { e.stopPropagation(); const k = b.dataset.gall; PA.allParts.has(k) ? PA.allParts.delete(k) : PA.allParts.add(k); redraw(); });
  L.querySelectorAll("[data-pdel]").forEach(b => b.onclick = e => { e.stopPropagation(); paClearLayer(b.dataset.pdel); });
  L.querySelectorAll("[data-psel]").forEach(r => r.onclick = e => { if (e.target.tagName === "INPUT" || e.target.dataset.nfold || e.target.closest("[data-pdel]")) return;
    PA.sel = r.dataset.psel; paLevels(); paLayers(); paDraw(); });
  L.querySelectorAll("[data-pfk]").forEach(el => el.onchange = guard(() => paSetLayer(el.dataset.pfk, el.value)));
  L.querySelectorAll("[data-pslot]").forEach(el => el.onchange = guard(() => { const [iid, cid] = el.value ? el.value.split("|") : ["", ""]; return paSetItem(el.dataset.pslot, iid, cid); }));
  paPupHead();
}
function paPupHead() {   // the puppet's header (static: its selects keep their handlers); its tree is the first group in #paLayers
  const g = paG("puppet");
  $("paPupFold").textContent = PA.fold.has("puppet") ? "▸" : "▾"; $("paPupVis").checked = !PA.ghide.has("puppet");
  $("paPupNote").textContent = g ? `${paGroupDirty("puppet") ? " •" : ""}${g.doc.edited ? " · edited" : ""}` : "";
  $("paSkinCell").innerHTML = paChip("puppet", "", "skin", !!g && !!(PA.rampCols && PA.rampCols.skin));
}
function paRow(g, n, depth, kids) {
  const id = `${g.gid}|${n}`, k = paKey(PA.view, g.gid, n), folded = PA.fold.has(id);
  const label = n === "layer" ? `${g.kind} layer` : n, empty = g.kind === "item" && PA.empty[k] && !PA.dirty[k];
  const edited = (g.doc.bandsEdited || []).includes(`${PA.view}/${n}`);
  return `<div class="tpl ${id === PA.sel ? "on" : ""} ${empty ? "empty" : ""}" data-psel="${id}" style="padding-left:${depth * 14}px">`
    + `<span class="fold" ${kids ? `data-nfold="${id}"` : ""}>${kids ? (folded ? "▸" : "▾") : ""}</span>`
    + `<input type="checkbox" data-ply="${id}" ${PA.hidden.has(id) ? "" : "checked"} style="width:auto;margin:0 6px 0 0">${label}`
    + `${PA.dirty[k] || PA.dirty[k + "#b"] ? " •" : ""}${PA.parts[k + "#b"] ? ' <span class="hint" title="has ramp bands">≡</span>' : ""}${edited ? ' <span class="hint" title="bands edited by hand">✎</span>' : ""}${empty ? ' <span class="hint">(empty)</span>' : ""}`
    + (g.kind === "item" && !PA.empty[k] ? `<button class="ldel" data-pdel="${id}" title="Delete this layer (${PA.view} view; Del key). Save removes it, Ctrl+Z brings it back"><svg><use href="#i-trash"/></svg></button>` : "") + `</div>`;
}
function paStatus(t) { const n = Object.values(PA.dirty).filter(Boolean).length, msg = (t || (n ? `${n} unsaved layer${n > 1 ? "s" : ""}` : "")).trim();
  $("paStatus").textContent = msg; $("paStatusBox").classList.toggle("off", !msg);
  $("paSave").classList.toggle("dirty", n > 0); $("paSave").title = n ? `Save ${n} edited layer${n > 1 ? "s" : ""} (puppet, items, hair, face)` : "Save (nothing edited yet)"; }
function paSetTool(t) {   // Ramp turns the band view on (Heal and Eyedropper then work on the bands); the painting tools turn it off
  if (t === "ramp") PA.bandMode = true; else if (!["heal", "pick"].includes(t)) PA.bandMode = false;
  PA.tool = t; paCtx(); paLevels(); paDraw(); }
function paPos(e) { const r = $("paCv").getBoundingClientRect(), s = [(e.clientX - r.left) * PA_N / r.width, (e.clientY - r.top) * PA_N / r.height];
  return { s, w: [(s[0] - PA.vw.ox) / PA.vw.z, (s[1] - PA.vw.oy) / PA.vw.z] }; }
function paPick(p) {   // the colour (bands on: the level) under the cursor, from the visible layers (selected on top)
  let seq = paOrder(PA.view).filter(([g, n]) => paVisible(g, n)).map(([g, n]) => `${g}|${n}`);
  if ($("paTop").checked && PA.sel) seq = [...seq.filter(s => s !== PA.sel), PA.sel];
  for (const s of seq.reverse()) { const k = `${PA.view}|${s}`, b = paBandsOn() && PA.parts[k + "#b"], c = PA.parts[k];
    const q = paToLocal(paXf(...s.split("|")), p), x = Math.round(q[0]), y = Math.round(q[1]), at = c => c.getContext("2d").getImageData(x, y, 1, 1).data;
    if (b && at(b)[3] > 127) { const d = at(b); PA.lv = paBandLevel(d[0], d[1], d[2]); paLevels(); return; }
    if (c && at(c)[3] > 127) { if (paBandsOn()) return; const d = at(c); $("paColor").value = "#" + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, "0")).join(""); paSwatch(); return; } }
}
const paStamp = stamp;   // round stamps from a to b
function paMoveStart(p) {   // lift the selected layer (or only its marked pixels) to move it
  const key = paSelKey(), part = PA.parts[key], ctx = part.getContext("2d");
  const lifted = paCanvas(), base = paCanvas(), lx = lifted.getContext("2d"), bx = base.getContext("2d");
  lx.drawImage(part, 0, 0);
  if (PA.mask) { lx.globalCompositeOperation = "destination-in"; lx.drawImage(PA.mask, 0, 0);
    bx.drawImage(part, 0, 0); bx.globalCompositeOperation = "destination-out"; bx.drawImage(PA.mask, 0, 0); }
  let maskCopy = null; if (PA.mask) { maskCopy = paCanvas(); maskCopy.getContext("2d").drawImage(PA.mask, 0, 0); }
  PA.stroke = { key, move: true, start: p, lifted, base, maskCopy, dx: 0, dy: 0, before: ctx.getImageData(0, 0, PA_N, PA_N) };
}
function paMoveTo(dx, dy) {
  const st = PA.stroke; st.dx = Math.round(dx); st.dy = Math.round(dy);
  const ctx = PA.parts[st.key].getContext("2d"); ctx.clearRect(0, 0, PA_N, PA_N); ctx.drawImage(st.base, 0, 0); ctx.drawImage(st.lifted, st.dx, st.dy);
  if (st.maskCopy) { const m = PA.mask.getContext("2d"); m.clearRect(0, 0, PA_N, PA_N); m.drawImage(st.maskCopy, st.dx, st.dy); }
  paTouch(st.key); paStatus(` move ${st.dx}, ${st.dy} px`); paDraw();
}
function paNudge(dx, dy) {
  if (!PA.sel || !PA.parts[paSelKey()] || PA.hidden.has(PA.sel) || paBandsOn()) return;
  paMoveStart([0, 0]); paMoveTo(dx, dy); paEnd();
}
// A stroke: pen samples only stamp into st.layer (cheap); once per frame (paFlush, from paDrawNow) the layer is rebuilt in the
// region the new stamps touched: the start-of-stroke copy (st.base) put back there, the stroke drawn on top. No full-canvas work per sample.
function paBegin(p) {
  const key = paEK(), part = PA.parts[key], base = paCanvas(); base.getContext("2d").drawImage(part, 0, 0);
  PA.stroke = { key, last: p, base, layer: paCanvas(), clip: paClip(key, base), scratch: paCanvas(), box: [p[0], p[1], p[0], p[1]], dirty: null };
  paExtend(p);
}
function paExtend(p) {
  const st = PA.stroke, r = PA.tool === "outline" ? 2 : +$("paSize").value / 2, L = st.layer.getContext("2d");
  L.fillStyle = PA.tool === "outline" ? PA_LINE : (PA.tool === "brush" || PA.tool === "ramp" ? (st.key.endsWith("#b") ? PA_BAND[PA.lv] : $("paColor").value) : "#fff");
  paStamp(L, st.last, p, r);
  const seg = [Math.min(st.last[0], p[0]) - r, Math.min(st.last[1], p[1]) - r, Math.max(st.last[0], p[0]) + r, Math.max(st.last[1], p[1]) + r];
  st.last = p;
  st.box = [Math.min(st.box[0], seg[0]), Math.min(st.box[1], seg[1]), Math.max(st.box[2], seg[2]), Math.max(st.box[3], seg[3])];
  const d = st.dirty; st.dirty = d ? [Math.min(d[0], seg[0]), Math.min(d[1], seg[1]), Math.max(d[2], seg[2]), Math.max(d[3], seg[3])] : seg;
  paDraw();
}
function paClip(key, base) {   // where a stroke may land: the part's template shape plus what it already covers; bands: the banded area; null = anywhere
  if (key.endsWith("#b")) return PA.bmask[key.slice(0, -2)];
  if (!$("paClamp").checked || !PA.tmpl[key]) return null;
  const c = paCanvas(), x = c.getContext("2d"); x.drawImage(PA.tmpl[key], 0, 0); x.drawImage(base, 0, 0); return c;
}
function paFlush() {   // the stroke so far onto its layer, in the region changed since the last frame
  const st = PA.stroke; if (!st || !st.dirty || st.move) return;
  const pad = 2, x0 = Math.max(0, Math.floor(st.dirty[0]) - pad), y0 = Math.max(0, Math.floor(st.dirty[1]) - pad);
  const x1 = Math.min(PA_N, Math.ceil(st.dirty[2]) + pad), y1 = Math.min(PA_N, Math.ceil(st.dirty[3]) + pad), W = x1 - x0, H = y1 - y0;
  st.dirty = null; if (W < 1 || H < 1 || PA.tool === "heal") return;   // heal shows its result when the pen lifts
  const ctx = PA.parts[st.key].getContext("2d");
  ctx.clearRect(x0, y0, W, H); ctx.drawImage(st.base, x0, y0, W, H, x0, y0, W, H);
  if (PA.tool === "eraser") { ctx.globalCompositeOperation = "destination-out"; ctx.drawImage(st.layer, x0, y0, W, H, x0, y0, W, H); ctx.globalCompositeOperation = "source-over"; }
  else if (PA.tool === "brush" || PA.tool === "ramp" || PA.tool === "outline") {
    let src = st.layer;
    if (st.clip) { const sx = st.scratch.getContext("2d"); sx.globalCompositeOperation = "source-over"; sx.clearRect(x0, y0, W, H);
      sx.drawImage(st.clip, x0, y0, W, H, x0, y0, W, H); sx.globalCompositeOperation = "source-in";
      sx.drawImage(st.layer, x0, y0, W, H, x0, y0, W, H); sx.globalCompositeOperation = "source-over"; src = st.scratch; }
    ctx.drawImage(src, x0, y0, W, H, x0, y0, W, H);
  }
  paTouch(st.key, [x0, y0, x1, y1]);
}
function paHeal(st) {   // fill the stroke from the colours around it (diffusion from the layer's own pixels outside the stroke)
  const ctx = PA.parts[st.key].getContext("2d"), pad = 6;
  const x0 = Math.max(0, Math.floor(st.box[0]) - pad), y0 = Math.max(0, Math.floor(st.box[1]) - pad);
  const x1 = Math.min(PA_N, Math.ceil(st.box[2]) + pad), y1 = Math.min(PA_N, Math.ceil(st.box[3]) + pad), W = x1 - x0, H = y1 - y0;
  if (W < 2 || H < 2) return;
  const im = ctx.getImageData(x0, y0, W, H), d = im.data, s = st.layer.getContext("2d").getImageData(x0, y0, W, H).data;
  const clamp = $("paClamp").checked && PA.tmpl[st.key] ? PA.tmpl[st.key].getContext("2d").getImageData(x0, y0, W, H).data : null;
  const todo = new Uint8Array(W * H), known = new Uint8Array(W * H), col = new Float32Array(W * H * 3);
  for (let i = 0; i < W * H; i++) { const inS = s[i * 4 + 3] > 0, ok = !clamp || clamp[i * 4 + 3] > 127 || d[i * 4 + 3] > 127;
    if (inS && ok) todo[i] = 1; else if (d[i * 4 + 3] > 127) { known[i] = 1; col[i * 3] = d[i * 4]; col[i * 3 + 1] = d[i * 4 + 1]; col[i * 3 + 2] = d[i * 4 + 2]; } }
  for (let pass = 0; pass < 200; pass++) {   // grow inward: every unknown pixel next to known ones takes their mean
    const add = [];
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const i = y * W + x; if (!todo[i] || known[i]) continue;
      let r = 0, g = 0, b = 0, c = 0;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1]]) { const xx = x + dx, yy = y + dy;
        if (xx < 0 || yy < 0 || xx >= W || yy >= H) continue; const j = yy * W + xx; if (known[j]) { r += col[j * 3]; g += col[j * 3 + 1]; b += col[j * 3 + 2]; c++; } }
      if (c) add.push([i, r / c, g / c, b / c]); }
    for (const [i, r, g, b] of add) { known[i] = 1; col[i * 3] = r; col[i * 3 + 1] = g; col[i * 3 + 2] = b; }
    if (!add.length) break;
  }
  for (let pass = 0; pass < 3; pass++) for (let i = 0; i < W * H; i++) if (todo[i] && known[i]) {   // a light smoothing over the healed pixels
    const x = i % W, y = (i / W) | 0; let r = 0, g = 0, b = 0, c = 0;
    for (const [dx, dy] of [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]]) { const xx = x + dx, yy = y + dy; if (xx < 0 || yy < 0 || xx >= W || yy >= H) continue;
      const j = yy * W + xx; if (known[j]) { r += col[j * 3]; g += col[j * 3 + 1]; b += col[j * 3 + 2]; c++; } }
    col[i * 3] = r / c; col[i * 3 + 1] = g / c; col[i * 3 + 2] = b / c; }
  for (let i = 0; i < W * H; i++) if (todo[i] && known[i]) { d[i * 4] = col[i * 3]; d[i * 4 + 1] = col[i * 3 + 1]; d[i * 4 + 2] = col[i * 3 + 2]; d[i * 4 + 3] = 255; }
  ctx.putImageData(im, x0, y0);
}
function paEnd() {
  const st = PA.stroke; if (!st) return; paFlush(); PA.stroke = null;
  if (st.move && !st.dx && !st.dy) { paStatus(); return; }   // a click without a drag: nothing moved
  if (!st.before) st.before = st.base.getContext("2d").getImageData(0, 0, PA_N, PA_N);   // the undo snapshot, read once at pen-up (not at pen-down)
  if (PA.tool === "heal" && !st.move) { PA.parts[st.key].getContext("2d").putImageData(st.before, 0, 0); paHeal(st); if (st.key.endsWith("#b")) paBandSnap(st.key, st.box); }
  const sync = !st.move && paBandSync(st);   // the puppet's skin bands follow the paint (outline / erased out, new skin in)
  PA.undo.push(sync ? { multi: [{ key: st.key, before: st.before }, sync.band], mask: sync.mask } : { key: st.key, before: st.before });
  if (PA.undo.length > 25) PA.undo.shift(); PA.redo = [];
  paTouch(st.key); PA.dirty[st.key] = true; if (st.key in PA.empty) PA.empty[st.key] = paIsEmpty(PA.parts[st.key]);
  paLayers(); paStatus(); paDraw();
}
// A paint stroke on a banded puppet part keeps its bands in step (as the server redoes them on save): where the paint
// changed, a pixel that's no longer skin (outline, erased, clothes) loses its band, new skin gets the nearest key level.
// Hand levels on unchanged pixels stay. Returns the undo entries for the bands (or null: nothing to do).
const paSkinish = (r, g, b) => { const mx = Math.max(r, g, b), mn = Math.min(r, g, b); return r > b + 18 && mx - mn > 0.16 * mx && mx > 70; };
function paBandSync(st) {
  const key = st.key; if (key.endsWith("#b") || !PA.parts[key + "#b"] || !PA.bmask[key]) return null;
  const g = paG(key.split("|")[1]); if (!g || g.kind !== "paint") return null;
  const pad = 4 + (PA.tool === "outline" ? 2 : +$("paSize").value / 2), x0 = Math.max(0, Math.floor(st.box[0] - pad)), y0 = Math.max(0, Math.floor(st.box[1] - pad));
  const W = Math.min(PA_N, Math.ceil(st.box[2] + pad)) - x0, H = Math.min(PA_N, Math.ceil(st.box[3] + pad)) - y0; if (W < 1 || H < 1) return null;
  const bx = PA.parts[key + "#b"].getContext("2d", { willReadFrequently: true }), mx = PA.bmask[key].getContext("2d", { willReadFrequently: true });
  const bandBefore = bx.getImageData(0, 0, PA_N, PA_N), maskBefore = mx.getImageData(0, 0, PA_N, PA_N);
  const now = PA.parts[key].getContext("2d").getImageData(x0, y0, W, H).data, was = st.before.data;
  const bim = bx.getImageData(x0, y0, W, H), mim = mx.getImageData(x0, y0, W, H), b = bim.data, m = mim.data;
  const sk = g.doc.skinKey || [], luma = c => 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2], kl = sk.map(luma);
  let changed = false;
  for (let yy = 0, i = 0; yy < H; yy++) for (let xx = 0; xx < W; xx++, i += 4) {
    const j = ((y0 + yy) * PA_N + x0 + xx) * 4;
    if (now[i] === was[j] && now[i + 1] === was[j + 1] && now[i + 2] === was[j + 2] && now[i + 3] === was[j + 3]) continue;
    const skin = now[i + 3] > 127 && paSkinish(now[i], now[i + 1], now[i + 2]);
    if (!skin) { if (b[i + 3] || m[i + 3]) { b[i + 3] = 0; m[i + 3] = 0; changed = true; } continue; }
    if (b[i + 3] > 127) continue;   // still skin: its (hand) level stays
    let lv = Math.floor(PA_LEVELS / 2);
    if (kl.length) { const l = luma(now.subarray(i, i + 3)); lv = kl.reduce((bi, v, k) => Math.abs(v - l) < Math.abs(kl[bi] - l) ? k : bi, 0); }
    const c = PA_BAND_RGB[Math.min(lv, PA_BAND_RGB.length - 1)];
    b[i] = c[0]; b[i + 1] = c[1]; b[i + 2] = c[2]; b[i + 3] = 255; m[i] = m[i + 1] = m[i + 2] = m[i + 3] = 255; changed = true;
  }
  if (!changed) return null;
  bx.putImageData(bim, x0, y0); mx.putImageData(mim, x0, y0); paTouch(key + "#b");
  return { band: { key: key + "#b", before: bandBefore }, mask: { k: key, before: maskBefore } };
}
function paUndo(from, to) {
  const u = from.pop(); if (!u) return; const back = [];
  for (const e of u.multi || [u]) { if (!PA.parts[e.key]) continue; const ctx = PA.parts[e.key].getContext("2d");
    back.push({ key: e.key, before: ctx.getImageData(0, 0, PA_N, PA_N) }); ctx.putImageData(e.before, 0, 0); paTouch(e.key);
    if (e.key in PA.empty) PA.empty[e.key] = paIsEmpty(PA.parts[e.key]);
    if (!(u.multi && e.key.endsWith("#b"))) PA.dirty[e.key] = true; }   // a cleared layer's bands come back with it (not saved separately)
  let mask = null;
  if (u.mask && PA.bmask[u.mask.k]) { const mx = PA.bmask[u.mask.k].getContext("2d");
    mask = { k: u.mask.k, before: mx.getImageData(0, 0, PA_N, PA_N) }; mx.putImageData(u.mask.before, 0, 0); }
  if (back.length) to.push(u.multi ? { multi: back, ...(mask ? { mask } : {}) } : back[0]);
  paLayers(); paStatus(); paDraw();
}
function paClearLayer(id) {   // an item's layer on one part emptied in this view (Save removes its files); Ctrl+Z brings it back
  const k = `${PA.view}|${id}`; if (!PA.parts[k]) return;
  const keys = [k, ...(PA.parts[k + "#b"] ? [k + "#b"] : [])];
  PA.undo.push({ multi: keys.map(key => ({ key, before: PA.parts[key].getContext("2d").getImageData(0, 0, PA_N, PA_N) })) });
  if (PA.undo.length > 25) PA.undo.shift(); PA.redo = [];
  keys.forEach(key => { PA.parts[key].getContext("2d").clearRect(0, 0, PA_N, PA_N); paTouch(key); });
  PA.dirty[k] = true; PA.empty[k] = true;
  paLayers(); paStatus(` ${id.replace("|", " · ")} deleted (${PA.view} view): Save removes it, Ctrl+Z brings it back`); paDraw();
}
function paMaskStroke(a, b) { if (!PA.mask) PA.mask = paCanvas(); const m = PA.mask.getContext("2d"); m.fillStyle = "#ff2060"; paStamp(m, a, b, +$("paSize").value / 2); paDraw(); }
function paWire() {
  const cv = $("paCv");
  cv.oncontextmenu = e => e.preventDefault();
  cv.onwheel = e => { e.preventDefault(); zoomAt(PA.vw, paPos(e).s, wheelFactor(e)); paDraw(); };
  cv.style.touchAction = "none";   // no browser scrolling / zooming on the canvas (iPad): the pointer events come here
  const fingers = new Map();
  const pinchState = () => { const [a, b] = [...fingers.values()]; return { d: Math.hypot(a[0] - b[0], a[1] - b[1]), m: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] }; };
  cv.onpointerdown = e => {
    try { cv.setPointerCapture(e.pointerId); } catch (_) { }
    if (e.pointerType === "touch") { e.preventDefault(); if (PA.stroke) return;   // a palm on the screen while the pen paints
      const s = paPos(e).s; fingers.set(e.pointerId, s);
      if (fingers.size === 1) PA.pan = { s, ox: PA.vw.ox, oy: PA.vw.oy };
      else if (fingers.size === 2) { const q = pinchState(); PA.pan = null; PA.pinch = { ...q, z: PA.vw.z, ox: PA.vw.ox, oy: PA.vw.oy }; }
      return; }
    paDown(e); };
  cv.onpointermove = e => {
    if (e.pointerType === "touch") { if (!fingers.has(e.pointerId)) return; fingers.set(e.pointerId, paPos(e).s);
      if (PA.pinch && fingers.size >= 2) { const q = pinchState(), P0 = PA.pinch, vw = PA.vw, z = Math.min(8, Math.max(1, P0.z * q.d / Math.max(1, P0.d)));
        const wx = (P0.m[0] - P0.ox) / P0.z, wy = (P0.m[1] - P0.oy) / P0.z;   // the canvas point under the fingers stays under them
        vw.z = z; if (z === 1) { vw.ox = vw.oy = 0; } else { vw.ox = q.m[0] - wx * z; vw.oy = q.m[1] - wy * z; } paDraw(); }
      else if (PA.pan) { const s = fingers.get(e.pointerId); PA.vw.ox = PA.pan.ox + s[0] - PA.pan.s[0]; PA.vw.oy = PA.pan.oy + s[1] - PA.pan.s[1]; paDraw(); }
      return; }
    const evs = PA.stroke && e.getCoalescedEvents ? e.getCoalescedEvents() : [];   // every pen sample between frames: smooth strokes
    (evs.length ? evs : [e]).forEach(paMove); };
  const lift = e => { if (e.pointerType === "touch") { fingers.delete(e.pointerId); if (fingers.size < 2) PA.pinch = null;
      if (fingers.size === 1) { const s = [...fingers.values()][0]; PA.pan = { s, ox: PA.vw.ox, oy: PA.vw.oy }; } else if (!fingers.size) PA.pan = null; return; }
    PA.pan = null; PA.maskLast = null; if (PA.stroke) paEnd();
    if (e.pointerType === "pen") { PA.cursor = null; paDraw(); } };
  cv.onpointerup = lift; cv.onpointercancel = lift;
  cv.onpointerleave = e => { if (e.pointerType !== "touch" && !PA.stroke) { PA.cursor = null; paDraw(); } };
  const paDown = e => { if (!paG("puppet")) return; e.preventDefault(); const p = paPos(e);
    if (e.button === 1 || e.button === 2 || PA.space) { PA.pan = { s: p.s, ox: PA.vw.ox, oy: PA.vw.oy }; return; }
    if (PA.tool === "pick" || e.altKey) { paPick(p.w); return; }
    if (PA.playing) paPlay(false);   // painting on the walk: hold this frame
    p.w = paToLocal(paSelXf(), p.w);
    if (PA.tool === "mask") { PA.maskLast = p.w; paMaskStroke(p.w, p.w); return; }
    if (!PA.sel || !PA.parts[paSelKey()]) { paStatus(" select a layer first (click its name)"); return; }
    if (paBandsOn()) {
      if (!PA.parts[paSelKey() + "#b"]) { paStatus(` ${PA.sel.replace("|", " · ")} has no ramp bands in this view (pick a banded layer: ≡ in Layers)`); return; } }
    if (PA.tool === "move") { paMoveStart(p.w); return; }
    if (PA.hidden.has(PA.sel) || PA.ghide.has(paSelG().gid)) { paStatus(" the selected layer is hidden"); return; }
    paBegin(p.w); };
  const paMove = e => { const p = paPos(e); PA.cursor = p.w;
    if (PA.pan) { PA.vw.ox = PA.pan.ox + p.s[0] - PA.pan.s[0]; PA.vw.oy = PA.pan.oy + p.s[1] - PA.pan.s[1]; paDraw(); return; }
    p.w = paToLocal(paSelXf(), p.w);
    if (PA.maskLast && e.buttons & 1) { paMaskStroke(PA.maskLast, p.w); PA.maskLast = p.w; return; }
    if (PA.stroke && PA.stroke.move && e.buttons & 1) { paMoveTo(p.w[0] - PA.stroke.start[0], p.w[1] - PA.stroke.start[1]); return; }
    if (PA.stroke && e.buttons & 1) paExtend(p.w); else paDraw(); };
  window.addEventListener("pointerup", e => { if (e.pointerType === "touch") return; PA.pan = null; PA.maskLast = null; if (PA.stroke) paEnd(); });
  window.addEventListener("keydown", e => {
    if (!$("parts").classList.contains("on") || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); paUndo(PA.undo, PA.redo); return; }
    if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); paUndo(PA.redo, PA.undo); return; }
    if (e.code === "Space") { e.preventDefault(); PA.space = true; return; }
    if ((e.key === "Delete" || e.key === "Backspace") && PA.sel && paSelG() && paSelG().kind === "item") { e.preventDefault(); paClearLayer(PA.sel); return; }
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (arrows[e.key] && PA.tool === "move") { e.preventDefault(); const st = e.shiftKey ? 10 : 1; paNudge(arrows[e.key][0] * st, arrows[e.key][1] * st); return; }
    if (!e.ctrlKey && !e.metaKey && !e.altKey) {
      if (k === "w") { paToggle("paWalkOn"); return; }
      if (k === "p" && $("paWalkOn").checked) { paPlay(!PA.playing); return; }
      if ((k === "," || k === ".") && $("paWalkOn").checked) { paPlay(false); paSetFrame(PA.frame + (k === "." ? 1 : -1)); return; } }
    const tools = { b: "brush", e: "eraser", h: "heal", o: "outline", i: "pick", m: "mask", v: "move", r: "ramp" };
    if (tools[k] && !e.ctrlKey && !e.metaKey) paSetTool(tools[k]);
    if (paBandsOn() && /^[1-9]$/.test(k) && +k <= PA_BAND.length) { PA.lv = +k - 1; paLevels(); }
    if (k === "[" || k === "]") { $("paSize").value = Math.max(1, Math.min(80, +$("paSize").value + (k === "]" ? 2 : -2))); $("paSizeV").textContent = $("paSize").value; paDraw(); }
  });
  window.addEventListener("keyup", e => { if (e.code === "Space") PA.space = false; });
}
// ---- loading: the puppet, the items and the hair / face (each its own group)
function paDropCache(gid) {
  const m = `|${gid}|`;
  for (const o of [PA.parts, PA.tmpl, PA.bmask, PA.empty, PA.dirty, PA.chan, PA.flev, PA.rc]) for (const k of Object.keys(o)) if (k.includes(m)) delete o[k];
  PA.undo = PA.undo.filter(u => !u.key.includes(m)); PA.redo = PA.redo.filter(u => !u.key.includes(m)); PA.ghost = null;
}
function paPut(g) { const i = PA.groups.findIndex(x => x.gid === g.gid); paDropCache(g.gid); if (i >= 0) PA.groups[i] = g; else PA.groups.push(g); }
function paRemember() { try { localStorage.setItem("paLast", JSON.stringify({ race: PA.race, sex: PA.sex, view: PA.view })); } catch (e) { } }
async function paRefreshAll() { PA.rc = {}; await paLoadView(PA.view);
  if (PA.sel && (!paSelG() || !PA.parts[paSelKey()])) PA.sel = paG("puppet") ? "puppet|torso" : null;
  paLevels(); paLayers(); paStatus(); paDraw(); }
async function paFillPickers() {
  if (!PA.races) PA.races = Object.keys((await get("character.race", { race: PA.race || "human" })).spec.races);
  $("paRace").innerHTML = PA.races.map(r => `<option ${r === PA.race ? "selected" : ""}>${r}</option>`).join("");
  $("paSex").value = PA.sex; $("paView").value = PA.view;
  const st = await get("character.paint_state", { build: PA.build }); PA.pst = st;
  $("paCand").innerHTML = st.candidates.slice().reverse().map(c => `<option value="${c.id}" ${c.id === PA.id ? "selected" : ""}>${c.id}${c.id === st.approved ? " (approved)" : ""}${c.edited ? " · edited" : ""}</option>`).join("")
    || `<option value="">(no paint candidates)</option>`;
  if (!PA.its || PA.its.race !== PA.race) { PA.its = await get("items.state", { race: PA.race }); PA.its.race = PA.race; }
  if (!PA.fcs || PA.fcs.race !== PA.race) PA.fcs = await get("character.faces", { race: PA.race });
  if (!PA.rampCols) PA.rampCols = await get("items.ramp_colours");
  paLayers();
}
function paRampChanged(gid) { for (const k of Object.keys(PA.rc)) if (k.includes(`|${gid}|`)) delete PA.rc[k]; paDraw(); }
function paDefaultCand(st) { return st.approved || (st.candidates.length ? st.candidates[st.candidates.length - 1].id : null); }
async function paSetPuppet(race, sex, id, plain) {   // the base model; another race (or plain) takes the items, hair and face off
  const others = PA.groups.filter(g => g.gid !== "puppet"), drop = plain || race !== PA.race;
  const lose = [paG("puppet"), ...(drop ? others : [])].filter(g => g && paGroupDirty(g.gid));
  if (lose.length && (PA.race !== race || PA.sex !== sex || PA.id !== id || drop) && !confirm(`Discard the unsaved edits of ${lose.map(g => g.label).join(", ")}?`)) { await paFillPickers(); return; }
  if (drop) { others.forEach(g => paDropCache(g.gid)); PA.groups = PA.groups.filter(g => g.gid === "puppet"); }
  PA.race = race; PA.sex = sex; PA.build = `${race}.${sex}`; PA.mask = null;
  if (!id) id = paDefaultCand(await get("character.paint_state", { build: PA.build }));
  PA.id = id; paRemember();
  if (!id) { paDropCache("puppet"); PA.groups = PA.groups.filter(g => g.gid !== "puppet"); await paFillPickers(); paRefreshAll(); paStatus(" no paint candidates for this build yet (Race → Paint)"); return; }
  paStatus(" loading (a paint's skin bands are made the first time it opens, ~20 s)");
  const doc = await get("character.paint_parts", { build: PA.build, candidate: id });
  paPut({ gid: "puppet", kind: "paint", doc, build: PA.build, id, label: `${PA.build} · ${id}` });
  await paFillPickers(); await paRefreshAll();
}
async function paSetItem(slot, item, cid, doc) {   // an item candidate on its slot ("" = take it off)
  const gid = `item:${slot}`, old = paG(gid);
  if (old && paGroupDirty(gid) && !(old.item === item && old.id === cid) && !confirm(`Discard the unsaved edits of ${old.label}?`)) { paLayers(); return; }
  if (old && old.item === item && old.id === cid && !doc) return;
  if (!item) { paDropCache(gid); PA.groups = PA.groups.filter(g => g.gid !== gid); }
  else { paStatus(" loading the item…"); doc = doc || await get("items.layers", { race: PA.race, item, candidate: cid });
    paPut({ gid, kind: "item", doc, race: PA.race, item, id: cid, slot, label: `${slot}: ${paShort(item)}` }); PA.fold.delete(gid); }
  await paRefreshAll();
}
async function paSetLayer(kind, id, doc) {   // a hair / face candidate ("" = take it off)
  const old = paG(kind);
  if (old && paGroupDirty(kind) && old.id !== id && !confirm(`Discard the unsaved edits of the ${kind} layer?`)) { paLayers(); return; }
  if (old && old.id === id && !doc) return;
  if (!id) { paDropCache(kind); PA.groups = PA.groups.filter(g => g.gid !== kind); }
  else { doc = doc || await get("character.face_layers", { race: PA.race, kind, candidate: id });
    paPut({ gid: kind, kind, doc, race: PA.race, id, label: `${kind}: ${id}` }); PA.fold.delete(kind); }
  await paRefreshAll();
}
function paDocOf(g) {
  return g.kind === "paint" ? get("character.paint_parts", { build: g.build, candidate: g.id })
    : g.kind === "item" ? get("items.layers", { race: g.race, item: g.item, candidate: g.id })
    : get("character.face_layers", { race: g.race, kind: g.kind, candidate: g.id });
}
// the other pages' Edit buttons open their candidate here: /editor?build=<build>&paint=<id> (a painted puppet),
// ?race=&layer=hair|face&id= (a hair / face layer), ?race=&item=&id= (a fitted item); common.js openInEditor()
async function openParts(build, id) { await initParts(true); const [race, sex] = build.split("."); await paSetPuppet(race, sex, id, true); }
async function paOpenOn(on, race0) {   // the puppet a layer was made on (unless it's already that build)
  const [race, sex] = (on || `${race0}.male`).split(".");
  if (!paG("puppet") || PA.build !== `${race}.${sex}`) await paSetPuppet(race, sex, null, race !== PA.race);
}
async function openLayer(race, kind, id) {   // a hair / face layer, on its puppet
  await initParts(true);
  const doc = await get("character.face_layers", { race, kind, candidate: id }); await paOpenOn(doc.on, race); await paSetLayer(kind, id, doc);
  PA.sel = `${kind}|layer`; paLevels(); paLayers(); paDraw();
}
async function openItemLayers(race, item, id) {   // a fitted item, on its puppet
  await initParts(true);
  const doc = await get("items.layers", { race, item, candidate: id }); await paOpenOn(doc.on, race); await paSetItem(doc.slot, item, id, doc);
  const covered = paInfo(paG(`item:${doc.slot}`), PA.view).names.find(n => !PA.empty[paKey(PA.view, `item:${doc.slot}`, n)]);
  PA.sel = `item:${doc.slot}|${covered || "torso"}`; paLevels(); paLayers(); paDraw();
  paStatus(` ${paShort(item)}: paint in the key colours (eyedropper) or any colour; levels are redone on save (hand-edited bands stay)`);
}
// ---- saving, reverting, redoing bands
async function paSaveGroup(g) {   // paint first (the server redoes those layers' bands), then the hand-edited bands (they win)
  const gid = g.gid, dirty = (v, n, b) => PA.dirty[paKey(v, gid, n) + (b ? "#b" : "")], png = (v, n) => PA.parts[paKey(v, gid, n)].toDataURL("image/png");
  let paint = 0, bands = 0;
  for (const v of VIEWS_PA) { const I = paInfo(g, v); if (!I) continue; const ps = I.names.filter(n => dirty(v, n)); if (!ps.length) continue;
    if (g.kind === "paint") g.doc = await post("character.save_paint_parts", { build: g.build, candidate: g.id, view: v, parts: Object.fromEntries(ps.map(n => [n, png(v, n)])) });
    else if (g.kind === "item") g.doc = await post("items.save_layers", { race: g.race, item: g.item, candidate: g.id, view: v, layers: Object.fromEntries(ps.map(n => [n + "@item", png(v, n)])) });
    else g.doc = await post("character.save_face_layer", { race: g.race, kind: g.kind, candidate: g.id, view: v, png: png(v, "layer") });
    paint += ps.length;
    for (const n of ps) if (!dirty(v, n, true)) { delete PA.parts[paKey(v, gid, n) + "#b"]; delete PA.bmask[paKey(v, gid, n)]; } }   // reload the redone bands
  for (const v of VIEWS_PA) { const I = paInfo(g, v); if (!I) continue; const bs = I.names.filter(n => dirty(v, n, true)); if (!bs.length) continue;
    const bp = n => paBandPng(paKey(v, gid, n) + "#b");
    if (g.kind === "paint") g.doc = await post("character.save_paint_bands", { build: g.build, candidate: g.id, view: v, bands: Object.fromEntries(bs.map(n => [n, bp(n)])) });
    else if (g.kind === "item") g.doc = await post("items.save_bands", { race: g.race, item: g.item, candidate: g.id, view: v, bands: Object.fromEntries(bs.map(n => [n + "@item", bp(n)])) });
    else g.doc = await post("character.save_face_bands", { race: g.race, kind: g.kind, candidate: g.id, view: v, png: bp("layer") });
    bands += bs.length; }
  for (const k of Object.keys(PA.dirty)) if (k.includes(`|${gid}|`)) delete PA.dirty[k];
  return { paint, bands };
}
async function paSave() {
  const done = []; let puppetPaint = false;
  for (const g of paRootOrder()) { if (!paGroupDirty(g.gid)) continue;
    paStatus(` saving ${g.label}…`); const r = await paSaveGroup(g);
    if (g.kind === "paint" && r.paint) puppetPaint = true;
    done.push(`${g.label}: ${[r.paint && `${r.paint} layer${r.paint > 1 ? "s" : ""}`, r.bands && `${r.bands} band${r.bands > 1 ? "s" : ""}`].filter(Boolean).join(" + ")}`); }
  if (!done.length) { paStatus(" nothing to save"); return; }
  await paRefreshAll();
  paStatus(` saved ${done.join("; ")}${puppetPaint ? "; the walk re-renders (queue)" : ""}`);
  if (puppetPaint) pollJobs();
}
async function paRevert() {
  const g = paSelG() || paG("puppet"); if (!g) return;
  if (!confirm(g.kind === "paint" ? `Throw away every edit of ${g.label} and go back to the painted parts?` : `Throw away every edit of ${g.label}?`)) return;
  if (g.kind === "paint") await post("character.revert_paint_parts", { build: g.build, candidate: g.id });
  else if (g.kind === "item") await post("items.revert_layers", { race: g.race, item: g.item, candidate: g.id });
  else await post("character.revert_face_layer", { race: g.race, kind: g.kind, candidate: g.id });
  paDropCache(g.gid); g.doc = await paDocOf(g); await paRefreshAll(); paStatus(` ${g.label} reverted`);
}
async function paBandReset(e) {
  const g = paSelG(), n = paSelN(), all = e.shiftKey;
  if (!g) throw new Error("Select a layer first.");
  if (g.kind === "face") throw new Error("A face has no ramp bands (its irises are keyed).");
  if (!confirm(all ? `Redo every band of ${g.label} from its paint (hand edits go${g.kind === "paint" ? ", new skin key" : ""})?` : `Redo the bands of ${g.label} · ${n} (${PA.view}) from its paint (its hand edits go)?`)) return;
  paStatus(" redoing bands…");
  if (g.kind === "paint") g.doc = await post("character.reset_paint_bands", all ? { build: g.build, candidate: g.id } : { build: g.build, candidate: g.id, view: PA.view, part: n });
  else if (g.kind === "item") g.doc = await post("items.reset_bands", { race: g.race, item: g.item, candidate: g.id, ...(all ? {} : { view: PA.view, part: n + "@item" }) });
  else g.doc = await post("character.reset_face_bands", { race: g.race, kind: g.kind, candidate: g.id, ...(all ? {} : { view: PA.view }) });
  for (const k of Object.keys(PA.parts)) if (k.endsWith("#b") && k.includes(`|${g.gid}|`) && (all || k === paSelKey() + "#b")) { delete PA.parts[k]; delete PA.bmask[k.slice(0, -2)]; delete PA.dirty[k]; }
  await paRefreshAll(); paStatus(" bands redone from the paint");
}
async function paFix() {
  if (!PA.sel || paSelG().gid !== "puppet") throw new Error("The AI fix works on puppet parts; for an item, hair or face layer, paint it or queue new candidates.");
  if (!PA.mask) throw new Error("Mark the area first (AI fix area tool, M).");
  if (PA.dirty[paSelKey()]) await paSave();   // the server paints on the saved part
  await queue([{ kind: "paint_fix", build: PA.build, id: PA.id, view: PA.view, part: paSelN(), mask: PA.mask.toDataURL("image/png"),
    prompt: $("paPrompt").value, label: `${PA.build} · paint ${PA.id} · AI fix ${paSelN()}` }]);
  PA.fixPending = true; paStatus(" AI fix queued: the part reloads when it's done");
}
async function initParts() {
  if (PA.inited) return; PA.inited = true;
  paLoadUi(); paSize(); paWire(); paSetTool("brush"); paStatus();
  document.querySelectorAll("#paRail [data-tool]").forEach(b => b.onclick = () => paSetTool(b.dataset.tool));
  $("paSize").oninput = () => { $("paSizeV").textContent = $("paSize").value; paDraw(); };
  document.querySelectorAll("#paRail [data-toggle]").forEach(b => { b.classList.toggle("on", $(b.dataset.toggle).checked);
    b.onclick = () => paToggle(b.dataset.toggle); });
  $("paColor").oninput = () => { if (paBandsOn()) paSetTool("brush"); paSwatch(); };
  $("paZIn").onclick = () => paZoomBy(1.25); $("paZOut").onclick = () => paZoomBy(0.8);
  $("paBandReset").onclick = guard(paBandReset);
  $("paZoom").onchange = () => { paSize(); paDraw(); };
  $("paReset").onclick = () => { PA.vw = { z: 1, ox: 0, oy: 0 }; paDraw(); };
  $("paUndo").onclick = () => paUndo(PA.undo, PA.redo); $("paRedo").onclick = () => paUndo(PA.redo, PA.undo);
  $("paAll").onclick = () => { PA.hidden.clear(); PA.ghide.clear(); paSaveUi(); paLayers(); paDraw(); };
  $("paView").onchange = guard(async () => { PA.view = $("paView").value; PA.ghost = null; paRemember(); await paRefreshAll(); });
  $("paRace").onchange = guard(() => paSetPuppet($("paRace").value, PA.sex || "male", null));
  $("paPupRoot").onclick = e => { if (e.target.id === "paPupVis") return; PA.fold.has("puppet") ? PA.fold.delete("puppet") : PA.fold.add("puppet"); paSaveUi(); paLayers(); };
  $("paPupVis").onchange = () => { $("paPupVis").checked ? PA.ghide.delete("puppet") : PA.ghide.add("puppet"); paSaveUi(); paLayers(); paDraw(); };
  document.addEventListener("click", e => { const c = e.target.closest("[data-rchip]"), pop = $("paRampPop");   // ramp chips (Layers) and their popover
    if (c && !c.disabled) { if (!pop.hidden && pop.dataset.for === c.dataset.rchip) { pop.hidden = true; return; } pop.dataset.for = c.dataset.rchip; paRampPop(c); return; }
    if (!pop.hidden && !e.target.closest("#paRampPop")) pop.hidden = true; });
  document.getElementById("paLayerCol").addEventListener("scroll", () => $("paRampPop").hidden = true);
  $("paSex").onchange = guard(() => paSetPuppet(PA.race, $("paSex").value, null));
  $("paCand").onchange = guard(async () => { if ($("paCand").value) await paSetPuppet(PA.race, PA.sex, $("paCand").value); });
  paLevels();
  $("paFrame").oninput = () => { paPlay(false); paSetFrame(+$("paFrame").value); };   // scrubbing stops the playback
  $("paPlay").onclick = () => paPlay(!PA.playing);
  $("paSave").onclick = guard(paSave);
  $("paRevert").onclick = guard(paRevert);
  $("paFix").onclick = guard(paFix);
  get("character.paint_spec").then(r => { PA.pspec = r.spec; $("paFixBase").value = r.spec.fix; }).catch(e => err(e.message));
  $("paFixSave").onclick = guard(async () => { const sp = JSON.parse(JSON.stringify(PA.pspec)); sp.fix = $("paFixBase").value.trim();
    PA.pspec = (await post("character.save_paint_spec", { spec: sp })).spec; $("paFixStatus").textContent = " saved"; });
  $("paClearMask").onclick = () => { PA.mask = null; paDraw(); };
  window.addEventListener("beforeunload", e => { if (Object.values(PA.dirty).some(Boolean)) { e.preventDefault(); e.returnValue = ""; } });
}
async function paStart() {   // straight to the editor: the last race and gender, as the plain base model
  await initParts();
  if (PA.opening || paG("puppet")) return;
  let last = {}; try { last = JSON.parse(localStorage.getItem("paLast") || "{}"); } catch (e) { }
  PA.view = VIEWS_PA.includes(last.view) ? last.view : "front";
  await paSetPuppet(last.race || "human", last.sex || "male", null, true);
}
document.querySelector('.tab[data-t="parts"]').addEventListener("click", guard(paStart));
setInterval(() => {   // after a save or an AI fix: reload the puppet's previews (and the fixed part) once its job is done
  const P = paG("puppet"); if (!P) return;
  const mine = jobs.filter(j => (j.kind === "paint_preview" || j.kind === "paint_fix") && j.label.startsWith(`${P.build} · paint ${P.id}`));
  const done = mine.filter(j => j.status === "done" || j.status === "failed").length;
  if (PA.jobsDone === undefined) { PA.jobsDone = done; return; }
  if (done !== PA.jobsDone) { PA.jobsDone = done;
    paDocOf(P).then(async d => {
      P.doc = d;
      if (PA.fixPending) { PA.fixPending = false;   // reload the fixed parts (unsaved edits were saved first)
        const fix = mine.filter(j => j.kind === "paint_fix").pop();
        if (fix && fix.status === "failed") paStatus(` AI fix failed: ${fix.error}`);
        else { PA.mask = null; for (const k of Object.keys(PA.parts)) if (k.includes("|puppet|") && !PA.dirty[k]) delete PA.parts[k];
          await paLoadView(PA.view); paStatus(" AI fix applied"); } }
      paDraw(); }).catch(e => err(e.message)); }
}, 2000);

// ------------------------------------------------------------------ the page
startPage("editor");
{ const q = new URLSearchParams(location.search), race = q.get("race");
  if (q.get("paint") || q.get("layer") || q.get("item")) PA.opening = true;   // a candidate from another page, not the last puppet
  wantedTab().click();
  if (q.get("build") && q.get("paint")) guard(() => openParts(q.get("build"), q.get("paint")))();
  else if (q.get("layer")) guard(() => openLayer(race, q.get("layer"), q.get("id")))();
  else if (q.get("item")) guard(() => openItemLayers(race, q.get("item"), q.get("id")))(); }

// <exports>
export { PA, PA_BAND, PA_BAND_RGB, PA_HELD, PA_LEVELS, PA_LINE, PA_LUT, PA_N, PA_TOOLS, PA_TORSO_KIDS, VIEWS_PA, initParts, openItemLayers, openLayer, openParts, paBandCanvas, paBandLevel, paBandPng, paBandReset, paBandSnap, paBandSync, paBandsOn, paBegin, paCanvas, paChip, paClearLayer, paClip, paCtx, paDefaultCand, paDocOf, paDraw, paDrawNow, paDropCache, paEK, paEnd, paExtend, paFaceLevels, paFillPickers, paFix, paFlush, paFrames, paG, paGhost, paGroupDirty, paHeal, paInfo, paIsEmpty, paKey, paLayers, paLevels, paLoadImg, paLoadUi, paLoadView, paMaskStroke, paMoveStart, paMoveTo, paNudge, paOpenOn, paOrder, paParent, paPick, paPickers, paPlay, paPos, paPosed, paPupHead, paPut, paRaf, paRampChanged, paRampFn, paRampNow, paRampPop, paRecolor, paRecolorBox, paRefreshAll, paRemember, paRevert, paRootOrder, paRow, paSave, paSaveGroup, paSaveUi, paSelG, paSelKey, paSelN, paSelXf, paSetFrame, paSetItem, paSetLayer, paSetPuppet, paSetTool, paShort, paSize, paSkinish, paSlots, paStamp, paStart, paStatus, paStrip, paSwatch, paToLocal, paToggle, paTouch, paTree, paUndo, paVisible, paWire, paXf, paZoomBy };
// </exports>
