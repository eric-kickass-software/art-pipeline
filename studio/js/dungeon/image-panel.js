// Dungeon Forge: the image panel (inspect one family image; approve, star, archive, edit, rescale, fit courses).
// ---- image panel: inspect one family image; approve / use, star, archive, edit (repaint), rescale (masters).
import { get, post, url } from "../api.js";
import { $, esc } from "./util.js";
// <imports>
import { famMsg, famSet, refreshReviewCount, renderFamilyDynamic, rnd, scheduleBuild } from "./families.js";
import { D, FAM, OUT, SET, candUrl, dget, dpost, load, natUrl, piece, pollJobs, pstate, renderList, seamUrl, viewScale } from "./main.js";
import { pvForceRefresh } from "./preview.js";
// </imports>
let IMG = null;  // {list: [{pid, rel, kind}], i, view}
function openImage(list, i) { IMG = {list, i, view: IMG?.view || "tiled"}; $("#imgPanel").classList.remove("hidden"); renderImage(); }
function closeImage() { if (IMG?.review) refreshReviewCount(); IMG = null; $("#imgPanel").classList.add("hidden"); }
function stepImage(d) { if (!IMG) return; IMG.i = (IMG.i + d + IMG.list.length) % IMG.list.length; renderImage(); }

function renderImage() {
  const it = IMG.list[IMG.i], p = piece(it.pid), s = pstate(it.pid), meta = it.kind === "trial" ? {} : (s.meta || {})[it.rel] || {};
  const master = it.kind === "master", trial = it.kind === "trial", active = pstate(FAM).approved[0];
  const varText = (famSet().variations.find(v => v.id === meta.variation) || {}).text;
  const n = p ? atlas_n_js(p) : 1;
  const elev = p && p.kind === "elevation";
  const views = trial ? [["image", "image"], ["game", "in game"]] : elev ? [["tiled", p.method === "door_repaint" ? "between wall slices" : "repeated sideways"], ["image", "image"], ...(master ? [["bands", "keep bands"], ["fit", "fit preview"]] : [])]
    : [["tiled", master ? "tiled 3×3" : "among master tiles"], ["image", "image"], ["game", "in game"]];
  if (!views.some(v => v[0] === IMG.view)) IMG.view = views[0][0];
  IMG.keepT ??= 0.25; IMG.keepB ??= 0.1;
  const bandsUrl = () => url("dungeon.elevation_bands", {set: SET, piece: it.pid, rel: it.rel, top: 0, bottom: 0});
  IMG.fitMode ??= "keep";
  const fitArgs = () => ({set: SET, piece: it.pid, rel: it.rel, top: IMG.keepT, bottom: IMG.keepB, mode: IMG.fitMode});
  const fitUrl = () => url("dungeon.fit_courses_image", fitArgs());
  const src = IMG.view === "fit" ? fitUrl() : IMG.view === "bands" ? bandsUrl() : IMG.view === "image" ? candUrl(it.rel)
    : IMG.view === "tiled" ? seamUrl(it.pid, it.rel)
    : natUrl(it.pid, it.rel, OUT(), D.spec.pixel ? viewScale() : 1);
  const doorText = p && p.method === "door_repaint" ? ((famSet().doors || []).find(d => d.id === meta.variation) || {}).text : null;
  $("#ipTitle").textContent = trial ? `variation test · ${it.variation}` : master ? `master · ${it.pid}`
    : p && p.method === "door_repaint" ? `door · ${doorText || "untagged"}`
    : p && p.method === "wall_repaint" ? `${n}-cell wall · ${varText || "untagged"}` : `${n}×${n} · ${varText || "untagged"}`;
  $("#ipCount").textContent = `${IMG.review ? "review · " : ""}${IMG.i + 1} / ${IMG.list.length}${IMG.review ? " · A approve · X archive · ← → skip" : ""}`;
  $("#ipTabs").innerHTML = views.map(([k, l]) => `<button data-v="${k}" class="${IMG.view === k ? "on" : ""}">${l}</button>`).join("");
  $("#ipTabs").querySelectorAll("button").forEach(b => b.onclick = () => { IMG.view = b.dataset.v; renderImage(); });
  $("#ipImg").src = src; $("#ipImg").classList.toggle("px", IMG.view === "game" && !!D.spec.pixel);
  IMG.guides = master && elev && IMG.view !== "game" && IMG.view !== "fit" ? it : null;
  $("#ipImg").onload = drawKeepGuides; drawKeepGuides();
  const on = master ? it.rel === active : s.approved.includes(it.rel), stale = !master && !trial && meta.master !== active;
  const rows = [["piece", it.pid], ["prompt", meta.prompt || ""], ["seed", meta.seed ?? ""], ["strength", meta.denoise ?? ""],
    ["made from", meta.edited_from ? `edit of ${meta.edited_from.split("/").pop()}` : meta.rescaled_from ? `×${Array.isArray(meta.scale) ? meta.scale.join(" wide, ×") + " tall" : meta.scale} of ${meta.rescaled_from.split("/").pop()}` : meta.evened_from ? `even tone ${meta.even} of ${meta.evened_from.split("/").pop()}` : meta.fitted_from ? `fit ${meta.fit.courses} courses (${meta.fit.mode}) of ${meta.fitted_from.split("/").pop()}` : meta.finalized_from ? `full master from draft ${meta.finalized_from.split("/").pop()}` : meta.draft ? "draft" : ""],
    ["file", it.rel.split("/").pop()]].filter(r => r[1] !== "");
  $("#ipSide").innerHTML = `
    ${stale ? `<div class="stale">STALE: made from another master. Its edges won't match the active master.</div>` : ""}
    <table>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join("")}</table>
    ${trial ? `<div class="hint">A variation test: not part of the family. Make it a variant by adding the variation and generating its row.</div>` : `
    <div class="row">${master
      ? `${meta.draft ? `<button id="ipFinalize" class="primary" title="Redraw this draft at full resolution with the same composition and make it seamless (~10 s), as a new master">Finalize draft</button>` : ""}<button id="ipUse" class="${on ? "on" : meta.draft ? "" : "primary"}">${on ? "✓ Active master" : "Use as master"}</button><button id="ipStar">${(s.starred || []).includes(it.rel) ? "★ Starred" : "☆ Star"}</button>`
      : `<button id="ipApprove" class="${on ? "on" : "primary"}">${on ? "✓ Approved (unapprove)" : "Approve"}</button>`}
      <button id="ipArchive" class="danger">Archive</button></div>
    <h3>Edit</h3>
    <div class="hint">Repaint this image's interior from itself (its edges stay, so it still fits the family). The result is a new image next to this one.</div>
    <textarea id="ipPrompt" rows="3">${esc(meta.prompt || (p && p.prompt) || "")}</textarea>
    <div class="row"><label>strength<input id="ipDen" type="range" min="0.2" max="1" step="0.05" value="0.5" style="width:120px"></label><span class="badge" id="ipDenLbl">0.50</span>
      <button id="ipEdit" class="primary">Repaint</button></div>
    ${master && !elev ? `<h3>Rescale</h3><div class="hint">Change the texture scale (stone size) as a new master. ×½ / ×⅓ are exact repeats; the others are resampled and made seamless again.</div>
      <div class="row">${[[1 / 3, "×⅓"], [0.5, "×½"], [0.75, "×¾"], [1.5, "×1.5"], [2, "×2"]].map(([f, l]) => `<button data-f="${f}" class="ipScale">${l}</button>`).join("")}</div>
      <label><input id="ipRedetail" type="checkbox" checked style="width:auto"> re-detail upscales (light repaint)</label>
      <h3>Even tone</h3><div class="hint">Pull stones that stand out (a darker slab, a stain) toward the tile's average colour, keeping their detail and the edges. Instant; the result is a new master.</div>
      <div class="row"><label>strength<input id="ipEven" type="range" min="0.2" max="1" step="0.1" value="0.8" style="width:120px"></label><span class="badge" id="ipEvenLbl">0.8</span>
        <button id="ipEvenBtn">Even tone</button></div>` : ""}
    ${master && elev ? `<h3>Rescale bricks</h3><div class="hint">The cap and base (tinted in <b>keep bands</b>) stay exactly as they are; the wall between them is rescaled, counted up from the base, and snapped to whole courses so none is cut under the cap. The result is a new master.</div>
      <div class="row"><label title="Units kept at the top (cap / coping)">keep top<input id="ipKeepT" type="number" step="0.05" min="0" max="1.5" value="${IMG.keepT}" style="width:58px"></label>
        <label title="Units kept at the base (plinth)">bottom<input id="ipKeepB" type="number" step="0.05" min="0" max="1.5" value="${IMG.keepB}" style="width:58px"></label><span class="hint">units (wall = ${famSet().height})</span>
        <button id="ipKeepFit" title="Set both to where the regular courses stop (the cap's underside, the plinth's top)">fit to cap &amp; base</button></div>
      <div class="hint">Drag the yellow lines on the image: they snap to the wall's joints (blue ticks; hold Shift for free placement) and turn green when snapped.</div>
      <div class="hint" id="ipMeasure">measuring the courses…</div>
      <div class="row"><label title="Courses between the kept bands">rows<input id="ipRows" type="number" step="1" min="2" max="80" style="width:56px"></label>
        <label title="Bricks per tile width (the wall repeats every tile)">bricks across<input id="ipAcross" type="number" step="1" min="1" max="24" style="width:56px"></label>
        <button id="ipWallScale" class="primary">Rescale</button></div>
      <div class="row"><span class="hint">both ways (same brick shape):</span>${[[0.5, "×½"], [0.75, "×¾"], [1.5, "×1.5"], [2, "×2"]].map(([f, l]) => `<button data-f="${f}" class="ipWScale">${l}</button>`).join("")}</div>
      <label><input id="ipRedetailW" type="checkbox" checked style="width:auto"> re-detail upscales (light repaint)</label>
      <h3>Fit courses</h3><div class="hint">Cut at the two lines (they snap to joints) and give every course between them the same height: no partial course at either end, and the stretch is spread over all the courses. Vertical only, so it still repeats sideways. See it in <b>fit preview</b>.</div>
      <div class="row"><label>mode<select id="ipFitMode" style="width:auto"><option value="keep" ${IMG.fitMode === "keep" ? "selected" : ""}>keep cap &amp; base</option><option value="crop" ${IMG.fitMode === "crop" ? "selected" : ""}>crop to the lines (fill the wall)</option></select></label></div>
      <div class="hint" id="ipFitInfo">…</div>
      <div class="row"><button id="ipFitView">fit preview</button><button id="ipFitSave" class="primary">Save fitted master</button></div>
      <label title="A light repaint (0.3) of the resampled courses, edges kept"><input id="ipFitRedetail" type="checkbox" style="width:auto"> re-detail stretched courses (light repaint)</label>` : ""}`}
    <div class="hint" id="ipMsg"></div>`;
  const msg = t => $("#ipMsg").textContent = t;
  const refresh = () => { renderFamilyDynamic(true); renderList(); renderImage(); };
  if ($("#ipApprove")) $("#ipApprove").onclick = async () => {
    try {
      D.state.pieces[it.pid] = (await dpost("dungeon.approve", {piece: it.pid, rel: it.rel, on: !on})).piece; scheduleBuild();
      if (IMG.review && !on) {  // review: approved -> off the list, on to the next
        IMG.list.splice(IMG.i, 1); renderFamilyDynamic(true); renderList();
        if (!IMG.list.length) { closeImage(); return famMsg("Review done."); }
        IMG.i = Math.min(IMG.i, IMG.list.length - 1); renderImage(); return;
      }
      refresh();
    } catch (e) { msg(e.message); }
  };
  if ($("#ipUse")) $("#ipUse").onclick = async () => { if (on) return; try { D.state.pieces[FAM] = (await dpost("dungeon.promote", {piece: FAM, rel: it.rel})).piece; await load(); renderImage(); scheduleBuild(); } catch (e) { msg(e.message); } };
  if ($("#ipFinalize")) $("#ipFinalize").onclick = async () => {
    try { await dpost("dungeon.finalize", {piece: it.pid, rel: it.rel}); msg("Queued: the full master appears next to the draft (Use as master when you like it)."); pollJobs(); }
    catch (e) { msg(e.message); }
  };
  if ($("#ipStar")) $("#ipStar").onclick = async () => { try { D.state.pieces[FAM] = (await dpost("dungeon.star", {piece: FAM, rel: it.rel, on: !(s.starred || []).includes(it.rel)})).piece; refresh(); } catch (e) { msg(e.message); } };
  if ($("#ipArchive")) $("#ipArchive").onclick = async () => {
    try {
      D.state.pieces[it.pid] = (await dpost("dungeon.discard", {piece: it.pid, rel: it.rel})).piece;
      IMG.list.splice(IMG.i, 1); renderFamilyDynamic(true); renderList();
      if (!IMG.list.length) return closeImage();
      IMG.i = Math.min(IMG.i, IMG.list.length - 1); renderImage();
    } catch (e) { msg(e.message); }
  };
  if ($("#ipDen")) $("#ipDen").oninput = () => $("#ipDenLbl").textContent = (+$("#ipDen").value).toFixed(2);
  if ($("#ipEdit")) $("#ipEdit").onclick = async () => {
    try { await dpost("dungeon.edit", {piece: it.pid, rel: it.rel, prompt: $("#ipPrompt").value, denoise: +$("#ipDen").value, seed: rnd()}); msg("Queued: the repaint appears next to this image."); pollJobs(); }
    catch (e) { msg(e.message); }
  };
  if ($("#ipEven")) $("#ipEven").oninput = () => $("#ipEvenLbl").textContent = (+$("#ipEven").value).toFixed(1);
  if ($("#ipKeepT")) {
    let M = null;
    const measure = async () => {
      try { M = await dget("dungeon.elevation_measure", {piece: it.pid, rel: it.rel, top: IMG.keepT, bottom: IMG.keepB}); }
      catch (e) { $("#ipMeasure").textContent = e.message; return; }
      $("#ipMeasure").textContent = M.rows ? `Now: ≈ ${M.rows} rows (each ≈ ${M.course_units} units tall), ≈ ${M.across ?? "?"} bricks across a tile.` : "Couldn't find clear courses: use the ×½ … ×2 buttons.";
      if (M.rows) $("#ipRows").value = Math.round(M.rows);
      if (M.across) $("#ipAcross").value = Math.round(M.across);
    };
    const fitInfo = async () => {
      const el = $("#ipFitInfo"); if (!el) return;
      try {
        const F = await get("dungeon.fit_courses_info", fitArgs());
        const pct = v => `${v > 0 ? "+" : ""}${Math.round(v * 100)}%`;
        el.innerHTML = `${F.courses} courses · overall ${pct(F.overall)} · each course ${pct(Math.min(...F.stretch))} to ${pct(Math.max(...F.stretch))}` +
          (F.warn ? ` <b style="color:#ffb4a8">· a lot of stretch: details may smear (try lines one joint further in or out)</b>` : "");
      } catch (e) { el.textContent = e.message; }
    };
    IMG.remeasure = () => { measure(); fitInfo(); if (IMG.view === "fit") $("#ipImg").src = fitUrl(); };
    $("#ipFitMode").onchange = () => { IMG.fitMode = $("#ipFitMode").value; IMG.remeasure(); };
    $("#ipFitView").onclick = () => { IMG.view = "fit"; renderImage(); };
    $("#ipFitSave").onclick = async () => {
      try { await dpost("dungeon.rescale", {piece: it.pid, rel: it.rel, fit: true, mode: IMG.fitMode, keep_top: IMG.keepT, keep_bottom: IMG.keepB, redetail: $("#ipFitRedetail").checked, seed: rnd()});
        msg("Queued: the fitted wall appears as a new master."); pollJobs(); }
      catch (e) { msg(e.message); }
    };
    const setKeeps = (t, b) => { IMG.keepT = t; IMG.keepB = b; $("#ipKeepT").value = t; $("#ipKeepB").value = b; drawKeepGuides(); IMG.remeasure(); };
    const loadJoints = async () => {
      if (IMG.joints && IMG.joints.rel === it.rel) return IMG.joints;
      try { IMG.joints = {rel: it.rel, ...(await dget("dungeon.elevation_joints", {piece: it.pid, rel: it.rel}))}; }
      catch { IMG.joints = {rel: it.rel, joints: [], suggest: {top: IMG.keepT, bottom: IMG.keepB}, height: famSet().height}; }
      drawKeepGuides(); return IMG.joints;
    };
    loadJoints().then(J => { if (IMG && IMG.keepFor !== it.rel) { IMG.keepFor = it.rel; setKeeps(J.suggest.top, J.suggest.bottom); } else IMG.remeasure(); });
    $("#ipKeepFit").onclick = async () => { const J = await loadJoints(); setKeeps(J.suggest.top, J.suggest.bottom); };
    const keeps = () => { setKeeps(Math.max(0, +$("#ipKeepT").value || 0), Math.max(0, +$("#ipKeepB").value || 0)); };
    $("#ipKeepT").onchange = $("#ipKeepB").onchange = keeps;
    const go = async (fx, fy) => {
      try {
        await dpost("dungeon.rescale", {piece: it.pid, rel: it.rel, fx: +fx.toFixed(3), fy: +fy.toFixed(3), keep_top: IMG.keepT, keep_bottom: IMG.keepB, redetail: $("#ipRedetailW").checked, seed: rnd()});
        msg(`Queued: bricks ×${fx.toFixed(2)} wide, ×${fy.toFixed(2)} tall (rows snap to whole courses). It appears as a new master.`); pollJobs();
      } catch (e) { msg(e.message); }
    };
    $("#ipWallScale").onclick = () => {
      if (!M || !M.rows) return msg("No course measurement: use the ×½ … ×2 buttons.");
      const rows = +$("#ipRows").value, across = +$("#ipAcross").value;
      go(M.across && across ? M.across / across : 1, rows ? M.rows / rows : 1);
    };
    document.querySelectorAll(".ipWScale").forEach(b => b.onclick = () => go(+b.dataset.f, +b.dataset.f));
  }
  if ($("#ipEvenBtn")) $("#ipEvenBtn").onclick = async () => {
    try {
      const r = await dpost("dungeon.even", {piece: it.pid, rel: it.rel, strength: +$("#ipEven").value});
      D.state.pieces[it.pid] = r.piece; IMG.list.splice(IMG.i, 0, {...it, rel: r.rel}); renderFamilyDynamic(true); renderImage();
      msg("Evened: shown here as a new master (Use as master to switch).");
    } catch (e) { msg(e.message); }
  };
  document.querySelectorAll(".ipScale").forEach(b => b.onclick = async () => {
    try { await dpost("dungeon.rescale", {piece: it.pid, rel: it.rel, factor: +b.dataset.f, redetail: $("#ipRedetail").checked, seed: rnd()}); msg(`Queued: ${b.textContent} appears as a new master.`); pollJobs(); }
    catch (e) { msg(e.message); }
  });
}
// Keep-band guides over a wall master in the image panel (all views show the full wall height).
function drawKeepGuides() {
  const g = $("#ipGuides"), im = $("#ipImg");
  if (!g) return;
  if (!IMG || !IMG.guides || !im.naturalHeight) { g.innerHTML = ""; g.style.display = "none"; return; }
  const box = im.parentElement.getBoundingClientRect(), r = im.getBoundingClientRect();
  Object.assign(g.style, {display: "block", left: `${r.left - box.left}px`, top: `${r.top - box.top}px`, width: `${r.width}px`, height: `${r.height}px`});
  const H = (IMG.joints && IMG.joints.height) || famSet().height, px = r.height / H;
  const joints = (IMG.joints && IMG.joints.joints) || [];
  const snapped = u => joints.some(j => Math.abs(j - u) < 1e-3);
  const t = IMG.keepT, b = IMG.keepB;
  let html = `<div class="band" style="top:0;height:${t * px}px"></div><div class="band" style="top:${(H - b) * px}px;height:${b * px}px"></div>`;
  for (const j of joints) html += `<div class="joint" style="top:${j * px}px;left:0;width:12px"></div><div class="joint" style="top:${j * px}px;right:0;width:12px"></div>`;
  for (let u = 0; u <= H + 1e-6; u += 0.25) html += `<div class="tick" style="top:${u * px}px">${Math.abs(u % 0.5) < 1e-6 ? u.toFixed(1) : ""}</div>`;
  html += `<div class="edge ${snapped(t) ? "snapped" : ""}" data-k="t" style="top:${t * px}px"></div><div class="edge ${snapped(H - b) ? "snapped" : ""}" data-k="b" style="top:${(H - b) * px}px"></div>`;
  html += `<div class="lbl" style="top:${Math.max(0, t * px - 20)}px">keep top ${t.toFixed(2)} u</div><div class="lbl" style="top:${Math.min(r.height - 18, (H - b) * px + 4)}px">keep bottom ${b.toFixed(2)} u</div>`;
  g.innerHTML = html;
  g.querySelectorAll(".edge").forEach(e => e.onpointerdown = ev => {
    ev.preventDefault(); e.setPointerCapture(ev.pointerId);
    const k = e.dataset.k;
    e.onpointermove = mv => {
      const rr = $("#ipImg").getBoundingClientRect();
      let u = Math.min(Math.max((mv.clientY - rr.top) / rr.height * H, 0), H);
      if (!mv.shiftKey) { const near = joints.reduce((a, j) => Math.abs(j - u) < Math.abs(a - u) ? j : a, Infinity); if (Math.abs(near - u) * px < 10) u = near; }
      if (k === "t") IMG.keepT = +Math.min(u, H - IMG.keepB - 0.3).toFixed(3); else IMG.keepB = +Math.min(H - u, H - IMG.keepT - 0.3).toFixed(3);
      if ($("#ipKeepT")) { $("#ipKeepT").value = IMG.keepT; $("#ipKeepB").value = IMG.keepB; }
      drawKeepGuides();
    };
    e.onpointerup = () => { e.onpointermove = null; IMG.remeasure && IMG.remeasure(); };
  });
}
window.addEventListener("resize", drawKeepGuides);

const atlas_n_js = p => p.method === "atlas" ? (p.atlas || 4) : p.method === "repaint" ? (p.atlas || 1) : 1;
$("#famRefresh").onclick = pvForceRefresh;
$("#ipClose").onclick = closeImage; $("#ipPrev").onclick = () => stepImage(-1); $("#ipNext").onclick = () => stepImage(1);
$("#imgPanel").onclick = e => { if (e.target.id === "imgPanel") closeImage(); };
document.addEventListener("keydown", e => {
  if (!IMG || /TEXTAREA|INPUT/.test(document.activeElement.tagName)) return;
  if (e.key === "Escape") closeImage(); else if (e.key === "ArrowLeft") stepImage(-1); else if (e.key === "ArrowRight") stepImage(1);
  else if (e.key === "a" && $("#ipApprove")) $("#ipApprove").click();
  else if (e.key === "x" && $("#ipArchive")) $("#ipArchive").click();
});

// <exports>
export { IMG, atlas_n_js, closeImage, drawKeepGuides, openImage, renderImage, stepImage };
// </exports>
