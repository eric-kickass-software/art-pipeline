// The Dungeon Forge page (/dungeon): the page state, the set and its pieces, the piece editor, the room preview,
// the job queue and the actions. Floor / wall families, the image panel, the family preview, the group board and the
// materials library are their own modules.
import { get, post, url } from "../api.js";
import { initMenu } from "../menu.js";
import { jobsPanel } from "../jobs.js";
import { $, esc } from "./util.js";
import "./library.js";
import "./image-panel.js";
// <imports>
import { KZ, famInfo, newFamily, renderFamiliesList, renderFamily, renderFamilyDynamic } from "./families.js";
import { groupPieces, renderGroup, renderGroupRow } from "./group.js";
import { LIB, renderLibrary } from "./library.js";
import { WALL_UNITS, pvStatus } from "./preview.js";
// </imports>
let SET = localStorage.getItem("dun.set") || "crypt";
let D = null, SEL = null, TRY = null, VIEW = "pieces", JOBS = [], seenDone = new Set(), capStamp = Date.now();
let FAM = null, GROUP = null;   // the open family; the open group board
/** Rebind the page state from the other modules (an ES module can't assign another module's variables). */
function setState(p) {
  if ("D" in p) D = p.D; if ("SEL" in p) SEL = p.SEL; if ("TRY" in p) TRY = p.TRY; if ("FAM" in p) FAM = p.FAM;
}

// the set-scoped ops: the open set goes with every call
const dpost = (name, b = {}) => post(name, {set: SET, ...b});
const dget = (name, b = {}) => get(name, {set: SET, ...b});

function notice(msg) { $("#notice span").textContent = msg; $("#notice").classList.remove("hidden"); }

initMenu("dungeon");

async function load() {
  try { D = await dget("dungeon.state"); }
  catch (e) {  // say so instead of quietly switching sets
    const sets = await get("dungeon.sets").catch(() => []), next = sets.find(s => s !== SET);
    if (!next) { notice(`Couldn't open set "${SET}": ${e.message}`); throw e; }
    notice(`Couldn't open set "${SET}" (${e.message}). Showing "${next}" instead.`);
    SET = next; localStorage.setItem("dun.set", SET); return load();
  }
  $("#setSel").innerHTML = D.sets.map(s => `<option ${s === SET ? "selected" : ""}>${s}</option>`).join("");
  syncPixel();
  renderSetPanel();
  renderSheetPanel();
  renderRecipes();
  if (!LIB.length) renderLibrary();
  // Start on a floor family (or the walls board) instead of an empty editor; keep the view when it still exists.
  const fams = Object.keys(D.families || {});
  if (VIEW === "family" && !famInfo()) { FAM = fams[0] || null; if (!FAM) { VIEW = "group"; GROUP = "Walls"; } }
  if (VIEW === "pieces" && !SEL) { if (fams.length) { VIEW = "family"; FAM = FAM && famInfo() ? FAM : fams[0]; } else { VIEW = "group"; GROUP = "Walls"; } }
  applyChrome();
  if (SEL && !piece(SEL)) SEL = null;
  renderList(); renderCenter(); refreshRoom(); loadCaps();
}
const piece = id => D.spec.pieces.find(p => p.id === id);
const pstate = id => D.state.pieces[id] || {candidates: [], approved: []};
const natUrl = (pid, rel, tile, scale) => url("dungeon.native_image", {set: SET, piece: pid, rel, tile, scale});
const candUrl = (rel) => url("dungeon.candidate_image", {set: SET, rel});
const seamUrl = (pid, rel) => url("dungeon.seam_image", {set: SET, piece: pid, rel});

// Dungeon Forge areas: floor families, walls (walls, doors, pillars, arches, windows), props (later: their own area).
function group(p) {
  if (p.retired) return "Retired";
  if (["master", "repaint", "wall_master", "wall_repaint", "door_repaint"].includes(p.method) || p.built_from || p.kind === "elevation") return "Family";
  if (p.kind === "prop" || p.kind === "wall_decor" || p.id.startsWith("feature.") && p.kind !== "tile") return "Props";
  if (p.shape === "floor") return "Floors";
  return "Walls";
}
function running(pid) { return JOBS.some(j => j.status !== "done" && j.status !== "failed" && j.label.startsWith(`${SET} · ${pid} `)); }

function renderList() {
  const by = {}; D.spec.pieces.forEach(p => (by[group(p)] ||= []).push(p));
  const count = g => (by[g] || []).length, done = g => (by[g] || []).filter(p => pstate(p.id).approved.length >= (p.variants || 1)).length;
  const link = (g, label, hint) => `<div class="navlink ${VIEW === "group" && GROUP === g ? "sel" : ""}" data-g="${esc(g)}" title="${esc(hint)}"><span>${label}</span><span class="badge ${done(g) === count(g) ? "ok" : "part"}">${done(g)}/${count(g)}</span></div>`;
  $("#plist").innerHTML = renderFamiliesList() +
    `<div class="navgrp">Older walls</div>` + link("Walls", "Single wall pieces ▸", "Wall parts made before wall families (pillars and arches live here for now)") +
    (count("Floors") ? `<div class="navgrp">Older floors</div>` + link("Floors", "Single floor pieces ▸", "Floor pieces made before families (warp tiles, atlases)") : "") +
    `<div class="navnote">Props and wall decor are moving to their own part of Dungeon Forge (wall decor will use the decal system). Until then they're on the Style &amp; set tab's add-piece list and keep exporting.</div>` +
    renderOrphans();
  document.querySelectorAll(".plist .navlink").forEach(el => el.onclick = () => { GROUP = el.dataset.g; setView("group"); });
  document.querySelectorAll(".plist .fitem").forEach(el => el.onclick = () => { FAM = el.dataset.fam; setView("family"); });
  document.querySelectorAll(".plist .newFam").forEach(b => b.onclick = () => newFamily(b.dataset.k));
  document.querySelectorAll(".orphan button").forEach(b => b.onclick = async () => {
    const pid = b.closest(".orphan").dataset.id;
    try {
      if (b.dataset.a === "restore") { await dpost("dungeon.restore_piece", {piece: pid}); SEL = pid; }
      else if (confirm(`Archive the ${D.orphans[pid].count} images of ${pid}? They move to archive/dungeon/${SET}/.`)) await dpost("dungeon.archive_orphan", {piece: pid});
      await load(); if (SEL === pid) setView("pieces");
    } catch (e) { notice(e.message); }
  });
}

