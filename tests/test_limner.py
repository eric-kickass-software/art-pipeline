"""Limner (hdart.limner): entry lists from the contract, prompt layers (style > category > entry), generation and
variations on the fake ComfyUI, the finish re-run without the GPU, approve / archive, export into a temp game project.
Never touches the real spec, data or game. Run: uv run python tests/test_limner.py"""
import json, os, shutil, tempfile
from pathlib import Path

from PIL import Image

from hdart import config, limner as L
from hdart.fake_comfy import FakeComfy, info

tmp = Path(tempfile.mkdtemp())
game, data = tmp / "game", tmp / "data"
os.environ["HD_PROJECT"], os.environ["HD_DATA"] = str(game), str(data)
(game / "ArtContract").mkdir(parents=True)
(game / "ArtContract" / "requirements.json").write_text(json.dumps({
    "damageTypes": ["fire", "holy"],
    "wanted": {"icons": {"items": [{"id": "item.bandage", "name": "Bandage", "kind": "consumable"}],
                         "classes": [{"id": "class.acolyte", "name": "Acolyte", "role": "healer", "text": "Rituals."}],
                         "statuses": [{"id": "status.burn", "name": "Burning"}]}}}), encoding="utf-8")
L.SPEC = tmp / "limner.json"
shutil.copyfile(config.ROOT / "specs" / "limner.json", L.SPEC)
repo_spec = (config.ROOT / "specs" / "limner.json").read_text(encoding="utf-8")

# entries: the contract's wanted lists + the spec's gear, skill placeholders per damage type, portraits per look × gender
e = L.entries()
assert [x["id"] for x in e["items"]] == ["item.bandage"] and e["items"][0]["draft"] == "bandage"
assert [x["id"] for x in e["skills"]] == ["placeholder.fire.active", "placeholder.fire.passive",
                                          "placeholder.holy.active", "placeholder.holy.passive"]
sp = L.spec()
n_looks = sum(len(a["looks"]) for a in sp["archetypes"].values())
assert len(e["portraits"]) == 2 * n_looks and len(e["gear"]) == len(sp["subjects"]["gear"])
assert e["classes"][0]["draft"] == sp["subjects"]["classes"]["class.acolyte"]

# prompt layers
p = L.prompt("statuses", "status.burn")
st = sp["styles"][sp["categories"]["statuses"]["style"]]
assert p.startswith(st["prefix"] + "status effect icon, ") and "roaring orange flame" in p and p.endswith(st["suffix"]), p
L.save_entry("statuses", "status.burn", {"subject": "a blue flame", "style": "painted", "seed": 40})
p = L.prompt("statuses", "status.burn")
assert "a blue flame" in p and p.startswith(sp["styles"]["painted"]["prefix"]), p

with FakeComfy() as fake:
    # icons: fixed seed → seed, seed+1; transparent master kept; finished icon at the category size
    cids = L.generate("statuses", "status.burn", n=2)
    assert len(cids) == 2 and [L.store.read_meta(L.entry_dir("statuses", "status.burn") / c)["seed"] for c in cids] == [40, 41]
    assert "a blue flame" in info(fake.prompts[-1], fake.uploads)["prompt"]
    assert "RemoveBackground" in info(fake.prompts[-1], fake.uploads)["classes"]
    d = L.entry_dir("statuses", "status.burn") / cids[0]
    assert Image.open(d / "icon.png").size == (64, 64) and Image.open(d / "master.png").mode == "RGBA"
    meta = L.store.read_meta(d)
    assert meta["prompts"]["prompt"] == p and meta["params"]["style"] == "painted" and meta["kind"] == "txt2img"

    # the finish re-runs without the GPU: entry override, then the category
    n = len(fake.prompts)
    L.save_entry("statuses", "status.burn", {"finish": {"size": 32, "colors": 8}})
    assert Image.open(d / "icon.png").size == (32, 32) and len(fake.prompts) == n
    L.save_entry("statuses", "status.burn", {"finish": {"size": None, "colors": None}})
    assert "statuses/status.burn" in L.spec()["entries"] and "finish" not in L.spec()["entries"]["statuses/status.burn"]
    L.save_category("statuses", {"finish": {"size": 48}})
    assert Image.open(d / "icon.png").size == (48, 48)

    # variations: img2img from the master, background removed again, linked to its source
    v = L.vary("statuses", "status.burn", cids[0], n=1, denoise=0.3)
    vm = L.store.read_meta(L.entry_dir("statuses", "status.burn") / v[0])
    assert vm["inputs"] == {"from": cids[0]} and vm["kind"] == "vary" and vm["denoise"] == 0.3
    assert any("KSampler" in info(x, fake.uploads)["classes"] for x in fake.prompts[-2:])

    # portraits: no background removal; portrait at the finish width + a square face thumb
    pid = e["portraits"][0]["id"]
    pc = L.generate("portraits", pid, n=1, seed=7)
    assert "RemoveBackground" not in info(fake.prompts[-1], fake.uploads)["classes"]
    pd = L.entry_dir("portraits", pid) / pc[0]
    assert Image.open(pd / "portrait.png").width == 512 and Image.open(pd / "thumb.png").size == (128, 128)
    L.save_entry("portraits", pid, {"finish": {"crop": [0.1, 0.1, 0.3]}})
    assert Image.open(pd / "thumb.png").size == (128, 128)

