// <imports>
import { $, SEXSYM, guard } from "./common.js";
import { IT, itGet, itRenderCands, itRenderForm, itRenderList, itRenderPrompts } from "./items.js";
import { ZE } from "./zone.js";
// </imports>
// Item Workshop, Outfit tab: the game's slots, one game item each (all its pieces), previewed live.
// ---- outfit: the game's slots, one game item each (all its pieces), live preview
const OF = { pick: {}, ramps: {}, build: "", hair: "", hairRamp: "", walk: false, seq: 0, timer: null };   // pick[slot] = game id ("" = none chosen)
try { Object.assign(OF, JSON.parse(localStorage.getItem("hd.outfit") || "{}")); } catch (e) { }
const ofStore = () => localStorage.setItem("hd.outfit", JSON.stringify({ pick: OF.pick, ramps: OF.ramps, build: OF.build, hair: OF.hair, hairRamp: OF.hairRamp, walk: OF.walk }));
const ofOn = () => document.querySelector('.tab[data-t="outfit"]').classList.contains("on");
const ofBuilds = () => OF.build === "both" ? IT.st.builds : IT.st.builds.includes(OF.build) ? [OF.build] : IT.st.builds.slice(0, 1);
function ofFit(gid, build) {   // the item's pieces this build has an approved fit for, and the ones it lacks
  const sx = build.split(".")[1], ok = [], miss = [];
  for (const p of IT.st.game[gid].pieces) {
    const d = IT.st.spec.items[p], ap = IT.st.approved[p] || {};
    ((d.fit === "per_sex" ? ap[sx] : null) || ap.all ? ok : miss).push(p);
  }
  return { ok, miss };
}
function ofTwoHanded() { const m = OF.pick.main; return m && IT.st.game[m] && IT.st.game[m].hands === 2 ? m : null; }
function ofRender() {
  const st = IT.st, slots = st.vocab.slots || [], builds = ofBuilds();
  $("ofBuild").innerHTML = st.builds.map(b => `<option>${b}</option>`).join("") + (st.builds.length > 1 ? `<option value="both">both</option>` : "");
  $("ofBuild").value = OF.build === "both" || st.builds.includes(OF.build) ? OF.build : st.builds[0] || "";
  $("ofHair").innerHTML = `<option value="">no hair</option>` + st.hair.map(h => `<option>${h}</option>`).join("");
  $("ofHair").value = st.hair.includes(OF.hair) ? OF.hair : "";
  $("ofHairRamp").innerHTML = st.hair_ramps.map(h => `<option>${h}</option>`).join(""); if (st.hair_ramps.includes(OF.hairRamp)) $("ofHairRamp").value = OF.hairRamp;
  $("ofWalk").checked = !!OF.walk;
  const bySlot = {};
  for (const [gid, g] of Object.entries(st.game)) (bySlot[g.slot] ??= []).push({ gid, name: g.name, art: true, wanted: g.wanted });
  for (const [gid, g] of Object.entries(st.noart || {})) (bySlot[g.slot] ??= []).push({ gid, name: g.name, art: false, wanted: g.wanted });
  for (const s of slots) if (OF.pick[s.key] === undefined && (s.artSlots || []).length) {   // first visit: the first item each build can wear
    const first = (bySlot[s.key] || []).find(o => o.art && builds.every(b => ofFit(o.gid, b).ok.length));
    OF.pick[s.key] = first && !(s.key === "off" && ofTwoHanded()) ? first.gid : "";
  }
  const two = ofTwoHanded();
  $("ofSlots").innerHTML = slots.map(s => {
    const label = `<div class="nm">${s.name || s.key}${s.count > 1 ? ` ×${s.count}` : ""}${(s.artSlots || []).length > 1 ? `<span class="hint">${s.artSlots.join(" · ")}</span>` : ""}</div>`;
    if (!(s.artSlots || []).length) return `<div class="ofslot">${label}<div class="hint" style="padding-top:3px">not drawn on the puppet (no art slot)</div></div>`;
    const opts = (bySlot[s.key] || []).sort((a, b) => (b.art - a.art) || a.name.localeCompare(b.name)), cur = OF.pick[s.key];
    const chips = opts.map(o => {
      if (!o.art) return `<span class="chip noart" title="${o.gid}: in the game, no Workshop art yet">${o.name}${o.wanted ? " ★" : ""}</span>`;
      const fits = builds.map(b => ofFit(o.gid, b)), full = fits.every(f => !f.miss.length), none = fits.every(f => !f.ok.length);
      const mark = builds.length > 1 ? builds.map((b, k) => fits[k].miss.length ? "" : SEXSYM[b.split(".")[1]]).join("") : "";
      return `<span class="chip ${cur === o.gid ? "on" : ""} ${none ? "nofit" : ""}" data-ofpick="${s.key}|${o.gid}" title="${o.gid} · pieces: ${st.game[o.gid].pieces.join(", ")}${none ? " · nothing approved for this build yet" : full ? "" : " · some pieces not approved"}">${o.name}<span class="fit ${full ? "" : "part"}">${none ? "·" : full ? (mark || "✓") : "½"}</span></span>`;
    }).join("") || `<span class="hint">no items for this slot</span>`;
    let sub = "", warn = "";
    if (cur && st.game[cur]) {
      const g = st.game[cur];
      sub = Object.entries(g.materials).map(([m, def]) => {
        const grp = st.spec.materials[m].group, on = (OF.ramps[cur] || {})[m] || def;
        return `<div class="ofmat"><span class="hint">${m}</span><span class="ofsws">${Object.entries(st.swatches[grp] || {}).map(([n, hex]) => `<i class="ofsw ${on === `${grp}.${n}` ? "on" : ""}" style="background:${hex}" title="${grp}.${n}" data-ofsw="${cur}|${m}|${grp}.${n}"></i>`).join("")}</span></div>`;
      }).join("");
      warn = builds.map(b => { const f = ofFit(cur, b); return f.miss.length ? `${b}: ${f.miss.map(p => p.replace(/^item\./, "")).join(", ")} not approved` : ""; }).filter(Boolean).join(" · ");
    }
    const blocked = s.key === "off" && two;
    const edit = cur && st.game[cur] ? `<button data-ofedit="${st.game[cur].pieces[0]}" title="Open ${st.game[cur].name} in the Items tab" style="padding:1px 7px;margin-top:6px;font-size:11px">✎ edit</button>` : "";
    return `<div class="ofslot ${blocked ? "blocked" : ""}">${label.replace(/<\/div>$/, edit + "</div>")}<div class="chips">${chips}</div>
      ${blocked ? `<div class="warn">both hands hold ${st.game[two].name}</div>` : ""}${sub ? `<div class="sub">${sub}</div>` : ""}${warn ? `<div class="warn">${warn}</div>` : ""}</div>`;
  }).join("");
  $("ofSlots").querySelectorAll("[data-ofpick]").forEach(c => c.onclick = () => {
    const [slot, gid] = c.dataset.ofpick.split("|"); OF.pick[slot] = OF.pick[slot] === gid ? "" : gid;
    if (slot === "main" && ofTwoHanded()) OF.pick.off = "";
    ofChanged();
  });
  $("ofSlots").querySelectorAll("[data-ofsw]").forEach(c => c.onclick = () => { const [gid, m, r] = c.dataset.ofsw.split("|"); (OF.ramps[gid] ??= {})[m] = r; ofChanged(); });
  $("ofSlots").querySelectorAll("[data-ofedit]").forEach(b => b.onclick = () => {
    IT.sel = b.dataset.ofedit; ZE.loaded = null; itRenderList(); itRenderForm(); itRenderCands(); itRenderPrompts();
    document.querySelector('.tab[data-t="items"]').click(); });
  if (ofOn()) ofQueue();
}
function ofChanged() { ofStore(); ofRender(); }
function ofQueue() { clearTimeout(OF.timer); OF.timer = setTimeout(guard(ofRun), 120); }
function ofBody(build) {   // the outfit request of one build: every piece of the worn items, coloured per item
  const items = [], ramps = {}, two = ofTwoHanded();
  for (const [slot, gid] of Object.entries(OF.pick)) {
    if (!gid || !IT.st.game[gid] || (slot === "off" && two)) continue;
    const mats = { ...IT.st.game[gid].materials, ...(OF.ramps[gid] || {}) };
    for (const p of IT.st.game[gid].pieces) { items.push(p); ramps[p] = mats; }
  }
  return { build, items, ramps, hair: OF.hair || null, hair_ramp: OF.hairRamp || $("ofHairRamp").value };
}
async function ofRun() {
  const seq = ++OF.seq, builds = ofBuilds(), t0 = performance.now();
  if (!builds.length) { $("ofOut").innerHTML = `<p class="hint">No build has a paint yet.</p>`; return; }
  $("ofStatus").textContent = "rendering…";
  const rest = await Promise.all(builds.map(b => itGet("items.outfit", { ...ofBody(b), part: "rest" })));
  if (seq !== OF.seq) return;   // a newer change is already on its way
  $("ofOut").innerHTML = `<div class="ofcols ${OF.walk ? "walk" : ""}">` + builds.map((b, k) => `<div class="ofcol">${["front", "back"].map(v =>
    `<div class="ofview"><figure><img src="${rest[k][v]}"><figcaption>${b} ${v}</figcaption></figure><figure id="ofWalk_${k}_${v}"></figure></div>`).join("")}</div>`).join("") + "</div>";
  const worn = [...new Set(rest.flatMap(r => r.worn.map(w => w[0].replace(/^item\./, ""))))];
  $("ofStatus").textContent = `${Math.round(performance.now() - t0)} ms${rest.every(r => r.cached) ? " (cached)" : ""} · pieces: ${worn.join(", ") || "none"}`;
  if (!OF.walk) return;
  const walk = await Promise.all(builds.map(b => itGet("items.outfit", { ...ofBody(b), part: "walk" })));
  if (seq !== OF.seq) return;
  builds.forEach((b, k) => ["front", "back"].forEach(v =>
    $(`ofWalk_${k}_${v}`).innerHTML = `<img src="${walk[k]["walk_" + v]}"><figcaption>${b} ${v} walk</figcaption>`));
}
$("ofBuild").onchange = () => { OF.build = $("ofBuild").value; ofChanged(); };
$("ofHair").onchange = () => { OF.hair = $("ofHair").value; ofChanged(); };
$("ofHairRamp").onchange = () => { OF.hairRamp = $("ofHairRamp").value; ofChanged(); };
$("ofWalk").onchange = () => { OF.walk = $("ofWalk").checked; ofChanged(); };
$("ofClear").onclick = () => { for (const k of Object.keys(OF.pick)) OF.pick[k] = ""; ofChanged(); };
document.querySelector('.tab[data-t="outfit"]').addEventListener("click", () => { if (IT.st) ofQueue(); });

// <exports>
export { OF, ofBody, ofBuilds, ofChanged, ofFit, ofOn, ofQueue, ofRender, ofRun, ofStore, ofTwoHanded };
// </exports>