// Pieces with generated images that are no longer in the set (removed, or lost from the spec).
function renderOrphans() {
  const o = Object.entries(D.orphans || {});
  if (!o.length) return "";
  return `<div class="grp" title="These pieces have generated images but aren't in the set any more">Not in set (images kept)</div>` +
    o.map(([id, v]) => `<div class="orphan" data-id="${esc(id)}" title="${esc(v.prompt)}"><span class="nm">${esc(id)}</span>
      <span class="badge part">${v.count} img</span><button data-a="restore">Restore</button><button data-a="archive" class="danger">Archive</button></div>`).join("");
}


// ---- set & style panel (left column): set name, grid and the shared descriptors, saved in place.
function renderSetPanel() {
  $("#sp_name").value = D.spec.name || SET; $("#sp_tile").value = D.spec.tile;
  renderStyleBox($("#spStyle"), ["medium", "style", "view", "rendering", "framing", "biome"], () => { if (VIEW === "pieces") syncFormVis(); });
  $("#sp_save").onclick = async () => {
    const fields = {};
    if ($("#sp_name").value !== (D.spec.name || SET)) fields.name = $("#sp_name").value;
    if (+$("#sp_tile").value !== +D.spec.tile) fields.tile = $("#sp_tile").value;
    try { if (Object.keys(fields).length) { D.spec = (await dpost("dungeon.update_set", {fields})).spec; await load(); } } catch (e) { notice(e.message); }
  };
}

// ---- set material sheet: what every family of the set is made of (palette, stone module, joints, words).
// Linked family templates follow it; wall masters are colour-matched to the floor master.
function renderSheetPanel() {
  const sh = D.sheet, el = $("#sheetBody"); if (!sh || !el) return;
  const floors = Object.keys(D.families || {}).filter(id => D.families[id].kind === "floor");
  const short = id => id.replace(/^tile\./, "").replace(/_master$/, "");
  const H = 3 * KZ;
  el.innerHTML = `
    <div class="hint">Decide once what this set is made of. Families linked to the sheet take its palette, stone size and joints into their layout templates; its words go into every texture prompt; wall masters are colour-matched to the floor master.</div>
    <div class="row"><span class="hint">stones</span>${sh.palette.stones.map((c, i) => `<input type="color" class="shStone" data-i="${i}" value="${c}" style="width:28px">`).join("")}
      <button id="shAddStone" title="another tone">+</button>${sh.palette.stones.length > 1 ? `<button id="shDelStone">−</button>` : ""}
      <span class="hint">joint</span><input type="color" id="shMortar" value="${sh.palette.mortar}" style="width:28px"></div>
    <div class="row"><label title="A floor stone's size in tiles">floor stone<input id="sh_floor_stone" type="number" step="0.01" min="0.05" max="1" value="${sh.floor_stone}"></label><span class="hint" id="shFs"></span></div>
    <div class="row"><label title="A wall course's height in units (walls are 3)">wall course<input id="sh_wall_course" type="number" step="0.01" min="0.04" max="1" value="${sh.wall_course}"></label><span class="hint" id="shWc"></span></div>
    <div class="row"><label title="A wall brick's length in tiles">wall brick<input id="sh_wall_brick" type="number" step="0.01" min="0.05" max="1" value="${sh.wall_brick}"></label><span class="hint" id="shWb"></span></div>
    <div class="row"><label title="Mortar / grout width in tiles">joint<input id="sh_joint" type="number" step="0.002" min="0" max="0.06" value="${sh.joint}"></label>
      <label title="Edge rounding in tiles">bevel<input id="sh_bevel" type="number" step="0.002" min="0" max="0.08" value="${sh.bevel}"></label></div>
    <label>material words<textarea id="sh_words" rows="2" placeholder="e.g. weathered grey granite, dark mortar">${esc(sh.words || "")}</textarea></label>
    <div class="row"><label title="Pull new wall masters toward the floor master's colours (0 = off)">wall colour match<input id="sh_match" type="number" step="0.1" min="0" max="1" value="${sh.match}"></label>
      <label title="Lightness added to walls (CIELAB L; walls usually read a little lighter than floors)">wall lighter<input id="sh_wall_light" type="number" step="1" min="-30" max="30" value="${sh.wall_light}"></label></div>
    <div class="row"><label>floor to match<select id="sh_floor"><option value="">first with a master</option>${floors.map(id => `<option value="${esc(id)}" ${sh.floor === id ? "selected" : ""}>${esc(short(id))}</option>`).join("")}</select></label></div>
    <div class="row"><button id="shFromFloor" title="Palette from the chosen floor family's active master, stone size from its template (and link it)">Fill from floor master</button>
      <button id="shSave" class="primary">Save sheet</button></div>
    <div class="hint" id="shMsg"></div>`;
  const readouts = () => {
    $("#shFs").textContent = `≈ ${(1 / +$("#sh_floor_stone").value).toFixed(1)} per tile`;
    $("#shWc").textContent = `≈ ${(3 / +$("#sh_wall_course").value).toFixed(0)} rows per wall`;
    $("#shWb").textContent = `≈ ${(1 / +$("#sh_wall_brick").value).toFixed(1)} per tile`;
  };
  readouts(); el.querySelectorAll("input[type=number]").forEach(i => i.oninput = readouts);
  const collect = () => ({
    palette: {...sh.palette, stones: [...el.querySelectorAll(".shStone")].map(i => i.value), mortar: $("#shMortar").value},
    floor_stone: +$("#sh_floor_stone").value, wall_course: +$("#sh_wall_course").value, wall_brick: +$("#sh_wall_brick").value,
    joint: +$("#sh_joint").value, bevel: +$("#sh_bevel").value, words: $("#sh_words").value.trim(),
    match: +$("#sh_match").value, wall_light: +$("#sh_wall_light").value, floor: $("#sh_floor").value});
  const done = async (r, m) => { D.spec = r.spec; await load(); $("#sheetPanel").open = true; $("#shMsg").textContent = m; if (VIEW === "family") renderFamily(); };
  $("#shSave").onclick = async () => { try { done(await dpost("dungeon.update_sheet", {fields: collect()}), "Saved: linked families follow it."); } catch (e) { $("#shMsg").textContent = e.message; } };
  $("#shFromFloor").onclick = async () => {
    const fid = $("#sh_floor").value || floors.find(id => pstate(id).approved[0]);
    if (!fid) return $("#shMsg").textContent = "No floor family with an active master yet.";
    try { done(await dpost("dungeon.sheet_from_floor", {piece: fid}), `Filled from ${short(fid)} (now linked).`); } catch (e) { $("#shMsg").textContent = e.message; }
  };
  $("#shAddStone").onclick = () => { D.sheet.palette.stones.push(D.sheet.palette.stones.at(-1)); renderSheetPanel(); $("#sheetPanel").open = true; };
  if ($("#shDelStone")) $("#shDelStone").onclick = () => { D.sheet.palette.stones.pop(); renderSheetPanel(); $("#sheetPanel").open = true; };
}

