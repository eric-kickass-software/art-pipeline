// The Item Workshop page (/items): the Items tab (catalogue, settings, generate, candidates); the zone editor,
// grip and outfit live in zone.js, grip.js and outfit.js.
import { get, post, url } from "../api.js";
import "./outfit.js";
// <imports>
import { $, SEXSYM, guard, img, onJobsDone, openInEditor, pollJobs, queue, startPage, wantedTab } from "./common.js";
import { GR, gripDef, gripRender } from "./grip.js";
import { ofRender } from "./outfit.js";
import { ZE, zeLoad } from "./zone.js";
// </imports>
const IT = { st: null, sel: null, race: "human" };
// the race-scoped Item Workshop ops: the page's race goes with every call
const itGet = (name, b = {}) => get(name, { race: IT.race, ...b });
const itPost = (name, b = {}) => post(name, { race: IT.race, ...b });
const itDef = () => IT.st.spec.items[IT.sel] || {};
const itHeld = () => itDef().kind === "held";

async function itRefresh() {
  IT.st = await itGet("items.state");
  const ids = Object.keys(IT.st.spec.items);
  if (!IT.sel || !ids.includes(IT.sel)) IT.sel = ids[0] || null;
  const opts = IT.st.builds.map(b => `<option>${b}</option>`).join("");
  for (const id of ["zeBuild", "itBuild"]) { const el = $(id), v = el.value; el.innerHTML = opts; if (IT.st.builds.includes(v)) el.value = v; }
  itRenderList(); itRenderForm(); itRenderCands(); itRenderPrompts(); ofRender();
}
function itFitsText(id) {
  const ap = IT.st.approved[id] || {}, keys = Object.keys(ap);
  return keys.length ? keys.map(k => SEXSYM[k] || k).join(" ") : "";
}
function itRenderList() {
  const items = IT.st.spec.items, slots = {};
  for (const [id, d] of Object.entries(items)) (slots[d.slot || "other"] ??= []).push(id);
  $("itList").innerHTML = Object.entries(slots).map(([s, ids]) => `<div class="hint" style="margin-top:8px;text-transform:uppercase;letter-spacing:1px">${s}</div>` +
    ids.map(id => `<div class="tpl ${id === IT.sel ? "on" : ""}" data-it="${id}"><span style="flex:1">${id.replace(/^item\./, "")}${items[id].item && items[id].item !== id ? ` <span class="hint">→ ${items[id].item.replace(/^item\./, "")}</span>` : ""}${items[id].kind === "held" ? ' <span class="hint">held</span>' : ""}${items[id].fit === "per_sex" ? ' <span class="hint">♂♀</span>' : ""}</span>
      <span style="color:var(--ok)">${itFitsText(id)}</span> <span class="hint">${(IT.st.candidates[id] || []).length}</span></div>`).join("")).join("");
  $("itList").querySelectorAll("[data-it]").forEach(r => r.onclick = () => { IT.sel = r.dataset.it; ZE.loaded = null; itRenderList(); itRenderForm(); itRenderCands(); itRenderPrompts(); zeLoad(); });
}
function itRenderForm() {
  const d = itDef(); if (!IT.sel) return;
  $("itTitle").textContent = IT.sel; $("itFits").textContent = itFitsText(IT.sel) ? `approved: ${itFitsText(IT.sel)}` : "nothing approved yet";
  const slots = IT.st.vocab.artSlots, cur = d.slot || "";
  $("itSlot").innerHTML = (slots.includes(cur) || !cur ? "" : `<option value="${cur}">${cur} (not a game slot)</option>`) + slots.map(s => `<option>${s}</option>`).join("");
  $("itSlotHint").textContent = IT.st.vocab.found ? `the game's layer slots (requirements.json ${IT.st.vocab.revision || ""})` : "no requirements.json found: built-in slots";
  itRenderDesign();
  $("itGame").value = d.item || ""; $("itGameIds").innerHTML = IT.st.vocab.known.map(i => `<option>${i}</option>`).join(""); itGameHint();
  $("itId").value = IT.sel; $("itSlot").value = cur; $("itKind").value = d.kind || ""; $("itFit").value = d.fit || "shared";
  $("itText").value = d.text || ""; $("itDepth").value = d.depth ?? 3; $("itBulk").value = d.bulk ?? 6;
  $("itClipHair").checked = (d.clips || []).includes("hair"); $("itComplete").checked = d.complete !== false;
  $("itHand").value = d.hand || "armB"; $("itLength").value = d.length ?? 0.48; $("itGripAngle").value = $("itGripAngleR").value = d.grip_angle ?? 0;
  $("itHeldAs").value = d.held_as || "weapon";
  $("itFlip").checked = !!d.flip; GR.at = d.grip_at || null; if (itHeld()) gripRender();
  $("itMats").innerHTML = Object.entries(IT.st.spec.materials).map(([m, e]) => `<label style="display:flex;align-items:center;gap:4px">
    <input type="checkbox" data-mat="${m}" style="width:auto" ${(d.materials || []).includes(m) ? "checked" : ""}>
    <i style="width:14px;height:14px;border-radius:3px;background:${e.key};display:inline-block"></i>${m}</label>`).join("");
  document.querySelectorAll(".itfit").forEach(el => el.style.display = itHeld() ? "none" : "");
  document.querySelectorAll(".itheld").forEach(el => el.style.display = itHeld() ? "" : "none");
}
$("itSave").onclick = guard(async () => {
  const held = $("itKind").value === "held", num = (id) => $(id).value === "" ? null : +$(id).value;
  const def = { slot: $("itSlot").value, item: $("itGame").value.trim() || null, kind: held ? "held" : null, fit: $("itFit").value, text: $("itText").value.trim(),
    materials: [...document.querySelectorAll("[data-mat]")].filter(c => c.checked).map(c => c.dataset.mat), depth: num("itDepth"),
    design: $("itDesign").value || null };
  if (held) Object.assign(def, gripDef());
  else Object.assign(def, { bulk: num("itBulk"), clips: $("itClipHair").checked ? ["hair"] : null, complete: $("itComplete").checked });
  const id = $("itId").value.trim();
  IT.st = await itPost("items.save_item", { item: id, old_id: IT.sel, definition: def }); IT.sel = id;
  $("itSaved").textContent = "saved"; setTimeout(() => $("itSaved").textContent = "", 1500); await itRefresh();
});
// design reference: the approved Limner gear art the item copies (auto = its game item's icon, else the slot x material
// or weapon-type fallback); shown as a thumbnail, previewed live while picking, saved with the item
const itDesignImg = (eid) => {
  const cid = IT.st.designChoices[eid], im = $("itDesignImg");
  im.style.display = cid ? "" : "none"; if (cid) { im.src = url("limner.image", { path: `gear/${eid}/${cid}/master.png` }); im.title = eid; }
};
function itRenderDesign() {
  const d = itDef(), auto = IT.st.design[IT.sel], set = d.design || "";
  const autoLbl = d.design ? "auto" : `auto: ${auto ? auto.entry + " (" + auto.why + ")" : "nothing approved"}`;
  $("itDesign").innerHTML = `<option value="">${autoLbl}</option><option value="none">none (prompt only)</option>` +
    Object.keys(IT.st.designChoices).map(e => `<option>${e}</option>`).join("");
  $("itDesign").value = set; itDesignChange();
}
function itDesignChange() {
  const v = $("itDesign").value, auto = IT.st.design[IT.sel] && !itDef().design ? IT.st.design[IT.sel] : null;
  const eid = v === "none" ? null : v || (auto && auto.entry);
  itDesignImg(eid);
  $("itDesignHint").textContent = !eid ? "no reference: FLUX paints from the description alone"
    : (itHeld() ? "redrawn upright in the colour keys" : "front pass copies its cut, trims, straps, plates") + " · save to keep";
}
$("itDesign").onchange = () => itDesignChange();
function itGameHint() {
  const gid = $("itGame").value.trim() || IT.sel, g = IT.st.game[gid];
  const pieces = g ? g.pieces.map(p => p.replace(/^item\./, "")).join(" + ") : "";
  $("itGameHint").innerHTML = !g ? (gid === IT.sel ? "" : `${gid}: save to make it a game item`)
    : (g.exists ? `<span style="color:var(--ok)">in the game</span>: art only` : `<b>new</b>: the export writes an intake file`) +
      ` · ${g.slot || "?"}${g.weaponType ? " / " + g.weaponType : ""} · ${pieces}` +
      (g.problems.length ? ` · <span style="color:var(--bad, #e05050)">${g.problems.join("; ")}</span>` : "");
}
$("itGame").oninput = () => itGameHint();
async function itExport(items) {
  $("itExportOut").textContent = "exporting…";
  const r = await itPost("items.export", { items });
  $("itExportOut").textContent = [...Object.entries(r.exported).map(([g, e]) => `✓ ${g}: ${e.builds.join(", ")}${e.intake ? " + intake file" : ""}${e.warnings.map(w => "\n  ! " + w).join("")}`),
    ...Object.entries(r.skipped).map(([g, why]) => `✗ ${g}: ${why}`), `index.json: ${r.index} packs`].join("\n");
}
$("itExport").onclick = guard(async () => itExport([itDef().item || IT.sel]));
$("itExportAll").onclick = guard(async () => itExport(null));
$("itNew").onclick = guard(async () => {
  let id = $("itNewId").value.trim(); if (!id) throw new Error("Type an id first, e.g. item.leather_vest");
  if (!id.startsWith("item.")) id = "item." + id;
  if (IT.st.spec.items[id]) throw new Error(`${id} already exists`);
  await itPost("items.save_item", { item: id, definition: { slot: "shirt", text: "", materials: ["cloth"], depth: 3, bulk: 6 } });
  IT.sel = id; $("itNewId").value = ""; ZE.loaded = null; await itRefresh(); zeLoad();
});

