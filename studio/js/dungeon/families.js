// Dungeon Forge: floor and wall families (a master, variations per size, doors), their layout template and saving.
// ---- floor families: one master + repainted variants per block size, on one board.
// Static part (prompt versions, variation phrases, targets, weights) renders once; the master grid, size strips and
// preview re-render when jobs finish, without touching what's being typed.
import { get, post, url } from "../api.js";
import { $, esc } from "./util.js";
// <imports>
import { IMG, openImage, renderImage } from "./image-panel.js";
import { D, FAM, SET, VIEW, candUrl, dget, dpost, load, natUrl, notice, piece, pollJobs, pstate, renderList, seamUrl, setState, setView } from "./main.js";
import { PV, loadFamLayout, pvStatus, setupFamCanvas } from "./preview.js";
// </imports>
const famInfo = () => (D.families || {})[FAM];
const famSet = () => famInfo().settings;
const SIZES = [1, 2, 3, 4];
const rnd = () => 1 + Math.floor(Math.random() * 99999);

function renderFamiliesList() {
  const ids = Object.keys(D.families || {}), kind = id => D.families[id].kind;
  const item = id => {
    const rel = pstate(id).approved[0], wall = kind(id) === "wall";
    const img = rel ? `<img src="${wall ? candUrl(rel) : natUrl(id, rel, D.spec.tile, 1)}" style="${wall ? "object-fit:cover" : ""}">` : `<div class="ph"></div>`;
    const badge = !rel ? ["none", "no master"] : wall ? (D.families[id].built.length ? ["ok", "built"] : ["part", "not built"]) : ["ok", "family"];
    return `<div class="pitem fitem ${VIEW === "family" && FAM === id ? "sel" : ""}" data-fam="${esc(id)}">${img}<span class="nm" title="${esc(id)}">${esc(id.replace(/^tile\./, "").replace(/_(wall_)?master$/, ""))}</span><span class="badge ${badge[0]}">${badge[1]}</span></div>`;
  };
  return `<div class="grp">Floor families</div>` + ids.filter(id => kind(id) === "floor").map(item).join("") +
    `<div class="row"><button class="newFam" data-k="floor" style="width:100%">+ New floor family</button></div>` +
    `<div class="grp">Wall families</div>` + ids.filter(id => kind(id) === "wall").map(item).join("") +
    `<div class="row"><button class="newFam" data-k="wall" style="width:100%">+ New wall family</button></div>`;
}

async function newFamily(kind = "floor") {
  const wall = kind === "wall";
  const nm = (prompt(`${wall ? "Wall" : "Floor"} family name (letters, digits, _), e.g. stone, brick, sewer:`, "stone") || "").trim();
  if (!nm) return;
  const material = prompt(`What the ${wall ? "wall" : "floor"} is made of (the master's prompt):`,
    wall ? "rough grey stone blocks with dark mortar, flat stone cap along the top" : "cracked grey flagstones with dark mortar lines") || "";
  const slug = nm.replace(/\W/g, "_");
  let piece;
  if (wall) piece = {id: `tile.${SET}_${slug}_wall_master`, kind: "elevation", shape: "elevation", method: "wall_master", variants: 1, prompt: material, family: {prompts: [material]}};
  else {
    const board = (D.spec.pieces.find(q => q.method === "master") || D.spec.pieces.find(q => q.shape === "floor" && (q.board || q.id).endsWith("_floor")) || {}).board || `tile.${SET}_floor`;
    piece = {id: `tile.${SET}_${slug}_master`, kind: "tile", shape: "floor", method: "master", board, variants: 1, prompt: material, family: {prompts: [material]}};
  }
  try { await dpost("dungeon.save_piece", {piece}); setState({FAM: piece.id}); await load(); setView("family"); } catch (e) { notice(e.message); }
}


function renderFamPrompts() {
  const ps = famSet().prompts;
  $("#famPrompts").innerHTML = ps.map((t, i) => `<div class="row famver"><span class="badge">${String.fromCharCode(65 + i)}</span>
    <textarea rows="2" data-i="${i}" style="flex:1">${esc(t)}</textarea>${ps.length > 1 ? `<button data-x="${i}" title="remove this version">✕</button>` : ""}</div>`).join("");
  document.querySelectorAll("#famPrompts textarea").forEach(t => t.oninput = () => { famSet().prompts[+t.dataset.i] = t.value; saveFamilySoon(); });
  document.querySelectorAll("#famPrompts [data-x]").forEach(b => b.onclick = () => { famSet().prompts.splice(+b.dataset.x, 1); renderFamPrompts(); saveFamily(); });
}