// ---- recipes: a family's whole setup (or a set's floor + wall families, with the sheet), reusable in any set.
async function renderRecipes() {
  const el = $("#recipeBody"); if (!el) return;
  let R = [];
  try { R = await get("dungeon.recipes"); } catch {}
  const sw = r => r.sheet ? r.sheet.palette.stones.map(c => `<span style="display:inline-block;width:10px;height:10px;background:${c}"></span>`).join("") +
    `<span style="display:inline-block;width:10px;height:10px;background:${r.sheet.palette.mortar}"></span>` : "";
  el.innerHTML = `<div class="hint">Start families from a saved setup (prompts, variations, doors, template, switches; a set recipe also brings the material sheet).</div>
    ${R.map(r => `<div class="row" style="align-items:center" title="${esc(r.notes || "")} (from ${esc(r.from_set)}, ${esc(r.saved)})">
      <span style="flex:1"><b>${esc(r.title)}</b> <span class="hint">${r.families.map(f => f.kind).join(" + ")}</span> ${sw(r)}</span>
      <button class="rcUse" data-id="${esc(r.id)}">Use</button></div>`).join("") || `<div class="hint">No recipes yet.</div>`}
    <div class="row"><button id="rcSaveFam" title="The open family's setup (and the set's material sheet)">Save this family</button>
      <button id="rcSaveSet" title="Every family of this set (floors and walls) and the material sheet, as one kit">Save set kit</button></div>
    <div class="hint" id="rcMsg"></div>`;
  const msg = t => $("#rcMsg").textContent = t;
  el.querySelectorAll(".rcUse").forEach(b => b.onclick = async () => {
    const r = R.find(x => x.id === b.dataset.id);
    let use = true;
    if (r.sheet && D.spec.sheet) use = confirm(`"${r.title}" has its own material sheet. Replace this set's sheet with it? (Cancel keeps this set's sheet.)`) ? "replace" : false;
    try {
      const out = await dpost("dungeon.apply_recipe", {recipe: r.id, use_sheet: use !== false, replace_sheet: use === "replace"});
      FAM = out.pieces[0]; await load(); setView("family"); $("#recipePanel").open = true;
      notice(`Made ${out.pieces.join(", ")} from "${r.title}". Draft masters to start.`);
    } catch (e) { msg(e.message); }
  });
  $("#rcSaveFam").onclick = async () => {
    if (VIEW !== "family" || !famInfo()) return msg("Open a family first.");
    const t = (prompt("Recipe name:", FAM.replace(/^tile\.[^_]+_/, "").replace(/_(wall_)?master$/, "").replace(/_/g, " ")) || "").trim(); if (!t) return;
    try { await dpost("dungeon.save_recipe", {pieces: [FAM], title: t}); msg(`Saved "${t}".`); renderRecipes(); } catch (e) { msg(e.message); }
  };
  $("#rcSaveSet").onclick = async () => {
    const ids = Object.keys(D.families || {}); if (!ids.length) return msg("No families in this set yet.");
    const t = (prompt(`Kit name (${ids.length} families + the material sheet):`, `${D.spec.name || SET} kit`) || "").trim(); if (!t) return;
    try { await dpost("dungeon.save_recipe", {pieces: ids, title: t}); msg(`Saved "${t}".`); renderRecipes(); } catch (e) { msg(e.message); }
  };
}

// Which panels show for the current VIEW (middle column views, and the right column: room or family preview).
function applyChrome() {
  const v = VIEW, fam = v === "family";
  $("#pieceView").classList.toggle("hidden", v !== "pieces");
  $("#groupView").classList.toggle("hidden", v !== "group");
  $("#familyView").classList.toggle("hidden", !fam);
  $("#styleView").classList.toggle("hidden", v !== "style");
  $("#addView").classList.toggle("hidden", v !== "add");
  $("#tabPieces").classList.toggle("on", v !== "style"); $("#tabStyle").classList.toggle("on", v === "style");
  document.body.classList.toggle("famview", fam);
  $("#famPanel").classList.toggle("hidden", !fam); $("#roomPanel").classList.toggle("hidden", fam);
}
function setView(v) {
  VIEW = v; applyChrome();
  renderList(); renderCenter();
}


// ---- shared style: the descriptors every piece's prompt is built from. Editable from the piece editor, the group
// board and the Style tab; unsaved edits live in OVR (prompt previews use them) and only changed fields are saved,
// so an older open page can't overwrite somebody else's edits.
const STYLE_KIND = {medium: "group", style: "group", view: "group", rendering: "group", framing: "group", biome: "biome",
                    prop_medium: "prop", prop_view: "prop", prop_framing: "prop"};
