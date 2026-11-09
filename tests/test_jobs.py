"""hdart.jobs: the Studio's queue and job-kind registry (persistence, cancel, waiting for a busy ComfyUI, a failing job
doesn't stall the queue), and that every tool kind the pages queue is registered. Run: uv run python tests/test_jobs.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, tempfile, time
from pathlib import Path
from PIL import Image
from hdart import comfy, jobs, studio
from hdart.fake_comfy import FakeComfy

tmp = Path(tempfile.mkdtemp())
ran = []


@jobs.kind("t_add", gpu=False)
def _add(a):
    ran.append(a["x"]); return a["x"] + 1


@jobs.kind("t_exit", gpu=False)
def _exit(a):
    raise SystemExit("old code exits")   # must not kill the worker


@jobs.kind("t_gpu")
def _gpu(a):
    return comfy.img2img(Image.new("RGB", (8, 8)), "x", seed=1, denoise=0.5).size


with FakeComfy() as fake:
    q = jobs.Jobs(tmp / "jobs.json", autostart=False, busy_poll=0.05)
    a, b, c = q.add("t_add", {"x": 1}, "one"), q.add("t_exit", {}, "exit"), q.add("t_add", {"x": 2}, "two")
    q.add("nope", {}, "unknown")
    assert q.cancel(c["id"]) == "cancelled" and q.cancel(999) is None
    q.drain()
    st = {j["label"]: (j["status"], j["error"], j["result"]) for j in q.snapshot()}
    assert st["one"] == ("done", None, 2) and st["exit"] == ("failed", "old code exits", None) and ran == [1]
    assert st["two"][0] == "cancelled" and st["unknown"][0] == "failed" and "unknown job kind" in st["unknown"][1]
    assert "args" not in q.snapshot()[0]

    # the queue survives a restart: queued jobs run again, a job that was running is marked interrupted
    q2 = jobs.Jobs(tmp / "jobs.json", autostart=False)
    q2.add("t_add", {"x": 5}, "later")
    q2.items[-1]["status"] = "running"; q2._save()
    q2.add("t_add", {"x": 7}, "queued")
    q3 = jobs.Jobs(tmp / "jobs.json", autostart=False)
    st = {j["label"]: j for j in q3.snapshot()}
    assert st["later"]["status"] == "failed" and "restarted" in st["later"]["error"] and st["queued"]["status"] == "queued"
    assert q3.add("t_add", {"x": 0}, "next")["id"] == max(j["id"] for j in q3.items)   # ids keep counting
    q3.drain()
    assert ran[-2:] == [7, 0]
    q3.clear_done()
    assert q3.snapshot() == [] and json.loads((tmp / "jobs.json").read_text())["jobs"] == []

    # GPU jobs wait while ComfyUI runs someone else's work (shown as a note), then run
    fake.busy = 1
    q4 = jobs.Jobs(None, autostart=True, poll=0.02, busy_poll=0.05)
    g = q4.add("t_gpu", {}, "gpu")
    time.sleep(0.3)
    assert g["status"] == "running" and "busy" in g.get("note", "")
    fake.busy = 0
    for _ in range(100):
        if g["status"] != "running":
            break
        time.sleep(0.05)
    assert g["status"] == "done" and g["result"] == (8, 8) and "note" not in g

    # cancel the running GPU job: ComfyUI is interrupted, the job ends cancelled, the next one still runs
    fake.delay = 10
    g2, nxt = q4.add("t_gpu", {}, "long"), q4.add("t_add", {"x": 9}, "after")
    for _ in range(100):
        if fake.running:
            break
        time.sleep(0.02)
    assert q4.cancel(g2["id"]) == "running"
    for _ in range(150):
        if nxt["status"] == "done":
            break
        time.sleep(0.05)
    assert g2["status"] == "cancelled" and g2["error"] is None and nxt["status"] == "done"
    fake.delay = 0

# every kind the pages queue is registered by its tool module, with its reload list
import importlib
for name in studio.JOB_MODULES:
    importlib.import_module(f"hdart.{name}")
want = {"dun", "dun_trial", "dun_edit", "dun_rescale", "dun_finalize", "dun_build", "dun_capture", "lib_import",
        "puppet", "paint", "paint_preview", "paint_fix", "face_gen", "item", "tpl", "tpl_measure", "race"}
assert want <= set(jobs.KINDS), want - set(jobs.KINDS)
assert jobs.KINDS["item"].reload[-1] == "puppet_items" and not jobs.KINDS["dun_capture"].gpu and jobs.KINDS["dun"].gpu
# a reload re-registers the fresh function
import hdart.dungeon as dg
old = jobs.KINDS["dun"].fn
importlib.reload(dg)
assert jobs.KINDS["dun"].fn is not old and jobs.KINDS["dun"].fn is dg._job_generate
print("ok")
