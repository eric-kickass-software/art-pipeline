// The job queue panel every page has: the header button ("Queue: 2 (label)"), the panel it opens (each job with its
// status, note and error, ✕ to cancel a queued or running one) and "Clear finished". It polls jobs.list; the page
// reacts to finished jobs in `onPoll` (each tool reloads what its jobs changed).
import { get, post } from "./api.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const isActive = (j) => j.status === "queued" || j.status === "running";
export const isFinished = (j) => j.status === "done" || j.status === "failed";

/**
 * Mount the panel. Elements by id: `button` (toggles the panel), `panel`, `list`, `clear`.
 * Options: `every` (ms between polls; 0: the page polls itself), `busy` ("running": the button names the running job, "first": the first
 * active one), `errors` ("title": a job's error on hover, "inline": under its label), `empty` (html for no jobs),
 * `onPoll(jobs)` (after each poll; may be async).
 * Returns { poll(), jobs() (the last list), queue(list) (each {kind, label, ...args} as one job) }.
 */
export function jobsPanel({ button = "jobsBtn", panel = "jobsPanel", list = "jobsList", clear = "jobsClear", every = 2000,
                            busy = "running", errors = "title", empty = `<p class="hint">Nothing queued.</p>`, onPoll = null } = {}) {
  const $ = (id) => document.getElementById(id);
  let jobs = [];
  $(button).onclick = () => $(panel).classList.toggle("on");
  $(clear).onclick = async () => { try { await post("jobs.clear"); } catch (e) { /* shown on the next poll */ } poll(); };
  $(list).addEventListener("click", async (e) => {
    const x = e.target.closest("[data-cancel]"); if (!x) return;
    try { await post("jobs.cancel", { id: +x.dataset.cancel }); } catch (err) { /* already finished */ }
    poll();
  });
  const row = (j) => `<div class="job"><span>${esc(j.label)}${j.note ? `<div class="note">${esc(j.note)}</div>` : ""}${errors === "inline" && j.error ? `<div class="err">${esc(j.error)}</div>` : ""}</span>`
    + `<span><span class="s ${j.status}"${errors === "title" ? ` title="${esc(j.error || "")}"` : ""}>${j.status}</span>`
    + `${isActive(j) ? `<button class="x" title="Cancel this job" data-cancel="${j.id}">✕</button>` : ""}</span></div>`;

  async function poll() {
    try {
      jobs = (await get("jobs.list")).jobs;
    } catch (e) { return; }   // the Studio restarting: try again next time
    const active = jobs.filter(isActive), run = jobs.find(j => j.status === "running");
    const name = busy === "first" ? (active[0] || {}).label : (run ? run.label : "starting");
    $(button).textContent = active.length ? `Queue: ${active.length} (${name})` : "Queue: idle";
    $(list).innerHTML = jobs.slice().reverse().map(row).join("") || empty;
    if (onPoll) { try { await onPoll(jobs); } catch (e) { console.warn("jobs panel:", e); } }
  }

  /** Queue jobs ({kind, label, ...args}: the whole dict is the job's args, as the job handlers read them). */
  async function queue(list) {
    for (const j of list) await post("jobs.queue", { kind: j.kind, args: j, label: j.label || j.kind });
    poll();
  }

  if (every > 0) setInterval(poll, every);
  return { poll, queue, jobs: () => jobs };
}