# approve + export: icons and portraits with their indexes; unapproved art leaves the export
L.approve("statuses", "status.burn", cids[1])
L.approve("portraits", pid, pc[0])
r = L.export()
art = game / "Assets" / "StreamingAssets" / "Art"
assert r["exported"]["statuses"] == 1 and r["exported"]["portraits"] == 1 and r["exported"]["items"] == 0
assert json.loads((art / "Icons" / "statuses" / "index.json").read_text()) == {"status.burn": "status.burn.png"}
idx = json.loads((art / "Portraits" / "index.json").read_text())
assert idx[pid]["thumb"] == f"{pid}.thumb.png" and (art / "Portraits" / f"{pid}.thumb.png").exists()
L.approve("statuses", "status.burn", cids[1], on=False)
L.export(["statuses"])
assert not (art / "Icons" / "statuses" / "status.burn.png").exists()

# archive: out of the entry, into archive/limner, and unapproved
L.approve("statuses", "status.burn", cids[0])
L.archive("statuses", "status.burn", cids[0])
assert not (L.entry_dir("statuses", "status.burn") / cids[0]).exists()
assert (data / "archive" / "limner" / "statuses" / "status.burn" / cids[0] / "master.png").exists()
assert "status.burn" not in L._state().load()["approved"].get("statuses", {})

# looks: add and remove (each makes two portrait entries)
ak = next(iter(sp["archetypes"]))
L.save_look(ak, "test", "a test look")
assert len(L.entries()["portraits"]) == 2 * n_looks + 2
L.save_look(ak, "test", "")
assert len(L.entries()["portraits"]) == 2 * n_looks

# the page's data
doc = L.doc()
assert set(doc["categories"]) == set(L.CATS) and doc["categories"]["statuses"]["entries"][0]["candidates"]

# overnight batches: entries × styles × variations; a seed sweep never repeats a seed; variation words + batch id in meta
bid, plan, est = L.plan_batch("statuses", ["status.burn"], ["pixel", "painted", "nope"], ["", "in moonlight"], 3, "sweep", 100)
assert est == {"jobs": 4, "images": 12, "minutes": est["minutes"]} and [a["seed"] for a in plan] == [100, 103, 106, 109]
assert {a["style"] for a in plan} == {"pixel", "painted"} and all(a["retries"] == 2 and a["batch"] == bid for a in plan)
assert L.plan_batch("statuses", ["status.burn"], None, None, 2)[1][0]["style"] is None   # the entry/category default
with FakeComfy() as fake:
    c = L.generate("statuses", "status.burn", 1, 5, "pixel", "in moonlight", bid, timeout=30)[0]
    assert "a blue flame, in moonlight" in info(fake.prompts[-1], fake.uploads)["prompt"]
m = L.store.read_meta(L.entry_dir("statuses", "status.burn") / c)
assert m["batch"] == bid and m["extra"] == "in moonlight"

# review: candidates newer than the last "Mark reviewed" are new (approved ones don't count)
e = next(x for x in L.doc()["categories"]["statuses"]["entries"] if x["id"] == "status.burn")
assert e["new"] >= 1 and any(x["new"] for x in e["candidates"])
import time; time.sleep(1.1); L.mark_reviewed("statuses")
assert L.doc()["categories"]["statuses"]["new"] == 0

# the job queue: a failing job with retries is queued again (retries - 1); a job interrupted by a restart is re-queued
from hdart import jobs
calls = []
@jobs.kind("t_flaky", gpu=False)
def _flaky(a):
    calls.append(a["retries"])
    if len(calls) < 3:
        raise RuntimeError("boom")
    return "ok"
q = jobs.Jobs(tmp / "q.json", autostart=False)
q.add("t_flaky", {"retries": 2}, "Limner batch b1 · x")
q.drain()
assert calls == [2, 1, 0] and [j["status"] for j in q.items] == ["failed", "failed", "done"], q.items
assert q.items[-1]["label"] == "Limner batch b1 · x (retry)"
q.add("t_flaky", {"retries": 1}, "Limner batch b2 · y"); q.add("t_flaky", {}, "other")
q.items[-2]["status"] = "running"; q.items[-1]["status"] = "running"; q._save()
q2 = jobs.Jobs(tmp / "q.json", autostart=False)
assert [(j["status"], j["args"].get("retries")) for j in q2.items[-2:]] == [("queued", 0), ("failed", None)]
q2.add("t_flaky", {}, "Limner batch b3 · z"); q2.add("t_flaky", {}, "Limner batch b4 · z")
assert q2.cancel_where("Limner batch b3") == 1 and q2.cancel_where("Limner batch") == 2   # b2's retry + b4

# the watchdog's files: no heartbeat → not alive; a fresh one → alive
from hdart import watchdog
assert not watchdog.alive() and watchdog.status()["alive"] is False
(data / "work").mkdir(exist_ok=True)
(data / "work" / "watchdog.json").write_text(json.dumps({"pid": 1, "started": time.time(), "beat": time.time()}))
watchdog.log("test line")
assert watchdog.alive() and watchdog.status()["log"][-1].endswith("test line")

assert (config.ROOT / "specs" / "limner.json").read_text(encoding="utf-8") == repo_spec, "the test touched the real spec"
os.environ.pop("HD_PROJECT"); os.environ.pop("HD_DATA"); shutil.rmtree(tmp, ignore_errors=True)
print("test_limner: ok")
