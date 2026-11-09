// Shared by the three Workshop pages (Character Workshop, Item Workshop, Workshop Editor): the error line, the tabs,
// images of the character data, the job queue panel, and opening a candidate in the Workshop Editor.
import { url } from "../api.js";
import { initMenu } from "../menu.js";
import { jobsPanel, isFinished } from "../jobs.js";

const $ = (id) => document.getElementById(id);
// the rest-pose canvas (hdart.puppet: PX = 576 rig px, painted at 2x; PAD = where the old 512 frame / the 512 px mannequins sit)
const RIG = 576, RIG_PAD = [32, 64], CANVAS = 2 * RIG;
const SEXSYM = { male: "♂", female: "♀", all: "✓" };
/** A file of the character data (sprites/ai/...): op character.image. Append `&_t=<stamp>` to bust the cache. */
const img = (rel) => url("character.image", { path: rel });
const err = (m) => $("err").textContent = m || "";
const guard = (fn) => async (...a) => { err(""); try { await fn(...a); } catch (e) { err(e.message); } };
const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;");

/** The page's tabs: a click shows its section. Wired first, before the tools add their own tab listeners. */
function initTabs() {
  document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x === t));
    document.querySelectorAll("section").forEach(s => s.classList.toggle("on", s.id === t.dataset.t));
  });
}
initTabs();
/** The tab the URL's #anchor names (/workshop#races), else the first. */
function wantedTab() {
  const want = location.hash.slice(1), tabs = [...document.querySelectorAll(".tab")];
  return tabs.find(t => t.dataset.t === want) || tabs[0];
}

// ------------------------------------------------------------------ jobs
let jobs = [];   // the last poll (every job, newest last)
let lastDone = null;   // the first poll only learns the count (the page loads its data itself)
const onJobsDone = [];   // async functions run whenever a job finishes (a tool reloads its lists)
const panel = jobsPanel({
  onPoll: async (list) => {
    jobs = list;
    const done = jobs.filter(isFinished).length;
    if (lastDone === null) lastDone = done;
    if (done !== lastDone) { lastDone = done; const f = jobs.filter(j => j.status === "failed").pop(); if (f) err(`${f.label}: ${f.error}`); for (const fn of onJobsDone) await fn(); }
  },
});
const pollJobs = () => panel.poll();
const queue = (list) => panel.queue(list);

/** Start a Workshop page: the tool switcher and the first jobs poll. */
function startPage(app) {
  initMenu(app); pollJobs();
}

/** Open a candidate in the Workshop Editor (/editor): {build, paint} a painted puppet, {race, layer, id} a hair / face
 * layer, {race, item, id} a fitted item. */
function openInEditor(params) {
  location.href = "/editor?" + new URLSearchParams(params).toString();
}

// <exports>
export { $, CANVAS, RIG, RIG_PAD, SEXSYM, err, esc, guard, img, initTabs, jobs, lastDone, onJobsDone, openInEditor, panel, pollJobs, queue, startPage, wantedTab };
// </exports>
