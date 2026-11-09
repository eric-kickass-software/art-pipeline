"""The Studio's job queue and the registry of job kinds.

Tools register their kinds where the work lives, so the Studio has no dispatch chain:

    @jobs.kind("dun")          # gpu=True by default
    def _job_generate(a): return generate(a["set"], ...)

Code changes apply through a Studio restart (hdart/supervisor.py), never by reloading modules: the supervisor pauses
the queue (`pause()`), waits for the running job to finish, then restarts the server.

Jobs run one at a time (ComfyUI is single-GPU). The queue is saved to a JSON file on every change: after a restart the
queued jobs run again, and a job that was running is marked failed ("interrupted"), not re-run, because it may have
written half its outputs. Retries: a job whose args carry `retries` (> 0) is queued again (at the back, `retries` - 1)
when it fails or is interrupted by a restart; overnight batches use this so one bad render or a crash costs a retry,
not the night. Before a GPU job the runner waits while ComfyUI is busy with someone else's work (a test, the
CLI, a second Studio). Cancel drops a queued job, or interrupts ComfyUI for the running one.
"""
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

KINDS = {}
STATUSES = ("queued", "running", "done", "failed", "cancelled")
KEEP = 60   # finished jobs kept in the list (and the file)
QUEUE = None   # the running Studio's queue (set by hdart.studio); ops add jobs to it through enqueue()


def current() -> "Jobs":
    """The running Studio's queue (RuntimeError when there is none: long work runs in the Studio)."""
    if QUEUE is None:
        raise RuntimeError("no job queue: long work runs in the Studio (uv run hdart studio)")
    return QUEUE


def enqueue(kind: str, args: dict, label: str) -> dict:
    """Add a job to the Studio's queue. Returns the job dict (its "id" is what ops hand back)."""
    return current().add(kind, args, label)


@dataclass
class Kind:
    fn: object
    gpu: bool


def kind(name: str, gpu: bool = True):
    """Register the decorated function as the handler of job kind `name` (`gpu`: it runs ComfyUI work, so the runner
    waits while ComfyUI is busy with someone else's and a cancel interrupts ComfyUI)."""
    def deco(fn):
        KINDS[name] = Kind(fn, gpu)
        return fn
    return deco


class Cancelled(Exception):
    pass