const STYLE_LABEL = {biome: "biome (this set)", prop_medium: "prop medium", prop_view: "prop view", prop_framing: "prop framing"};
let OVR = {};
function savedStyle(f) { return STYLE_KIND[f] === "group" ? D.style.groups[f].text : f === "biome" ? D.set_style.biome.text : D.style[f] || ""; }
const sv = f => f in OVR ? OVR[f] : savedStyle(f);
function fieldsFor(p) {
  if (p.kind === "prop") return ["prop_medium", "style", "rendering", ...(p.biome === false ? [] : ["biome"]), "prop_view", "prop_framing"];
  if (p.shape === "floor") return ["medium", "style", "rendering", "biome"];
  return ["medium", "style", "view", "rendering", "framing", "biome"];
}
function styleHint(f) { return STYLE_KIND[f] === "group" ? (D.style.groups[f].hint || "") : f === "biome" ? "Only this set. The other fields are shared by every set." : ""; }
function renderStyleBox(box, fields, onEdit) {
  const n = fields.filter(f => f in OVR).length;
  box.querySelector("summary").dataset.n = n;
  box.querySelector(".sbody").innerHTML = fields.map(f => `<div class="sf ${f in OVR ? "dirty" : ""}" data-f="${f}"><label title="${esc(styleHint(f))}">${STYLE_LABEL[f] || f}</label>
      <textarea rows="2">${esc(sv(f))}</textarea></div>`).join("") +
    `<div class="row"><button class="primary ssave">Save style</button><button class="sundo">Undo edits</button><span class="hint smsg"></span></div>`;
  box.querySelectorAll(".sf textarea").forEach(t => t.oninput = () => {
    const f = t.closest(".sf").dataset.f;
    if (t.value === savedStyle(f)) delete OVR[f]; else OVR[f] = t.value;
    t.closest(".sf").classList.toggle("dirty", f in OVR);
    box.querySelector("summary").dataset.n = fields.filter(f => f in OVR).length;
    onEdit && onEdit();
  });
  box.querySelector(".sundo").onclick = () => { fields.forEach(f => delete OVR[f]); renderStyleBox(box, fields, onEdit); onEdit && onEdit(); };
  box.querySelector(".ssave").onclick = async () => {
    try { const n = await saveStyle(fields); box.querySelector(".smsg").textContent = n ? `Saved ${n} field${n > 1 ? "s" : ""}.` : "Nothing changed."; renderStyleBox(box, fields, onEdit); }
    catch (e) { box.querySelector(".smsg").textContent = e.message; }
  };
}
async function saveStyle(fields) {
  const ch = fields.filter(f => f in OVR && OVR[f] !== savedStyle(f));
  if (!ch.length) { fields.forEach(f => delete OVR[f]); return 0; }
  const groups = {}, body = {};
  ch.forEach(f => { if (STYLE_KIND[f] === "group") groups[f] = OVR[f]; else if (STYLE_KIND[f] === "prop") body[f] = OVR[f]; });
  if (Object.keys(groups).length || Object.keys(body).length) D.style = (await post("dungeon.save_style", {groups, ...body})).style;
  if (ch.includes("biome")) D.spec = (await dpost("dungeon.update_set", {fields: {biome: OVR.biome}})).spec;
  fields.forEach(f => delete OVR[f]);
  await load();
  return ch.length;
}

function styleText(groups) { return groups.map(sv).filter(t => t.trim()).join(", "); }
function fullPrompt(p, text) {
  const all = ["medium", "style", "view", "rendering", "framing", "biome"], core = ["medium", "style", "rendering", "biome"];
  if (p.kind === "prop") return `${sv("prop_medium") || sv("medium")}, ${styleText(p.biome === false ? ["style", "rendering"] : ["style", "rendering", "biome"])}, ${p.view || sv("prop_view")}, ${text}, ${sv("prop_framing")}`;
  if (["atlas", "master", "repaint"].includes(p.method))
    return `${styleText(core)}, seamless tileable floor texture seen straight from above, ${text}, the pattern continues past every edge of the image, no border, no frame, no walls, flat even lighting`;
  if (p.shape === "floor" && (p.method || "warp") === "warp")
    return `${styleText(core)}, top-down orthographic view of a single square dungeon floor tile, ${text}, the tile fills the entire image edge to edge, flat even lighting`;
  return `${styleText(all)}, ${text}`;
}

function formPiece() {
  const num = id => $(id).value === "" ? undefined : Number($(id).value);
  const kind = $("#f_kind").value, p = {id: $("#f_id").value.trim(), kind, prompt: $("#f_prompt").value.trim()};
  const old0 = piece(SEL) || {};
  if (kind === "tile") {
    p.shape = $("#f_shape").value; p.height = num("#f_height");
    if (["run", "mesh"].includes(p.shape)) p.length = +$("#f_length").value;
    if (p.shape === "run" && $("#f_opening").value) p.opening = $("#f_opening").value;
    if (p.shape === "mesh") p.mesh = $("#f_mesh").value;
    if (["run", "post", "mesh"].includes(p.shape)) p.thickness = old0.thickness ?? 0.25;
    for (const k of ["door_height", "door_width", "opening_width", "spring"]) if (old0[k] != null) p[k] = old0[k];
  }
  else { p.footprint = num("#f_footprint"); if ($("#f_view").value.trim()) p.view = $("#f_view").value.trim(); if ($("#f_biome").value === "0") p.biome = false; }
  p.variants = num("#f_variants") || 1;
  if (kind === "wall_decor") p.of = $("#f_of").value;
  if (kind === "tile" && p.shape === "floor" && $("#f_method").value !== "warp") p.method = $("#f_method").value;
  if (p.method === "atlas") p.atlas = +$("#f_atlas").value;
  if (p.method === "repaint") {
    p.atlas = +$("#f_atlas").value; p.from = $("#f_from").value;
    if ($("#f_border").value !== "") p.border = +$("#f_border").value;
  }
  if ($("#f_board").value.trim()) p.board = $("#f_board").value.trim();
  if ($("#f_tint").value.trim()) p.tint = $("#f_tint").value.split(",").map(Number);
  const old = piece(SEL); if (old && old.denoise && kind !== "prop") p.denoise = old.denoise;
  return p;
}

