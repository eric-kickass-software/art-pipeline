"""Face tab backend (hdart.puppet_face), ComfyUI stubbed: paint passes return their init; hair passes paint the masked
area in the green key, face passes draw a dark eye with a magenta iris. Run: uv run python tests/test_puppet_face.py"""
import json, shutil, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import hdart.ai_kit as ak
import hdart.workshop as ws
import hdart.puppet_paint as ppt
import hdart.puppet_face as pf

tmp = Path(tempfile.mkdtemp())
ak.AI_DIR = tmp / "ai"; ws.CAND = ak.AI_DIR / "candidates"; ws.STATE = ak.AI_DIR / "workshop.json"; ws.ARCHIVE = tmp / "archive"
shutil.copy(pf.SPEC, tmp / "face.json"); pf.SPEC = tmp / "face.json"
uploads, calls = {}, []


class FakeComfy:
    @staticmethod
    def upload(p):
        uploads[Path(p).name] = Path(p); return Path(p).name
    @staticmethod
    def build_img2img(name, prompt, **kw):
        calls.append(prompt); return {"init": name, "mask": kw.get("mask_name"), "prompt": prompt}
    @staticmethod
    def run(nodes, out_dir):
        im = Image.open(uploads[nodes["init"]]).convert("RGB"); a = np.asarray(im).copy()
        m = np.asarray(Image.open(uploads[nodes["mask"]]).convert("L")) > 127 if nodes["mask"] else None
        if m is not None and "green-screen" in nodes["prompt"]:   # hair in the key colour, shaded top to bottom
            yy = np.mgrid[:a.shape[0], :a.shape[1]][0] / a.shape[0]
            g = np.stack([30 + 0 * yy, 150 + 80 * (1 - yy), 30 + 0 * yy], -1).astype(np.uint8); a[m] = g[m]
        elif m is not None and "magenta" in nodes["prompt"]:      # a face: a dark eye with a magenta iris in the masked area
            ys, xs = np.nonzero(m); cx, cy = int(xs.mean()), int(ys.mean())
            im2 = Image.fromarray(a); d = ImageDraw.Draw(im2)
            for dx in (-60, 60):
                d.ellipse([cx + dx - 30, cy - 40, cx + dx + 30, cy + 20], fill=(20, 16, 18))
                d.ellipse([cx + dx - 18, cy - 28, cx + dx + 18, cy + 8], fill=(200, 30, 200))
            a = np.asarray(im2)
        p = Path(out_dir) / f"out_{len(calls)}.png"; Image.fromarray(a).save(p); return [p]


ppt.comfy = FakeComfy; pf.ppt = ppt
for sex in ("male", "female"):
    c = ppt.paint(f"human.{sex}", n=1, seed=1); ppt.approve(f"human.{sex}", c[0])

# prompts: the race's text in the shared wording; the hair prompt asks for the key colour
cp = pf.composed("human", "male")
assert pf.spec()["races"]["human"]["hair"] in cp["hair"]["front"] and "green-screen" in cp["hair"]["front"] and "magenta" in cp["face"]

h = pf.generate("human", "hair", "male", n=1, seed=3)[0]
d = pf.out_dir("human", "hair") / h; meta = json.loads((d / "meta.json").read_text())
for v in ("front", "back"):
    lev = np.asarray(Image.open(d / f"levels_{v}.png")); assert (lev[..., 3] > 0).sum() > 500, v   # the key found hair
    assert set(np.unique(lev[..., 0][lev[..., 3] > 0])) <= set(range(5)) | {254}
assert meta["keyed"]["front"] > 500 and "Three-quarter" in calls[-2] and "reference image" in calls[-1]
for f in ("prev_male_front.png", "prev_female_back.png", "head_female.png"):
    assert (d / f).exists(), f
# the hair is cut by colour: no skin in it (the head under it stays the paint's)
lay = np.asarray(Image.open(d / "layer_front.png")); hm = lay[..., 3] > 0
assert (lay[hm][:, 1].astype(int) - lay[hm][:, 0]).mean() > 60   # green-dominant (outline pixels aside)

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

# Ramps: the puppet wears the set's hair + the newest face; three level maps
rp = pf.ramp_preview("human.male")
assert rp["hair"] == h and rp["face"] == f and {"art", "levels", "hair", "eyes"} <= set(rp["facings"]["front"]) and "hair" in rp["facings"]["back"]

# shared prompts are validated
bad = pf.spec(); bad["hair"]["front"] = "no placeholder"
try:
    pf.save_spec(bad); raise SystemExit("no error")
except ValueError:
    pass
shutil.rmtree(tmp)
print("ok")
