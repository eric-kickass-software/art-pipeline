// Dungeon Forge: the family preview (the planner's layout drawn with the cut sprites, pan + zoom, scale guides).
// ---- live preview: the planner's layout drawn with the cut sprites (what the Unity export ships), pan + zoom.
import { get, post, url } from "../api.js";
import { $, esc } from "./util.js";
// <imports>
import { buildWalls, famInfo, famSet, renderFamilyDynamic, rnd } from "./families.js";
import { D, FAM, JOBS, SET, dget, pstate, setState } from "./main.js";
// </imports>
let FAMSEED = 0;   // the floor-only layout's seed (↻ rerolls it)
const PV = {scale: 1, ox: 0, oy: 0, data: null, imgs: {}, fitted: false, raf: 0, loading: false, keys: [], pending: 0, at: "", nonce: 0, buildPending: false};
const pvStamp = () => `${D.spec.pixel || 0}_${D.spec.harmonize || 0}_${(PV.data && PV.data.rev) || 0}_${PV.nonce}`;

// Preview status chip: is what's drawn current? Updating / loading sprites / walls rebuilding / out of date / ✓.
function pvStatus() {
  const el = $("#pvStatus"); if (!el || !D || !famInfo()) return;
  const active = (JOBS || []).filter(j => (j.status === "queued" || j.status === "running") && (j.label || "").startsWith(SET + " ·"));
  const wallId = famInfo().kind === "wall" ? FAM : famRoomMode() ? famWallForRoom() : "";
  const wf = wallId ? D.families[wallId] : null;
  const building = active.some(j => j.kind === "dun_build") || PV.buildPending;
  // approved variants made from another master: their edges won't meet the active master's
  const staleVars = [];
  for (const [fid, f] of Object.entries(D.families || {})) {
    if (fid !== FAM && fid !== wallId && fid !== famFloorForRoom()) continue;
    const mrel = pstate(fid).approved[0]; if (!mrel) continue;
    for (const pid of [...Object.values(f.pieces || {}), f.door].filter(Boolean)) {
      const ps = pstate(pid);
      for (const r of ps.approved || []) if ((ps.meta || {})[r]?.master && ps.meta[r].master !== mrel) staleVars.push(pid);
    }
  }
  let cls = "ok", txt, tip = "The preview matches the saved set.";
  if (PV.loading) { cls = "busy"; txt = "⟳ updating layout…"; }
  else if (PV.pending > 0) { cls = "busy"; txt = `⟳ loading ${PV.pending} sprite${PV.pending > 1 ? "s" : ""}…`; }
  else if (building) { cls = "busy"; txt = "⟳ rebuilding walls…"; tip = "Walls are re-projected from the active master and approvals; the preview updates when it's done."; }
  else if (wf && wf.build && wf.build.stale) { cls = "warn"; txt = `⚠ walls out of date (${wf.build.why}): click to build`; tip = "Click to build walls & doors now."; }
  else if (staleVars.length) { cls = "warn"; txt = `⚠ ${staleVars.length} approved variant${staleVars.length > 1 ? "s" : ""} from another master`; tip = "Approved variants made from an older master are still in the mix; their edges won't match. Unapprove or regenerate them."; }
  else txt = `✓ up to date${PV.at ? " · " + PV.at : ""}`;
  if (cls !== "busy" && active.length) txt += ` · ${active.length} job${active.length > 1 ? "s" : ""} running`;
  el.className = "pvstatus " + cls; el.textContent = txt; el.title = tip;
  el.onclick = cls === "warn" && wf && wf.build && wf.build.stale ? () => buildWalls(wallId) : null;
  $("#famStage")?.classList.toggle("busy", cls === "busy");
}
function pvTrack() {
  PV.pending = PV.keys.filter(k => PV.imgs[k] && !PV.imgs[k].complete).length;
  if (!PV.pending && !PV.loading) PV.at = new Date().toLocaleTimeString();
  pvStatus();
}
async function pvForceRefresh() {
  try { await post("dungeon.refresh_previews"); } catch {}
  PV.imgs = {}; PV.nonce = Date.now();
  setState({D: await dget("dungeon.state")});
  renderFamilyDynamic(true);
}

