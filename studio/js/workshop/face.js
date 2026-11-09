// Character Workshop, Face tab: the race's hair and faces, painted on its painted puppets (specs/face.json).
// ------------------------------------------------------------------ Face tab (racial hair + faces)
import { get, post } from "../api.js";
// <imports>
import { $, err, esc, guard, img, jobs, openInEditor, queue } from "./common.js";
import { progressCard } from "./races.js";
import { RS } from "./ramps-tab.js";
// </imports>
const FC = { race: null, st: null, inited: false, drafts: {}, ramp: { hair: "", face: "" }, urls: {} };
function fcCard(kind) {
  const st = FC.st, r = st.spec.races[FC.race] || {}, sex = $("fcSex").value, comp = st.composed[sex];
  const group = kind === "hair" ? "hair" : "eyes", pal = (RS.doc || {}).groups ? (RS.doc.groups[group] || {}) : (FC.pal || {})[group] || {};
  const cands = st.kinds[kind].slice().reverse().map(c => {
    if (c.progress) return progressCard(c.progress, `data-fcarc="${c.id}" data-kind="${kind}"`);
    const u = (f) => FC.urls[`${kind}|${c.id}|${f}`] || `${img(c.dir + "/" + f)}&_t=${c.stamp}`;
    const files = kind === "hair" ? ["prev_male_front.png", "prev_male_back.png", "prev_female_front.png", "prev_female_back.png"] : ["head_male.png", "head_female.png"];
    const keyed = c.meta.keyed || {}, nokey = Object.values(keyed).some(n => n === 0);
    return `<div class="pcand ${c.in_set ? "on" : ""}" style="width:auto">
      <div class="row" style="gap:3px;margin:0;flex-wrap:nowrap">${files.filter(f => c.files.includes(f)).map(f => `<img loading="lazy" src="${u(f)}" style="width:${kind === "hair" ? 120 : 170}px;height:${kind === "hair" ? 120 : 170}px;background:#c4c6cc;border-radius:3px" title="${f}">`).join("")}</div>
      <div class="hint">${c.meta.from_paint ? `from paint ${c.meta.paint} (${c.meta.on})` : `seed ${c.meta.seed ?? "?"} · on ${c.meta.on || "?"}`}${nokey ? ` · <span style="color:var(--warn)">${kind === "hair" ? "hair" : "irises"} not keyed: can't be recoloured</span>` : ""}${(c.meta.clipped || []).length ? ` · <span style="color:var(--warn)">cut flat by the canvas top (${c.meta.clipped.join(", ")})</span>` : ""}</div>
      <div class="row" style="gap:4px;margin:0"><button data-fcset="${c.id}" data-kind="${kind}" data-on="${c.in_set ? 0 : 1}" style="flex:1">${c.in_set ? "✓ in the set" : "Add to the set"}</button>
        <button data-fcedit="${c.id}" data-kind="${kind}" title="Edit the layer in the Parts editor">Edit</button>
        ${c.in_set ? "" : `<button data-fcarc="${c.id}" data-kind="${kind}" title="Archive (moves to archive/workshop/face/)" style="width:auto;padding:2px 8px">✕</button>`}</div></div>`; }).join("");
  const key = `${kind}:${FC.race}`, val = key in FC.drafts ? FC.drafts[key] : (r[kind] || "");
  return `<div class="card"><h2 style="margin-top:0">${kind === "hair" ? "Hair" : "Face (eyes, brows, nose, mouth)"} <span class="hint">${st.sets[kind].length} in ${FC.race}'s set</span></h2>
    <div class="row"><label style="min-width:110px">${kind === "hair" ? "Hairstyle" : "Face"} (${FC.race})</label>
      <textarea data-fcdraft="${key}" rows="2" style="flex:1" placeholder="${kind === "hair" ? "the style only, e.g. long braided hair (the colour comes from the hair ramps)" : "e.g. narrow eyes, thin brows, a small smile"}">${esc(val)}</textarea></div>
    <div class="row"><button data-fcsave="${kind}">Save</button>
      <label>Candidates</label><input data-fcn="${kind}" type="number" value="2" min="1" max="6" style="max-width:60px">
      <label>Seed</label><input data-fcseed="${kind}" type="number" value="${st.kinds[kind].length * 7 + 1}" style="max-width:80px">
      <button class="primary" data-fcgen="${kind}">Queue ${kind} candidates</button>
      <label>${kind === "hair" ? "Hair" : "Eye"} colour</label><select data-fcramp="${kind}" style="max-width:150px">${Object.keys(pal).map(n => `<option ${n === FC.ramp[kind] ? "selected" : ""}>${n}</option>`).join("")}</select>
      <span class="hint">recolours the previews (Ramps edits the ramps)</span></div>
    <details><summary class="hint">the prompt${kind === "hair" ? "s" : ""} this sends (painted on the ${sex})</summary>${kind === "hair"
      ? `<div class="hint">front</div><div class="prompt">${esc(comp.hair.front)}</div><div class="hint">back: edit of the front</div><div class="prompt">${esc(comp.hair_back_edit)}</div><div class="hint">back: guided pass</div><div class="prompt">${esc(comp.hair.back)}</div>`
      : `<div class="prompt">${esc(comp.face)}</div>`}</details>
    <div class="cands pcands">${cands || `<p class="hint">No ${kind} candidates yet.</p>`}</div></div>`;
}
function fcRender() {
  const a = document.activeElement;
  if ($("fcKinds").contains(a) && ["TEXTAREA", "INPUT"].includes(a.tagName)) return;
  const st = FC.st;
  $("fcBases").textContent = Object.entries(st.bases).map(([s_, c]) => `${s_}: ${c || "no paint yet (Race → Paint)"}`).join(" · ");
  $("fcKinds").innerHTML = fcCard("hair") + fcCard("face");
  const keep = id => document.activeElement === $(id);
  if (!keep("fcHairFront")) $("fcHairFront").value = st.spec.hair.front; if (!keep("fcHairBack")) $("fcHairBack").value = st.spec.hair.back;
  if (!keep("fcFace")) $("fcFace").value = st.spec.face;
  if (!keep("fcHairBackEdit")) $("fcHairBackEdit").value = st.spec.hair_back_edit || "";
  $("fcHairKey").value = st.spec.hair_key || "#22c822"; $("fcEyeKey").value = st.spec.eye_key || "#d020d0";
  document.querySelectorAll("[data-fcdraft]").forEach(t => t.oninput = () => { FC.drafts[t.dataset.fcdraft] = t.value; });
  document.querySelectorAll("[data-fcsave]").forEach(b_ => b_.onclick = guard(async () => {
    const kind = b_.dataset.fcsave, key = `${kind}:${FC.race}`, sp = JSON.parse(JSON.stringify(FC.st.spec));
    sp.races[FC.race] = sp.races[FC.race] || {}; sp.races[FC.race][kind] = (key in FC.drafts ? FC.drafts[key] : sp.races[FC.race][kind] || "").trim();
    FC.st = await post("character.save_face_spec", { race: FC.race, spec: sp }); delete FC.drafts[key]; fcRender(); }));
  document.querySelectorAll("[data-fcgen]").forEach(b_ => b_.onclick = guard(async () => {
    const kind = b_.dataset.fcgen;
    if (`${kind}:${FC.race}` in FC.drafts) throw new Error("Save the text first (it'd be ignored).");
    await queue([{ kind: "face_gen", what: kind, race: FC.race, sex: $("fcSex").value, n: +document.querySelector(`[data-fcn="${kind}"]`).value,
      seed: +document.querySelector(`[data-fcseed="${kind}"]`).value, label: `${FC.race} · ${kind} · face` }]); }));
  document.querySelectorAll("[data-fcset]").forEach(b_ => b_.onclick = guard(async () => {
    FC.st = await post("character.set_face", { race: FC.race, kind: b_.dataset.kind, candidate: b_.dataset.fcset, on: b_.dataset.on === "1" }); fcRender(); }));
  document.querySelectorAll("[data-fcedit]").forEach(b_ => b_.onclick = guard(async () => {
    openInEditor({ race: FC.race, layer: b_.dataset.kind, id: b_.dataset.fcedit }); }));
  document.querySelectorAll("[data-fcarc]").forEach(b_ => b_.onclick = guard(async () => {
    FC.st = await post("character.archive_face", { race: FC.race, kind: b_.dataset.kind, candidate: b_.dataset.fcarc }); fcRender(); }));
  document.querySelectorAll("[data-fcramp]").forEach(sel => sel.onchange = guard(async () => {   // previews in another hair / eye colour
    const kind = sel.dataset.fcramp; FC.ramp[kind] = sel.value; sel.disabled = true;
    try { for (const c of FC.st.kinds[kind]) {
      const urls = await get("character.face_preview", { race: FC.race, kind, candidate: c.id, ramp: sel.value });
      for (const [f, u] of Object.entries(urls)) FC.urls[`${kind}|${c.id}|${f}`] = u; } }
    finally { fcRender(); } }));
}
async function fcLoad() {
  FC.st = await get("character.faces", { race: FC.race });
  if (!RS.doc) FC.pal = (await get("character.palettes")).groups;
  fcRender();
}
async function initFace() {
  if (!FC.inited) {
    FC.inited = true;
    const races = Object.keys((await get("character.race", { race: "human" })).spec.races);
    $("fcRace").innerHTML = races.map(r => `<option>${r}</option>`).join(""); FC.race = races[0];
    $("fcRace").onchange = guard(async () => { FC.race = $("fcRace").value; FC.urls = {}; await fcLoad(); });
    $("fcSex").onchange = fcRender;
    $("fcSharedSave").onclick = guard(async () => {
      const sp = JSON.parse(JSON.stringify(FC.st.spec));
      sp.hair = { front: $("fcHairFront").value.trim(), back: $("fcHairBack").value.trim() }; sp.face = $("fcFace").value.trim();
      sp.hair_back_edit = $("fcHairBackEdit").value.trim();
      sp.hair_key = $("fcHairKey").value; sp.eye_key = $("fcEyeKey").value;
      FC.st = await post("character.save_face_spec", { race: FC.race, spec: sp }); $("fcSharedStatus").textContent = " saved"; fcRender(); });
  }
  await fcLoad();
}
document.querySelector('.tab[data-t="face"]').addEventListener("click", guard(initFace));
setInterval(() => {   // when a face job finishes: reload the candidates
  if (!FC.inited) return;
  const done = jobs.filter(j => j.kind === "face_gen" && (j.status === "done" || j.status === "failed")).length;
  const running = jobs.some(j => j.kind === "face_gen" && j.status === "running");
  if (FC.done === undefined) { FC.done = done; return; }
  const onTab = document.querySelector('.tab[data-t="face"]').classList.contains("on");
  if (done !== FC.done || (running && onTab)) { FC.done = done; fcLoad().catch(e => err(e.message)); }   // live progress
}, 2500);

// <exports>
export { FC, fcCard, fcLoad, fcRender, initFace };
// </exports>