function syncFormVis() {
  const kind = $("#f_kind").value, shape = kind === "wall_decor" ? "wall" : $("#f_shape").value;
  document.querySelectorAll(".t-only").forEach(e => e.classList.toggle("hidden", kind === "prop"));
  document.querySelectorAll(".p-only").forEach(e => e.classList.toggle("hidden", kind !== "prop"));
  document.querySelectorAll(".d-only").forEach(e => e.classList.toggle("hidden", kind !== "wall_decor"));
  document.querySelectorAll(".f-only").forEach(e => e.classList.toggle("hidden", !(kind === "tile" && shape === "floor")));
  const fm = kind === "tile" && shape === "floor" ? $("#f_method").value : "";
  document.querySelectorAll(".a-only").forEach(e => e.classList.toggle("hidden", !["atlas", "repaint"].includes(fm)));
  document.querySelectorAll(".rp-only").forEach(e => e.classList.toggle("hidden", fm !== "repaint"));
  document.querySelectorAll(".r-only").forEach(e => e.classList.toggle("hidden", !(kind === "tile" && (shape === "run" || shape === "mesh"))));
  document.querySelectorAll(".m-only").forEach(e => e.classList.toggle("hidden", !(kind === "tile" && shape === "mesh")));
  if (kind === "wall_decor") document.querySelectorAll(".t-only").forEach(e => e.classList.add("hidden"));
  const warp = kind === "tile" && shape === "floor" && $("#f_method").value === "warp";
  document.querySelectorAll(".nf-only").forEach(e => e.classList.toggle("hidden", kind === "prop" || warp));
  $("#f_shape").disabled = kind === "wall_decor";
  $("#f_opening").parentElement.classList.toggle("hidden", !(kind === "tile" && shape === "run"));
  const p = formPiece();
  $("#fullPrompt").textContent = fullPrompt(p, p.prompt);
  const base = kind === "wall_decor" && p.of && pstate(p.of).approved.length;
  const masterOk = fm === "repaint" && p.from && pstate(p.from).approved.length;
  $("#genHint").textContent = fm === "master" ? "Master: one seamless top-down tile (lighting flattened, edges made periodic, seam repainted). Approve one: every 'repaint of a master' piece starts from it and keeps its edges."
    : fm === "repaint" ? (masterOk ? `Repaint: the approved ${p.from} tiled ${p.atlas}×${p.atlas}, everything inside the kept border redrawn from this prompt. Edges = the master's, so it fits next to any piece of the family. denoise: 0.85 default, ~0.6 subtle, 1.0 freer.`
                                   : `Approve a candidate of the master (${p.from || "pick one"}) first: the repaint starts from it.`)
    : kind === "prop" ? "Prop: clean prompt on white → background removed → cropped and scaled to the footprint (tiles wide) → downsampled."
    : warp ? "Floor: a top-down square tile is generated, then warped exactly onto the 2:1 diamond."
    : kind === "wall_decor" ? (base ? `Painted over the approved ${p.of} (img2img, denoise ~0.6) so the stone matches.` : `No approved ${p.of} yet: approve the base wall first for matching stone (falls back to the geometry template).`)
    : "Tile: FLUX paints over an exact iso geometry template (img2img) and the result is clamped to its outline.";
}

function renderCenter() {
  if (VIEW === "style") return renderStyle();
  if (VIEW === "group") return renderGroup();
  if (VIEW === "family") return renderFamily();
  if (VIEW === "add") return renderCatalog();
  const p = piece(SEL);
  $("#noPiece").classList.toggle("hidden", !!p); $("#pieceEd").classList.toggle("hidden", !p);
  if (!p) return;
  const s = pstate(p.id);
  $("#pTitle").textContent = p.id;
  $("#pBadge").textContent = `${s.approved.length}/${p.variants || 1} approved`;
  $("#f_id").value = p.id; $("#f_kind").value = p.kind; $("#f_shape").value = p.shape || "floor";
  $("#f_length").value = p.length || 1; $("#f_opening").value = p.opening || ""; $("#f_mesh").value = p.mesh || "archway";
  $("#f_atlas").value = p.atlas || (p.method === "repaint" ? 1 : 4);
  $("#f_from").innerHTML = D.spec.pieces.filter(q => q.method === "master").map(q => `<option ${q.id === p.from ? "selected" : ""}>${esc(q.id)}</option>`).join("") || `<option value="">(no master piece yet)</option>`;
  $("#f_border").value = p.border ?? "";
  $("#f_height").value = p.height ?? ""; $("#f_footprint").value = p.footprint ?? ""; $("#f_variants").value = p.variants || 1;
  $("#f_of").innerHTML = D.spec.pieces.filter(q => q.kind === "tile" && (q.shape === "run" && !q.opening && (q.length || 1) === 1 || q.shape === "wall") && !q.retired).map(q => `<option ${q.id === p.of ? "selected" : ""}>${esc(q.id)}</option>`).join("");
  $("#f_method").value = p.method || "warp"; $("#f_board").value = p.board || ""; $("#f_tint").value = (p.tint || []).join(",");
  $("#f_prompt").value = p.prompt || ""; $("#f_view").value = p.view || ""; $("#f_biome").value = p.biome === false ? "0" : "1"; $("#pErr").textContent = "";
  syncFormVis();
  renderStyleBox($("#pieceStyle"), fieldsFor(p), syncFormVis);
  renderCands();
}