// The preview draws either a floor-only layout (family_layout) or, when the set has built walls (or this is a wall
// family), a room: floor + joined walls and doors (room_layout), with every sprite exactly as exported.
function famFloorForRoom() {
  if (famInfo().kind === "floor") return FAM;
  return Object.keys(D.families || {}).find(id => D.families[id].kind === "floor" && pstate(id).approved[0]) || "";
}
const famWallForRoom = () => famInfo().kind === "wall" ? FAM : Object.keys(D.families || {}).find(id => D.families[id].kind === "wall" && (D.families[id].built.length || D.families[id].settings.template)) || "";
const famRoomMode = () => famInfo().kind === "wall" || $("#famBlockout")?.checked || Object.values(D.families || {}).some(f => f.kind === "wall" && f.built.length);
async function loadFamLayout(keepView) {
  if (!$("#famCanvas")) return;
  if (!pstate(FAM).approved[0] && !($("#famBlockout")?.checked && famSet().template)) { PV.req = (PV.req || 0) + 1; PV.loading = false; PV.keys = []; pvTrack(); PV.data = null; $("#famStageMsg").textContent = "Pick an active master (or turn on blockout with a template) to see it in a room."; drawFam(); return; }
  const room = famRoomMode(), w = +$("#famW").value || 14, h = +$("#famH").value || 10;
  const req = PV.req = (PV.req || 0) + 1;  // only the latest request may draw (switching sets/families races)
  PV.loading = true; pvStatus();
  let data;
  try {
    const bo = $("#famBlockout")?.checked ? {blockout: true, wall: famWallForRoom()} : {};
    data = await (room ? dget("dungeon.room_layout", {w, h, floor: famFloorForRoom(), ...bo})
                       : dget("dungeon.family_layout", {piece: FAM, seed: FAMSEED, w, h}));
  } catch (e) { if (req === PV.req) { $("#famStageMsg").textContent = e.message; PV.loading = false; pvStatus(); } return; }
  if (req !== PV.req) return;
  PV.loading = false;
  PV.data = data;
  const D0 = PV.data;
  if (!D0.items) {  // floor-only layout: convert cells to positioned items
    D0.ox = 0; D0.oy = 0;
    D0.items = D0.cells.map(c => ({piece: c.piece, rel: c.rel, suf: c.suf, gx: c.x, gy: c.y}));
    D0.blocks = [...new Map(D0.cells.map(c => [`${c.bx},${c.by}`, [c.bx, c.by, c.n]])).values()];
    D0.walls = D0.holes;
  }
  $("#famStageMsg").textContent = room && famInfo().kind === "wall" && !famInfo().built.length ? "Not built yet: approve a master, then Build walls & doors." : "";
  const st = pvStamp();
  PV.keys = [];
  for (const c of D0.items) {
    c.key = `${c.piece}|${c.rel}|${c.suf}|${st}`;
    PV.keys.push(c.key);
    if (!PV.imgs[c.key]) {
      const im = new Image();
      im.onload = () => { scheduleFamDraw(); pvTrack(); };
      im.onerror = pvTrack;
      im.src = url("dungeon.cell_image", {set: SET, piece: c.piece, rel: c.rel, suf: c.suf}, st);
      PV.imgs[c.key] = im;
    }
  }
  if (!keepView || !PV.fitted) fitFam();
  drawFam();
  pvTrack();
}

function scheduleFamDraw() { if (!PV.raf) PV.raf = requestAnimationFrame(() => { PV.raf = 0; drawFam(); }); }

function famBounds() {
  const d = PV.data, t = d.tile;
  if (d.w) return {x0: 0, x1: d.w, y0: 0, y1: d.h};  // room: pixel canvas
  return {x0: -d.H * t / 2, x1: d.W * t / 2, y0: 0, y1: (d.W + d.H) * t / 4};
}

