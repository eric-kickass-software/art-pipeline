"""Item Workshop backend (hdart.puppet_items) on the fake ComfyUI: a fitted item (cloth trousers) and a held one (iron sword) are
generated on a fake-painted puppet, keyed into cel levels per material, split into per-part layers, approved, worn
and re-keyed. The fake paints every masked area in the item material's key colour (shaded top to bottom, so there are
several cel levels) and returns the txt2img sword as a leather grip + metal blade cut-out.
Run: uv run python tests/test_puppet_items.py"""
import json, shutil, tempfile
from pathlib import Path
import numpy as np
from PIL import Image
import hdart.ai_kit as ak
import hdart.workshop as ws
import hdart.puppet_paint as ppt
import hdart.puppet_face as pf
import hdart.puppet_items as pi
from hdart.fake_comfy import FakeComfy, info

tmp = Path(tempfile.mkdtemp())
ak.AI_DIR = tmp / "ai"; ws.CAND = ak.AI_DIR / "candidates"; ws.STATE = ak.AI_DIR / "workshop.json"; ws.ARCHIVE = tmp / "archive"
shutil.copy(pi.SPEC, tmp / "items.json"); pi.SPEC = tmp / "items.json"
doc = pi.spec()
KEY = {m: np.array([int(e["key"][i:i + 2], 16) for i in (1, 3, 5)], float) for m, e in doc["materials"].items()}
calls, paint_key = [], {"m": None}


def shaded(key, h, w):
    yy = np.linspace(1.0, 0.45, h)[:, None, None]
    return np.clip(np.broadcast_to(key * yy, (h, w, 3)), 0, 255).astype(np.uint8)


def respond(nodes, uploads):
    i = info(nodes, uploads); calls.append(i["prompt"])
    if i["init"] is None:   # txt2img: the held item on white, background removed
        im = np.zeros((768, 768, 4), np.uint8)
        im[120:560, 360:408, :3] = shaded(KEY["metal"], 440, 48); im[560:660, 368:400, :3] = shaded(KEY["leather"], 100, 32)
        im[120:660, 360:408, 3] = np.where(im[120:660, 360:408, :3].sum(-1) > 0, 255, 0)
        return [Image.fromarray(im, "RGBA")]
    a = np.asarray(i["init"].convert("RGB")).copy()
    if i["mask"] is not None and paint_key["m"]:
        m = i["mask"]; a[m] = shaded(KEY[paint_key["m"]], *a.shape[:2])[m]
    return [Image.fromarray(a)]


fake = FakeComfy(respond).start()
c = ppt.paint("human.male", n=1, seed=1)[0]; ppt.approve("human.male", c)

# fitted: zones from the spec's rules, both views keyed, split per body part, levels in 0..4 on the cloth channel
paint_key["m"] = "cloth"
t = pi.generate("human", "item.cloth_trousers", seed=2)
d = pi.out_dir("human", "item.cloth_trousers") / t; meta = json.loads((d / "meta.json").read_text())
assert meta["on"] == "human.male" and meta["paint"] == c and meta["keyed"]["front"] > 2000 and meta["keyed"]["back"] > 2000
assert any("from behind" in (p or "") for p in calls)   # the back is guided by an edit of the painted front (AB2)
for v in ("front", "back"):
    lev = np.asarray(Image.open(d / f"levels_{v}.png")); on = lev[..., 3] > 0
    assert set(np.unique(lev[..., 1][on])) <= {4}, v                                         # cloth = channel 4
    assert set(np.unique(lev[..., 0][on])) <= set(range(pi.LEVELS)) | {254} and len(np.unique(lev[..., 0][on])) >= 3, v
    parts = {f.stem for f in (d / "parts" / v).glob("*.png")}
    assert "torso" in parts and any(p.startswith("leg") for p in parts) and not any(p.startswith("arm") for p in parts), (v, parts)
    assert {f.stem for f in (d / "plevels" / v).glob("*.png")} == parts