// Master candidates grouped by the prompt version that made them (meta.prompt).
function renderFamMasters() {
  const s = pstate(FAM), meta = s.meta || {}, active = s.approved[0], starred = s.starred || [], trials = s.trials || {};
  const versions = famSet().prompts, groups = {};
  s.candidates.forEach(rel => { const pr = (meta[rel] || {}).prompt || ""; (groups[pr] ||= []).push(rel); });
  const label = pr => { const i = versions.indexOf(pr); return i >= 0 ? `${String.fromCharCode(65 + i)} · ${pr}` : `older prompt · ${pr}`; };
  const order = Object.keys(groups).sort((a, b) => (versions.indexOf(a) + 1 || 99) - (versions.indexOf(b) + 1 || 99));
  $("#famMasters").innerHTML = s.candidates.length ? order.map(pr => `<div class="hint famgrp" title="${esc(pr)}">${esc(label(pr))}</div><div class="famcards">` +
    groups[pr].map(rel => `<div class="famcard ${rel === active ? "active" : ""}" data-rel="${esc(rel)}">${(meta[rel] || {}).draft ? `<span class="draft" title="a quick draft: open it and Finalize for a full master">DRAFT</span>` : ""}
      <img class="seam" src="${seamUrl(FAM, rel)}" title="tiled 3×3 (click to inspect)">
      <div class="btns"><button data-a="star" class="${starred.includes(rel) ? "on" : ""}" title="keep for comparing">${starred.includes(rel) ? "★" : "☆"}</button>
        <button data-a="use" class="${rel === active ? "on" : ""}">${rel === active ? "✓ Active master" : "Use as master"}</button>
        <button data-a="drop" class="danger" title="archive">✕</button></div>
      <div class="trials">${(trials[rel] || []).map(t => `<img src="${candUrl(t.rel)}" title="${esc(t.variation)}">`).join("")}</div>
    </div>`).join("") + `</div>`).join("") : `<div class="hint">No masters yet: write a prompt version or two and hit Generate masters.</div>`;
  const masterList = [...document.querySelectorAll(".famcard")].map(c => ({pid: FAM, rel: c.dataset.rel, kind: "master"}));
  document.querySelectorAll(".famcard").forEach((card, ci) => {
    const rel = card.dataset.rel;
    card.querySelector(".seam").onclick = () => openImage(masterList, ci);
    const tl = (trials[rel] || []).map(t => ({pid: FAM, rel: t.rel, kind: "trial", variation: t.variation, from: rel}));
    card.querySelectorAll(".trials img").forEach((i, ti) => i.onclick = () => openImage(tl, ti));
    card.querySelectorAll("button").forEach(b => b.onclick = async () => {
      try {
        if (b.dataset.a === "star") D.state.pieces[FAM] = (await dpost("dungeon.star", {piece: FAM, rel, on: !(pstate(FAM).starred || []).includes(rel)})).piece;
        if (b.dataset.a === "use") {
          const stale = SIZES.some(n => famPiece(n) && pstate(famPiece(n)).approved.length);
          if (stale && rel !== pstate(FAM).approved[0] && !confirm("Switch the active master? Variants made from the old one keep its edges and will be marked stale.")) return;
          D.state.pieces[FAM] = (await dpost("dungeon.promote", {piece: FAM, rel})).piece; await load(); return;
        }
        if (b.dataset.a === "drop") D.state.pieces[FAM] = (await dpost("dungeon.discard", {piece: FAM, rel})).piece;
        renderFamilyDynamic(); renderList();
      } catch (e) { famMsg(e.message); }
    });
  });
}

const famPiece = n => (famInfo().pieces || {})[n];



function famMsg(t) { const el = $("#famMsg"); if (el) el.textContent = t; }

let famSaveT = null;
function saveFamilySoon() { clearTimeout(famSaveT); famSaveT = setTimeout(saveFamily, 600); }

async function generateMasters(draft = false) {
  await saveFamily();
  const k = Math.max(1, +$("#famSeeds").value || 1) * (draft ? 3 : 1), vers = famSet().prompts.filter(p => p.trim());
  try {
    for (const pr of vers) for (let i = 0; i < k; i++) await dpost("dungeon.generate", {piece: FAM, n: 1, seed: rnd(), prompt: pr.trim(), draft});
    famMsg(`Queued ${vers.length * k} ${draft ? "drafts (3× the seeds: they're quick)" : "masters"}.`); pollJobs();
  } catch (e) { famMsg(e.message); }
}

async function testVariation() {
  const starred = pstate(FAM).starred || [], v = $("#famTrialText").value.trim();
  if (!starred.length) { famMsg("★ a master or two first."); return; }
  try { for (const rel of starred) await dpost("dungeon.trial", {piece: FAM, rel, variation: v, seed: rnd()}); famMsg(`Testing "${v}" on ${starred.length} master${starred.length > 1 ? "s" : ""}.`); pollJobs(); }
  catch (e) { famMsg(e.message); }
}

