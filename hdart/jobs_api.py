"""The job queue's public API: list, inspect, wait for, cancel and queue Studio jobs (hdart/jobs.py has the queue).

Every tool's long work (generation, renders, Unity captures) runs as a job, one at a time; a tool's op that starts
work returns {"job": id} (or {"jobs": [ids]}). Typical flow for an agent: <tool>.generate → jobs.wait → <tool>.state.

Every function here is an op (hdart/ops.py). LEGACY maps the pages' older /api/ws/jobs routes onto them.
"""
import time

from . import jobs, ops

ACTIVE = ("queued", "running")

ops.param_docs("jobs.", {
    "id": "a job id (what an op that queues work returns)",
    "ids": "several job ids",
})


def _public(j: dict, with_args: bool = False) -> dict:
    """A job without its (sometimes large) arguments, unless asked for."""
    return {k: v for k, v in j.items() if with_args or k != "args"}


@ops.op("jobs.list", read_only=True)
def list_jobs(active_only: bool = False) -> dict:
    """Every queued and running job plus the newest finished ones, in queue order: {"jobs": [...]}.

    Each job has id, kind, label, status (queued, running, done, failed, cancelled), error, result and, while it
    waits for ComfyUI, a note.

    Args:
        active_only: only queued and running jobs
    """
    snap = jobs.current().snapshot()
    return {"jobs": [j for j in snap if j["status"] in ACTIVE] if active_only else snap}


@ops.op("jobs.get", read_only=True)
def get(id: int, with_args: bool = False) -> dict:
    """One job: id, kind, label, status, error, result.

    Args:
        with_args: also the arguments it was queued with
    """
    j = jobs.current().get(id)
    if j is None:
        raise ops.OpError(f"no job {id} (finished jobs are kept for a while, then dropped)", 404)
    return _public(j, with_args)


@ops.op("jobs.wait", read_only=True)
def wait(id: int | None = None, ids: list | None = None, timeout: float = 300.0) -> dict:
    """Wait until a job (or every job in ids) has finished, or the timeout passes. Returns {"jobs": [...],
    "done": true when none is still queued or running}. Waiting changes nothing, so call it again to keep waiting.

    Args:
        timeout: seconds to wait at most (the HTTP request stays open that long)
    """
    want = ([id] if id is not None else []) + [int(i) for i in ids or []]
    if not want:
        raise ops.OpError("jobs.wait: give id or ids")
    q, end = jobs.current(), time.monotonic() + max(0.0, timeout)
    while True:
        found = [q.get(i) for i in want]
        if None in found:
            raise ops.OpError(f"no job {want[found.index(None)]}", 404)
        done = all(j["status"] not in ACTIVE for j in found)
        if done or time.monotonic() >= end:
            return {"jobs": [_public(j) for j in found], "done": done}
        time.sleep(0.25)


@ops.op("jobs.cancel")
def cancel(id: int) -> dict:
    """Cancel a queued job, or stop the running one (ComfyUI is interrupted). Returns {"status": its status after}."""
    status = jobs.current().cancel(id)
    if status is None:
        raise ops.OpError(f"no job {id}", 404)
    return {"status": status}


@ops.op("jobs.cancel_label")
def cancel_label(prefix: str) -> dict:
    """Cancel every queued job whose label starts with prefix (a whole batch). Returns {"cancelled": how many}.

    Args:
        prefix: the label's start, e.g. "Limner batch b1107_225626"
    """
    if not prefix:
        raise ops.OpError("jobs.cancel_label: an empty prefix would cancel everything")
    return {"cancelled": jobs.current().cancel_where(prefix)}


@ops.op("jobs.clear")
def clear() -> dict:
    """Drop finished jobs from the list (queued and running ones stay)."""
    jobs.current().clear_done()
    return {"ok": True}


@ops.op("jobs.queue")
def queue(kind: str, args: dict | None = None, label: str | None = None) -> dict:
    """Queue a job of any registered kind with raw arguments (low level; the tools' own ops check their arguments
    and are the better way in). Returns {"job": id}.

    Args:
        kind: the job kind (hdart.jobs.KINDS: "paint", "race", "face_gen", "item", "dun", "limner_gen"...)
        args: what the kind's handler reads
        label: what the queue panels show
    """
    if kind not in jobs.KINDS:
        raise ops.OpError(f"unknown job kind {kind!r}; known: {', '.join(sorted(jobs.KINDS))}")
    return {"job": jobs.enqueue(kind, args or {}, label or kind)["id"]}


def _queue_many(b: dict) -> dict:
    """The pages' POST /api/ws/jobs {jobs: [{kind, label, ...args}]}: each job's whole dict is its args, as before."""
    return {"added": [ops.call("jobs.queue", {"kind": j["kind"], "args": j, "label": j.get("label", j["kind"])})["job"]
                      for j in b["jobs"]]}


# /api/ws/<action> -> op (see ops.call_alias). Kept so the pages and the watchdog work unchanged.
LEGACY = {
    "jobs": _queue_many,
    "jobs/clear": ("jobs.clear", {}),
    "jobs/cancel": ("jobs.cancel", {}),
}
GET_LEGACY = {
    "jobs": ("jobs.list", {}),   # the pages' job panels and the watchdog poll this
}
