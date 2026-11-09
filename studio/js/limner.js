// The Limner page (/limner): icons and portraits per category of the game (items, gear, classes, portraits, tokens...),
// their style and finish settings, candidates, overnight batches and the export (ops limner.*).
import { get, post, url } from "./api.js";
import { initMenu } from "./menu.js";
import { jobsPanel, isActive } from "./jobs.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const err = (m) => { $("err").textContent = m || ""; };
const guard = (fn) => async (...a) => { try { err(); await fn(...a); } catch (e) { err(String(e.message || e)); } };

initMenu("limner");
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("zoom").classList.remove("on"); });

let D = null, CAT = localStorage.limnerCat || "items", OPEN = null, BUST = Date.now(), BG = localStorage.limnerBg || "#2a2333";
const SEL = new Set();
const NAMES = { items: "Items", gear: "Gear", classes: "Classes", statuses: "Statuses", damageTypes: "Damage", skills: "Skills", ui: "UI", portraits: "Portraits", monsters: "Monsters", npcs: "NPCs", tokens: "Tokens" };
const isP = () => cat().finish.kind === "portrait";
const isT = () => cat().finish.kind === "token";
const FILE = () => isP() ? "portrait.png" : isT() ? "token.png" : "icon.png";
const img = (cat, id, cid, file) => url("limner.image", { path: `${cat}/${id}/${cid}/${file}` }, BUST);

async function load() { D = await get("limner.doc"); render(); }
function cat() { return D.categories[CAT]; }
function entry(id) { return cat().entries.find(e => e.id === id); }

function render() {
  if (!D.categories[CAT]) CAT = "items";
  $("tabs").innerHTML = D.order.map(c => { const es = D.categories[c].entries, a = es.filter(e => e.approved).length;
    const nw = D.categories[c].new;
    return `<button class="tab ${c === CAT ? "on" : ""}" data-c="${c}">${NAMES[c] || c}<span class="n">${a}/${es.length}</span>${nw ? `<span class="nw" title="new candidates to review">+${nw}</span>` : ""}</button>`; }).join("");
  document.querySelectorAll("#tabs .tab").forEach(b => b.onclick = () => { CAT = localStorage.limnerCat = b.dataset.c; OPEN = null; SEL.clear(); render(); });
  $("addBtn").hidden = !cat().add_prefix; if (!cat().add_prefix) $("addBox").hidden = true;
  renderSide(); renderGrid(); renderDetail(); if (!$("batch").hidden) renderBatch(); if (!$("addBox").hidden) renderAdd();
}

// ------------------------------------------------------------------ add your own entry
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
$("addBtn").onclick = () => { $("addBox").hidden = !$("addBox").hidden; if (!$("addBox").hidden) { renderAdd(); $("aName").focus(); } };
function renderAdd() {
  const c = cat(), pre = c.add_prefix;
  $("addBox").innerHTML = `<div class="row" style="margin-bottom:6px"><b>Add to ${esc(c.name)}</b>
      <span class="hint">${CAT === "ui" ? "Any icon for menus, buttons or the HUD." : "An item the game doesn't have yet: its icon exports with the rest; the game uses it once it has that id."}</span>
      <button style="margin-left:auto" id="aClose">✕</button></div>
    <div class="f"><label>Name</label><input id="aName" placeholder="${CAT === "ui" ? "e.g. Crafting" : "e.g. Silver Dagger"}"></div>
    <div class="f"><label>Id</label><span><input id="aId" placeholder="from the name" style="width:240px"> <span class="idp" id="aIdP"></span></span></div>
    <div class="f"><label>Subject</label><textarea id="aSubj" rows="2" placeholder="what the icon shows (empty = the name)"></textarea></div>
    <div class="f"><label>Label</label><input id="aSub" placeholder="${CAT === "gear" ? "e.g. head, dagger" : CAT === "ui" ? "e.g. menu" : "e.g. consumable"}" style="width:160px" title="The small grey label on the card"></div>
    <div class="row" style="margin-top:6px"><button class="primary" id="aGo">Add</button><button id="aGoGen">Add &amp; generate</button><span class="hint" id="aMsg"></span></div>`;
  const idOf = () => { const typed = $("aId").value.toLowerCase().replace(/[^a-z0-9.]+/g, "_").replace(/^[_.]+|[_.]+$/g, "");
    const raw = typed || slug($("aName").value); return raw ? (raw.startsWith(pre) ? raw : pre + raw) : ""; };
  const upd = () => { $("aIdP").textContent = idOf() ? "→ " + idOf() : ""; };
  $("aName").oninput = upd; $("aId").oninput = upd;
  $("aClose").onclick = () => { $("addBox").hidden = true; };
  const add = async (andGen) => {
    if (!$("aName").value.trim() && !$("aId").value.trim()) throw new Error("Give the new entry a name.");
    const r = await post("limner.add_entry", { cat: CAT, entry: idOf(), name: $("aName").value.trim(), subject: $("aSubj").value.trim(), sub: $("aSub").value.trim() });
    if (andGen) await gen([r.id], +$("genN").value);
    OPEN = r.id; $("addBox").hidden = true; await load();
    document.querySelector(".card.sel")?.scrollIntoView({ block: "nearest" });
  };
  $("aGo").onclick = guard(() => add(false)); $("aGoGen").onclick = guard(() => add(true));
}