function renderCands() {
  const p = piece(SEL), s = pstate(p.id), meta = s.meta || {};
  $("#candHint").textContent = s.candidates.length ? `· click Approve on up to ${p.variants || 1} (the order = variant order) · Try = show in the room preview` : "· none yet, hit Generate";
  const nScale = D.spec.pixel ? viewScale() : 1, outLbl = D.spec.pixel ? `${OUT()} px tiles, shown ×${nScale}` : "full resolution";
  $("#cands").innerHTML = s.candidates.map(rel => {
    const on = s.approved.includes(rel), m = meta[rel] || {}, ai = s.approved.indexOf(rel);
    return `<div class="cand ${on ? "on" : ""} ${TRY === rel ? "trying" : ""}" data-rel="${esc(rel)}">
      <div class="views">
        <img class="raw" src="${candUrl(rel)}" title="generated (8×)">
        <div><img class="nat" src="${natUrl(p.id, rel, OUT(), nScale)}" title="as exported: ${outLbl}"></div>
        ${["master", "repaint"].includes(p.method) ? `<img class="seam" src="${seamUrl(p.id, rel)}" title="seam check: ${p.method === "master" ? "tiled 3×3" : "in a ring of master tiles"} (click to zoom)">` : ""}
      </div>
      <div class="meta">seed ${m.seed ?? "?"}${m.denoise ? " · dn " + m.denoise : ""}${m.lora != null ? " · lora " + m.lora : ""}${m.on_base ? " · on base" : ""}${on ? ` · <b style="color:var(--ok)">variant ${ai}</b>` : ""}${m.prompt && m.prompt !== p.prompt ? `<br><i title="${esc(m.prompt)}">older prompt</i>` : ""}</div>
      <div class="btns"><button data-a="approve" class="${on ? "on" : ""}">${on ? "✓ Approved" : "Approve"}</button><button data-a="try">${TRY === rel ? "Trying" : "Try"}</button><button data-a="seed" title="reuse this seed">Seed</button><button data-a="discard" class="danger" title="Archive this image (moves to archive/)">✕</button></div>
    </div>`;
  }).join("");
  document.querySelectorAll(".cand").forEach(el => {
    const rel = el.dataset.rel;
    el.querySelector(".raw").onclick = () => zoom(el.querySelector(".raw").src);
    const seam = el.querySelector(".seam"); if (seam) seam.onclick = () => zoom(seam.src);
    el.querySelectorAll("button").forEach(b => b.onclick = async () => {
      const a = b.dataset.a;
      try {
        if (a === "approve") { const on = !pstate(SEL).approved.includes(rel); D.state.pieces[SEL] = (await dpost("dungeon.approve", {piece: SEL, rel, on})).piece; TRY = null; renderList(); renderCands(); refreshRoom(); }
        if (a === "try") { TRY = TRY === rel ? null : rel; renderCands(); refreshRoom(); }
        if (a === "seed") { $("#g_seed").value = (meta[rel] || {}).seed ?? 1; $("#g_n").value = 1; }
        if (a === "discard") { D.state.pieces[SEL] = (await dpost("dungeon.discard", {piece: SEL, rel})).piece; if (TRY === rel) TRY = null; renderList(); renderCands(); refreshRoom(); }
      } catch (e) { $("#pErr").textContent = e.message; }
    });
  });
}

// Pixelate slider: 0 = off (full resolution), then the set's pixel_steps from fine to chunky.
const pixelStops = () => [0, ...D.pixel_steps.slice().reverse()];
function syncPixel() {
  const stops = pixelStops(), px = D.spec.pixel || 0;
  $("#r_pixel").max = stops.length - 1; $("#r_pixel").value = Math.max(0, stops.indexOf(px));
  pixelLabel(px);
  document.body.classList.toggle("hires", !px);
  $("#r_harm").value = D.spec.harmonize || 0; harmLabel(D.spec.harmonize || 0);
}
function harmLabel(k) { $("#r_harmLbl").textContent = +k ? `${Math.round(k * 100)}%` : "off"; }
function pixelLabel(px) { $("#r_pixelLbl").textContent = px ? `${px} px tiles` : `off (${D.spec.tile * 8} px)`; }
// Previews: pixel-art sizes drawn enlarged with crisp pixels; full resolution capped so the room stays small.
const OUT = () => D.output_tile;
const viewScale = () => Math.max(1, Math.round(128 / OUT()));
function refreshRoom() {
  const t = D.spec.pixel ? OUT() : Math.min(OUT(), 128);
  const args = {set: SET, tile: t, scale: D.spec.pixel ? viewScale() : 1, ...(TRY && SEL ? {piece: SEL, rel: TRY} : {})};
  $("#roomHint").textContent = TRY ? `Trying a candidate of ${SEL} in the room (not approved yet).` : "Approved pieces in a mock room (back walls only, props in set order).";
  $("#room").src = url("dungeon.room_image", args, Date.now());
}

async function loadCaps() {
  const caps = await get("dungeon.captures").catch(() => []);
  const want = ["v05_close_post_revealed.png", "v04_close_post.png", "v02_overview_post.png", "v08_pixelperfect_post.png"];
  $("#caps").innerHTML = want.filter(f => caps.includes(f)).map(f => `<figure><img src="${url("dungeon.capture_image", {f}, capStamp)}"><figcaption>${f.replace(".png", "")}</figcaption></figure>`).join("") || `<div class="hint">No captures yet.</div>`;
  document.querySelectorAll("#caps img").forEach(i => i.onclick = () => zoom(i.src));
}
function zoom(src) { $("#zoom img").src = src; $("#zoom").classList.add("on"); }
$("#zoom").onclick = () => $("#zoom").classList.remove("on");
$("#room").onclick = () => zoom($("#room").src);

function renderStyle() {
  $("#s_name").value = D.spec.name || SET; $("#s_tile").value = D.spec.tile; $("#s_biome").value = D.spec.biome || D.style.groups.biome.text;
  const ideas = D.style.descriptor_ideas || {};
  $("#ideas").innerHTML = Object.entries(ideas).map(([k, v]) => `<div class="chips"><span class="hint" style="width:44px">${k}</span>${v.map(t => `<span class="chip" data-t="${esc(t)}">${esc(t)}</span>`).join("")}</div>`).join("");
  document.querySelectorAll("#ideas .chip").forEach(c => c.onclick = () => { const b = $("#s_biome"); b.value = b.value.trim() ? b.value.trim() + ", " + c.dataset.t : c.dataset.t; });
  $("#groups").innerHTML = Object.entries(D.style.groups).filter(([g]) => g !== "biome").map(([g, v]) =>
    `<label class="hint">${g}</label><textarea rows="2" data-g="${g}">${esc(v.text)}</textarea><div class="hint">${esc(v.hint || "")}</div>`).join("");
  $("#s_pmedium").value = D.style.prop_medium || ""; $("#s_pview").value = D.style.prop_view; $("#s_pframe").value = D.style.prop_framing; $("#sErr").textContent = "";
}

function renderCatalog() {
  const have = new Set(D.spec.pieces.map(p => p.id)), groups = {};
  (D.style.catalog || []).forEach(c => (groups[c.group] ||= []).push(c));
  $("#catalog").innerHTML = Object.entries(groups).map(([g, items]) => `<div class="catgrp">${g}</div><div class="chips">` + items.map((c, i) => {
    const id = c.id.replaceAll("{set}", SET);
    return `<span class="chip ${have.has(id) ? "have" : ""}" data-g="${esc(g)}" data-i="${i}" title="${esc(c.prompt)}">${esc(id.replace(/^(prop|tile|feature)\./, ""))}</span>`;
  }).join("") + `</div>`).join("");
  document.querySelectorAll("#catalog .chip:not(.have)").forEach(el => el.onclick = async () => {
    const c = {...groups[el.dataset.g][+el.dataset.i]}; delete c.group;
    c.id = c.id.replaceAll("{set}", SET); if (c.of) c.of = c.of.replaceAll("{set}", SET);
    if (c.of && !piece(c.of)) c.of = (D.spec.pieces.find(q => q.kind === "tile" && (q.shape === "run" && !q.opening && (q.length || 1) === 1 || q.shape === "wall") && !q.retired) || {}).id;
    await addPiece(c);
  });
}

