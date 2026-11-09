// Dungeon Forge: the materials library (CC0 materials from ambientCG / Poly Haven, ops dungeon.*material*).
// ---- materials library (hdart/library.py): CC0 materials from ambientCG / Poly Haven, used by families.
import { get, post, url } from "../api.js";
import { $, esc } from "./util.js";
// <imports>
import { famInfo, renderFamily } from "./families.js";
import { D, FAM, SET, VIEW, dpost, load, pollJobs, setState, setView } from "./main.js";
// </imports>
let LIB = [];
async function renderLibrary() {
  try { LIB = await get("dungeon.materials"); } catch { LIB = []; }
  $("#libList").innerHTML = LIB.length ? LIB.map(m => `<div class="libitem" data-id="${esc(m.id)}" title="${esc(m.name)} · ${esc(m.source)}"><img src="${url("dungeon.material_image", {id: m.id, size: 64})}" alt=""><span>${esc(m.name)}</span></div>`).join("")
    : `<div class="hint">No materials yet: import some (brick, flagstone, plaster, wood…).</div>`;
  document.querySelectorAll("#libList .libitem").forEach(el => el.onclick = () => openMaterial(el.dataset.id));
}
function libOpen(title) { $("#libTitle").textContent = title; $("#libPanel").classList.remove("hidden"); }
function libClose() { $("#libPanel").classList.add("hidden"); }

function openImport() {
  libOpen("Import materials (CC0: ambientCG, Poly Haven)");
  $("#libBody").innerHTML = `<div class="gbar"><input id="libQ" placeholder="e.g. bricks, flagstone, cobblestone, plaster, wood planks" style="flex:1">
      <select id="libSrc" style="width:auto"><option value="all">both</option><option value="ambientcg">ambientCG</option><option value="polyhaven">Poly Haven</option></select>
      <button id="libGo" class="primary">Search</button><span class="hint" id="libMsg"></span></div><div id="libRes" class="libgrid"></div>`;
  const go = async () => {
    $("#libMsg").textContent = "searching…"; $("#libRes").innerHTML = "";
    let hits;
    try { hits = await get("dungeon.search_materials", {q: $("#libQ").value, src: $("#libSrc").value}); } catch (e) { $("#libMsg").textContent = e.message; return; }
    const have = new Set(LIB.map(m => m.id));
    $("#libMsg").textContent = `${hits.length} found`;
    $("#libRes").innerHTML = hits.map((h, i) => `<div class="libcard"><img src="${esc(h.thumb || "")}" alt="" loading="lazy">
      <b>${esc(h.name)}</b> <span class="hint">${h.source} · ${h.size_m ? h.size_m.map(v => v.toFixed(1)).join(" × ") + " m" : "size ?"}</span>
      <div class="tags">${esc((h.tags || []).slice(0, 8).join(", "))}</div>
      <button data-i="${i}" class="${have.has(`${h.source}_${h.asset}`.toLowerCase().replace(/[^a-z0-9_]+/g, "_")) ? "" : "primary"}">${have.has(`${h.source}_${h.asset}`.toLowerCase().replace(/[^a-z0-9_]+/g, "_")) ? "In library (import again)" : "Import"}</button></div>`).join("");
    $("#libRes").querySelectorAll("button").forEach(b => b.onclick = async () => {
      const h = hits[+b.dataset.i];
      try { await post("dungeon.import_material", {source: h.source, asset: h.asset, hit: h}); b.textContent = "importing…"; b.disabled = true; pollJobs(); } catch (e) { b.textContent = e.message; }
    });
  };
  $("#libGo").onclick = go; $("#libQ").onkeydown = e => { if (e.key === "Enter") go(); };
  $("#libQ").focus();
}

