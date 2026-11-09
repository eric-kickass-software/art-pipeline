// Character Workshop, Ramps tab: skin / hair / eye ramps (specs/palettes.json) and the race's colour set,
// previewed on the race's painted puppets.
import { get, post } from "../api.js";
import { stripHtml } from "../ramp-chips.js";
import { MONK, TUNE0, SLIDERS, hex2rgb, rgb2hex, rampFrom } from "../ramps.js";
// <imports>
import { $, guard } from "./common.js";
// </imports>

const RS = { doc:null, group:"skin", name:null, base:"#c98d63", tune:{...TUNE0}, over:{}, prev:null, imgs:{}, inited:false };
function curRamp() { const size = RS.doc ? RS.doc.rampSize : 5; const r = rampFrom(RS.base, RS.tune, size);
  return r.map((c,i) => RS.over[i] ? hex2rgb(RS.over[i]) : c); }
function rampOf(e) { const size = RS.doc.rampSize; return e.colors ? e.colors.map(hex2rgb) : rampFrom(e.base, {...TUNE0, ...(e.tune||{})}, size); }
async function initRamps() {
  if (!RS.inited) {
    RS.inited = true;
    $("rMonk").innerHTML = MONK.map((h,i) => `<span style="background:${h};color:${i>4?'#fffa':'#000a'}" data-h="${h}" title="Monk ${i+1} ${h}">${i+1}</span>`).join("");
    $("rMonk").onclick = e => { const h = e.target.dataset.h; if (h) { RS.base = h; drawRamp(); } };
    $("rSliders").innerHTML = SLIDERS.map(([k,l,a,b,st]) => `<div class="sl"><span>${l}</span><input type="range" min="${a}" max="${b}" step="${st}" data-k="${k}"><span data-v="${k}"></span></div>`).join("");
    document.querySelectorAll("#rSliders input").forEach(inp => inp.oninput = () => { RS.tune[inp.dataset.k] = +inp.value; drawRamp(); });
    $("rBase").oninput = () => { RS.base = $("rBase").value; drawRamp(); };
    $("rBaseHex").onchange = () => { if (/^#[0-9a-f]{6}$/i.test($("rBaseHex").value)) { RS.base = $("rBaseHex").value.toLowerCase(); drawRamp(); } };
    $("rReset").onclick = () => { RS.tune = {...TUNE0}; RS.over = {}; drawRamp(); };
    $("rGroup").onchange = () => { RS.group = $("rGroup").value; RS.name = null; $("rName").value = ""; listRamps(); };
    $("rRace").onchange = guard(loadPreview);
    $("rNew").onclick = () => { RS.name = null; $("rName").value = "new_tone"; RS.base = "#c98d63"; RS.tune = {...TUNE0}; RS.over = {}; $("rRaces").value = ""; listRamps(); drawRamp(); };
    $("rDup").onclick = () => { RS.name = null; $("rName").value = ($("rName").value || "tone") + "_copy"; listRamps(); };
    $("rDel").onclick = guard(async () => { if (!RS.name || !confirm(`Delete ${RS.group}.${RS.name}?`)) return;
      RS.doc = await post("character.delete_ramp", { group: RS.group, name: RS.name }); RS.name = null; $("rName").value = ""; listRamps(); });
    $("rSave").onclick = guard(saveRamp);
    $("rPick").oninput = () => { RS.over[RS.pick] = $("rPick").value; drawRamp(); };
    const setTag = (on) => guard(async () => {   // add / remove the race on the ramp being edited, then save it
      const race = $("rRace").value, races = new Set($("rRaces").value.split(",").map(x => x.trim()).filter(Boolean));
      on ? races.add(race) : races.delete(race); $("rRaces").value = [...races].join(", "); await saveRamp(); });
    $("rSetAdd").onclick = setTag(true); $("rSetDel").onclick = setTag(false);
    $("rSetMake").onclick = guard(async () => {
      const race = $("rRace").value, size = RS.doc.rampSize || 5, have = RS.doc.groups.skin || {};
      const picks = $("rMonkPick").value.split(",").map(x => +x.trim()).filter(n => n >= 1 && n <= 10);
      if (!picks.length) throw new Error("List Monk tone numbers 1-10, e.g. 1,3,5,7,9.");
      let made = 0;
      for (const i of picks) { const name = `${race}_monk${i}`; if (have[name]) continue;
        RS.doc = await post("character.save_ramp", { group: "skin", name, base: MONK[i - 1], tune: {...TUNE0},
          colors: rampFrom(MONK[i - 1], TUNE0, size).map(rgb2hex), races: [race] }); made++; }
      RS.group = "skin"; $("rGroup").value = "skin"; listRamps(); $("rStatus").textContent = ` made ${made} ramp${made === 1 ? "" : "s"} for ${race}`; });
  }
  RS.doc = await get("character.palettes");
  $("rGroup").innerHTML = Object.keys(RS.doc.groups).map(g => `<option ${g===RS.group?"selected":""}>${g}</option>`).join("");
  const races = Object.keys((await get("character.race", { race: $("rRace").value || "human" })).spec.races);
  const keep = $("rRace").value; $("rRace").innerHTML = races.map(r => `<option>${r}</option>`).join(""); if (keep) $("rRace").value = keep;
  listRamps(); await loadPreview();
}
function listRamps() {
  const grp = RS.doc.groups[RS.group] || {};
  if (RS.name === null && $("rName").value === "") { const first = Object.keys(grp)[0]; if (first) { selectRamp(first); return; } }
  $("rList").innerHTML = Object.entries(grp).map(([n,e]) => `<div class="ritem ${n===RS.name?"on":""}" data-n="${n}"><span class="nm">${n}</span>
    ${stripHtml(rampOf(e))}
    <span class="hint">${(e.races||[]).join(", ")}</span></div>`).join("") || `<p class="hint">No ramps in this group.</p>`;
  document.querySelectorAll("#rList .ritem").forEach(el => el.onclick = () => selectRamp(el.dataset.n));
  drawAll();
}
function selectRamp(n) {
  const e = RS.doc.groups[RS.group][n]; RS.name = n; $("rName").value = n;
  RS.base = e.base || (e.colors ? e.colors[Math.floor(e.colors.length/2)] : "#c98d63");
  RS.tune = {...TUNE0, ...(e.tune || {})}; RS.over = {};
  if (e.colors) { const gen = rampFrom(RS.base, RS.tune, RS.doc.rampSize).map(rgb2hex);   // hand-set colours = overrides
    e.colors.forEach((c,i) => { if (c.toLowerCase() !== gen[i]) RS.over[i] = c; }); }
  $("rRaces").value = (e.races || []).join(", ");
  listRamps(); drawRamp();
}
function drawRamp() {
  $("rBase").value = RS.base; $("rBaseHex").value = RS.base;
  document.querySelectorAll("#rSliders input").forEach(inp => { inp.value = RS.tune[inp.dataset.k];
    document.querySelector(`[data-v="${inp.dataset.k}"]`).textContent = (+RS.tune[inp.dataset.k]).toFixed(3); });
  const r = curRamp();
  $("rSw").innerHTML = r.map((c,i) => `<div><span class="${RS.over[i]?"ovr":""}" data-i="${i}" style="background:${rgb2hex(c)}"></span>${rgb2hex(c)}</div>`).join("");
  document.querySelectorAll("#rSw span").forEach(sp => {
    sp.onclick = () => { RS.pick = +sp.dataset.i; $("rPick").value = rgb2hex(r[RS.pick]); $("rPick").click(); };
    sp.oncontextmenu = ev => { ev.preventDefault(); delete RS.over[+sp.dataset.i]; drawRamp(); }; });
  drawPreview(); drawAll();
}
async function loadPreview() {   // the race's painted male and female puppets (approved Paint, else the newest), front and back
  const race = $("rRace").value; if (!race) return;
  RS.imgs = {}; const src = [];
  const load = s_ => new Promise((ok, no) => { const im = new Image(); im.onload = () => ok(im); im.onerror = no; im.src = s_; });
  for (const sex of ["male", "female"]) {
    const pv = await get("character.face_ramp_preview", { build: `${race}.${sex}` });
    src.push(pv.id ? `${sex}: ${pv.id}${pv.approved ? " (approved)" : " (newest, not approved)"}` : `${sex}: no paint yet (Race → Paint)`);
    if (sex === "male") RS.wear = `hair ${pv.hair || "none"} · face ${pv.face || "none"}`;
    for (const [f, v] of Object.entries(pv.facings)) RS.imgs[`${sex}|${f}`] = { art: await load(v.art), lev: await load(v.levels), key: v.key,
      hair: v.hair ? await load(v.hair) : null, eyes: v.eyes ? await load(v.eyes) : null };
  }
  $("rSrc").textContent = src.join(" · ") + (RS.wear ? ` · wearing ${RS.wear} (Face tab)` : "");
  $("rKey").textContent = ["male", "female"].filter(s_ => RS.imgs[`${s_}|front`]).map(s_ => `${s_} skin levels found: ${RS.imgs[`${s_}|front`].key.map(rgb2hex).join(" ")}`).join(" · ");
  $("rSetName").textContent = race[0].toUpperCase() + race.slice(1);
  drawPreview(); drawAll();
}
function setRamp(group, race) {   // the race's first ramp of a group (else the group's first): the default for the other layers
  const grp = (RS.doc && RS.doc.groups[group]) || {}, ent = Object.entries(grp);
  const mine = ent.find(([n, e]) => (e.races || []).includes(race)) || ent[0];
  return mine ? rampOf(mine[1]) : null;
}
function rampsFor(over) {   // {skin, hair, eyes} ramps: the one being edited for its own group, the set's first for the others
  const race = $("rRace").value, out = {};
  for (const g of ["skin", "hair", "eyes"]) out[g] = setRamp(g, race);
  if (RS.group in out) out[RS.group] = curRamp();
  return Object.assign(out, over || {});
}
function levelData(im, W, H) { if (!im) return null; const l = document.createElement("canvas"); l.width = W; l.height = H;
  const lx = l.getContext("2d"); lx.drawImage(im, 0, 0, W, H); return lx.getImageData(0, 0, W, H).data; }
function recolor(k, ramps) {   // a painted puppet: skin, hair and iris levels each coloured from its ramp (an array = skin only)
  if (Array.isArray(ramps)) ramps = rampsFor({ skin: ramps });
  const I = RS.imgs[k], W = I.art.naturalWidth, H = I.art.naturalHeight;
  const c = document.createElement("canvas"); c.width = W; c.height = H; const x = c.getContext("2d"); x.drawImage(I.art, 0, 0);
  const d = x.getImageData(0, 0, W, H);
  const paint = (data, ramp, skip254) => { if (!data || !ramp) return;
    for (let p = 0; p < data.length; p += 4) if (data[p+3] > 0 && !(skip254 && data[p] === 254)) {
      const col = ramp[Math.min(ramp.length-1, data[p])]; d.data[p]=col[0]; d.data[p+1]=col[1]; d.data[p+2]=col[2]; } };
  paint(levelData(I.lev, W, H), ramps.skin); paint(levelData(I.hair, W, H), ramps.hair, true); paint(levelData(I.eyes, W, H), ramps.eyes);
  x.putImageData(d, 0, 0); return c;
}
function onBg(src, n) { const c = document.createElement("canvas"); c.width = n; c.height = n; const x = c.getContext("2d");
  x.fillStyle = "#c4c6cc"; x.fillRect(0, 0, n, n); x.imageSmoothingEnabled = true; x.imageSmoothingQuality = "high"; x.drawImage(src, 0, 0, n, n); return c; }
function small(src, n) { return onBg(src, n); }
function shown(c, css, cap) { c.style.width = css + "px"; c.style.height = css + "px";
  const fig = document.createElement("figure"); fig.appendChild(c); const fc = document.createElement("figcaption"); fc.textContent = cap; fig.appendChild(fc); return fig; }
function drawPreview() {
  if (!RS.imgs) return; const el = $("rPrev"); el.innerHTML = ""; const ramp = rampsFor();
  const keys = Object.keys(RS.imgs);
  if (!keys.length) { el.innerHTML = `<p class="hint">No painted puppets for this race yet: Race → Paint.</p>`; return; }
  el.style.cssText = "display:grid;grid-template-columns:repeat(2,minmax(0,300px));gap:8px";   // male on top, female below
  for (const sex of ["male", "female"]) for (const f of ["front", "back"]) { const k = `${sex}|${f}`;
    if (RS.imgs[k]) el.appendChild(shown(onBg(recolor(k, ramp), 512), 300, `${sex}, ${f}`)); else el.appendChild(document.createElement("div")); }
}
function drawAll() {   // the race's colour set: every skin, hair and eye ramp tagged with it, on its male and female fronts
  if (!RS.doc || !RS.imgs) return;
  const el = $("rAllPrev"), race = $("rRace").value; el.innerHTML = ""; el.style.display = "block";   // one group under the other
  const names = { skin: "Skin", hair: "Hair", eyes: "Eyes" };
  for (const g of ["skin", "hair", "eyes"]) {
    const row = document.createElement("div"); row.innerHTML = `<h2>${names[g]}</h2>`; el.appendChild(row);
    const tones = Object.entries(RS.doc.groups[g] || {}).filter(([n, e]) => (e.races || []).includes(race));
    if (!tones.length) { const p_ = document.createElement("p"); p_.className = "hint";
      p_.textContent = `No ${g} ramps tagged "${race}" yet: pick one in the ${g} group and "Add this ramp to the set"${g === "skin" ? ", or create a starter set" : ""}.`; row.appendChild(p_); continue; }
    for (const [n, e] of tones) {
      const on = RS.group === g && n === RS.name, ramp = on ? curRamp() : rampOf(e);
      const box = document.createElement("div"); box.className = "ritem" + (on ? " on" : "");
      box.innerHTML = `<span class="nm">${n}</span>${stripHtml(ramp)}`;
      box.onclick = () => { if (RS.group !== g) { RS.group = g; $("rGroup").value = g; RS.name = null; } selectRamp(n); };
      row.appendChild(box);
    }
  }
}
async function saveRamp() {
  const name = $("rName").value.trim();
  const races = $("rRaces").value.split(",").map(s => s.trim()).filter(Boolean);
  RS.doc = await post("character.save_ramp", { group: RS.group, name, old_name: RS.name, base: RS.base, tune: RS.tune,
                                            colors: curRamp().map(rgb2hex), races });
  RS.name = name; listRamps(); $("rStatus").textContent = ` saved ${RS.group}.${name}`;
}
document.querySelector('.tab[data-t="ramps"]').addEventListener("click", guard(initRamps));

// <exports>
export { RS, curRamp, drawAll, drawPreview, drawRamp, initRamps, levelData, listRamps, loadPreview, onBg, rampOf, rampsFor, recolor, saveRamp, selectRamp, setRamp, shown, small };
// </exports>
