// Item Workshop: the grip card of a held item (grip hands, the live stage, the walk preview).
// ---- grip (held items)
import { get, post } from "../api.js";
import { loadImg } from "../canvas.js";
// <imports>
import { $, guard, img, queue } from "./common.js";
import { IT, itGet, itHeld, itPost, itRefresh } from "./items.js";
// </imports>
const GR = { at: null, imgEl: null, imgKey: null };
const gripDef = () => ({ hand: $("itHand").value, length: +$("itLength").value || 0.48, grip_angle: +$("itGripAngle").value || 0,
  flip: $("itFlip").checked, grip_at: GR.at, held_as: $("itHeldAs").value });
function gripRender() {
  const st = IT.st, gs = st.grip_spec;
  if (document.activeElement?.closest?.("#gripCard details") == null) { $("gripPFront").value = gs.prompt; $("gripPBack").value = gs.prompt_back; }
  if (document.activeElement !== $("gripWrist")) $("gripWrist").value = gs.wrist;
  $("gripBuilds").innerHTML = st.builds.map(b => { const g = st.grips[b] || { candidates: [] };
    return `<div class="row" style="align-items:flex-start"><b style="width:110px">${b}</b>
      <button data-ggen="${b}">Generate grip hands</button>
      <div class="row" style="margin:0">${g.candidates.map(c => `<div class="itc ${g.approved === c.id ? "on" : ""}" style="padding:4px">
        <div class="itcimgs">${["front", "back"].map(v => c.sep[v] ? `<img src="${img(c.sep[v])}&_t=${c.stamp}" style="width:256px;height:128px" title="${v}">` : "").join("")}</div>
        <div class="row" style="margin:2px 0 0"><span class="hint">${c.id}</span>
          <button data-gap="${b}|${c.id}" class="${g.approved === c.id ? "on" : ""}">${g.approved === c.id ? "✓ approved" : "Approve"}</button>
          <button data-greg="${b}|${c.id}" title="re-split the hands from the saved paint (no GPU)">Re-split</button></div></div>`).join("") || `<span class="hint">no grip hands yet</span>`}</div></div>`; }).join("");
  $("gripBuilds").querySelectorAll("[data-ggen]").forEach(x => x.onclick = guard(async () => {
    await queue([{ kind: "item", action: "grip", race: IT.race, build: x.dataset.ggen, seed: +$("gripSeed").value, label: `grip hands · ${x.dataset.ggen} (s${$("gripSeed").value})` }]);
    $("gripSeed").value = +$("gripSeed").value + 1; }));
  $("gripBuilds").querySelectorAll("[data-gap]").forEach(x => x.onclick = guard(async () => { const [b, id] = x.dataset.gap.split("|");
    const on = (IT.st.grips[b] || {}).approved !== id; await itPost("items.approve_grip", { build: b, candidate: id, on }); await itRefresh(); }));
  $("gripBuilds").querySelectorAll("[data-greg]").forEach(x => x.onclick = guard(async () => { const [b, id] = x.dataset.greg.split("|");
    await queue([{ kind: "item", action: "regrip", race: IT.race, build: b, id, label: `grip hands · ${b} · re-split ${id}` }]); }));
  const cands = st.candidates[IT.sel] || [], cur = $("gripCand").value;
  $("gripCand").innerHTML = cands.map(c => `<option>${c.id}</option>`).join("");
  if (cands.some(c => c.id === cur)) $("gripCand").value = cur;
  const bcur = $("gripBuild").value; $("gripBuild").innerHTML = st.builds.map(b => `<option>${b}</option>`).join(""); if (st.builds.includes(bcur)) $("gripBuild").value = bcur;
  gripDrawImg(); gripStageLoad();
}
async function gripDrawImg() {
  const c = (IT.st.candidates[IT.sel] || []).find(x => x.id === $("gripCand").value);
  const cv = $("gripImg"), x = cv.getContext("2d"); x.clearRect(0, 0, cv.width, cv.height);
  if (!c || !c.held) { $("gripAt").textContent = ""; return; }
  const key = c.held + c.stamp; if (GR.imgKey !== key) { GR.imgEl = await loadImg(img(c.held) + `&_t=${c.stamp}`); GR.imgKey = key; }
  const im = GR.imgEl, k = Math.min(cv.width / im.width, cv.height / im.height), w = im.width * k, h = im.height * k;
  GR.box = [(cv.width - w) / 2, (cv.height - h) / 2, w, h]; x.drawImage(im, GR.box[0], GR.box[1], w, h);
  $("gripAt").textContent = GR.at ? `at ${GR.at.map(v => v.toFixed(2)).join(", ")}` : "auto (the leather's middle)";
  if (GR.at) { const px = GR.box[0] + GR.at[0] * w, py = GR.box[1] + GR.at[1] * h;
    x.strokeStyle = "#e1288f"; x.lineWidth = 2; x.beginPath(); x.arc(px, py, 6, 0, 7); x.stroke(); x.beginPath(); x.moveTo(px - 12, py); x.lineTo(px + 12, py); x.stroke(); }
}
$("gripImg").onclick = (e) => { if (!GR.box) return; const r = $("gripImg").getBoundingClientRect();
  const fx = ((e.clientX - r.left) * $("gripImg").width / r.width - GR.box[0]) / GR.box[2], fy = ((e.clientY - r.top) * $("gripImg").height / r.height - GR.box[1]) / GR.box[3];
  if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return; GR.at = [+fx.toFixed(3), +fy.toFixed(3)]; gripDrawImg(); gripStageDraw(); $("itSaved").textContent = "grip point changed: Save item to keep it"; };
