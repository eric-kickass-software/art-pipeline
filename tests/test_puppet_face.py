"""Face tab backend (hdart.puppet_face), on the fake ComfyUI: paint passes return their init; hair passes paint the masked
area in the green key, face passes draw a dark eye with a magenta iris. Run: uv run python tests/test_puppet_face.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, os, shutil, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import hdart.workshop as ws
import hdart.puppet_paint as ppt
import hdart.puppet_face as pf

tmp = Path(tempfile.mkdtemp())
os.environ["HD_DATA"] = str(tmp)   # the data root (sprites/ai, archive, ...) is a temp folder
from hdart.fake_comfy import FakeComfy, info
calls = []


def respond(nodes, uploads):
    i = info(nodes, uploads); calls.append(i["prompt"])
    a = np.asarray(i["init"].convert("RGB")).copy(); m = i["mask"]   # the back-hair edit has no mask: returns its input
    if m is not None and "green-screen" in i["prompt"]:   # hair in the key colour, shaded top to bottom
        yy = np.mgrid[:a.shape[0], :a.shape[1]][0] / a.shape[0]
        g = np.stack([30 + 0 * yy, 150 + 80 * (1 - yy), 30 + 0 * yy], -1).astype(np.uint8); a[m] = g[m]
    elif m is not None and "magenta" in i["prompt"]:      # a face: a dark eye with a magenta iris in the masked area
        ys, xs = np.nonzero(m); cx, cy = int(xs.mean()), int(ys.mean())
        im2 = Image.fromarray(a); d = ImageDraw.Draw(im2)
        for dx in (-60, 60):
            d.ellipse([cx + dx - 30, cy - 40, cx + dx + 30, cy + 20], fill=(20, 16, 18))
            d.ellipse([cx + dx - 18, cy - 28, cx + dx + 18, cy + 8], fill=(200, 30, 200))
        a = np.asarray(im2)
    return [Image.fromarray(a)]


fake = FakeComfy(respond).start()
for sex in ("male", "female"):
    c = pf.paint(f"human.{sex}", n=1, seed=1); ppt.approve(f"human.{sex}", c[0])

# prompts: the race's text in the shared wording; the hair prompt asks for the key colour
cp = pf.composed("human", "male")
assert pf.spec()["races"]["human"]["hair"] in cp["hair"]["front"] and "green-screen" in cp["hair"]["front"] and "magenta" in cp["face"]

h = pf.generate("human", "hair", "male", n=1, seed=3)[0]
d = pf.out_dir("human", "hair") / h; meta = json.loads((d / "meta.json").read_text())
for v in ("front", "back"):
    lev = np.asarray(Image.open(d / f"levels_{v}.png")); assert (lev[..., 3] > 0).sum() > 500, v   # the key found hair
    assert set(np.unique(lev[..., 0][lev[..., 3] > 0])) <= set(range(5)) | {254}
assert meta["keyed"]["front"] > 500 and "Three-quarter" in calls[-3] and "from behind" in calls[-2] and "reference image" in calls[-1]
assert (d / "edit_back.png").exists() and meta["keyed"]["back"] > 500   # AB2: the edit, then the guided back pass
for f in ("prev_male_front.png", "prev_female_back.png", "head_female.png"):
    assert (d / f).exists(), f
# the hair is cut by colour: no skin in it (the head under it stays the paint's)
lay = np.asarray(Image.open(d / "layer_front.png")); hm = lay[..., 3] > 0
assert (lay[hm][:, 1].astype(int) - lay[hm][:, 0]).mean() > 60   # green-dominant (outline pixels aside)
# headroom: the hair grows above the head and stays clear of the canvas top (no flat cut, no `clipped` flag)
_, _, parts = pf.base("human.male")
for v in ("front", "back"):
    top = np.nonzero(np.asarray(Image.open(d / f"layer_{v}.png"))[..., 3].any(1))[0].min()
    head_top = np.nonzero((parts[v]["head"][..., 3] > 127).any(1))[0].min()
    assert 0 < top < head_top - 20, (v, top, head_top)
assert "clipped" not in meta

f = pf.generate("human", "face", "female", n=1, seed=5)[0]
fd = pf.out_dir("human", "face") / f
ir = np.asarray(Image.open(fd / "levels_front.png")); assert (ir[..., 3] > 0).sum() > 100   # irises keyed
assert json.loads((fd / "meta.json").read_text())["keyed"]["front"] > 100

# another hair colour for the previews; sets; archive
urls = pf.previews("human", "hair", h, "blonde", save=False)
assert set(urls) >= {"prev_male_front.png", "head_male.png"} and urls["prev_male_front.png"].startswith("data:image/png")
st = pf.set_member("human", "hair", h)
assert st["sets"]["hair"] == [h] and st["kinds"]["hair"][0]["in_set"]
try:
    pf.archive("human", "hair", h); raise SystemExit("no error")
except ValueError:
    pass
h2 = pf.generate("human", "hair", "male", n=1, seed=8)[0]
pf.archive("human", "hair", h2); assert not (pf.out_dir("human", "hair") / h2).exists()

# editing a layer (Parts editor): the doc puts the layer after the head, a save keeps the original, recomputes levels
import base64, io
ld = pf.layer_doc("human", "face", f)
assert list(ld["views"]) == ["front"] and ld["views"]["front"]["order"].index("layer") == ld["views"]["front"]["order"].index("head") + 1
assert ld["editable"] == ["layer"] and ld["views"]["front"]["parts"]["head"].endswith("ctx_head_front.png")
L0 = np.asarray(Image.open(fd / "layer_front.png")).copy(); L1 = L0.copy(); L1[L1[..., 3] > 0] = 0   # erase everything
buf = io.BytesIO(); Image.fromarray(L1).save(buf, "PNG")
pf.save_layer("human", "face", f, "front", "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
assert (fd / "layer_orig_front.png").exists() and json.loads((fd / "meta.json").read_text())["keyed"]["front"] == 0
pf.revert_layer("human", "face", f)
assert np.array_equal(np.asarray(Image.open(fd / "layer_front.png")), L0) and not (fd / "layer_orig_front.png").exists()
hl = pf.levels_from_layer("hair", np.asarray(Image.open(d / "layer_front.png")))
assert (hl[..., 3] > 0).sum() > 500 and set(np.unique(hl[..., 0][hl[..., 3] > 0])) <= set(range(5)) | {254}

# hair bands (Workshop Editor): edited by hand, kept by a repaint, redone on request
hd = pf.out_dir("human", "hair") / h
hdoc = pf.layer_doc("human", "hair", h); assert hdoc["views"]["front"]["levels"]["layer"].endswith("levels_front.png") and hdoc["bandsEdited"] == []
lv0 = np.asarray(Image.open(hd / "levels_front.png")).copy(); band = (lv0[..., 3] > 127) & (lv0[..., 0] < 254)
paint4 = np.zeros_like(lv0); paint4[..., 0] = 4; paint4[..., 3] = 255   # every band pixel to the lightest level
buf = io.BytesIO(); Image.fromarray(paint4).save(buf, "PNG"); url4 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
hdoc = pf.save_bands("human", "hair", h, "front", url4)
lv1 = np.asarray(Image.open(hd / "levels_front.png"))
assert (lv1[..., 0][band] == 4).all() and np.array_equal(lv1[..., 0][~band], lv0[..., 0][~band]) and hdoc["bandsEdited"] == ["front/layer"]
buf = io.BytesIO(); Image.open(hd / "layer_front.png").save(buf, "PNG")   # a repaint (unchanged) keeps the hand bands
pf.save_layer("human", "hair", h, "front", "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
assert (np.asarray(Image.open(hd / "levels_front.png"))[..., 0][band] == 4).all()
hdoc = pf.reset_bands("human", "hair", h, "front"); assert hdoc["bandsEdited"] == []
assert not (np.asarray(Image.open(hd / "levels_front.png"))[..., 0][band] == 4).all()
pf.revert_layer("human", "hair", h); assert "bands_edited" not in json.loads((hd / "meta.json").read_text())

# Ramps: the puppet wears the set's hair + the newest face; three level maps
rp = pf.ramp_preview("human.male")
assert rp["hair"] == h and rp["face"] == f and {"art", "levels", "hair", "eyes"} <= set(rp["facings"]["front"]) and "hair" in rp["facings"]["back"]

# saving keeps the key colours (a race's text save from the tab doesn't send them)
sv = pf.spec(); sv.pop("hair_key", None); sv.pop("eye_key", None); sv["races"]["human"]["hair"] = "long braided hair"
out_ = pf.save_spec(sv); assert out_["hair_key"] == "#22c822" and out_["eye_key"] == "#d020d0"
# shared prompts are validated
bad = pf.spec(); bad["hair"]["front"] = "no placeholder"
try:
    pf.save_spec(bad); raise SystemExit("no error")
except ValueError:
    pass
fake.stop(); shutil.rmtree(tmp)
print("ok")