function renderFamily() {
  if (!famInfo()) { $("#familyView").innerHTML = `<div class="hint">Pick a floor family on the left, or make one.</div>`; return; }
  const f = famSet(), wall = famInfo().kind === "wall";
  $("#familyView").innerHTML = `
    <h2 style="margin-top:0">${esc(FAM)} <span class="hint">· ${wall
      ? "wall family: one front-view elevation (3 units tall, repeats every tile), wall variations per run length and doors repainted from it, then built onto every wall piece"
      : "floor family: one seamless master, variants repainted from it (they share its edges)"}</span>
      <button id="famDelete" class="danger" style="float:right" title="Remove this family and all its pieces from the set; their images move to archive/families/">Delete family</button>
      ${wall ? "" : `<button id="famMatchWall" style="float:right;margin-right:6px" title="A wall family made to go with this floor: same palette and joints (via the material sheet, filled from this floor if empty), stone module carried over">+ matching wall family</button>`}</h2>
    <div class="row" style="align-items:center"><div class="stages" id="famStages" style="flex:1;margin:0"></div>
      <button id="famFill" title="Queue only what's missing: every variation x size (and door) with no candidate from the active master yet">Fill gaps</button>
      <button id="famReview" title="Step through every candidate waiting for a decision: A approve, X archive, arrows skip">Review</button></div>
    <h3>Layout template <span class="hint">a repeating layout at a known scale: masters start from it (FLUX keeps the layout and paints the surface), and the preview's blockout shows it before anything is generated</span></h3>
    <div class="row"><label style="display:flex;flex-direction:row;gap:6px;align-items:center" title="Take the palette, stone size and joints from the set's material sheet (left), and follow it when it changes"><input id="famLink" type="checkbox" style="width:auto" ${(f.template || {}).link ? "checked" : ""}> linked to the set's material sheet</label></div>
    <div class="tplrow"><img id="tplImg" alt="" title="the template, 2 tiles"><div id="tplForm" class="tplform"></div></div>
    <h3>1 · Masters <span class="hint">prompt versions × seeds, judged tiled 3×3. Click a master to inspect, ★, use, edit or rescale it.</span></h3>
    <div id="famPrompts"></div>
    <div class="gbar"><button id="famAddVer">+ prompt version</button>
      <label>seeds each<input id="famSeeds" type="number" value="2" min="1" max="8" style="width:52px"></label>
      <label class="${wall ? "hidden" : ""}" title="Even out stone-sized differences in colour (a darker slab, a stain) on new masters: a master repeats every tile, so anything that stands out repeats too. 0 = off">even tone<input id="famEven" type="number" step="0.1" min="0" max="1" value="${f.even_tone ?? 0.8}" style="width:52px"></label>
      <button id="famDraftMasters" title="Quick looks: a quarter of the pixels, half the steps, no seam repaint (~2 s each). Finalize the one you like into a full master (same composition)">Draft masters</button>
      <button id="famGenMasters" class="primary">Generate masters</button>
      <span class="hint" id="famMsg"></span></div>
    <div id="famMasters"></div>
    <div class="gbar ${wall ? "hidden" : ""}"><label style="flex:1">variation test<input id="famTrialText" value="${esc((f.variations[0] || {}).text || "")}" style="width:100%"></label>
      <button id="famTrialBtn" title="A quick 1×1 repaint of this variation from every ★ master, to see how each takes variations">Test on ★ masters</button></div>
    <h3>2 · Variations <span class="hint">${wall ? "each row is repainted from the active elevation for runs of every ticked length (the edges, cap and base stay the master's)." : "each row is repainted from the active master at every ticked size."} Click an image to inspect, approve or edit it.</span></h3>
    <div id="famMatrix"></div>
    <div class="gbar"><button id="famAddVar">+ variation</button>${wall ? "" : `<button id="famAddReroll" title="A plain variation: the same surface with the stones redrawn and nothing special added. Mixed in with the master, these break up the every-tile repeat">+ plain reroll</button>`}
      <label>per cell<input id="famN" type="number" value="1" min="1" max="6" style="width:52px"></label>
      <button id="famGenAll" class="primary">Generate all</button>
      <label title="${wall ? "2-4 cell variations are placed as one piece: paint the feature once across the run (a strong repaint in one zone, light elsewhere), or spread it over the whole run (e.g. moss everywhere)" : "2x2-4x4 blocks are placed as one piece: redraw the block, then paint the feature once in a zone that crosses tiles (two passes), or spread it over the block (one pass, the old way)"}">multi-cell features<select id="famSpan" style="width:auto"><option value="once" ${(f.span || "once") === "once" ? "selected" : ""}>once across the ${wall ? "run" : "block"}</option><option value="spread" ${f.span === "spread" ? "selected" : ""}>spread over the ${wall ? "run" : "block"}</option></select></label>
      <span class="hint">▶ on a row = that variation at every ticked size · ▶ on a column = that size for every variation</span></div>
    ${wall ? `<h3>3 · Doors <span class="hint">each door is painted into a one-tile slice of the active elevation (only the 2-unit opening changes, so it keeps the wall's stone and colours)</span></h3>
    <div id="famDoors"></div>
    <div class="gbar"><label title="Door width in tiles (1 = the whole cell: no side frame, just the header)">width<input id="famDoorW" type="number" step="0.05" min="0.3" max="1" value="${f.door_width}" style="width:58px"></label>
      <label title="Door height in units (walls are ${f.height})">height<input id="famDoorH" type="number" step="0.1" min="1" max="${f.height}" value="${f.door_height}" style="width:58px"></label>
      <button id="famAddDoor">+ door</button><button id="famGenDoors" class="primary">Generate all doors</button>
      <button id="famBuild" class="primary" title="Project the active elevation, approved variations and doors onto runs 1-4, the post and the doorway (also runs by itself after approving)">Build walls &amp; doors</button>
      <span class="hint" id="famBuildMsg">${!famInfo().built.length ? "not built yet" : famInfo().build?.stale ? "out of date: " + famInfo().build.why : "built, up to date"}</span></div>` : ""}
    <h3>Palette <span class="hint">every image's colour (inside its kept border) next to the master's, sorted by distance (dE: ~2 barely visible, 5+ obvious). Click a chip to inspect.</span></h3>
    <div class="gbar"><label title="Right after a repaint, match its interior to the master's colours (the kept border is never touched)"><input id="famLock" type="checkbox" style="width:auto" ${f.palette_lock ? "checked" : ""}> lock new variants and edits to the master's palette</label>
      <span class="hint">For existing images, use Harmonize (Output, top right): on a family it pulls variant interiors to the master and never moves the edges.</span></div>
    <div id="famPalette" class="fampal"></div>`;
  renderFamPrompts();
  renderTemplateForm();
  $("#famAddVer").onclick = () => { famSet().prompts.push(famSet().prompts[famSet().prompts.length - 1] || ""); renderFamPrompts(); saveFamily(); };
  $("#famDelete").onclick = deleteFamily;
  $("#famFill").onclick = fillFamily;
  $("#famReview").onclick = reviewFamily;
  $("#famLink").onchange = async () => { try { D.spec = (await dpost("dungeon.link_family", {piece: FAM, on: $("#famLink").checked})).spec; await load(); renderFamily(); } catch (e) { famMsg(e.message); } };
  if ($("#famMatchWall")) $("#famMatchWall").onclick = async () => {
    try { const r = await dpost("dungeon.matching_wall", {piece: FAM}); setState({FAM: r.piece}); await load(); setView("family"); notice(`Made ${r.piece}: linked to the material sheet. Generate masters to start.`); }
    catch (e) { famMsg(e.message); }
  };
  $("#famGenMasters").onclick = () => generateMasters(false);
  $("#famDraftMasters").onclick = () => generateMasters(true);
  $("#famTrialBtn").onclick = testVariation;
  $("#famAddVar").onclick = () => { famSet().variations.push({id: "v" + Date.now().toString(36), text: ""}); renderFamMatrix(); saveFamily();
    const ins = document.querySelectorAll("#famMatrix .vtext"); ins[ins.length - 1]?.focus(); };
  $("#famGenAll").onclick = () => generateVariants(null, null);
  $("#famLock").onchange = () => { famSet().palette_lock = $("#famLock").checked; saveFamily(); };
  if ($("#famSpan")) $("#famSpan").onchange = () => { famSet().span = $("#famSpan").value; saveFamily(); };
  $("#famEven").onchange = () => { famSet().even_tone = Math.max(0, Math.min(1, +$("#famEven").value || 0)); saveFamily(); };
  if ($("#famAddReroll")) $("#famAddReroll").onclick = () => { famSet().variations.unshift({id: "v" + Date.now().toString(36), text: "the same stones redrawn, different shapes and wear, nothing special"}); renderFamMatrix(); saveFamily(); };
  if (wall) {
    $("#famAddDoor").onclick = () => { famSet().doors.push({id: "d" + Date.now().toString(36), text: ""}); renderFamDoors(); saveFamily();
      const ins = document.querySelectorAll("#famDoors .vtext"); ins[ins.length - 1]?.focus(); };
    $("#famGenDoors").onclick = () => generateVariants(null, null, true);
    $("#famBuild").onclick = () => buildWalls();
    $("#famDoorW").onchange = $("#famDoorH").onchange = () => { famSet().door_width = +$("#famDoorW").value; famSet().door_height = +$("#famDoorH").value; saveFamily(); };
  }
  renderFamilyDynamic(false);
}

async function deleteFamily() {
  const ids = D.spec.pieces.filter(p => p.id === FAM || p.from === FAM || p.built_from === FAM).map(p => p.id);
  const imgs = ids.reduce((n, id) => n + new Set([...(D.state.pieces[id]?.candidates || []), ...(D.state.pieces[id]?.approved || [])]).size, 0);
  if (!confirm(`Delete the family ${FAM}? Its ${ids.length} pieces leave the set and their ${imgs} images move to ` +
               `archive/families/ (delete that folder by hand once you're sure). Anything exported to Unity stays until the next export.`)) return;
  try {
    const r = await dpost("dungeon.archive_family", {piece: FAM});
    notice(`Deleted ${FAM} → ${r.path}`);
    setState({FAM: null}); await load();
  } catch (e) { notice(e.message); }
}

async function fillFamily() {
  await saveFamily();
  try {
    const r = await dpost("dungeon.fill_family", {piece: FAM, n: +($("#famN") || {}).value || 1});
    famMsg(r.jobs.length ? `Queued ${r.jobs.length} to fill the gaps. Review them when they're done.` : "No gaps: every variation and size has a candidate from the active master.");
    pollJobs();
  } catch (e) { famMsg(e.message); }
}
async function reviewFamily() {
  let list;
  try { list = await dget("dungeon.review", {piece: FAM}); } catch (e) { return famMsg(e.message); }
  if (!list.length) return famMsg("Nothing waiting: every candidate from the active master is approved or archived.");
  openImage(list.map(x => ({pid: x.pid, rel: x.rel, kind: "variant"})), 0); IMG.review = true; renderImage();
}
async function refreshReviewCount() {
  if (!$("#famReview")) return;
  try { const n = (await dget("dungeon.review", {piece: FAM})).length; $("#famReview").textContent = n ? `Review (${n})` : "Review"; $("#famReview").classList.toggle("primary", !!n); } catch {}
}

function renderStages() {
  const el = $("#famStages"); if (!el || !famInfo()?.stages) return;
  const icon = {done: "✓", part: "◐", stale: "⚠", todo: "○"};
  el.innerHTML = famInfo().stages.map((st, i) => `<span class="stage ${st.state}" data-k="${st.key}" title="${esc(st.detail)}">${i + 1} · ${icon[st.state]} ${st.label}</span>`).join('<span class="hint">→</span>');
  const target = {template: "#tplForm", master: "#famMasters", variations: "#famMatrix", doors: "#famDoors", built: "#famBuild", export: "#famExport"};
  el.querySelectorAll(".stage").forEach(c => c.onclick = () => {
    const t = $(target[c.dataset.k]); if (!t) return;
    t.scrollIntoView({behavior: "smooth", block: "center"}); t.animate([{outline: "2px solid #ffd23c"}, {outline: "2px solid transparent"}], {duration: 1400});
  });
}

function renderFamilyDynamic(keepView = true) {
  if (VIEW !== "family" || !famInfo() || !$("#famMasters")) return;
  renderStages(); refreshReviewCount();
  setupFamCanvas(); renderFamMasters(); renderFamMatrix(); if (famInfo().kind === "wall") renderFamDoors(); renderFamPalette(); loadFamLayout(keepView);
}

// Variations × sizes. Candidates are matched to a row by meta.variation (older ones without it: "untagged").
function renderFamMatrix() {
  const f = famSet(), active = pstate(FAM).approved[0];
  const cellOf = (n, vid) => {
    const pid = famPiece(n); if (!pid) return [];
    const s = pstate(pid), meta = s.meta || {};
    return s.candidates.filter(r => (meta[r] || {}).variation === vid || (!vid && !(meta[r] || {}).variation))
      .map(r => ({pid, rel: r, on: s.approved.includes(r), stale: (meta[r] || {}).master !== active, prompt: (meta[r] || {}).prompt || ""}))
      .sort((a, b) => b.on - a.on);
  };
  const thumbs = list => list.map(c => `<img class="${c.on ? "on" : ""} ${c.stale ? "stale" : ""}" data-pid="${esc(c.pid)}" data-rel="${esc(c.rel)}" src="${candUrl(c.rel)}" title="${esc(c.prompt)}${c.stale ? " · STALE: made from another master" : ""}">`).join("");
  const rows = f.variations.map(v => ({v, cells: SIZES.map(n => cellOf(n, v.id))}));
  const untagged = SIZES.map(n => cellOf(n, null));
  const wall = famInfo().kind === "wall";
  const head = `<tr><th>variation</th>${SIZES.map(n => `<th><div class="szhead"><label><input type="checkbox" class="szon" data-n="${n}" ${f.sizes.includes(n) ? "checked" : ""} style="width:auto"> ${wall ? `${n} cell${n > 1 ? "s" : ""}` : `${n}×${n}`}</label>
      <button class="szgen" data-n="${n}" title="generate ${wall ? `${n}-cell runs` : `${n}×${n}`} for every variation">▶</button></div>
      ${wall ? (n === 1 ? `<div class="hint">+ the master</div>` : "") : n > 1 ? `<div class="szw" title="How often the planner uses a ${n}×${n} where one fits"><input type="range" class="fweight" data-n="${n}" min="0" max="100" step="5" value="${f.weights[n] ?? 70}"><span class="fwlbl">${f.weights[n] ?? 70}%</span></div>` : `<div class="hint">+ the master</div>`}</th>`).join("")}<th></th></tr>`;
  const body = rows.map(({v, cells}) => `<tr data-vid="${esc(v.id)}"><td class="vcell"><textarea class="vtext" rows="2" placeholder="what's different, e.g. a shallow puddle">${esc(v.text)}</textarea></td>
      ${cells.map((c, i) => `<td class="mcell ${f.sizes.includes(SIZES[i]) ? "" : "off"}">${thumbs(c) || `<span class="none">–</span>`}</td>`).join("")}
      <td class="vact"><button class="vgen primary" title="generate this variation at every ticked size">▶</button><button class="vdel danger" title="remove this variation">✕</button></td></tr>`).join("") +
    (untagged.some(c => c.length) ? `<tr class="untagged"><td class="hint">untagged (made before variations had ids)</td>${untagged.map(c => `<td class="mcell">${thumbs(c)}</td>`).join("")}<td></td></tr>` : "");
  $("#famMatrix").innerHTML = active ? `<table class="fammatrix ${wall ? "wallm" : ""}">${head}${body}</table>` : `<div class="hint">Pick an active master first (Use as master).</div>`;
  if (!active) return;
  document.querySelectorAll("#famMatrix tr[data-vid]").forEach(tr => {
    const v = famSet().variations.find(x => x.id === tr.dataset.vid);
    tr.querySelector(".vtext").oninput = e => { v.text = e.target.value; saveFamilySoon(); };
    tr.querySelector(".vgen").onclick = () => generateVariants([v.id], null);
    tr.querySelector(".vdel").onclick = () => {
      const n = tr.querySelectorAll(".mcell img").length;
      if (n && !confirm(`Remove "${v.text}"? Its ${n} image${n > 1 ? "s" : ""} stay (shown as untagged).`)) return;
      famSet().variations = famSet().variations.filter(x => x !== v); renderFamMatrix(); saveFamily();
    };
  });
  document.querySelectorAll("#famMatrix .szon").forEach(cb => cb.onchange = () => {
    const n = +cb.dataset.n, s = new Set(famSet().sizes); cb.checked ? s.add(n) : s.delete(n);
    famSet().sizes = SIZES.filter(x => s.has(x)); renderFamMatrix(); saveFamily();
  });
  document.querySelectorAll("#famMatrix .szgen").forEach(b => b.onclick = () => generateVariants(null, [+b.dataset.n]));
  document.querySelectorAll("#famMatrix .fweight").forEach(w => {
    w.oninput = () => w.nextElementSibling.textContent = w.value + "%";
    w.onchange = () => { famSet().weights[+w.dataset.n] = +w.value; saveFamily().then(() => loadFamLayout(true)); };
  });
  const cellList = [...document.querySelectorAll("#famMatrix .mcell img")].map(el => ({pid: el.dataset.pid, rel: el.dataset.rel, kind: "variant"}));
  document.querySelectorAll("#famMatrix .mcell img").forEach((el, i) => el.onclick = () => openImage(cellList, i));
}

// Palette drift: one chip per family image (its interior's mean colour) beside the master's, worst first.
async function renderFamPalette() {
  const el = $("#famPalette"); if (!el) return;
  let P;
  try { P = await dget("dungeon.family_palette", {piece: FAM}); } catch (e) { el.textContent = e.message; return; }
  if (!P.master) { el.innerHTML = `<span class="hint">Pick an active master first.</span>`; return; }
  const vt = id => (famSet().variations.find(v => v.id === id) || {}).text || "untagged";
  const list = P.items.map(it => ({pid: it.piece, rel: it.rel, kind: "variant"}));
  el.innerHTML = `<div class="pchip master" style="background:${P.master.rgb}" title="the active master"><span>master</span></div>` +
    P.items.map((it, i) => `<div class="pchip ${it.approved ? "on" : ""} ${it.stale ? "stale" : ""} ${it.dE >= 5 ? "far" : ""}" data-i="${i}" style="background:${it.rgb}"
      title="${it.n}×${it.n} · ${esc(vt(it.variation))}${it.approved ? " · approved" : ""}${it.stale ? " · stale" : ""} · dE ${it.dE}"><span>${it.dE}</span></div>`).join("");
  el.querySelectorAll(".pchip[data-i]").forEach(c => c.onclick = () => openImage(list, +c.dataset.i));
}

// Doors (wall families): one row per door; candidates of the family's door piece tagged with the row's id.
function renderFamDoors() {
  const el = $("#famDoors"); if (!el) return;
  const pid = famInfo().door, s = pid ? pstate(pid) : {candidates: [], approved: [], meta: {}}, meta = s.meta || {}, active = pstate(FAM).approved[0];
  const imgs = id => s.candidates.filter(r => (meta[r] || {}).variation === id).sort((a, b) => s.approved.includes(b) - s.approved.includes(a));
  el.innerHTML = active ? `<table class="fammatrix">` + famSet().doors.map(d => `<tr data-did="${esc(d.id)}"><td class="vcell"><textarea class="vtext" rows="2" placeholder="e.g. a heavy riveted black iron door">${esc(d.text)}</textarea></td>
      <td class="mcell">${imgs(d.id).map(r => `<img class="${s.approved.includes(r) ? "on" : ""} ${(meta[r] || {}).master !== active ? "stale" : ""}" data-rel="${esc(r)}" src="${candUrl(r)}" title="${esc((meta[r] || {}).prompt || "")}">`).join("") || `<span class="none">–</span>`}</td>
      <td class="vact"><button class="vgen primary" title="generate this door">▶</button><button class="vdel danger" title="remove this door">✕</button></td></tr>`).join("") + `</table>`
    : `<div class="hint">Pick an active master first (Use as master).</div>`;
  if (!active) return;
  const list = [...el.querySelectorAll(".mcell img")].map(i => ({pid, rel: i.dataset.rel, kind: "variant"}));
  el.querySelectorAll(".mcell img").forEach((i, k) => i.onclick = () => openImage(list, k));
  el.querySelectorAll("tr[data-did]").forEach(tr => {
    const d = famSet().doors.find(x => x.id === tr.dataset.did);
    tr.querySelector(".vtext").oninput = e => { d.text = e.target.value; saveFamilySoon(); };
    tr.querySelector(".vgen").onclick = () => generateVariants([d.id], null, true);
    tr.querySelector(".vdel").onclick = () => { famSet().doors = famSet().doors.filter(x => x !== d); renderFamDoors(); saveFamily(); };
  });
}

let buildT = null;
function scheduleBuild() { if (famInfo()?.kind !== "wall") return; clearTimeout(buildT); PV.buildPending = true; pvStatus(); buildT = setTimeout(buildWalls, 1500); }
async function buildWalls(id) {
  clearTimeout(buildT);
  try { await dpost("dungeon.build_walls", {piece: typeof id === "string" ? id : FAM}); if ($("#famBuildMsg")) $("#famBuildMsg").textContent = "building…"; await pollJobs(); }
  catch (e) { famMsg(e.message); }
  PV.buildPending = false; pvStatus();
}

// ---- layout template editor (family board). PATTERNS_DEF comes from the dungeon.patterns op.
let PATTERNS_DEF = null;
const KZ = Math.SQRT1_2 / Math.cos(Math.PI / 6);  // one height unit in tiles (iso._KZ)
const TPL_FIELDS = [["across", "across", 1, 1, 24, "units per tile width (whole number: the layout repeats every tile)"],
  ["course", "course", 0.01, 0.03, 0.6, "row height in tiles (bricks, ashlar, planks)"], ["mortar", "joint", 0.002, 0, 0.06, "mortar / grout width in tiles"],
  ["bevel", "bevel", 0.002, 0, 0.08, "edge rounding in tiles"], ["jitter", "jitter", 0.05, 0, 1, "irregularity (flagstones, cobbles)"]];
async function renderTemplateForm() {
  if (!PATTERNS_DEF) { try { PATTERNS_DEF = await get("dungeon.patterns"); } catch { PATTERNS_DEF = {}; } }
  const t = famSet().template || {pattern: "none"}, def = PATTERNS_DEF[t.pattern] || {}, prm = {...def, ...(t.params || {})};
  const pal = {stones: ["#7d7c80", "#6f6e73", "#87858a", "#76736f"], mortar: "#3b3a3f", variation: 0.08, ...(t.palette || {})};
  const on = t.pattern && t.pattern !== "none";
  $("#tplForm").innerHTML = `
    <div class="row"><label>pattern<select id="tplPattern" style="width:auto"><option value="none">none (prompt only)</option>${Object.entries(PATTERNS_DEF).map(([k, v]) => `<option value="${k}" ${k === t.pattern ? "selected" : ""}>${esc(v.label)}</option>`).join("")}${t.pattern === "image" ? `<option value="image" selected>library image: ${esc(t.material || "")}</option>` : ""}</select></label>
      <label title="How much FLUX may change the template: lower keeps the layout tighter">strength<input id="tplStrength" type="number" step="0.05" min="0.3" max="1" value="${t.strength ?? 0.7}" style="width:58px"></label>
      <label title="Layout seed">seed<input id="tplSeed" type="number" value="${t.seed ?? 1}" style="width:58px"></label></div>
    ${t.link && on && t.pattern !== "image" ? `<div class="hint">Linked: size, joint, bevel and colours come from the material sheet (edits here are replaced by it; untick "linked" to set your own).</div>` : ""}
    ${on && t.pattern !== "image" ? `<div class="row">${TPL_FIELDS.filter(([k]) => k in def).map(([k, l, st, mn, mx, tip]) => `<label title="${tip}">${l}<input class="tplP" data-k="${k}" type="number" step="${st}" min="${mn}" max="${mx}" value="${prm[k]}" style="width:62px"></label>`).join("")}
      ${famInfo().kind === "wall" && prm.course > 0 ? `<label title="Courses up the whole wall (sets course): ${famSet().height} units = ${(famSet().height * KZ).toFixed(2)} tiles">rows<input id="tplRows" type="number" step="1" min="2" max="80" value="${Math.round(famSet().height * KZ / prm.course)}" style="width:52px"></label>` : ""}</div>
    <div class="row"><span class="hint">stones</span>${pal.stones.map((c, i) => `<input type="color" class="tplStone" data-i="${i}" value="${c}">`).join("")}
      <button id="tplAddStone" title="another stone tone">+</button>${pal.stones.length > 1 ? `<button id="tplDelStone" title="remove the last tone">−</button>` : ""}
      <span class="hint">joint</span><input type="color" id="tplMortar" value="${pal.mortar}"></div>` : ""}
    ${t.pattern === "image" ? `<div class="row"><span class="hint">materials library: ${esc(t.material || "")}</span>
      <label title="Whole repeats of the texture per tile (1 tile ≈ 1.5 m): judge it against the hero in the blockout">repeats per tile<input id="tplRepeats" type="number" min="1" max="12" value="${t.repeats ?? 1}" style="width:52px"></label></div>` : ""}`;
  $("#tplImg").classList.toggle("hidden", !on);
  if (on) $("#tplImg").src = url("dungeon.template_image", {set: SET, piece: FAM}, Date.now());
  const collect = () => {
    const pattern = $("#tplPattern").value;
    if (pattern === "none") return {pattern: "none"};
    if (pattern === "image") return {...t, strength: +$("#tplStrength").value, seed: +$("#tplSeed").value, repeats: +($("#tplRepeats")?.value || t.repeats || 1)};
    const params = {};
    document.querySelectorAll("#tplForm .tplP").forEach(i => params[i.dataset.k] = +i.value);
    const stones = [...document.querySelectorAll("#tplForm .tplStone")].map(i => i.value);
    return {pattern, params: pattern === t.pattern ? params : {}, palette: {stones: stones.length ? stones : pal.stones, mortar: $("#tplMortar")?.value || pal.mortar},
            strength: +$("#tplStrength").value, seed: +$("#tplSeed").value, ...(t.link ? {link: true} : {})};
  };
  const changed = async (rerender) => { famSet().template = collect(); await saveFamily(); if (rerender) renderTemplateForm();
    else if ($("#tplImg")) $("#tplImg").src = url("dungeon.template_image", {set: SET, piece: FAM}, Date.now());
    if ($("#famBlockout").checked) loadFamLayout(true); };
  $("#tplPattern").onchange = () => changed(true);
  if ($("#tplRows")) $("#tplRows").oninput = () => { const r = Math.max(2, +$("#tplRows").value || 2), c = document.querySelector('#tplForm .tplP[data-k="course"]');
    if (c) c.value = (famSet().height * KZ / r).toFixed(4); };
  document.querySelectorAll("#tplForm input").forEach(i => i.onchange = () => changed(false));
  if ($("#tplAddStone")) $("#tplAddStone").onclick = () => { famSet().template = {...collect(), palette: {...collect().palette, stones: [...pal.stones, pal.stones[pal.stones.length - 1]]}}; saveFamily().then(() => renderTemplateForm()); };
  if ($("#tplDelStone")) $("#tplDelStone").onclick = () => { famSet().template = {...collect(), palette: {...collect().palette, stones: pal.stones.slice(0, -1)}}; saveFamily().then(() => renderTemplateForm()); };
}

async function generateVariants(varIds, sizes, doors = false) {
  await saveFamily();
  try {
    const r = await dpost("dungeon.generate_family", {piece: FAM, variations: varIds, sizes, doors, n: +($("#famN") || {}).value || 1});
    D.spec = r.spec; await load();
    famMsg(r.jobs.length ? `Queued ${r.jobs.length} variant${r.jobs.length > 1 ? "s" : ""}.` : "Nothing to generate (no ticked sizes, or empty variations).");
    pollJobs();
  } catch (e) { famMsg(e.message); }
}

async function saveFamily() {
  clearTimeout(famSaveT);
  const m = piece(FAM), f = famSet();
  const family = {prompts: f.prompts, variations: f.variations, sizes: f.sizes, weights: f.weights, palette_lock: f.palette_lock, template: f.template || null, span: f.span || "once"};
  if (famInfo().kind !== "wall") family.even_tone = f.even_tone;
  if (famInfo().kind === "wall") Object.assign(family, {doors: f.doors, height: f.height, door_height: f.door_height, door_width: f.door_width, span: f.span || "once"});
  try { D.spec = (await dpost("dungeon.save_piece", {piece: {...m, prompt: f.prompts[0] || m.prompt, family}})).spec; }
  catch (e) { famMsg(e.message); }
}

// <exports>
export { KZ, PATTERNS_DEF, SIZES, TPL_FIELDS, buildT, buildWalls, deleteFamily, famInfo, famMsg, famPiece, famSaveT, famSet, fillFamily, generateMasters, generateVariants, newFamily, refreshReviewCount, renderFamDoors, renderFamMasters, renderFamMatrix, renderFamPalette, renderFamPrompts, renderFamiliesList, renderFamily, renderFamilyDynamic, renderStages, renderTemplateForm, reviewFamily, rnd, saveFamily, saveFamilySoon, scheduleBuild, testVariation };
// </exports>