async function addPiece(p) {
  let id = p.id, k = 2; while (piece(id)) id = `${p.id}_${k++}`; p.id = id;
  const r = await dpost("dungeon.save_piece", {piece: p}); D.spec = r.spec; SEL = id; setView("pieces");
}
document.querySelectorAll("[data-blank]").forEach(b => b.onclick = () => {
  // A family shares the main floor's board id (what the game asks for): the master's, else the plain floor's.
  const mp = D.spec.pieces.find(q => q.method === "master"), master = mp && mp.id;
  const floorBoard = mp && mp.board || (D.spec.pieces.find(q => q.shape === "floor" && (q.board || q.id).endsWith("_floor")) || {}).board || `tile.${SET}_floor`;
  const wall = (D.spec.pieces.find(q => q.kind === "tile" && (q.shape === "run" && !q.opening && (q.length || 1) === 1 || q.shape === "wall") && !q.retired) || {}).id;
  const t = {prop: {id: "prop.new", kind: "prop", footprint: 0.5, prompt: "an old wooden ..."},
             floor: {id: `tile.${SET}_floor_new`, kind: "tile", shape: "floor", variants: 2, prompt: "flagstones ..."},
             wall: {id: `tile.${SET}_wall_run_new`, board: `tile.${SET}_wall`, kind: "tile", shape: "run", length: 3, height: WALL_UNITS, thickness: 0.25, variants: 2, prompt: "a thin wall of ..."},
             door: {id: `tile.${SET}_door_new`, board: `tile.${SET}_door`, kind: "tile", shape: "run", opening: "door", length: 1, height: WALL_UNITS, door_height: 2, thickness: 0.25, prompt: "a thin stone wall with ... door set into it"},
             arch: {id: `tile.${SET}_arch_new`, board: `tile.${SET}_arch`, kind: "tile", shape: "mesh", mesh: "archway", length: 1, height: WALL_UNITS, thickness: 0.25, prompt: "a thin wall with an open stone archway ..."},
             area: {id: `tile.${SET}_floor_area_new`, board: `tile.${SET}_floor`, kind: "tile", shape: "floor", method: "atlas", atlas: 4, variants: 2, prompt: "flagstones ..."},
             decor: {id: `tile.${SET}_wall_decor`, kind: "wall_decor", shape: "wall", height: WALL_UNITS, of: wall, prompt: "the same stone wall block with ... on the front faces"},
             master: {id: `tile.${SET}_floor_master`, board: floorBoard, kind: "tile", shape: "floor", method: "master", variants: 1, prompt: "cracked grey flagstones with dark mortar lines"},
             variant: {id: `tile.${SET}_floor_var`, board: floorBoard, kind: "tile", shape: "floor", method: "repaint", from: master, atlas: 1, variants: 2, prompt: "cracked grey flagstones, one with a large crack and moss in the joints"},
             pillar: {id: `tile.${SET}_pillar_new`, kind: "tile", shape: "pillar", height: WALL_UNITS, prompt: "a carved stone pillar ..."}}[b.dataset.blank];
  addPiece(t);
});

// ---- actions
["#f_kind", "#f_shape", "#f_method", "#f_of", "#f_prompt", "#f_view", "#f_biome", "#f_length", "#f_opening", "#f_atlas", "#f_from", "#f_border"].forEach(s => $(s).addEventListener("input", syncFormVis));
$("#tabPieces").onclick = () => setView("pieces");
$("#tabStyle").onclick = () => setView("style");
$("#addPieceBtn").onclick = () => setView("add");
$("#setSel").onchange = () => { SET = $("#setSel").value; localStorage.setItem("dun.set", SET); SEL = null; TRY = null; load(); };
$("#r_pixel").oninput = () => pixelLabel(pixelStops()[+$("#r_pixel").value]);
$("#r_pixel").onchange = async () => {
  try {
    const r = await dpost("dungeon.update_set", {fields: {pixel: pixelStops()[+$("#r_pixel").value]}});
    D.spec = r.spec; await load();
  } catch (e) { notice(e.message); syncPixel(); }
};
$("#roomBtn").onclick = refreshRoom;
$("#r_harm").oninput = () => harmLabel($("#r_harm").value);
$("#r_harm").onchange = async () => {
  try { D.spec = (await dpost("dungeon.update_set", {fields: {harmonize: +$("#r_harm").value}})).spec; await load(); }
  catch (e) { notice(e.message); syncPixel(); }
};

