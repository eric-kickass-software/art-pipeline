"""Limner (hdart.limner): entry lists from the contract, prompt layers (style > category > entry), generation and
variations on the fake ComfyUI, the finish re-run without the GPU, approve / archive, export into a temp game project.
Never touches the real spec, data or game. Run: uv run python tests/test_limner.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
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
_s = json.loads(L.spec_path().read_text(encoding="utf-8")); _s["added"] = {}   # Eric's own ＋ Add entries would change the lists below
L.spec_path().write_text(json.dumps(_s), encoding="utf-8")
repo_spec = (config.ROOT / "specs" / "limner.json").read_text(encoding="utf-8")   # the live spec: must stay untouched

# entries: the contract's wanted lists + the spec's gear, skill placeholders per damage type, portraits per look × gender
e = L.entries()
assert [x["id"] for x in e["items"]] == ["item.bandage"] and e["items"][0]["draft"] == "bandage"
assert [x["id"] for x in e["skills"]] == ["placeholder.fire.active", "placeholder.fire.passive",
                                          "placeholder.holy.active", "placeholder.holy.passive"]
sp = L.spec()
n_looks = sum(len(a["looks"]) for a in sp["archetypes"].values()) * len(sp["races"])   # per race (2026-11-07)
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
assert "status.burn" not in L.state_store().load()["approved"].get("statuses", {})

# looks: add and remove (each makes two portrait entries)
ak = next(iter(sp["archetypes"]))
L.save_look(ak, "test", "a test look")
assert len(L.entries()["portraits"]) == 2 * n_looks + 2 * len(sp["races"])
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

# items vs gear: the game's content decides (a slot = equippable → Gear); delivered ids stay listed
content = game / "Assets" / "StreamingAssets" / "Content" / "core" / "items"
content.mkdir(parents=True)
(content / "items.json").write_text(json.dumps({"items": [
    {"id": "item.bandage", "name": "Bandage", "kind": "consumable", "text": "Stops bleeding."},
    {"id": "item.iron_helm", "name": "Iron Helm", "kind": "armor", "slot": "head"},
    {"id": "item.dagger", "name": "Dagger", "kind": "weapon", "slot": "main", "weaponType": "dagger"}]}), encoding="utf-8")
e = L.entries()
assert [x["id"] for x in e["items"]] == ["item.bandage"] and e["items"][0]["draft"] == "Bandage, stops bleeding"
g = {x["id"]: x for x in e["gear"]}
assert g["item.iron_helm"]["group"] == "Armour" and g["item.dagger"]["sub"] == "dagger" and "slot.head.cloth" in g
assert e["gear"][0]["group"] == "Armour" and e["gear"][-1]["group"].startswith("Fallbacks")
assert len(e["ui"]) == len(L.spec()["subjects"]["ui"]) and all(x["id"].startswith("ui.") for x in e["ui"])

# moving entries keeps everything: candidates (and archived ones), approval, overrides; never overwrites
helm = L.root() / "items" / "item.iron_helm"
with FakeComfy():
    hc = L.generate("items", "item.bandage", 1)   # a real candidate, then pose it as an old Items one of the helm
shutil.copytree(L.entry_dir("items", "item.bandage") / hc[0], helm / hc[0])
(data / "archive" / "limner" / "items" / "item.iron_helm" / "old").mkdir(parents=True)
L.approve("items", "item.iron_helm", hc[0])
L.save_entry("items", "item.iron_helm", {"subject": "a dented helm"})
assert L.move_entries("items", "gear", dry_run=True) == {"would_move": ["item.iron_helm"], "clash": []}
r = L.move_entries("items", "gear")
assert r["moved"] == ["item.iron_helm"] and not helm.exists() and (L.root() / "gear" / "item.iron_helm" / hc[0] / "icon.png").exists()
assert (data / "archive" / "limner" / "gear" / "item.iron_helm" / "old").exists()
assert L.state_store().load()["approved"]["gear"]["item.iron_helm"] == hc[0] and "item.iron_helm" not in L.state_store().load()["approved"].get("items", {})
assert L.subject("gear", "item.iron_helm") == "a dented helm" and "items/item.iron_helm" not in L.spec()["entries"]
assert L.store.read_meta(L.root() / "gear" / "item.iron_helm" / hc[0])["category"] == "gear"
assert L.move_entries("items", "gear")["moved"] == []   # idempotent; bandage isn't equippable
assert L.export(["gear"])["exported"]["gear"] == 1 and (art / "Icons" / "gear" / "item.iron_helm.png").exists()

# your own entries: add (prefix added, id from the name), use, remove (candidates kept), no duplicates across categories
assert L.add_entry("gear", "", "Silver Dagger!", "a slim silver dagger", "dagger")["id"] == "item.silver_dagger"
assert L.add_entry("ui", "menu.crafting", "Crafting")["id"] == "ui.menu.crafting"
x = next(x for x in L.entries()["gear"] if x["id"] == "item.silver_dagger")
assert x["added"] and x["group"] == "Added here" and x["sub"] == "dagger" and "a slim silver dagger" in L.prompt("gear", x["id"])
for bad in (lambda: L.add_entry("items", "item.silver_dagger", "x"), lambda: L.add_entry("gear", "item.dagger", "x"),
            lambda: L.add_entry("statuses", "status.x", "x"), lambda: L.add_entry("ui", "Bad Id!", "")):
    try:
        bad(); raise AssertionError("should fail")
    except ValueError:
        pass
with FakeComfy():
    sc = L.generate("gear", "item.silver_dagger", 1)
L.approve("gear", "item.silver_dagger", sc[0])
assert L.export(["gear"])["exported"]["gear"] == 2
L.remove_entry("gear", "item.silver_dagger")
assert "item.silver_dagger" not in {x["id"] for x in L.entries()["gear"]} and (L.root() / "gear" / "item.silver_dagger" / sc[0]).exists()
assert L.export(["gear"])["exported"]["gear"] == 1   # an approval without an entry isn't exported
try:
    L.remove_entry("gear", "item.dagger"); raise AssertionError("game entries can't be removed")
except ValueError:
    pass
assert L.doc()["categories"]["ui"]["add_prefix"] == "ui." and L.doc()["categories"]["classes"]["add_prefix"] is None

# player portraits per race: humans keep their old ids, other races get portrait.<race>.…; the traits follow the look
ids = {x["id"]: x for x in L.entries()["portraits"]}
ak, lk = next(iter(sp["archetypes"])), next(iter(sp["archetypes"][next(iter(sp["archetypes"]))]["looks"]))
assert f"portrait.{ak}.{lk}.m" in ids and ids[f"portrait.{ak}.{lk}.m"]["race"] == "human"
assert ids[f"portrait.halforc.{ak}.{lk}.f"]["draft"].startswith("a female half-orc ") and "tusks" in ids[f"portrait.halforc.{ak}.{lk}.f"]["draft"]
L.save_race("dwarf", {"traits": "stocky, braided beard"})
assert f"portrait.dwarf.{ak}.{lk}.m" in {x["id"] for x in L.entries()["portraits"]}

# monsters + NPCs from the game's content: a portrait and a token each; beasts and masses get their own framing
mon = game / "Assets" / "StreamingAssets" / "Content" / "core"
(mon / "monsters").mkdir(parents=True); (mon / "npcs").mkdir(parents=True)
(mon / "monsters" / "m.json").write_text(json.dumps([
    {"id": "monster.skeleton", "name": "Skeleton", "role": "brute", "family": "family.undead", "visual": {"placeholder": {"shape": "humanoid"}}},
    {"id": "monster.web_spider", "name": "Web Spider", "role": "controller", "family": "family.vermin", "visual": {"placeholder": {"shape": "beast"}}},
    {"id": "monster.slime", "name": "Slime", "family": "family.slimes", "visual": {"placeholder": {"shape": "beast"}}},
    {"id": "monster.lich", "name": "Lich", "family": "family.undead", "visual": {"placeholder": {"shape": "humanoid", "scale": 1.5}}}]), encoding="utf-8")
(mon / "npcs" / "n.json").write_text(json.dumps([
    {"id": "npc.trader", "name": "Trader", "text": "A friendly trader.", "visual": {"placeholder": {"shape": "humanoid"}}},
    {"id": "npc.restoring_pond", "name": "Pond", "visual": {"placeholder": {"shape": "block"}}}]), encoding="utf-8")
e = L.entries()
assert [x["id"] for x in e["monsters"]] == ["monster.skeleton", "monster.web_spider", "monster.slime", "monster.lich"]
assert [x["id"] for x in e["npcs"]] == ["npc.trader"] and len(e["tokens"]) == 5   # the pond is a board object
assert e["monsters"][3]["sub"] == "boss" and e["monsters"][2]["shape"] == "mass" and e["tokens"][0]["group"] == "Monsters · Undead"
assert L.style_of("monsters", "monster.skeleton") == "semireal" and L.style_of("monsters", "monster.web_spider") == "creature"
assert L.style_of("tokens", "monster.slime") == "token_mass" and "bust portrait" not in L.prompt("monsters", "monster.web_spider")
with FakeComfy():
    tc = L.generate("tokens", "monster.skeleton", 1)[0]
    mc = L.generate("monsters", "monster.skeleton", 1)[0]
    nc = L.generate("npcs", "npc.trader", 1)[0]
td = L.entry_dir("tokens", "monster.skeleton") / tc
tok = Image.open(td / "token.png")
assert max(tok.size) <= 512 and Image.open(td / "token.n.png").size == tok.size
L.save_entry("tokens", "monster.skeleton", {"finish": {"mirror": True}})   # refinish, no GPU
assert Image.open(td / "token.png").size == tok.size
for c_, i_, x_ in (("tokens", "monster.skeleton", tc), ("monsters", "monster.skeleton", mc), ("npcs", "npc.trader", nc)):
    L.approve(c_, i_, x_)
r = L.export(["monsters", "tokens"])
idx = json.loads((art / "Portraits" / "index.json").read_text())
assert idx["monster.skeleton"]["archetype"] == "monster" and idx["npc.trader"]["archetype"] == "npc" and pid in idx   # one index
assert idx[pid]["race"] == "human" and r["exported"]["monsters"] == 1 and r["exported"]["npcs"] == 1
ti = json.loads((art / "Monsters" / "index.json").read_text())
assert ti["monster.skeleton"]["file"] == "monster.skeleton/token.png" and (art / "Monsters" / "monster.skeleton" / "token.n.png").exists()
assert ti["monster.skeleton"]["pivot"][0] == 0.5 and ti["monster.skeleton"]["facing"] == "right"
L.approve("tokens", "monster.skeleton", tc, on=False)
L.export(["tokens"])
assert not (art / "Monsters" / "monster.skeleton").exists()

assert (config.ROOT / "specs" / "limner.json").read_text(encoding="utf-8") == repo_spec, "the test touched the real spec"
os.environ.pop("HD_PROJECT"); os.environ.pop("HD_DATA"); shutil.rmtree(tmp, ignore_errors=True)
print("test_limner: ok")
