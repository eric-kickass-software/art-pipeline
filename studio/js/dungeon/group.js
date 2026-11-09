// Dungeon Forge: the group board (every piece of one group on one page: prompts, candidates, generate).
// ---- group board: every piece of one group (Floors, Walls & doors, ...) on one page. Edit prompts side by side,
// see all their candidates, approve by clicking, and generate just the ticked pieces.
import { get, post, url } from "../api.js";
import { $, esc } from "./util.js";
// <imports>
import { D, GROUP, candUrl, dpost, fieldsFor, group, piece, pollJobs, pstate, refreshRoom, renderList, renderStyleBox, running, setState, setView, zoom } from "./main.js";
// </imports>
let GSEL = new Set(), GPROMPT = {};
const groupPieces = () => D.spec.pieces.filter(p => group(p) === GROUP);

function renderGroup() {
  const ps = groupPieces();
  if (!ps.length) { $("#groupView").innerHTML = `<div class="hint">No pieces in ${esc(GROUP)}.</div>`; return; }
  const fields = [...new Set(ps.flatMap(fieldsFor))];
  $("#groupView").innerHTML = `<h2 style="margin-top:0">${esc(GROUP)} <span class="hint">· ${ps.length} pieces · click an image to approve / unapprove, double-click to zoom</span></h2>
    <div class="gbar">
      <button id="gAll">Select all</button><button id="gNone">None</button>
      <label>count<input id="gN" type="number" value="4" min="1" max="12" style="width:52px"></label>
      <button id="gGen" class="primary">Generate selected</button>
      <button id="gSave">Save prompts</button>
      <span class="hint" id="gMsg"></span>
    </div>
    <details class="stylebox" id="groupStyle"><summary>Edit the shared style for ${esc(GROUP.toLowerCase())}</summary><div class="sbody"></div></details>
    <div id="gRows">${ps.map(p => `<div class="grow" data-id="${esc(p.id)}">
      <div class="ghead"><input type="checkbox" class="gsel" ${GSEL.has(p.id) ? "checked" : ""}>
        <span class="gid" title="open in the piece editor">${esc(p.id)}</span><span class="badge gbadge"></span></div>
      <textarea class="gprompt ${p.id in GPROMPT ? "dirty" : ""}" rows="2">${esc(GPROMPT[p.id] ?? p.prompt ?? "")}</textarea>
      <div class="strip"></div></div>`).join("")}</div>`;
  renderStyleBox($("#groupStyle"), fields, null);
  ps.forEach(p => renderGroupRow(p.id));
  document.querySelectorAll(".grow").forEach(row => {
    const id = row.dataset.id;
    row.querySelector(".gsel").onchange = e => { e.target.checked ? GSEL.add(id) : GSEL.delete(id); groupMsg(); };
    row.querySelector(".gid").onclick = () => { setState({SEL: id, TRY: null}); setView("pieces"); };
    row.querySelector(".gprompt").oninput = e => {
      if (e.target.value === (piece(id).prompt || "")) delete GPROMPT[id]; else GPROMPT[id] = e.target.value;
      e.target.classList.toggle("dirty", id in GPROMPT); groupMsg();
    };
  });
  $("#gAll").onclick = () => { ps.forEach(p => GSEL.add(p.id)); renderGroup(); };
  $("#gNone").onclick = () => { GSEL.clear(); renderGroup(); };
  $("#gSave").onclick = () => saveGroupPrompts().then(n => $("#gMsg").textContent = n ? `Saved ${n} prompt${n > 1 ? "s" : ""}.` : "No prompt changes.").catch(e => $("#gMsg").textContent = e.message);
  $("#gGen").onclick = generateGroup;
  groupMsg();
}

function groupMsg() {
  const dirty = Object.keys(GPROMPT).filter(id => piece(id) && group(piece(id)) === GROUP).length;
  $("#gMsg").textContent = `${[...GSEL].filter(id => piece(id) && group(piece(id)) === GROUP).length} selected` + (dirty ? ` · ${dirty} unsaved prompt${dirty > 1 ? "s" : ""}` : "");
}

// Badge + thumbnails for one row (also called when a job finishes, without touching what's being typed).
function renderGroupRow(id) {
  const row = document.querySelector(`.grow[data-id="${CSS.escape(id)}"]`), p = piece(id);
  if (!row || !p) return;
  const s = pstate(id), v = p.variants || 1, rest = s.candidates.filter(r => !s.approved.includes(r));
  row.querySelector(".gbadge").textContent = running(id) ? "generating" : `${s.approved.length}/${v} approved · ${s.candidates.length} cand`;
  row.querySelector(".gbadge").className = "badge gbadge " + (running(id) ? "run" : s.approved.length >= v ? "ok" : s.candidates.length ? "part" : "none");
  const img = (rel, on) => `<img class="${on ? "on" : ""}" data-rel="${esc(rel)}" loading="lazy" src="${candUrl(rel)}" title="${on ? "approved: click to unapprove" : "click to approve"} · double-click to zoom">`;
  row.querySelector(".strip").innerHTML = s.approved.map(r => img(r, true)).join("") + rest.map(r => img(r, false)).join("") ||
    `<span class="none">No candidates yet.</span>`;
  row.querySelectorAll(".strip img").forEach(el => {
    let t = null;
    el.onclick = () => { clearTimeout(t); t = setTimeout(async () => {  // wait out a double-click
      const rel = el.dataset.rel, on = !pstate(id).approved.includes(rel);
      try { D.state.pieces[id] = (await dpost("dungeon.approve", {piece: id, rel, on})).piece; renderGroupRow(id); renderList(); refreshRoom(); }
      catch (e) { $("#gMsg").textContent = e.message; }
    }, 220); };
    el.ondblclick = () => { clearTimeout(t); zoom(el.src); };
  });
}

async function saveGroupPrompts() {
  const ids = Object.keys(GPROMPT).filter(id => piece(id));
  for (const id of ids) { D.spec = (await dpost("dungeon.save_piece", {piece: {...piece(id), prompt: GPROMPT[id].trim()}})).spec; delete GPROMPT[id]; }
  document.querySelectorAll(".grow textarea.dirty").forEach(t => { if (!(t.closest(".grow").dataset.id in GPROMPT)) t.classList.remove("dirty"); });
  groupMsg();
  return ids.length;
}

async function generateGroup() {
  const ids = [...GSEL].filter(id => piece(id) && group(piece(id)) === GROUP);
  if (!ids.length) { $("#gMsg").textContent = "Tick the pieces to generate first."; return; }
  try {
    await saveGroupPrompts();
    const n = +$("#gN").value || 1;
    for (const id of ids) await dpost("dungeon.generate", {piece: id, n, seed: 1 + Math.floor(Math.random() * 99999)});
    $("#gMsg").textContent = `Queued ${ids.length} piece${ids.length > 1 ? "s" : ""} × ${n}.`;
    pollJobs(); ids.forEach(renderGroupRow);
  } catch (e) { $("#gMsg").textContent = e.message; }
}

// <exports>
export { GPROMPT, GSEL, generateGroup, groupMsg, groupPieces, renderGroup, renderGroupRow, saveGroupPrompts };
// </exports>
