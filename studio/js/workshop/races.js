// Character Workshop, Race tab: the race's text, its mannequins and the Paint step per build (specs/races.json,
// specs/paint.json).
// the style groups (specs/ai_style.json) in prompt order; the Race tab picks which of them a race prompt uses
import { get, post } from "../api.js";
import { stripHtml } from "../ramp-chips.js";
// <imports>
import { $, esc, guard, img, jobs, openInEditor, queue } from "./common.js";
// </imports>
const ORDER = ["medium", "style", "proportions", "view", "rendering", "framing", "world"];
// ------------------------------------------------------------------ races
const RC = { race: null, st: null, inited: false };
async function initRaces() {
  if (!RC.inited) {
    RC.inited = true;
    $("raceSel").onchange = guard(async () => { RC.race = $("raceSel").value; await loadRace(); });
    $("raceSave").onclick = guard(saveRaceSpec);
    $("raceNew").onclick = guard(async () => {
      const id = (prompt("New race id (lowercase, e.g. dwarf):") || "").trim(); if (!id) return;
      const doc = RC.st.spec; if (doc.races[id]) throw new Error(`${id} already exists.`);
      doc.races[id] = { name: id[0].toUpperCase() + id.slice(1), traits: id };
      await post("character.save_races", { spec: doc }); RC.race = id; await loadRace(); });
    $("raceRamps").onclick = () => document.querySelector('.tab[data-t="ramps"]').click();
    $("ppSave").onclick = guard(async () => {
      const sp = JSON.parse(JSON.stringify(RC.pspec.spec));
      sp.figure = { front: $("ppFigFront").value.trim(), back: $("ppFigBack").value.trim() };
      sp.complete = { front: $("ppCmpFront").value.trim(), back: $("ppCmpBack").value.trim() };
      sp.back_reference = $("ppBackRef").value.trim(); sp.face_guide = $("ppFaceGuide").value;
      sp.style_groups = [...document.querySelectorAll("#ppGroups input:checked")].map(i => i.dataset.g);
      await post("character.save_paint_spec", { spec: sp }); $("ppStatus").textContent = " saved"; await loadRace(); });
    $("rpSave").onclick = guard(async () => {
      const doc = RC.st.spec;
      doc.pose = $("rpPose").value.trim(); doc.base_body = $("rpBody").value.trim();
      doc.style_groups = [...document.querySelectorAll("#rpGroups input:checked")].map(i => i.dataset.g);
      await post("character.save_races", { spec: doc }); $("rpStatus").textContent = " saved"; await loadRace(); });
  }
  await loadRace();
}
function fillSharedPrompts() {
  const doc = RC.st.spec, keep = id => document.activeElement === $(id);
  if (!keep("rpPose")) $("rpPose").value = doc.pose || "";
  if (!keep("rpBody")) $("rpBody").value = doc.base_body || "";
  const ps = (RC.pspec || {}).spec;
  if (ps) {
    const set = (id, v) => { if (!keep(id)) $(id).value = v || ""; };
    set("ppFigFront", ps.figure.front); set("ppFigBack", ps.figure.back); set("ppBackRef", ps.back_reference);
    set("ppCmpFront", ps.complete.front); set("ppCmpBack", ps.complete.back);
    if (!keep("ppFaceGuide")) $("ppFaceGuide").value = ps.face_guide || "keyed";
    const pon = new Set(ps.style_groups || []);
    if (!$("ppGroups").contains(document.activeElement))
      $("ppGroups").innerHTML = ORDER.map(g => `<label class="chip"><input type="checkbox" data-g="${g}" style="width:auto" ${pon.has(g)?"checked":""}> ${g}</label>`).join("");
  }
  const on = new Set(doc.style_groups || []);
  if (!$("rpGroups").contains(document.activeElement))
    $("rpGroups").innerHTML = ORDER.map(g => `<label class="chip"><input type="checkbox" data-g="${g}" style="width:auto" ${on.has(g)?"checked":""}> ${g}</label>`).join("");
}
async function loadRace() {
  const first = await get("character.race", { race: RC.race || "human" });
  RC.race = first.race; RC.st = first;
  RC.paint = {}; RC.pcomposed = {};   // the Paint step's candidates and the prompts it sends, per sex
  await Promise.all(["unisex", "male", "female"].map(async sex => {
    try { RC.paint[sex] = await get("character.paint_state", { build: `${RC.race}.${sex}` });
      const ps = await get("character.paint_spec", { build: `${RC.race}.${sex}` }); RC.pspec = ps; RC.pcomposed[sex] = ps.composed; }
    catch (e) { RC.paint[sex] = null; } }));
  const doc = RC.st.spec;
  $("raceSel").innerHTML = Object.entries(doc.races).map(([id, r]) => `<option value="${id}" ${id===RC.race?"selected":""}>${r.name} (${id})</option>`).join("");
  const r = doc.races[RC.race];
  if (document.activeElement !== $("raceTraits")) $("raceTraits").value = r.traits || "";
  if (document.activeElement !== $("raceDesc")) $("raceDesc").value = r.description || "";
  fillSharedPrompts();
  $("raceFrom").innerHTML = `<option value="">(generated)</option>` + Object.keys(doc.races).filter(x => x !== RC.race)
    .map(x => `<option ${x===r.from?"selected":""}>${x}</option>`).join("");
  if (document.activeElement !== $("raceFromEdit")) $("raceFromEdit").value = r.from_edit || "";
  $("raceFromRow").style.display = r.from ? "" : "none";
  $("raceFrom").onchange = () => { $("raceFromRow").style.display = $("raceFrom").value ? "" : "none"; };
  const ramps = Object.entries(RC.st.ramps);
  $("raceRampList").innerHTML = ramps.length ? ramps.map(([n, e]) => `<span class="ritem" title="${n}"><span class="nm">${n}</span>${stripHtml(e.colors)}</span>`).join("")
    : `<span class="hint">No skin ramps tagged "${RC.race}" yet: tag them in the Ramps tab (Races field).</span>`;
  fillSharedPrompts(); renderRaceGrid();
}
const RCD = {};   // unsaved prompt text in the race grid (survives the periodic refresh), by data-draft key
const draft = (key, saved) => (key in RCD ? RCD[key] : (saved || ""));
function promptField(key, label, saved, rows = 2, hint = "") {
  return `<div class="row" style="margin:4px 0"><label style="min-width:120px">${label}</label>
    <textarea data-draft="${key}" rows="${rows}" style="flex:1">${esc(draft(key, saved))}</textarea></div>${hint ? `<div class="hint">${hint}</div>` : ""}`;
}
function progressCard(pr, arcAttrs) {   // a candidate still being made (or failed): its progress instead of its images
  const failed = pr.stage === "failed", pct = failed ? 100 : Math.max(4, Math.round((pr.step - 0.5) / pr.of * 100));
  const mins = Math.floor((Date.now() / 1000 - (pr.at || 0)) / 60), stalled = !failed && mins >= 10;   // a job that died mid-way
  const msg = failed ? `<span style="color:var(--bad)">seed ${pr.seed} failed: ${esc(pr.error || "")}</span>`
    : `⏳ seed ${pr.seed}: step ${pr.step} of ${pr.of}, ${esc(pr.stage)}${mins >= 2 ? ` <span class="hint">(${mins} min on this step${stalled ? ": stalled? a busy GPU slows FLUX down; a Studio restart stops it" : ""})</span>` : ""}`;
  return `<div class="pcand"><div class="pwork"><div>${msg}</div>
    <div class="pbar"><i style="width:${pct}%;${failed ? "background:var(--bad)" : ""}"></i></div></div>
    ${failed || stalled ? `<div class="row" style="margin:0"><button ${arcAttrs} title="Archive it" style="width:auto;padding:2px 8px">✕ archive</button></div>` : ""}</div>`;
}
function fitWarn(fit) {   // parts of the mannequin that sat badly on the template: FLUX painted those from the template shape
  const bad = Object.entries(fit || {}).flatMap(([v, f]) => Object.entries(f).filter(([, s]) => s < 0.8).map(([n, s]) => `${v} ${n} ${s}`));
  return bad.length ? ` · <span style="color:var(--warn)" title="${bad.join(", ")}">${bad.length} part${bad.length > 1 ? "s" : ""} off the template</span>` : "";
}
function paintBlock(sex) {   // the Paint step: the clean template painted as this build (figure first, then hidden areas)
  const p = (RC.paint || {})[sex], build = `${RC.race}.${sex}`, ps = RC.pspec;
  if (!p || !ps) return "";
  const e = ps.spec.sexes[sex] || {}, comp = (RC.pcomposed || {})[sex];
  const cands = p.candidates.slice().reverse().map(c => {
    if (c.progress) return progressCard(c.progress, `data-pparc="${c.id}" data-sex="${sex}"`);
    const u = (f) => `${img(c.dir + "/" + f)}&_t=${c.stamp}`, on = c.id === p.approved;
    const has = (f) => c.files.includes(f);
    return `<div class="pcand ${on ? "on" : ""}">
      <div class="row" style="gap:4px;margin:0;flex-wrap:nowrap">${["rest_front.png", "rest_back.png"].filter(has).map(f => `<a href="${u(f.replace("rest_", "walk_").replace(".png", ".gif"))}" target="_blank" title="click: the walk"><img loading="lazy" src="${u(f)}" style="background:#c4c6cc;border-radius:3px"></a>`).join("")}</div>
      <div class="hint">seed ${c.meta.seed ?? "?"}${c.edited ? " · hand-edited" : ""}${c.stale ? ` · <span style="color:var(--warn)">painted on an older template</span>` : ""}${fitWarn(c.meta.fit)}</div>
      <div class="row" style="gap:4px;margin:0"><button data-ppapp="${c.id}" data-sex="${sex}" data-on="${on ? 0 : 1}" style="flex:1">${on ? "✓ approved" : "Approve"}</button>
        <button data-ppedit="${c.id}" data-sex="${sex}" title="Open in the Parts editor">Edit parts</button>
        ${on ? "" : `<button data-pparc="${c.id}" data-sex="${sex}" title="Archive (moves to archive/workshop/paint/)" style="width:auto;padding:2px 8px">✕</button>`}</div></div>`; }).join("");
  return `<div style="border-top:1px solid var(--line);margin-top:8px;padding-top:8px"><div class="row"><b>Paint</b>
    <span class="hint">the clean template (Template tab) painted as ${build}: figure first, then the hidden ends; ~1 min each</span></div>
    ${promptField(`pex:${sex}`, `Face &amp; body (${sex})`, e.extra, 2)}
    ${promptField(`pcl:${sex}`, `Clothes (${sex})`, e.clothes, 1, `Both apply to every race's ${sex}; they're added to the body text above. Shared paint wording (view, completion): Shared prompts card.`)}
    <div class="row"><button data-ppsave="${sex}">Save paint prompts</button>
      <label>Candidates</label><input data-ppn="${sex}" type="number" value="2" min="1" max="6" style="max-width:60px">
      <label>Seed</label><input data-ppseed="${sex}" type="number" value="${(p.candidates.length * 7 + 1)}" style="max-width:80px">
      <button class="primary" data-ppgen="${sex}">Queue paint candidates</button></div>
    ${comp ? `<details><summary class="hint">the prompts this sends (front / back; figure pass, then completion pass)</summary>
      ${["front", "back"].map(v => `<div class="hint">${v}: figure</div><div class="prompt">${esc(comp[v].figure)}</div><div class="hint">${v}: completion</div><div class="prompt">${esc(comp[v].complete)}</div>`).join("")}</details>` : ""}
    <div class="cands pcands">${cands || `<p class="hint">No paint candidates yet.</p>`}</div></div>`;
}
function renderRaceGrid() {
  const a = document.activeElement;   // don't re-render under the cursor while a prompt is being typed
  if ($("raceGrid").contains(a) && ["TEXTAREA", "INPUT"].includes(a.tagName)) return;
  const rows = { unisex: "Unisex (the race's base; its mannequin is the Template's reference art)", male: "Male (painted from the Template)",
                 female: "Female (painted from the Template)" };
  $("raceGrid").innerHTML = Object.entries(rows).map(([sex, label]) => `<div class="card"><h2 style="margin-top:0">${label}</h2>
    ${promptField(`sex:${sex}`, `Body (${sex})`, RC.st.spec.sexes[sex], 2, `Every race's ${sex}: used by ${RC.st.builds[sex] ? `the ${sex} mannequin below and ` : ""}its Paint step.`)}
    <div class="row"><button data-sexsave="${sex}">Save body text</button></div>
    ${RC.st.builds[sex] ? `<div class="grid2">${["front", "back"].map(f => {
      const d = RC.st.builds[sex][f];
      const cands = d.candidates.map(c => `<div class="cand ${c===d.approved?"on":""}"><img loading="lazy" src="${img(c)}">
        ${d.stale[c] ? `<div class="hint" style="color:var(--warn)">made from an older source</div>` : ""}
        <div class="row" style="gap:4px;margin:0"><button data-rap="${c}" data-sex="${sex}" data-f="${f}" style="flex:1">${c===d.approved?"✓ approved":"Approve"}</button>
        ${c===d.approved ? "" : `<button data-rdel="${c}" data-sex="${sex}" data-f="${f}" title="Archive (moves to archive/workshop/)" style="width:auto;padding:2px 8px">✕</button>`}</div></div>`).join("");
      return `<div><div class="row"><b>Mannequin ${f}</b>
        ${d.ready === true ? `<button class="primary" data-rgen="${sex}" data-f="${f}">Queue candidates</button>` : `<span class="hint">${d.ready}</span>`}</div>
        <details><summary class="hint">the prompt this sends (${d.mode === "edit" ? "FLUX edit of the source image" : "text to image"})</summary><div class="prompt">${esc(d.prompt)}</div></details>
        <div class="cands bcands">${cands || `<p class="hint">No candidates yet.</p>`}</div></div>`; }).join("")}</div>` : ""}${paintBlock(sex)}</div>`).join("");
  document.querySelectorAll("#raceGrid [data-draft]").forEach(t => t.oninput = () => { RCD[t.dataset.draft] = t.value; });
  document.querySelectorAll("[data-sexsave]").forEach(btn => btn.onclick = guard(async () => {
    const sex = btn.dataset.sexsave, doc = RC.st.spec;
    doc.sexes[sex] = draft(`sex:${sex}`, doc.sexes[sex]).trim();
    await post("character.save_races", { spec: doc }); delete RCD[`sex:${sex}`]; await loadRace(); }));
  document.querySelectorAll("[data-ppsave]").forEach(btn => btn.onclick = guard(async () => {
    const sex = btn.dataset.ppsave, sp = JSON.parse(JSON.stringify(RC.pspec.spec)), e = sp.sexes[sex] || (sp.sexes[sex] = {});
    e.extra = draft(`pex:${sex}`, e.extra).trim(); e.clothes = draft(`pcl:${sex}`, e.clothes).trim();
    await post("character.save_paint_spec", { spec: sp }); delete RCD[`pex:${sex}`]; delete RCD[`pcl:${sex}`]; await loadRace(); }));
  document.querySelectorAll("[data-ppgen]").forEach(btn => btn.onclick = guard(async () => {
    const sex = btn.dataset.ppgen, n = +document.querySelector(`[data-ppn="${sex}"]`).value, seed = +document.querySelector(`[data-ppseed="${sex}"]`).value;
    if (RCD[`pex:${sex}`] !== undefined || RCD[`pcl:${sex}`] !== undefined) throw new Error("Save the paint prompts first (they'd be ignored).");
    await queue([{ kind: "paint", build: `${RC.race}.${sex}`, n, seed, label: `${RC.race} · ${sex} · paint` }]); }));
  document.querySelectorAll("[data-ppapp]").forEach(btn => btn.onclick = guard(async () => {
    await post("character.approve_paint", { build: `${RC.race}.${btn.dataset.sex}`, candidate: btn.dataset.ppapp, on: btn.dataset.on === "1" }); await loadRace(); }));
  document.querySelectorAll("[data-pparc]").forEach(btn => btn.onclick = guard(async () => {
    await post("character.archive_paint", { build: `${RC.race}.${btn.dataset.sex}`, candidate: btn.dataset.pparc }); await loadRace(); }));
  document.querySelectorAll("[data-ppedit]").forEach(btn => btn.onclick = guard(async () => {
    openInEditor({ build: `${RC.race}.${btn.dataset.sex}`, paint: btn.dataset.ppedit }); }));
  document.querySelectorAll("[data-rgen]").forEach(btn => btn.onclick = guard(async () => {
    const sex = btn.dataset.rgen, f = btn.dataset.f, n = +$("raceN").value, seed = +$("raceSeed").value;
    if (RCD[`sex:${sex}`] !== undefined) throw new Error("Save the body text first (it'd be ignored).");
    await queue([{ kind: "race", race: RC.race, sex, facing: f, n, seed, label: `${RC.race} · ${sex} · ${f}` }]);
    $("raceSeed").value = seed + n; }));
  document.querySelectorAll("[data-rap]").forEach(btn => btn.onclick = guard(async () => {
    await post("character.approve_mannequin", { build: `${RC.race}.${btn.dataset.sex}`, facing: btn.dataset.f, file: btn.dataset.rap }); await loadRace(); }));
  document.querySelectorAll("[data-rdel]").forEach(btn => btn.onclick = guard(async () => {
    await post("character.archive_mannequin", { build: `${RC.race}.${btn.dataset.sex}`, facing: btn.dataset.f, file: btn.dataset.rdel }); await loadRace(); }));
}
async function saveRaceSpec() {
  const doc = RC.st.spec, r = doc.races[RC.race];
  r.traits = $("raceTraits").value.trim();
  r.description = $("raceDesc").value.trim();
  if ($("raceFrom").value) { r.from = $("raceFrom").value; r.from_edit = $("raceFromEdit").value.trim(); }
  else { delete r.from; delete r.from_edit; }
  await post("character.save_races", { spec: doc }); $("raceStatus").textContent = " saved"; await loadRace();
}
document.querySelector('.tab[data-t="races"]').addEventListener("click", guard(initRaces));
RC.busy = false;
setInterval(() => {   // refresh while race jobs run, and once more when the queue goes idle
  if (!RC.inited || !document.querySelector('.tab[data-t="races"]').classList.contains("on")) return;
  const busy = jobs.some(j => j.status === "running" || j.status === "queued");
  if (busy || RC.busy) loadRace().catch(() => {});
  RC.busy = busy;
}, 4000);

// <exports>
export { ORDER, RC, RCD, draft, fillSharedPrompts, fitWarn, initRaces, loadRace, paintBlock, progressCard, promptField, renderRaceGrid, saveRaceSpec };
// </exports>