function fitFam() {
  const stage = $("#famStage"); if (!stage || !PV.data) return;
  const b = famBounds(), cw = stage.clientWidth, ch = stage.clientHeight;
  PV.scale = Math.min(cw / (b.x1 - b.x0), ch / (b.y1 - b.y0)) * 0.94;
  PV.ox = cw / 2 - (b.x0 + b.x1) / 2 * PV.scale; PV.oy = ch / 2 - (b.y0 + b.y1) / 2 * PV.scale;
  PV.fitted = true;
}

function drawFam() {
  const cv = $("#famCanvas"), stage = $("#famStage"); if (!cv || !stage) return;
  const dpr = window.devicePixelRatio || 1, cw = stage.clientWidth, ch = stage.clientHeight;
  if (cv.width !== Math.round(cw * dpr) || cv.height !== Math.round(ch * dpr)) { cv.width = Math.round(cw * dpr); cv.height = Math.round(ch * dpr); }
  const ctx = cv.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.fillStyle = "#16121c"; ctx.fillRect(0, 0, cv.width, cv.height);
  if (!PV.data) return;
  ctx.setTransform(dpr * PV.scale, 0, 0, dpr * PV.scale, dpr * PV.ox, dpr * PV.oy);
  ctx.imageSmoothingEnabled = !PV.data.pixel;  // pixel art: point filtering, like the export's "point" filter in Unity
  const d = PV.data, t = d.tile, at = (x, y) => [d.ox + (x - y) * t / 2, d.oy + (x + y) * t / 4];
  for (const c of d.items) {
    const im = PV.imgs[c.key];
    if (!im || !im.complete || !im.naturalWidth) continue;
    if (c.gx !== undefined) { const [sx, sy] = at(c.gx, c.gy); ctx.drawImage(im, sx - t / 2, sy, t, im.naturalHeight * t / im.naturalWidth); }
    else ctx.drawImage(im, c.x, c.y);  // room: placed by the server (pivots and wall joins as the game)
  }
  if ($("#famGuides").checked) drawGuides(ctx, t, at);
  if ($("#famOutline") && $("#famOutline").checked) {
    const colour = {1: "#ffffff", 2: "#5ac8ff", 3: "#ffc83c", 4: "#ff5a78"};
    ctx.lineWidth = 2 / PV.scale;
    for (const [bx, by, n] of d.blocks || []) {
      const pts = [at(bx, by), at(bx + n, by), at(bx + n, by + n), at(bx, by + n)];
      ctx.strokeStyle = colour[n] || "#ccc"; ctx.globalAlpha = n > 1 ? 0.95 : 0.35;
      ctx.beginPath(); pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath(); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
}

// Scale guides: hero figures (height in units: 1 unit = half a tile width; walls 3, doors 2) and a 1-tile block at wall
// height, drawn in the same iso projection, to judge how big the texture reads.
const WALL_UNITS = 3;  // Eric 2026-10-12: walls 3 units (2 heroes), doors 2, hero 1.5
function drawGuides(ctx, t, at) {
  const W = PV.data.W, H = PV.data.H, unit = t / 2, hero = Math.max(0.3, +$("#famHero").value || 1.5);
  const holes = new Set((PV.data.walls || PV.data.holes || []).map(([x, y]) => `${x},${y}`)), free = (x, y) => !holes.has(`${x},${y}`);
  const label = (txt, x, y) => { ctx.font = `${Math.round(t * 0.13)}px sans-serif`; ctx.textAlign = "center"; ctx.lineWidth = t * 0.03;
    ctx.strokeStyle = "rgba(0,0,0,.85)"; ctx.strokeText(txt, x, y); ctx.fillStyle = "#fff"; ctx.fillText(txt, x, y); };
  // block: 1 x 1 tile, wall height (3 units)
  const bx = 2, by = 2, h = WALL_UNITS * unit, up = ([x, y]) => [x, y - h];
  const A = at(bx, by), B = at(bx + 1, by), C = at(bx + 1, by + 1), E = at(bx, by + 1);
  const face = (pts, fill) => { ctx.beginPath(); pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath();
    ctx.fillStyle = fill; ctx.fill(); ctx.lineWidth = t * 0.012; ctx.strokeStyle = "rgba(10,8,14,.9)"; ctx.stroke(); };
  face([E, C, up(C), up(E)], "rgba(110,124,150,.92)"); face([C, B, up(B), up(C)], "rgba(84,96,120,.92)"); face([up(A), up(B), up(C), up(E)], "rgba(150,164,190,.92)");
  label(`1 tile · wall ${WALL_UNITS}u`, up(A)[0], up(A)[1] - t * 0.06);
  // heroes: a simple figure on its cell centre
  for (const [x, y] of [[Math.floor(W / 2), Math.floor(H / 2)], [3, H - 3], [W - 3, 3]]) {
    if (!free(x, y)) continue;
    const [cx, cy] = at(x + 0.5, y + 0.5), hp = hero * unit, bw = t * 0.17;
    ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.beginPath(); ctx.ellipse(cx, cy, t * 0.16, t * 0.08, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#c9a15a"; ctx.strokeStyle = "rgba(20,14,8,.95)"; ctx.lineWidth = t * 0.012;
    ctx.beginPath(); ctx.roundRect(cx - bw / 2, cy - hp * 0.8, bw, hp * 0.8, bw * 0.45); ctx.fill(); ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy - hp * 0.89, hp * 0.11, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    label(`hero ${hero}u`, cx, cy - hp - t * 0.05);
  }
}

function setupFamCanvas() {
  const cv = $("#famCanvas"); let drag = null;
  cv.onmousedown = e => { drag = {x: e.clientX, y: e.clientY, ox: PV.ox, oy: PV.oy}; cv.classList.add("grabbing"); };
  cv.onmouseup = cv.onmouseleave = () => { drag = null; cv.classList.remove("grabbing"); };
  cv.onmousemove = e => { if (!drag) return; PV.ox = drag.ox + e.clientX - drag.x; PV.oy = drag.oy + e.clientY - drag.y; scheduleFamDraw(); };
  cv.onwheel = e => {
    e.preventDefault();
    const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const k = Math.exp(-e.deltaY * 0.0015), s = Math.min(Math.max(PV.scale * k, 0.02), 16);
    PV.ox = mx - (mx - PV.ox) * s / PV.scale; PV.oy = my - (my - PV.oy) * s / PV.scale; PV.scale = s; scheduleFamDraw();
  };
  cv.ondblclick = () => { fitFam(); drawFam(); };
  if (PV.bound) return;  // page-level listeners once
  PV.bound = true;
  try { $("#famHero").value = localStorage.getItem("dun.hero") || 1.5; } catch {}
  $("#famHero").onchange = () => { try { localStorage.setItem("dun.hero", $("#famHero").value); } catch {} drawFam(); };
  $("#famGuides").onchange = $("#famOutline").onchange = drawFam;
  $("#famBlockout").onchange = () => loadFamLayout(true);
  $("#famReroll").onclick = () => { FAMSEED = rnd(); loadFamLayout(true); };
  $("#famW").onchange = $("#famH").onchange = () => loadFamLayout(false);
  $("#famFit").onclick = () => { fitFam(); drawFam(); };
  $("#famFull").onclick = () => { $("#famStage").classList.toggle("full"); fitFam(); drawFam(); };
  $("#famExport").onclick = () => $("#exportBtn").click();
  window.addEventListener("resize", scheduleFamDraw);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#famStage")?.classList.contains("full")) { $("#famStage").classList.remove("full"); fitFam(); drawFam(); } });
}

// <exports>
export { FAMSEED, PV, WALL_UNITS, drawFam, drawGuides, famBounds, famFloorForRoom, famRoomMode, famWallForRoom, fitFam, loadFamLayout, pvForceRefresh, pvStamp, pvStatus, pvTrack, scheduleFamDraw, setupFamCanvas };
// </exports>