// ---- prompts next to Generate
async function itRenderPrompts() {
  const sp = IT.st.spec;
  if (document.activeElement?.closest?.("details") == null) {
    $("ipFront").value = sp.prompt || ""; $("ipBack").value = sp.prompt_back || ""; $("ipComplete").value = sp.prompt_complete || "";
    $("ipHeld").value = sp.prompt_held || ""; $("ipHeldShield").value = sp.prompt_held_shield || ""; $("ipHeldFocus").value = sp.prompt_held_focus || ""; $("ipMat").value = sp.material_text || "";
    $("ipDesign").value = sp.prompt_design || ""; $("ipHeldDesign").value = sp.prompt_held_design || ""; }
  if (!IT.sel || !$("itBuild").value) return;
  try { const P = await itGet("items.prompts", { item: IT.sel, build: $("itBuild").value });
    const dz = IT.st.design[IT.sel];
    $("itPrompt").textContent = itHeld() ? (dz ? `Held, from ${dz.entry}: ${P.held_design}` : `Held: ${P.held}`)
      : `Front${dz ? ` (+ ${dz.entry} as reference)` : ""}: ${P.front}${dz ? P.design : ""}\n\nBack (front as reference): ${P.back}` +
      (itDef().complete !== false ? `\n\nHidden areas: ${P.complete}` : ""); } catch (e) { $("itPrompt").textContent = e.message; }
}
$("itBuild").onchange = () => itRenderPrompts();
$("itBatch").onclick = guard(async () => {   // two clicks: the first shows the plan, the second queues it
  const n = +$("itN").value, b = $("itBatch");
  if (!b.dataset.armed) {
    const p = await itPost("items.queue_design_batch", { n, dry_run: true });
    $("itBatchOut").textContent = `${p.added.length} new pieces (${p.added.map(i => i.replace(/^item\./, "")).join(", ") || "none"}), ` +
      `${p.jobs} jobs over ${p.items.length} pieces` + (Object.keys(p.skipped).length ? ` · skipped: ${Object.entries(p.skipped).map(([i, w]) => i.replace(/^item\./, "") + " (" + w + ")").join(", ")}` : "") + ". Click again to queue.";
    b.dataset.armed = 1; b.textContent = `Queue ${p.jobs} jobs`; setTimeout(() => { delete b.dataset.armed; b.textContent = "Queue all Limner gear"; }, 15000); return;
  }
  delete b.dataset.armed; b.textContent = "Queue all Limner gear";
  const r = await itPost("items.queue_design_batch", { n });
  $("itBatchOut").textContent = `queued ${r.jobs} jobs (batch ${r.batch}), ${r.added.length} new pieces; watchdog ${r.watchdog}`;
  await itRefresh(); pollJobs();
});
$("itSets").onclick = guard(async () => {   // two clicks, like Queue all Limner gear
  const n = +$("itN").value, b = $("itSets"), short = i => i.replace(/^item\./, "");
  if (!b.dataset.armed) {
    const p = await itPost("items.queue_gear_sets", { n, dry_run: true });
    if (!p.jobs) { $("itBatchOut").textContent = "Every set piece already exists" + (Object.keys(p.skipped).length ? ` · skipped: ${Object.entries(p.skipped).map(([i, w]) => short(i) + " (" + w + ")").join(", ")}` : "") + "."; return; }
    $("itBatchOut").textContent = `${p.added.length} new set pieces (${p.added.map(short).join(", ")}), ${p.jobs} jobs` +
      (Object.keys(p.skipped).length ? ` · skipped: ${Object.entries(p.skipped).map(([i, w]) => short(i) + " (" + w + ")").join(", ")}` : "") + ". Click again to queue.";
    b.dataset.armed = 1; b.textContent = `Queue ${p.jobs} jobs`; setTimeout(() => { delete b.dataset.armed; b.textContent = "Queue gear sets"; }, 15000); return;
  }
  delete b.dataset.armed; b.textContent = "Queue gear sets";
  const r = await itPost("items.queue_gear_sets", { n });
  $("itBatchOut").textContent = `queued ${r.jobs} jobs (batch ${r.batch}), new pieces: ${r.added.map(short).join(", ")}; watchdog ${r.watchdog}`;
  await itRefresh(); pollJobs();
});
$("ipSave").onclick = guard(async () => {
  await itPost("items.save_prompts", { prompts: { prompt: $("ipFront").value, prompt_back: $("ipBack").value, prompt_complete: $("ipComplete").value,
    prompt_held: $("ipHeld").value, prompt_held_shield: $("ipHeldShield").value, prompt_held_focus: $("ipHeldFocus").value, material_text: $("ipMat").value,
    prompt_design: $("ipDesign").value, prompt_held_design: $("ipHeldDesign").value } }); await itRefresh(); });