class Jobs:
    """Sequential background queue. `path`: where the queue is saved (None = memory only). `comfy`: the client used for
    the busy check and interrupts (hdart.comfy)."""

    def __init__(self, path=None, comfy=None, autostart=True, poll=0.4, busy_poll=2.0):
        self.path, self.poll, self.busy_poll = Path(path) if path else None, poll, busy_poll
        if comfy is None:
            from . import comfy
        self.comfy = comfy
        self.items, self.lock, self.n = [], threading.RLock(), 0
        self.paused = False   # True: no new job starts (a restart is waiting for the running one)
        self._load()
        if autostart:
            threading.Thread(target=self.run, daemon=True).start()

    # ------------------------------------------------------------------ persistence
    def _load(self):
        if not self.path or not self.path.exists():
            return
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))["jobs"]
        except (ValueError, KeyError, OSError):
            return
        for j in items:
            if j["status"] == "running":
                if int((j.get("args") or {}).get("retries") or 0) > 0:
                    j["args"]["retries"] = int(j["args"]["retries"]) - 1
                    j["status"], j["error"] = "queued", None
                    j["label"] = j["label"].removesuffix(" (retry)") + " (retry)"
                else:
                    j["status"], j["error"] = "failed", "interrupted: the Studio restarted while it ran"
            j.pop("note", None)
            j.pop("started", None)
        self.items = items
        self.n = max((j["id"] for j in items), default=0)

    def _save(self):
        if not self.path:
            return
        with self.lock:
            active = [j for j in self.items if j["status"] in ("queued", "running")]
            done = [j for j in self.items if j["status"] not in ("queued", "running")][-KEEP:]
            keep = sorted(active + done, key=lambda j: j["id"])
            self.items = keep
            data = json.dumps({"jobs": keep}, indent=1, default=str)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ API
    def add(self, kind, args, label):
        with self.lock:
            self.n += 1
            job = {"id": self.n, "kind": kind, "args": args, "label": label, "status": "queued", "error": None,
                   "result": None}
            self.items.append(job)
        self._save()
        return job

    def get(self, jid):
        with self.lock:
            return next((j for j in self.items if j["id"] == jid), None)

    def snapshot(self):
        """Every queued/running job plus the newest finished ones (a long overnight queue is listed whole)."""
        with self.lock:
            done = [j for j in self.items if j["status"] not in ("queued", "running")][-KEEP:]
            keep = {id(j) for j in done}
            return [{k: v for k, v in j.items() if k != "args"} for j in self.items
                    if id(j) in keep or j["status"] in ("queued", "running")]

    def cancel_where(self, prefix):
        """Cancel every queued job whose label starts with `prefix` (a whole batch). Returns how many."""
        n = 0
        with self.lock:
            for j in self.items:
                if j["status"] == "queued" and j["label"].startswith(prefix):
                    j["status"] = "cancelled"; n += 1
        self._save()
        return n

    def clear_done(self):
        with self.lock:
            self.items = [j for j in self.items if j["status"] in ("queued", "running")]
        self._save()

    def cancel(self, jid):
        """Cancel a queued job, or ask the running one to stop (ComfyUI is interrupted; the job ends as cancelled when
        its handler returns or fails). Returns the job's status afterwards, or None if there's no such job."""
        with self.lock:
            job = self.get(jid)
            if not job:
                return None
            if job["status"] == "queued":
                job["status"] = "cancelled"
            elif job["status"] == "running":
                job["cancel"] = True
                if KINDS.get(job["kind"], Kind(None, False)).gpu:
                    self.comfy.interrupt()
            status = job["status"]
        self._save()
        return status

    def running(self) -> dict | None:
        """The job that is running now, if any."""
        with self.lock:
            return next((j for j in self.items if j["status"] == "running"), None)

    def pause(self, on: bool = True):
        """Stop starting new jobs (on=False: start them again). The running job carries on; queued jobs stay queued,
        in the saved queue too, so a restart picks them up."""
        self.paused = on

    # ------------------------------------------------------------------ worker
    def _next(self):
        with self.lock:
            if self.paused:
                return None
            job = next((j for j in self.items if j["status"] == "queued"), None)
            if job:
                job["status"], job["started"] = "running", time.time()
        if job:
            self._save()
        return job

    def _wait_for_comfy(self, job):
        """Wait while ComfyUI runs someone else's work (our previous job has finished by now, so any is foreign)."""
        while not job.get("cancel"):
            q = self.comfy.queue()
            if not q or not (q["running"] or q["pending"]):
                break
            job["note"] = f"waiting: ComfyUI is busy with other work ({q['running'] + q['pending']} queued there)"
            time.sleep(self.busy_poll)
        job.pop("note", None)

    def run_one(self, job):
        a = job["args"]
        try:
            k = KINDS.get(job["kind"])
            if k is None:
                raise ValueError(f"unknown job kind {job['kind']}")
            if k.gpu:
                self._wait_for_comfy(job)
            if job.get("cancel"):
                raise Cancelled()
            job["result"] = k.fn(a)
            job["status"] = "cancelled" if job.get("cancel") else "done"
        except BaseException as e:   # a handler raising SystemExit (old code) must not kill this worker and stall the queue
            if job.get("cancel"):
                job["status"], job["error"] = "cancelled", None
            else:
                job["status"], job["error"] = "failed", str(e) or type(e).__name__
                left = int(a.get("retries") or 0) if isinstance(a, dict) else 0
                if left > 0:
                    self.add(job["kind"], {**a, "retries": left - 1}, job["label"].removesuffix(" (retry)") + " (retry)")
        job.pop("cancel", None)
        job.pop("started", None)
        self._save()

    def run(self):
        while True:
            job = self._next()
            if not job:
                time.sleep(self.poll)
                continue
            self.run_one(job)

    def drain(self):
        """Run every queued job now, on this thread (tests and scripts; don't combine with autostart)."""
        while (job := self._next()):
            self.run_one(job)