assert (d / "prev_male_front.png").exists()

# details sketch: saved with the zone (snapped to the palette, hard alpha; material strokes are zone in the UI), stamped on
# the guide FLUX starts from; an empty sketch removes the file, reset removes both
import base64, io
pi.ZONES = tmp / "zones"
_, sp, parts = pf.base("human.male")
_, Z = pi.zones_for(sp, "front", parts["front"], "item.cloth_trousers", doc["items"]["item.cloth_trousers"])
ys, xs = np.nonzero(Z); y0, xc = ys.min(), int(xs.mean())
sk = np.zeros((pi.N, pi.N, 4), np.uint8)
sk[y0 + 4:y0 + 24, xc - 40:xc + 40] = (*KEY["leather"].astype(np.uint8), 255)     # a leather belt
sk[y0 + 24:y0 + 26, xc - 40:xc + 40] = (40, 30, 30, 200)                         # a line, slightly off the line colour
sk[y0 + 40, xc] = (0, 255, 0, 60)                                                # faint: dropped
u = lambda a: "data:image/png;base64," + base64.b64encode((lambda b: (Image.fromarray(a).save(b, "PNG"), b.getvalue())[1])(io.BytesIO())).decode()
r = pi.save_zone("item.cloth_trousers", "front", u(np.dstack([np.where(Z, 255, 0).astype(np.uint8)] * 3)), sketch=u(sk))
saved = np.asarray(Image.open(pi.sketch_path("item.cloth_trousers", "front")))
assert r["sketch_px"] == 80 * 22 and set(np.unique(saved[..., 3])) == {0, 255}
assert tuple(saved[y0 + 25, xc, :3]) == pi._rgb(pi.LINE) and saved[y0 + 40, xc, 3] == 0
t2 = pi.generate("human", "item.cloth_trousers", seed=5)
d2 = pi.out_dir("human", "item.cloth_trousers") / t2; meta2 = json.loads((d2 / "meta.json").read_text())
assert meta2["sketch"] == ["front"], meta2.get("sketch")
g = np.asarray(Image.open(d2 / "guide_front.png").convert("RGB")).astype(int)
belt = g[y0 + 8:y0 + 20, xc - 30:xc + 30].reshape(-1, 3).mean(0)
assert belt[2] > belt[1] + 60, belt                                               # blue (leather), not the green zone tint
assert g[y0 + 25, xc].sum() < 150                                                 # the dark line
pi.save_zone("item.cloth_trousers", "front", u(np.dstack([np.where(Z, 255, 0).astype(np.uint8)] * 3)), sketch=u(np.zeros_like(sk)))
assert not pi.sketch_path("item.cloth_trousers", "front").exists() and pi.mask_path("item.cloth_trousers", "front").exists()
pi.reset_zone("item.cloth_trousers", "front"); assert not pi.mask_path("item.cloth_trousers", "front").exists()
pi.archive("human", "item.cloth_trousers", t2); pi.ZONES = ak.ROOT / "specs" / "item_zones"

# held: the cut-out keyed per material (leather grip, metal blade) and framed
paint_key["m"] = None
s = pi.generate("human", "item.iron_sword", seed=3)
sd = pi.out_dir("human", "item.iron_sword") / s
hl = np.asarray(Image.open(sd / "held_levels.png")); on = hl[..., 3] > 0
assert {5, 6} <= set(np.unique(hl[..., 1][on])) and Image.open(sd / "held.png").size == hl.shape[1::-1]

# approve, resolve, wear: the dressed build has the item over the paint; candidates listing
pi.approve("human", "item.cloth_trousers", t); pi.approve("human", "item.iron_sword", s)
assert pi.resolve("human", "item.cloth_trousers", "human.male") == t and pi.resolve("human", "item.cloth_trousers", "human.female") == t
r = pi.render("human.male", [("item.cloth_trousers", t, {}), ("item.iron_sword", s, {})], frames=0)
bare = pi.render("human.male", [], frames=0)
assert np.any(np.asarray(r["front"][0]) != np.asarray(bare["front"][0]))
assert [x["id"] for x in pi.candidates("human", "item.cloth_trousers")] == [t]