async function savePiece() {
  const p = formPiece();
  const r = await dpost("dungeon.save_piece", {piece: p, old_id: SEL});
  D.spec = r.spec;
  if (p.id !== SEL) { D.state.pieces[p.id] = D.state.pieces[SEL]; delete D.state.pieces[SEL]; SEL = p.id; }
  renderList();
}
$("#saveBtn").onclick = async () => { try { await savePiece(); $("#pErr").textContent = ""; renderCenter(); } catch (e) { $("#pErr").textContent = e.message; } };
$("#genBtn").onclick = async () => {
  try {
    await savePiece();
    await dpost("dungeon.generate", {piece: SEL, n: +$("#g_n").value || 1, seed: +$("#g_seed").value || 1,
                            denoise: $("#g_denoise").value, lora: $("#g_lora").value});
    $("#g_seed").value = (+$("#g_seed").value || 1) + (+$("#g_n").value || 1);  // next press = fresh seeds
    $("#pErr").textContent = ""; pollJobs(); renderList();
  } catch (e) { $("#pErr").textContent = e.message; }
};
$("#delBtn").onclick = async () => {
  const n = new Set([...pstate(SEL).candidates, ...pstate(SEL).approved]).size;
  if (!confirm(`Remove ${SEL} from the set?` + (n ? ` Its ${n} generated images move to archive/dungeon/${SET}/.` : ""))) return;
  try { await dpost("dungeon.delete_piece", {piece: SEL}); SEL = null; await load(); } catch (e) { $("#pErr").textContent = e.message; }
};
$("#archUnapprBtn").onclick = async () => {
  const s = pstate(SEL), n = s.candidates.filter(r => !s.approved.includes(r)).length;
  if (!n) { $("#pErr").textContent = "Nothing to archive: every candidate is approved."; return; }
  if (!confirm(`Archive the ${n} unapproved candidates of ${SEL}? They move to archive/dungeon/${SET}/.`)) return;
  try { D.state.pieces[SEL] = (await dpost("dungeon.archive_unapproved", {piece: SEL})).piece; TRY = null; renderList(); renderCands(); refreshRoom(); }
  catch (e) { $("#pErr").textContent = e.message; }
};
$("#archiveSetBtn").onclick = async () => {
  if (!confirm(`Archive the whole set "${SET}"? Its spec, generated images and approvals move to archive/sets/. ` +
               `Anything already exported to Unity stays there.`)) return;
  try {
    const r = await dpost("dungeon.archive_set", {});
    notice(`Archived "${SET}" → ${r.path}`);
    SET = r.sets[0]; localStorage.setItem("dun.set", SET); SEL = null; TRY = null; await load();
  } catch (e) { notice(e.message); }
};
// Style tab saves: only fields that differ from what the server has (an older open page can't clobber newer edits).
$("#saveSetBtn").onclick = async () => {
  const fields = {}, now = {name: D.spec.name || SET, tile: String(D.spec.tile), biome: D.spec.biome || D.style.groups.biome.text};
  for (const [k, el] of [["name", "#s_name"], ["tile", "#s_tile"], ["biome", "#s_biome"]]) if ($(el).value !== now[k]) fields[k] = $(el).value;
  try { if (Object.keys(fields).length) D.spec = (await dpost("dungeon.update_set", {fields})).spec; delete OVR.biome; await load(); setView("style"); }
  catch (e) { $("#sErr").textContent = e.message; }
};
$("#saveStyleBtn").onclick = async () => {
  const groups = {}, body = {};
  document.querySelectorAll("#groups textarea").forEach(t => { if (t.value !== D.style.groups[t.dataset.g].text) groups[t.dataset.g] = t.value; });
  for (const [k, el] of [["prop_medium", "#s_pmedium"], ["prop_view", "#s_pview"], ["prop_framing", "#s_pframe"]]) if ($(el).value !== (D.style[k] || "")) body[k] = $(el).value;
  try {
    if (Object.keys(groups).length || Object.keys(body).length) await post("dungeon.save_style", {groups, ...body});
    Object.keys(groups).concat(Object.keys(body)).forEach(f => delete OVR[f]);
    await load(); setView("style");
  } catch (e) { $("#sErr").textContent = e.message; }
};
$("#newSetBtn").onclick = async () => {
  const id = prompt("New set id (e.g. sewer, forge, ice_cave). Pieces are copied from the current set as a starting list:");
  if (!id) return;
  const biome = prompt("Biome descriptors for it:", "flooded sewer, slimy green brick, murky water, rusted pipes, dark fantasy") || "";
  try { await post("dungeon.new_set", {id, biome, copy_from: SET}); SET = id; localStorage.setItem("dun.set", SET); SEL = null; await load(); setView("style"); }
  catch (e) { alert(e.message); }
};
$("#exportBtn").onclick = async () => {
  try { const r = await dpost("dungeon.export", {}); $("#exportHint").textContent = "Exported → " + r.path; } catch (e) { $("#exportHint").textContent = e.message; }
};
$("#captureBtn").onclick = async () => { await dpost("dungeon.capture", {}); $("#exportHint").textContent = "Capture queued: export + Unity batch run. Screens appear below when it's done."; pollJobs(); };

// ---- jobs
// polled every 1.5 s once the set has loaded (startup below); a finished job reloads what it changed
const panel = jobsPanel({list: "jobs", clear: "clearJobs", every: 0, busy: "first", errors: "inline",
                         empty: `<div class="hint">Nothing queued.</div>`, onPoll: afterPoll});
function pollJobs() { return panel.poll(); }
async function afterPoll(jobs) {
  JOBS = jobs;
  let reload = false, caps = false;
  for (const j of JOBS) {
    if ((j.status === "done" || j.status === "failed") && !seenDone.has(j.id)) {
      seenDone.add(j.id);
      if (["dun", "dun_trial", "dun_edit", "dun_rescale", "dun_build", "dun_finalize"].includes(j.kind)) reload = true;
      if (j.kind === "lib_import") { renderLibrary(); if (j.error) notice(j.error); }
      if (j.kind === "dun_capture") { caps = true; if (j.error) $("#exportHint").textContent = j.error; }
    }
  }
  if (reload && D) { const sel = SEL; D = await dget("dungeon.state"); SEL = sel; renderList(); if (VIEW === "pieces") renderCands(); if (VIEW === "group") groupPieces().forEach(p => renderGroupRow(p.id)); if (VIEW === "family") renderFamilyDynamic(); }
  if (caps) { capStamp = Date.now(); loadCaps(); }
  if (!reload && D) renderList();
  pvStatus();
}
(async () => {
  try { JOBS = (await get("jobs.list")).jobs; JOBS.forEach(j => { if (j.status === "done" || j.status === "failed") seenDone.add(j.id); }); } catch {}
  await load();
  setInterval(pollJobs, 1500);
})();

// <exports>
export { D, FAM, GROUP, JOBS, OUT, OVR, SEL, SET, STYLE_KIND, STYLE_LABEL, TRY, VIEW, addPiece, afterPoll, applyChrome, candUrl, capStamp, dget, dpost, fieldsFor, formPiece, fullPrompt, group, harmLabel, load, loadCaps, natUrl, notice, panel, piece, pixelLabel, pixelStops, pollJobs, pstate, refreshRoom, renderCands, renderCatalog, renderCenter, renderList, renderOrphans, renderRecipes, renderSetPanel, renderSheetPanel, renderStyle, renderStyleBox, running, savePiece, saveStyle, savedStyle, seamUrl, seenDone, setState, setView, styleHint, styleText, sv, syncFormVis, syncPixel, viewScale, zoom };
// </exports>