// ------------------------------------------------------------------ category settings (left)
function styleOpts(sel, withDefault) {
  return (withDefault ? `<option value="">category default (${esc(D.styles[cat().style]?.name || cat().style)})</option>` : "") +
    Object.entries(D.styles).map(([k, s]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(s.name)}</option>`).join("");
}
function renderSide() {
  const c = cat(), f = c.finish, st = D.styles[c.style] || {};
  let h = `<h3>${esc(c.name)}</h3><div class="hint">${esc(c.hint || "")}</div>
    <h3>Style</h3>
    <div class="f"><label>Default</label><select id="cStyle">${styleOpts(c.style)}</select></div>
    <div class="f"><label>Style words</label><span class="hint">edits apply to every category using it</span></div>
    <textarea id="sPrefix" rows="3" title="Before the subject">${esc(st.prefix)}</textarea>
    <textarea id="sSuffix" rows="2" title="After the subject">${esc(st.suffix)}</textarea>
    <div class="f"><label>Pixel LoRA</label><input id="sLora" type="number" step="0.1" min="0" max="1.5" value="${st.lora ?? 0}"></div>
    <div class="row"><button id="sSave">Save style</button><button id="sNew" title="Copy this style under a new name">New style…</button></div>
    <h3>Category words</h3>
    <div class="f"><label>Before</label><input id="cPrefix" value="${esc(c.prefix)}"></div>
    <div class="f"><label>After</label><input id="cSuffix" value="${esc(c.suffix)}"></div>
    <div class="f"><label>Size</label><span><input id="gW" type="number" value="${c.gen.width}" style="width:62px"> × <input id="gH" type="number" value="${c.gen.height}" style="width:62px"></span></div>
    <div class="f"><label>Steps</label><input id="gSteps" type="number" min="1" max="30" value="${c.gen.steps}"></div>
    <div class="row"><button id="cSave">Save category</button></div>
    <h3>Finish <span class="hint" style="text-transform:none;letter-spacing:0">(no GPU; redoes every candidate)</span></h3>`;
  if (f.kind === "portrait") {
    h += `<div class="f"><label>Width</label><input id="fW" type="number" value="${f.width}"></div>
      <div class="f"><label>Thumb</label><input id="fT" type="number" value="${f.thumb}"></div>
      <div class="f"><label>Face crop</label><span><input id="fCx" type="number" step="0.01" value="${f.crop[0]}" style="width:52px"><input id="fCy" type="number" step="0.01" value="${f.crop[1]}" style="width:52px"><input id="fCs" type="number" step="0.01" value="${f.crop[2]}" style="width:52px"></span></div>
      <div class="hint">x, y, size as fractions of the width (each portrait can override it).</div>`;
  } else if (f.kind === "token") {
    h += `<div class="f"><label>Box px</label><input id="fSize" type="number" value="${f.size}" title="The token fits a box this big (tall figures and wide blobs alike)"></div>
      <div class="f"><label>Margin</label><input id="fMar" type="number" min="0" max="32" value="${f.margin}"></div>
      <div class="f"><label>Normals</label><input id="fNor" type="number" step="0.5" min="0" max="8" value="${f.normal ?? 2}" title="Normal map strength (from the shading)"></div>
      <div class="hint">Feet at the bottom centre (the pivot); facing right. An entry that faces left: Mirror in its panel.</div>`;
  } else {
    h += `<div class="f"><label>Icon px</label><input id="fSize" type="number" value="${f.size}"></div>
      <div class="f"><label>Colours</label><input id="fCol" type="number" min="0" max="64" value="${f.colors}" title="0 = smooth (no pixel quantize)"></div>
      <div class="f"><label>Margin</label><input id="fMar" type="number" min="0" max="16" value="${f.margin}"></div>
      <div class="f"><label>Outline</label><input id="fOut" value="${esc(f.outline)}" placeholder="none" title="#rrggbb, empty = none"></div>`;
  }
  h += `<div class="row"><button id="fSave">Save &amp; re-finish</button></div>
    <h3>Export</h3><div class="hint">Approved only → ${esc(D.export)}\\${isP() ? "Portraits (heroes, monsters and NPCs together)" : isT() ? "Monsters\\<id>\\token.png" : "Icons\\" + CAT}</div>
    <div class="row" style="margin-top:4px"><button class="primary" id="exCat">Export ${esc(c.name)}</button><button id="exAll">Export all</button></div>
    <div class="hint" id="exMsg"></div>`;
  if (CAT === "portraits") {
    h += `<h3>Races</h3><div class="hint">Every archetype × look × gender is made for each race. The traits go after the look.</div>` +
      Object.entries(D.races).map(([rk, r]) => `<div class="look"><span class="hint">${esc(r.name)}</span><textarea data-r="${rk}" placeholder="${rk === "human" ? "(none)" : "skin, ears, face…"}">${esc(r.traits || "")}</textarea><span></span></div>`).join("") +
      `<div class="look"><input placeholder="new key" class="raceKey"><textarea placeholder="traits, e.g. stocky, braided beard…" class="raceNew"></textarea><button class="raceAdd" title="Add a race">＋</button></div>`;
    h += `<h3>Archetypes</h3><div class="hint">Each look makes a male and a female portrait. Add looks freely: more portraits to pick from.</div>`;
    for (const [ak, a] of Object.entries(D.archetypes)) {
      h += `<div class="arch"><b>${esc(a.name)}</b> <span class="cls">${esc(a.classes.join(", "))}</span><div class="hint">${esc(a.summary)}</div>` +
        Object.entries(a.looks).map(([lk, t]) => `<div class="look"><span class="hint">${esc(lk)}</span><textarea data-a="${ak}" data-l="${lk}">${esc(t)}</textarea><button data-a="${ak}" data-l="${lk}" class="lookDel" title="Remove this look">✕</button></div>`).join("") +
        `<div class="look"><input placeholder="new key" data-a="${ak}" class="lookKey"><textarea placeholder="describe the look…" data-a="${ak}" class="lookNew"></textarea><button data-a="${ak}" class="lookAdd" title="Add">＋</button></div></div>`;
    }
  }
  $("side").innerHTML = h;
  $("cStyle").onchange = guard(async () => { await post("limner.save_category", { cat: CAT, fields: { style: $("cStyle").value } }); await load(); });
  $("sSave").onclick = guard(async () => { await post("limner.save_style", { key: c.style, fields: { prefix: $("sPrefix").value, suffix: $("sSuffix").value, lora: +$("sLora").value } }); await load(); });
  $("sNew").onclick = guard(async () => { const k = (prompt("New style key (lowercase word):") || "").trim(); if (!k) return;
    await post("limner.save_style", { key: k, fields: { name: k, prefix: $("sPrefix").value, suffix: $("sSuffix").value, lora: +$("sLora").value } });
    await post("limner.save_category", { cat: CAT, fields: { style: k } }); await load(); });
  $("cSave").onclick = guard(async () => { await post("limner.save_category", { cat: CAT, fields: { prefix: $("cPrefix").value, suffix: $("cSuffix").value,
    gen: { width: +$("gW").value, height: +$("gH").value, steps: +$("gSteps").value } } }); await load(); });
  $("fSave").onclick = guard(async () => {
    const fin = f.kind === "portrait" ? { width: +$("fW").value, thumb: +$("fT").value, crop: [+$("fCx").value, +$("fCy").value, +$("fCs").value] }
      : f.kind === "token" ? { size: +$("fSize").value, margin: +$("fMar").value, normal: +$("fNor").value }
      : { size: +$("fSize").value, colors: +$("fCol").value, margin: +$("fMar").value, outline: $("fOut").value.trim() };
    $("fSave").disabled = true; await post("limner.save_category", { cat: CAT, fields: { finish: fin } }); BUST = Date.now(); await load(); });
  const ex = async (cats) => { const r = await post("limner.export", { cats }); $("exMsg").textContent = "Exported: " + Object.entries(r.exported).map(([k, v]) => `${k} ${v}`).join(", "); };
  $("exCat").onclick = guard(() => ex([CAT])); $("exAll").onclick = guard(() => ex(null));
  document.querySelectorAll(".look textarea[data-l]").forEach(t => t.onchange = guard(async () => { await post("limner.save_look", { archetype: t.dataset.a, look: t.dataset.l, text: t.value.trim() }); await load(); }));
  document.querySelectorAll(".lookDel").forEach(b => b.onclick = guard(async () => { if (!confirmLook(b.dataset.l)) return; await post("limner.save_look", { archetype: b.dataset.a, look: b.dataset.l, text: "" }); await load(); }));
  document.querySelectorAll(".lookAdd").forEach(b => b.onclick = guard(async () => {
    const box = b.closest(".look"), key = box.querySelector(".lookKey").value.trim().toLowerCase(), text = box.querySelector(".lookNew").value.trim();
    if (!key || !text) throw new Error("A look needs a one-word key and a description.");
    await post("limner.save_look", { archetype: b.dataset.a, look: key, text }); await load(); }));
  document.querySelectorAll("textarea[data-r]").forEach(t => t.onchange = guard(async () => { await post("limner.save_race", { key: t.dataset.r, fields: { traits: t.value.trim() } }); await load(); }));
  document.querySelectorAll(".raceAdd").forEach(b => b.onclick = guard(async () => {
    const box = b.closest(".look"), key = box.querySelector(".raceKey").value.trim().toLowerCase(), traits = box.querySelector(".raceNew").value.trim();
    if (!key) throw new Error("A race needs a one-word key (e.g. dwarf).");
    await post("limner.save_race", { key, fields: { traits } }); await load(); }));
}
function confirmLook(l) { return true; }   // its candidates stay on disk; re-adding the key brings them back

// ------------------------------------------------------------------ entries (middle)
let ACTIVE = [];
function shown() {
  const fl = $("filter").value;
  return cat().entries.filter(e => fl === "all" || (fl === "missing" && !e.approved) || (fl === "approved" && e.approved) || (fl === "empty" && !e.candidates.length) || (fl === "new" && e.new));
}
function thumbOf(e) {
  const cid = e.approved || e.candidates[0]?.cid; if (!cid) return null;
  return { src: img(CAT, e.id, cid, FILE()), ghost: !e.approved };
}
function renderGrid() {
  const es = shown();
  $("grid").className = "grid" + (isP() ? " portraits" : isT() ? " tokens" : "");
  let h = "", lastA = null, lastG = null;
  const groups = new Set(cat().entries.map(e => e.group || ""));
  for (const e of es) {
    if (CAT === "portraits" && e.group !== lastA) { lastA = e.group; const a = D.archetypes[e.archetype]; h += `<div class="ahead">${esc(e.group)}<span>${esc(a.classes.join(" · "))}</span></div>`; }
    if (CAT !== "portraits" && groups.size > 1 && (e.group || "") !== lastG) { lastG = e.group || ""; const n = es.filter(x => (x.group || "") === lastG).length; h += `<div class="ahead">${esc(lastG || "Other")}<span>${n}</span></div>`; }
    const t = thumbOf(e), running = ACTIVE.some(j => j.label.startsWith("Limner") && j.label.includes(` · ${CAT} · ${e.id} `));
    h += `<div class="card ${OPEN === e.id ? "sel" : ""} ${running ? "running" : ""}" data-id="${esc(e.id)}">
      <input type="checkbox" class="ck" ${SEL.has(e.id) ? "checked" : ""}>${e.new ? `<span class="newb" title="new candidates">${e.new}</span>` : ""}
      <div class="pic ${t?.ghost ? "ghost" : ""}" style="background:${BG}">${t ? `<img src="${t.src}" loading="lazy">` : `<span class="none">no art</span>`}</div>
      <div class="nm" title="${esc(e.id)}">${esc(e.name)}</div>
      <div class="sb"><span>${esc(e.sub || "")}</span><span class="st ${e.approved ? "ok" : e.candidates.length ? "part" : ""}">${e.approved ? "★" : e.candidates.length || "·"}</span></div></div>`;
  }
  $("grid").innerHTML = h || `<p class="hint">Nothing here with this filter.</p>`;
  $("count").textContent = `${SEL.size} selected · ${es.length} shown`;
  $("grid").querySelectorAll(".card").forEach(el => {
    el.onclick = (ev) => { const id = el.dataset.id;
      if (ev.target.classList.contains("ck")) { ev.target.checked ? SEL.add(id) : SEL.delete(id); $("count").textContent = `${SEL.size} selected · ${shown().length} shown`; return; }
      OPEN = OPEN === id ? null : id; renderGrid(); renderDetail(); };
  });
}
$("filter").onchange = () => renderGrid();
$("selAll").onchange = () => { shown().forEach(e => $("selAll").checked ? SEL.add(e.id) : SEL.delete(e.id)); renderGrid(); };
async function gen(ids, n, extra = {}) {
  if (!ids.length) throw new Error("Nothing to generate: tick some entries first.");
  await post("limner.generate", { cat: CAT, ids, n, ...extra }); pollJobs();
}
$("genSel").onclick = guard(() => gen([...SEL], +$("genN").value));
$("genMissing").onclick = guard(() => gen(cat().entries.filter(e => !e.candidates.length).map(e => e.id), +$("genN").value));

// ------------------------------------------------------------------ batch (overnight)
$("batchBtn").onclick = () => { $("batch").hidden = !$("batch").hidden; if (!$("batch").hidden) renderBatch(); };
$("markRev").onclick = guard(async () => { await post("limner.mark_reviewed", { cat: CAT }); await load(); });
const B = { who: "sel", styles: null, variants: "", n: 2, seeds: "random", seed: 1, plain: true };
function batchIds() { return B.who === "sel" ? [...SEL] : shown().map(e => e.id); }
function renderBatch() {
  if (!B.styles || B.cat !== CAT) { B.styles = [cat().style]; B.cat = CAT; }
  const wd = D.watchdog || {};
  $("batch").innerHTML = `<div class="row" style="margin-bottom:8px"><b>Batch · ${esc(cat().name)}</b>
      <span class="hint">One small job per entry × style × variation, each retried twice if it fails; a watchdog keeps the PC awake and restarts what gets stuck. Review in the morning with Show: new.</span>
      <button style="margin-left:auto" id="bClose">✕</button></div>
    <div class="cols">
      <div><h3>Entries</h3>
        <label style="display:block;color:var(--text)"><input type="radio" name="bw" value="sel" ${B.who === "sel" ? "checked" : ""}> ticked (${SEL.size})</label>
        <label style="display:block;color:var(--text)"><input type="radio" name="bw" value="shown" ${B.who === "shown" ? "checked" : ""}> all shown (${shown().length}, filter: ${esc($("filter").selectedOptions[0].text)})</label>
        <h3 style="margin-top:10px">Candidates per run</h3><input id="bN" type="number" min="1" max="12" value="${B.n}" style="width:60px">
        <h3 style="margin-top:10px">Seeds</h3><select id="bSeeds"><option value="random" ${B.seeds === "random" ? "selected" : ""}>random</option><option value="sweep" ${B.seeds === "sweep" ? "selected" : ""}>sweep from</option></select>
        <input id="bSeed" type="number" value="${B.seed}" style="width:90px" ${B.seeds === "sweep" ? "" : "disabled"}></div>
      <div class="sty"><h3>Styles</h3>${Object.entries(D.styles).map(([k, st]) => `<label><input type="checkbox" value="${k}" ${B.styles.includes(k) ? "checked" : ""}> ${esc(st.name)}${k === cat().style ? ' <span class="hint">(default)</span>' : ""}</label>`).join("")}</div>
      <div><h3>Variations <span class="hint" style="text-transform:none;letter-spacing:0">one per line, added after the subject</span></h3>
        <textarea id="bVar" placeholder="in candlelight&#10;in cold moonlight&#10;battle-worn, scarred">${esc(B.variants)}</textarea>
        <label><input type="checkbox" id="bPlain" ${B.plain ? "checked" : ""}> also the plain subject</label></div>
    </div>
    <div class="row" style="margin-top:8px"><span class="est" id="bEst">…</span><span style="margin-left:auto"></span>
      <button id="bCancel" title="Cancel every queued batch job (the running one finishes)">Cancel queued batch jobs</button>
      <button class="primary" id="bGo">Queue batch</button></div>
    <div class="wd">Watchdog: ${wd.alive ? "running" : "not running (starts with a batch)"}${(wd.log || []).length ? "\n" + wd.log.map(esc).join("\n") : ""}</div>`;
  const read = () => { B.who = document.querySelector('input[name=bw]:checked').value; B.n = +$("bN").value; B.seeds = $("bSeeds").value; B.seed = +$("bSeed").value;
    B.styles = [...$("batch").querySelectorAll(".sty input:checked")].map(i => i.value); B.variants = $("bVar").value; B.plain = $("bPlain").checked; };
  const body = () => { const v = B.variants.split("\n").map(x => x.trim()).filter(Boolean); if (B.plain || !v.length) v.unshift("");
    return { cat: CAT, ids: batchIds(), styles: B.styles, variants: v, n: B.n, seeds: B.seeds, seed: B.seed }; };
  const est = async () => { read(); $("bSeed").disabled = B.seeds !== "sweep";
    if (!batchIds().length || !B.styles.length) { $("bEst").textContent = "Pick entries (tick them, or use all shown) and at least one style."; return false; }
    const e = (await get("limner.plan_batch", body())).estimate;
    $("bEst").textContent = `${e.jobs} jobs · ${e.images} images · about ${e.minutes >= 90 ? (e.minutes / 60).toFixed(1) + " h" : Math.ceil(e.minutes) + " min"} (plus model loading)`; return true; };
  $("batch").querySelectorAll("input, select, textarea").forEach(x => x.onchange = guard(est));
  $("bClose").onclick = () => { $("batch").hidden = true; };
  $("bGo").onclick = guard(async () => { if (!(await est())) return; const r = await post("limner.queue_batch", body());
    $("bEst").textContent = `Queued batch ${r.batch}: ${r.estimate.jobs} jobs, ${r.estimate.images} images. Watchdog: ${r.watchdog === true ? "started" : r.watchdog}.`; pollJobs(); });
  $("bCancel").onclick = guard(async () => { const r = await post("limner.cancel_batch"); $("bEst").textContent = `Cancelled ${r.cancelled} queued batch job(s).`; pollJobs(); });
  guard(est)();
}

// ------------------------------------------------------------------ one entry (right)
function renderDetail() {
  const box = $("detail"), e = OPEN && entry(OPEN);
  $("layout").classList.toggle("open", !!e); box.hidden = !e;
  if (!e) return;
  const fp = e.finish.crop || cat().finish.crop;
  let h = `<div class="row"><h3 style="margin:0">${esc(e.name)}</h3><span class="hint">${esc(e.id)}</span>
      ${e.added ? `<button id="dRemove" style="margin-left:auto" title="Take this added entry off the list (its candidates stay on disk; adding the same id again brings them back)">Remove entry</button>` : ""}
      <button style="${e.added ? "" : "margin-left:auto"}" id="dClose">✕</button></div>
    ${e.text ? `<div class="hint" style="margin-top:4px">${esc(e.text)}</div>` : ""}
    <h3>Subject</h3><textarea id="dSubj" rows="3" placeholder="${esc(e.draft)}">${esc(e.subject)}</textarea>
    <div class="hint">Empty = the draft (shown greyed). What the icon shows; style words are added around it.</div>
    <div class="f"><label>Style</label><select id="dStyle">${styleOpts(e.style, true)}</select></div>
    <div class="f"><label>Seed</label><span><input id="dSeed" type="number" value="${e.seed ?? ""}" placeholder="random" style="width:110px"> <span class="hint">fixed seed: candidates use seed, seed+1…</span></span></div>
    ${isP() ? `<div class="f"><label>Face crop</label><span><input id="dCx" type="number" step="0.01" value="${fp[0]}" style="width:52px"><input id="dCy" type="number" step="0.01" value="${fp[1]}" style="width:52px"><input id="dCs" type="number" step="0.01" value="${fp[2]}" style="width:52px"> <button id="dCrop" title="Clear this portrait's own crop">default</button></span></div>` : ""}
    ${isT() ? `<div class="f"><label>Mirror</label><span><label style="color:var(--text)"><input type="checkbox" id="dMirror" ${e.finish.mirror ? "checked" : ""}> the art faces left (flips every candidate's token; no GPU)</label></span></div>` : ""}
    <div class="row"><button id="dSave">Save</button><label>Candidates</label><input id="dN" type="number" value="${$("genN").value}" min="1" max="12" style="width:52px"><button class="primary" id="dGen">Generate</button></div>
    <div class="ptext" title="The full prompt the next candidate gets">${esc(e.prompt)}</div>
    <h3>Candidates <span class="hint" style="text-transform:none;letter-spacing:0">${e.candidates.length}</span>
      ${isP() ? "" : `<span class="bgs" style="float:right">${["#2a2333", "#0d0b10", "#6b5a44", "#e8e2ef"].map(c => `<button data-bg="${c}" style="background:${c}" title="Preview background"></button>`).join("")}</span>`}</h3>
    <div class="cands">`;
  for (const c of e.candidates) {
    const app = e.approved === c.cid;
    const main = img(CAT, e.id, c.cid, FILE());
    const k = e.candidates.indexOf(c) + 1;
    h += `<div class="cand ${isP() ? "portrait" : isT() ? "token" : ""} ${app ? "app" : ""} ${c.new && !app ? "new" : ""}" title="${esc(c.prompt || "")}">
      <div class="big" data-z="${img(CAT, e.id, c.cid, "master.png")}" style="background:${BG}"><img src="${main}" loading="lazy"></div>
      <div class="sizes">${isP() ? `<img src="${img(CAT, e.id, c.cid, "thumb.png")}" width="64" height="64" style="image-rendering:auto"><img src="${img(CAT, e.id, c.cid, "thumb.png")}" width="32" height="32" style="image-rendering:auto">`
        : `<img src="${main}" width="64" height="64" style="background:${BG}"><img src="${main}" width="32" height="32" style="background:${BG}">`}</div>
      <div class="meta">${k <= 9 ? `<b>${k}</b> · ` : ""}${c.new && !app ? `<span class="nb">new</span> · ` : ""}${esc(D.styles[c.style]?.name || c.style || "")} · seed ${c.seed}${c.kind === "vary" ? " · varied" : ""}${c.extra ? ` · ${esc(c.extra)}` : ""}${c.batch ? ` · ${esc(c.batch)}` : ""}</div>
      <div class="acts">
        <button data-a="approve" data-c="${c.cid}" title="${app ? "Approved: click to unapprove" : "Approve (exported)"}">${app ? "★" : "☆"}</button>
        <button data-a="vary" data-c="${c.cid}" title="3 variations from this one (img2img with the current subject)">Vary</button>
        <button data-a="seed" data-c="${c.cid}" data-s="${c.seed}" title="Use this seed for the entry (tweak words, keep the composition)">Seed</button>
        <button data-a="archive" data-c="${c.cid}" title="Archive (moved to archive/limner)">✕</button></div></div>`;
  }
  h += `</div>${e.candidates.length ? "" : `<p class="hint">No candidates yet.</p>`}
    <div class="f" style="margin-top:8px"><label>Vary strength</label><span><input id="dDen" type="number" step="0.05" min="0.1" max="0.95" value="${localStorage.limnerDen || 0.45}" style="width:62px"> <span class="hint">low = close to the pick</span></span></div>`;
  box.innerHTML = h;
  $("dClose").onclick = () => { OPEN = null; renderGrid(); renderDetail(); };
  if (e.added) $("dRemove").onclick = guard(async () => { await post("limner.remove_entry", { cat: CAT, entry: e.id }); OPEN = null; await load(); });
  const save = async () => {
    const fields = { subject: $("dSubj").value.trim(), style: $("dStyle").value, seed: $("dSeed").value === "" ? null : +$("dSeed").value };
    if (isP()) { const cr = [+$("dCx").value, +$("dCy").value, +$("dCs").value]; if (cr.join() !== fp.join()) { fields.finish = { crop: cr }; BUST = Date.now(); } }
    await post("limner.save_entry", { cat: CAT, entry: e.id, fields });
  };
  $("dSave").onclick = guard(async () => { await save(); await load(); });
  if (isT()) $("dMirror").onchange = guard(async () => { await post("limner.save_entry", { cat: CAT, entry: e.id, fields: { finish: { mirror: $("dMirror").checked || null } } }); BUST = Date.now(); await load(); });
  $("dGen").onclick = guard(async () => { await save(); await gen([e.id], +$("dN").value); await load(); });
  if (isP()) $("dCrop").onclick = guard(async () => { await post("limner.save_entry", { cat: CAT, entry: e.id, fields: { finish: { crop: null } } }); BUST = Date.now(); await load(); });
  $("dDen").onchange = () => localStorage.limnerDen = $("dDen").value;
  box.querySelectorAll("[data-bg]").forEach(b => b.onclick = () => { BG = localStorage.limnerBg = b.dataset.bg; renderGrid(); renderDetail(); });
  box.querySelectorAll(".big").forEach(b => b.onclick = () => { const z = $("zoom"); z.querySelector("img").src = b.dataset.z; z.querySelector("img").className = isP() || isT() ? "" : "px"; z.classList.add("on"); });
  box.querySelectorAll(".acts button").forEach(b => b.onclick = guard(async () => {
    const a = b.dataset.a, cid = b.dataset.c;
    if (a === "approve") await post("limner.approve", { cat: CAT, entry: e.id, candidate: cid, on: e.approved !== cid });
    if (a === "archive") await post("limner.archive", { cat: CAT, entry: e.id, candidate: cid });
    if (a === "seed") await post("limner.save_entry", { cat: CAT, entry: e.id, fields: { seed: +b.dataset.s } });
    if (a === "vary") { await save(); await post("limner.vary", { cat: CAT, entry: e.id, candidate: cid, n: 3, denoise: +$("dDen").value }); pollJobs(); }
    await load(); }));
}
$("zoom").onclick = () => $("zoom").classList.remove("on");
document.addEventListener("keydown", guard(async (ev) => {   // review keys while an entry is open
  if (!OPEN || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "")) return;
  const list = shown(), i = list.findIndex(e => e.id === OPEN), e = entry(OPEN);
  if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
    ev.preventDefault();
    const nx = list[(i + (ev.key === "ArrowRight" ? 1 : -1) + list.length) % list.length];
    if (nx) { OPEN = nx.id; renderGrid(); renderDetail(); document.querySelector(".card.sel")?.scrollIntoView({ block: "nearest" }); }
  } else if (/^[1-9]$/.test(ev.key) && e && e.candidates[+ev.key - 1]) {
    const c = e.candidates[+ev.key - 1];
    await post("limner.approve", { cat: CAT, entry: e.id, candidate: c.cid, on: e.approved !== c.cid }); await load();
  } else if (ev.key === "Escape") { OPEN = null; renderGrid(); renderDetail(); }
}));

// ------------------------------------------------------------------ jobs
let lastDone = null;
const panel = jobsPanel({ onPoll: async (jobs) => {
    ACTIVE = jobs.filter(isActive);
    const done = jobs.filter(j => (j.status === "done" || j.status === "failed") && j.label.startsWith("Limner")).length;
    if (lastDone === null) lastDone = done;
    if (done !== lastDone) { lastDone = done; const f = jobs.filter(j => j.status === "failed" && j.label.startsWith("Limner")).pop(); if (f) err(`${f.label}: ${f.error}`); await load(); }
    else if (D) renderGrid();
} });
const pollJobs = () => panel.poll();
guard(load)(); pollJobs();