# re-key from the saved paints (no GPU): same result
before = (d / "levels_front.png").read_bytes()
n_calls = len(calls)
k = pi.rekey("human", "item.cloth_trousers", t)
assert len(calls) == n_calls and k["front"] == meta["keyed"]["front"] and (d / "levels_front.png").read_bytes() == before

# the queue entry point (job kind "item") and archive
new = pi.job({"action": "generate", "race": "human", "item": "item.cloth_trousers", "n": 1, "seed": 4})
assert len(new) == 1 and new[0] != t
pi.archive("human", "item.cloth_trousers", new[0])
assert not (pi.out_dir("human", "item.cloth_trousers") / new[0]).exists() and pi.resolve("human", "item.cloth_trousers", "human.male") == t

# item bands (Workshop Editor): the doc lists each covered part's levels; hand edits change only band pixels, survive a
# repaint of the part, and are redone on request; revert brings back the originals
ld = pi.layer_doc("human", "item.cloth_trousers", t); lvs = ld["views"]["front"]["levels"]
assert lvs and ld["slot"] == "legs" and ld["bandsEdited"] == []
lk = sorted(lvs)[0]; part = lk[:-len(pi.LAYER_SEP)]; fl = d / "plevels" / "front" / f"{part}.png"
lv0 = np.asarray(Image.open(fl)).copy(); band = (lv0[..., 3] > 127) & (lv0[..., 0] < 254); assert band.any()
p4 = np.zeros_like(lv0); p4[..., 0] = 4; p4[..., 3] = 255
ld = pi.bands_save("human", "item.cloth_trousers", t, "front", {lk: u(p4)})
lv1 = np.asarray(Image.open(fl))
assert (lv1[..., 0][band] == 4).all() and np.array_equal(lv1[..., 1], lv0[..., 1]) and np.array_equal(lv1[..., 0][~band], lv0[..., 0][~band])
assert ld["bandsEdited"] == [f"front/{lk}"] and (d / "plevels_orig").exists()
pi.layer_save("human", "item.cloth_trousers", t, "front", {lk: u(np.asarray(Image.open(d / "parts" / "front" / f"{part}.png")))})
lv2 = np.asarray(Image.open(fl)); kept = band & (lv2[..., 3] > 127) & (lv2[..., 0] < 254) & (lv2[..., 1] == lv0[..., 1])
assert kept.sum() > 0.9 * band.sum() and (lv2[..., 0][kept] == 4).all()
ld = pi.bands_reset("human", "item.cloth_trousers", t, "front", lk); assert ld["bandsEdited"] == []
assert not (np.asarray(Image.open(fl))[..., 0][band] == 4).all()
pi.layer_revert("human", "item.cloth_trousers", t)
assert np.array_equal(np.asarray(Image.open(fl)), lv0) and "bands_edited" not in json.loads((d / "meta.json").read_text())
# the Workshop Editor's walk: a paint candidate wearing items as painted (ramps None) or in a ramp, with a skin ramp
pc = pf.base_cid("human.male")
strip = lambda **kw: np.asarray(Image.open(io.BytesIO(base64.b64decode(pi.editor_walk("human", "human.male", pc, "front", **kw)["strip"].split(",", 1)[1]))))
plain = strip(items=[]); assert plain.shape == (256, 256 * 8, 3)
painted = strip(items=[["item.cloth_trousers", t, None]]); ramped = strip(items=[["item.cloth_trousers", t, {"cloth": "red"}]])
assert np.any(painted != plain) and np.any(ramped != painted)
assert np.any(strip(items=[], skin="tan") != plain)
fake.stop(); shutil.rmtree(tmp)
print("ok")