function openMaterial(id) {
  const m = LIB.find(x => x.id === id); if (!m) return;
  const fam = FAM && famInfo() ? FAM : null;
  libOpen(`${m.name} · ${m.source} (${m.license})`);
  $("#libBody").innerHTML = `<div class="libmat"><img src="${url("dungeon.material_image", {id})}" alt="">
    <div class="side">
      <div><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.url)}</a> · imported ${esc(m.imported || "")}</div>
      <div>size ${m.size_m ? m.size_m.map(v => v.toFixed(2)).join(" × ") + " m" : "unknown"} ·
        <label title="Whole repeats of the texture per tile (1 tile ≈ 1.5 m). Metadata can be off: judge it against the hero in the blockout.">repeats per tile<input id="libRep" type="number" min="1" max="12" value="${m.repeats || 1}" style="width:52px"></label></div>
      <div><span class="hint">palette</span> ${(m.palette.stones || []).map(c => `<span class="swatch" style="background:${c}"></span>`).join("")} <span class="hint">joint</span> <span class="swatch" style="background:${m.palette.mortar}"></span></div>
      <div class="hint">tags: ${esc((m.tags || []).join(", "))}</div>
      <label class="hint">descriptors (added to a family's prompt by "words")</label><textarea id="libDesc" rows="3">${esc(m.descriptors || "")}</textarea>
      <div class="row"><button id="libSave">Save</button><span class="hint" id="libMsg2"></span></div>
      <h3>Use it</h3>
      ${fam ? `<div class="row"><span class="hint">on ${esc(fam)}:</span><button data-use="template">texture as template</button><button data-use="palette">palette</button><button data-use="words">words</button><button data-use="template,words" class="primary">template + words</button></div>` : `<div class="hint">Open a family to apply this material to it.</div>`}
      <div class="row"><button data-new="floor">New floor family from it</button><button data-new="wall">New wall family from it</button></div>
      <div class="hint">Floor and wall families can share a material: same texture scale, palette and words on both.</div>
    </div></div>`;
  const save = async () => { const mm = {...m, descriptors: $("#libDesc").value, repeats: +$("#libRep").value}; await post("dungeon.save_material", {material: mm}); await renderLibrary(); return mm; };
  $("#libSave").onclick = async () => { try { await save(); $("#libMsg2").textContent = "Saved."; } catch (e) { $("#libMsg2").textContent = e.message; } };
  $("#libBody").querySelectorAll("[data-use]").forEach(b => b.onclick = async () => {
    try { await save(); await dpost("dungeon.apply_material", {piece: fam, material: id, use: b.dataset.use.split(",")}); await load(); libClose(); if (VIEW === "family") renderFamily(); }
    catch (e) { $("#libMsg2").textContent = e.message; }
  });
  $("#libBody").querySelectorAll("[data-new]").forEach(b => b.onclick = async () => {
    const wall = b.dataset.new === "wall", slug = m.name.toLowerCase().replace(/\W+/g, "_").replace(/^_|_$/g, "").slice(0, 24) || "mat";
    let piece;
    if (wall) piece = {id: `tile.${SET}_${slug}_wall_master`, kind: "elevation", shape: "elevation", method: "wall_master", variants: 1, prompt: m.name.toLowerCase(), family: {prompts: [m.name.toLowerCase()]}};
    else {
      const board = (D.spec.pieces.find(q => q.method === "master") || D.spec.pieces.find(q => q.shape === "floor" && (q.board || q.id).endsWith("_floor")) || {}).board || `tile.${SET}_floor`;
      piece = {id: `tile.${SET}_${slug}_master`, kind: "tile", shape: "floor", method: "master", board, variants: 1, prompt: m.name.toLowerCase(), family: {prompts: [m.name.toLowerCase()]}};
    }
    try {
      await save(); await dpost("dungeon.save_piece", {piece}); await dpost("dungeon.apply_material", {piece: piece.id, material: id, use: ["template", "words"]});
      setState({FAM: piece.id}); await load(); libClose(); setView("family");
    } catch (e) { $("#libMsg2").textContent = e.message; }
  });
}
$("#libClose").onclick = libClose;
$("#libPanel").onclick = e => { if (e.target.id === "libPanel") libClose(); };
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#libPanel").classList.contains("hidden")) libClose(); });
$("#libImportBtn").onclick = openImport;

// <exports>
export { LIB, libClose, libOpen, openImport, openMaterial, renderLibrary };
// </exports>