$("itGen").onclick = guard(async () => {
  if (!itHeld() && ZE.dirty) throw new Error("Save the zone first (or undo your strokes).");
  if (itHeld() && ($("itHeldAs").value !== (itDef().held_as || "weapon") || $("itText").value.trim() !== (itDef().text || "")))
    throw new Error("Save the item first: Generate uses the saved description and held type.");
  const n = +$("itN").value, seed = +$("itSeed").value, build = $("itBuild").value;
  await queue([{ kind: "item", action: "generate", race: IT.race, item: IT.sel, build, n, seed, label: `${IT.sel} · ${build} ×${n} (s${seed})` }]);
  $("itSeed").value = seed + n;
});

// ---- candidates
function itRenderCands() {
  const cands = IT.st.candidates[IT.sel] || [], ap = IT.st.approved[IT.sel] || {}, perSex = itDef().fit === "per_sex";
  const keys = perSex ? ["male", "female"] : ["all"];
  $("itCands").innerHTML = cands.map(c => {
    const t = `&_t=${c.stamp}`, on = keys.filter(k => ap[k] === c.id);
    const prev = ["male_front", "male_back", "female_front", "female_back"].map(k => c.prev[k] ? `<img src="${img(c.prev[k]) + t}" title="${k.replace("_", " ")}">` : "").join("");
    return `<div class="itc ${on.length ? "on" : ""}">
      <div class="row" style="margin:0 0 4px"><b>${c.id}</b><span class="hint">on ${c.on || "?"}${c.completed ? " · FLUX hidden areas" : ""}</span></div>
      <div class="itcimgs">${c.paint ? `<img class="paint" src="${img(c.paint) + t}" title="what FLUX painted">` : ""}${prev}</div>
      <div class="row" style="margin:4px 0 0">${keys.map(k => `<button data-ap="${c.id}" data-k="${k}" class="${ap[k] === c.id ? "on" : ""}">${ap[k] === c.id ? "✓ " : ""}${k === "all" ? "Approve" : "Approve for " + k}</button>`).join("")}
        <button data-walk="${c.id}">Walk</button>
        ${itHeld() ? "" : `<button data-ed="${c.id}" title="paint and touch up this candidate's layers, part by part">Edit layers${c.edited ? " (edited)" : ""}</button>`}
        ${itHeld() ? "" : `<button data-rk="${c.id}" title="re-run the extraction from the saved paint (zone / keying changes); no GPU">Re-extract</button>
        <button data-rc="${c.id}" title="re-extract and run the FLUX pass for the hidden joint areas (GPU)">Re-extract + FLUX hidden</button>`}
        <button data-ar="${c.id}" style="margin-left:auto">Archive</button></div>
      <div class="itwalk" id="walk_${c.id}"></div></div>`;
  }).join("") || `<p class="hint">No candidates yet: paint the zone, then Generate.</p>`;
  const q = (s) => $("itCands").querySelectorAll(s);
  q("[data-ap]").forEach(b => b.onclick = guard(async () => {
    const was = (IT.st.approved[IT.sel] || {})[b.dataset.k] === b.dataset.ap;
    await itPost("items.approve", { item: IT.sel, candidate: b.dataset.ap, key: b.dataset.k, on: !was }); await itRefresh(); }));
  q("[data-ar]").forEach(b => b.onclick = guard(async () => { await itPost("items.archive", { item: IT.sel, candidate: b.dataset.ar }); await itRefresh(); }));
  q("[data-ed]").forEach(b => b.onclick = () => openInEditor({ race: IT.race, item: IT.sel, id: b.dataset.ed }));
  q("[data-rk]").forEach(b => b.onclick = guard(async () => queue([{ kind: "item", action: "rekey", race: IT.race, item: IT.sel, id: b.dataset.rk, label: `${IT.sel} · ${b.dataset.rk} · re-extract` }])));
  q("[data-rc]").forEach(b => b.onclick = guard(async () => queue([{ kind: "item", action: "rekey", complete: true, race: IT.race, item: IT.sel, id: b.dataset.rc, label: `${IT.sel} · ${b.dataset.rc} · re-extract + FLUX hidden` }])));
  q("[data-walk]").forEach(b => b.onclick = guard(async () => {
    const box = $(`walk_${b.dataset.walk}`); box.innerHTML = `<span class="hint">rendering…</span>`;
    const rs = await Promise.all(IT.st.builds.map(build => itGet("items.outfit", { build, items: [IT.sel], candidates: { [IT.sel]: b.dataset.walk }, part: "walk" })));
    box.innerHTML = rs.map((r, k) => `<figure><img src="${r.walk_front}"><figcaption>${IT.st.builds[k]} front</figcaption></figure><figure><img src="${r.walk_back}"><figcaption>${IT.st.builds[k]} back</figcaption></figure>`).join("");
  }));
}
// ------------------------------------------------------------------ the page
startPage("items");
onJobsDone.push(itRefresh);   // a finished job refreshes the item lists
guard(async () => { await itRefresh(); await zeLoad(); })();
document.querySelector('.tab[data-t="items"]').addEventListener("click", () => guard(zeLoad)());
wantedTab().click();

// <exports>
export { IT, itDef, itDesignChange, itDesignImg, itExport, itFitsText, itGameHint, itGet, itHeld, itPost, itRefresh, itRenderCands, itRenderDesign, itRenderForm, itRenderList, itRenderPrompts };
// </exports>