$("gripAtReset").onclick = (e) => { e.preventDefault(); GR.at = null; gripDrawImg(); gripStageDraw(); };
$("gripCand").onchange = () => { gripDrawImg(); gripStageLoad(); };
$("gripBuild").onchange = () => gripStageLoad();

// the live stage: the build's rest pose below / above the held item from held/stage, the item turned here in the browser
// exactly as held_layer does (grip point on the hand's grip point, grip axis turned by the angle, flip), so dragging is instant
const GS = { key: null, data: null, ims: {}, drag: null };
const rotV = (u, deg) => { const t = deg * Math.PI / 180, c = Math.cos(t), s = Math.sin(t); return [c * u[0] + s * u[1], -s * u[0] + c * u[1]]; };
const scrAng = (v) => Math.atan2(-v[1], v[0]) * 180 / Math.PI;   // counter-clockwise on screen, as _rot turns
function gsAxis(v, o) {   // the item's tip direction in view v
  const sgn = v === "front" ? 1 : -1, u = rotV(o.u0, (+$("itGripAngle").value || 0) * sgn);
  return $("itFlip").checked ? [-u[0], -u[1]] : u;
}
async function gripStageLoad() {
  if (!IT.sel || !itHeld() || !$("gripBuild").value) return;
  const cand = $("gripCand").value || null, key = [IT.sel, $("gripBuild").value, cand, $("itHand").value, $("itHeldAs").value].join("|");
  if (GS.key === key) return gripStageDraw();
  GS.key = key; $("gripStageMsg").textContent = "loading the stage…";
  try {
    const r = await itGet("items.held_stage", { item: IT.sel, build: $("gripBuild").value, candidate: cand, unsaved: { hand: $("itHand").value, held_as: $("itHeldAs").value } });
    if (GS.key !== key) return;
    const ims = {};
    for (const v of ["front", "back"]) { const o = r.views[v];
      ims[v] = { below: await loadImg(o.below), above: await loadImg(o.above), item: o.img ? await loadImg(o.img) : null }; }
    GS.data = r; GS.ims = ims;
    $("gripStageMsg").textContent = (cand ? "drag on a view to turn the item" : "no candidate yet: the ghost shows the length and angle; drag to turn it") +
      (r.views.front.grip_hand ? "" : " (no approved grip hand on this build: the item sits under the painted hand)");
  } catch (e) { GS.key = null; $("gripStageMsg").textContent = e.message; return; }
  gripStageDraw();
}
function gsView(v) {   // canvas transform of view v: the whole rest-pose canvas, or 2x around the hand's grip point
  const o = GS.data.views[v], cv = $("gripStage_" + v), k = cv.width / GS.data.N;
  if (!$("gripZoom").checked) return { k, ox: 0, oy: 0 };
  const z = 2 * k; return { k: z, ox: cv.width / 2 - o.c[0] * z, oy: cv.height / 2 - o.c[1] * z };
}
function gripStageDraw() {
  if (!GS.data) return;
  for (const v of ["front", "back"]) {
    const o = GS.data.views[v], im = GS.ims[v], cv = $("gripStage_" + v), x = cv.getContext("2d"), N = GS.data.N, T = gsView(v);
    x.setTransform(1, 0, 0, 1, 0, 0); x.clearRect(0, 0, cv.width, cv.height);
    x.setTransform(T.k, 0, 0, T.k, T.ox, T.oy); x.imageSmoothingQuality = "high";
    x.drawImage(im.below, 0, 0, N, N);
    const u = gsAxis(v, o), th = Math.atan2(u[0], -u[1]), Lpx = (+$("itLength").value || 0.48) * o.fig;   // th turns the image's up onto u
    x.save(); x.translate(o.c[0], o.c[1]); x.rotate(th);
    if (im.item) {
      const s = Lpx / Math.max(1, o.span);
      const g = GR.at ? [(v === "back" ? 1 - GR.at[0] : GR.at[0]) * o.w, GR.at[1] * o.h] : o.g_auto;
      x.scale(s, s); x.drawImage(im.item, -g[0], -g[1]);
    } else {   // a ghost before generation: a blade from the fist, or a kite shield on it
      x.fillStyle = "rgba(225,40,143,.55)"; x.strokeStyle = "#e1288f"; x.lineWidth = 2 / T.k;
      x.beginPath();
      if ($("itHeldAs").value === "shield") {
        const h = Lpx, w = 0.72 * Lpx;
        x.moveTo(-w / 2, -h / 2); x.lineTo(w / 2, -h / 2); x.quadraticCurveTo(w / 2, h * 0.15, 0, h / 2); x.quadraticCurveTo(-w / 2, h * 0.15, -w / 2, -h / 2);
      } else {
        const w = 0.05 * Lpx; x.rect(-w / 2, -0.85 * Lpx, w, Lpx); x.rect(-2.2 * w, -0.72 * Lpx, 4.4 * w, w * 0.8);
      }
      x.fill(); x.stroke();
    }
    x.restore();
    x.drawImage(im.above, 0, 0, N, N);
    x.strokeStyle = "#e1288f"; x.lineWidth = 1.5 / T.k;   // the hand's grip point
    x.beginPath(); x.arc(o.c[0], o.c[1], 5 / T.k, 0, 7); x.stroke();
  }
}
function gsSetAngle(a) {
  a = Math.round(((a + 540) % 360) - 180); $("itGripAngle").value = $("itGripAngleR").value = a;
  $("itSaved").textContent = "grip angle changed: Save item to keep it"; gripStageDraw();
}
document.querySelectorAll("[data-gsv]").forEach(cv => {
  const angAt = (e) => { const v = cv.dataset.gsv, o = GS.data.views[v], T = gsView(v), r = cv.getBoundingClientRect();
    const px = ((e.clientX - r.left) * cv.width / r.width - T.ox) / T.k, py = ((e.clientY - r.top) * cv.height / r.height - T.oy) / T.k;
    let w = [px - o.c[0], py - o.c[1]]; if (Math.hypot(...w) < 4) return null;
    if ($("itFlip").checked) w = [-w[0], -w[1]];
    return (v === "front" ? 1 : -1) * (scrAng(w) - scrAng(o.u0)); };
  cv.onpointerdown = (e) => { if (!GS.data) return; cv.setPointerCapture(e.pointerId); GS.drag = cv; cv.style.cursor = "grabbing";
    const a = angAt(e); if (a != null) gsSetAngle(a); };
  cv.onpointermove = (e) => { if (GS.drag !== cv) return; const a = angAt(e); if (a != null) gsSetAngle(a); };
  cv.onpointerup = cv.onpointercancel = () => { GS.drag = null; cv.style.cursor = "grab"; };
});
$("itGripAngle").oninput = () => { $("itGripAngleR").value = $("itGripAngle").value; gripStageDraw(); };
$("itGripAngleR").oninput = () => gsSetAngle(+$("itGripAngleR").value);
$("itLength").oninput = $("itFlip").onchange = $("gripZoom").onchange = () => gripStageDraw();
$("itHand").onchange = $("itHeldAs").onchange = () => gripStageLoad();
$("gripPreview").onclick = guard(async () => {
  if (!$("gripCand").value) throw new Error("Generate a candidate first.");
  $("gripOut").innerHTML = `<span class="hint">rendering…</span>`;
  const r = await get("items.held_preview", { item: IT.sel, candidate: $("gripCand").value, build: $("gripBuild").value, unsaved: gripDef() });
  $("gripOut").innerHTML = ["front_rest", "front_walk", "front_walk2", "back_rest", "back_walk", "back_walk2"].map(k =>
    `<figure style="margin:0"><img src="${r[k]}" style="width:190px;background:#c4c6cc;border-radius:3px"><figcaption class="hint">${k.replace("_", " ")}</figcaption></figure>`).join("");
});
$("gripSaveSpec").onclick = guard(async () => {
  await itPost("items.save_grip_spec", { prompt: $("gripPFront").value, prompt_back: $("gripPBack").value, wrist: +$("gripWrist").value }); await itRefresh(); });
$("gripWrist").onchange = guard(async () => { await itPost("items.save_grip_spec", { wrist: +$("gripWrist").value }); await itRefresh(); });

// <exports>
export { GR, GS, gripDef, gripDrawImg, gripRender, gripStageDraw, gripStageLoad, gsAxis, gsSetAngle, gsView, rotV, scrAng };
// </exports>
