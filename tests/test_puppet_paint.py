"""Puppet Paint step + Parts editor backend (hdart.puppet_paint), on the fake ComfyUI: every pass returns its init
image, so the flow runs without a GPU. Run: uv run python tests/test_puppet_paint.py"""
import base64, io, json, shutil, tempfile
from pathlib import Path
import numpy as np
from PIL import Image
import hdart.ai_kit as ak
import hdart.workshop as ws
import hdart.puppet_paint as ppt

tmp = Path(tempfile.mkdtemp())
ak.AI_DIR = tmp / "ai"; ws.CAND = ak.AI_DIR / "candidates"; ws.STATE = ak.AI_DIR / "workshop.json"; ws.ARCHIVE = tmp / "archive"
from hdart.fake_comfy import FakeComfy, default_respond, info
calls = []


def respond(nodes, uploads):
    i = info(nodes, uploads); calls.append((i["prompt"], i["refs"])); return default_respond(nodes, uploads)


fake = FakeComfy(respond).start()
shutil.copy(ppt.SPEC, tmp / "paint.json"); ppt.SPEC = tmp / "paint.json"
body, clothes = ppt.prompts("human.female")
assert "female" in body and ppt.spec()["sexes"]["female"]["extra"] in body and "sports bra" in clothes

# the paint prompts are a spec edited in the Workshop: saving changes what gets sent, bad input is refused
sp = ppt.spec(); sp["sexes"]["female"]["clothes"] = "a red tunic"; sp["figure"]["front"] = "Hero {body} wearing {clothes}."
ppt.save_spec(sp)
c_ = ppt.composed("human.female")
assert c_["front"]["figure"].endswith("Hero " + ppt.prompts("human.female")[0] + " wearing a red tunic.")
assert c_["back"]["figure"].endswith(sp["back_reference"]) and "a red tunic" in c_["fix"]
for bad in ({"figure": {"front": "no placeholder", "back": "x {body}"}}, {"fix": "no placeholder"}):
    b_ = dict(ppt.spec(), **bad)
    try:
        ppt.save_spec(b_); raise SystemExit("no error")
    except ValueError:
        pass
sp["sexes"]["female"]["clothes"] = "plain grey sports bra and grey briefs, bald head, barefoot"; sp["figure"]["front"] = json.loads(
    (Path(ppt.__file__).parents[1] / "specs" / "paint.json").read_text())["figure"]["front"]
ppt.save_spec(sp)

c = ppt.paint("human.female", n=1, seed=4)
assert len(c) == 1 and len(calls) == 4                         # figure + completion, front and back
assert calls[2][1] and not calls[0][1]                         # the back uses the painted front as reference
d = ppt.out_dir("human.female") / c[0]
for f in ("figure_front.png", "figure_back.png", "rest_front.png", "walk_back.gif", "meta.json", "template.json"):
    assert (d / f).exists(), f
spec = json.loads((d / "template.json").read_text())
for v in ("front", "back"):
    for n in spec["views"][v]["order"]:
        im = np.asarray(Image.open(d / "parts" / v / f"{n}.png"))
        assert im.shape == (1024, 1024, 4) and (im[..., 3] > 127).sum() > 200, (v, n)
head = np.asarray(Image.open(d / "parts" / "front" / "head.png"))
assert ((head[..., :3].astype(int).sum(-1) < 160) & (head[..., 3] > 0)).sum() > 100   # face marks + outline are in

assert not (d / "progress.json").exists()   # progress is only there while it's being painted

# faceless puppets by default (faint guide): faces are their own layer (Face tab), nothing harvested from the paint
import hdart.puppet_face as pf
assert ppt.spec()["face_guide"] == "faint" and not (pf.out_dir("human", "face") / f"p_{c[0]}").exists()

# a paint that fails keeps its progress (stage "failed" + the error); never stale, never used as the build's paint
fake.fail = "ComfyUI fell over"
try:
    ppt.paint("human.female", n=1, seed=77); raise SystemExit("no error")
except RuntimeError:   # comfy.ComfyError
    pass
bad = [c for c in ppt.state("human.female")["candidates"] if c["progress"]]
assert len(bad) == 1 and bad[0]["progress"]["stage"] == "failed" and "fell over" in bad[0]["progress"]["error"] and not bad[0]["stale"]
ppt.archive("human.female", bad[0]["id"])

st = ppt.state("human.female")
assert st["candidates"][0]["id"] == c[0] and st["candidates"][0]["stale"] is False and st["approved"] is None
assert ppt.approve("human.female", c[0])["approved"] == c[0]

# Ramps tab: the approved paint, front and back, with one shared set of skin levels
rp = ppt.ramp_preview("human.female")
assert rp["id"] == c[0] and rp["approved"] and set(rp["facings"]) == {"front", "back"}
assert len(rp["facings"]["front"]["key"]) == 5 and rp["facings"]["front"]["key"] == rp["facings"]["back"]["key"]
assert rp["facings"]["front"]["levels"].startswith("data:image/png;base64,") and ppt.ramp_preview("elf.male")["id"] is None

# Parts editor: save one edited part (backup of the originals), revert
doc = ppt.parts_doc("human.female", c[0])
assert set(doc["views"]["front"]["parts"]) == set(spec["views"]["front"]["order"]) and doc["views"]["front"]["template"]
im = Image.open(d / "parts" / "front" / "torso.png").convert("RGBA"); a = np.asarray(im).copy(); a[500:510, 500:510] = (255, 0, 0, 255)
buf = io.BytesIO(); Image.fromarray(a).save(buf, "PNG")
ppt.save_parts("human.female", c[0], "front", {"torso": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
assert (d / "parts_orig").exists() and tuple(np.asarray(Image.open(d / "parts" / "front" / "torso.png"))[505, 505]) == (255, 0, 0, 255)
try:
    ppt.save_parts("human.female", c[0], "front", {"nope": "x"}); raise SystemExit("no error")
except ValueError:
    pass
ppt.revert_parts("human.female", c[0])
assert not (d / "parts_orig").exists() and tuple(np.asarray(Image.open(d / "parts" / "front" / "torso.png"))[505, 505]) != (255, 0, 0, 255)

# skin bands: one per part, kept in step with the paint; hand edits stay until reset
assert doc["skinKey"] and all((d / "bands" / v / f"{n}.png").exists() for v in ppt.VIEWS for n in spec["views"][v]["order"])
band = lambda: np.asarray(Image.open(d / "bands" / "front" / "torso.png"))
b0 = band(); assert (b0[..., 3] > 0).any() and set(np.unique(b0[..., 0][b0[..., 3] > 0])) <= set(range(5)) | {ppt.NOT_SKIN}
skin = (b0[..., 3] > 0) & (b0[..., 0] != ppt.NOT_SKIN)
assert skin.any()   # the fake paint has skin
ys, xs = np.nonzero(skin); y, x = ys[len(ys) // 2], xs[len(xs) // 2]; lv = (int(b0[y, x, 0]) + 2) % 5
e = np.zeros_like(b0); e[skin, 0] = b0[skin, 0]; e[skin, 3] = 255; e[y, x, 0] = lv; e[0, 0] = (3, 0, 0, 255)   # [0, 0] isn't skin: ignored
buf = io.BytesIO(); Image.fromarray(e).save(buf, "PNG")
doc = ppt.save_bands("human.female", c[0], "front", {"torso": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
assert band()[y, x, 0] == lv and band()[0, 0, 3] == b0[0, 0, 3] and "front/torso" in doc["bandsEdited"]
buf = io.BytesIO(); Image.open(d / "parts" / "front" / "torso.png").save(buf, "PNG")
ppt.save_parts("human.female", c[0], "front", {"torso": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
assert band()[y, x, 0] == lv   # saving the paint keeps hand-edited bands
doc = ppt.reset_bands("human.female", c[0], "front", "torso")
assert band()[y, x, 0] == b0[y, x, 0] and "front/torso" not in doc["bandsEdited"]
ppt.revert_parts("human.female", c[0])

# AI fix: only the marked pixels of the part (inside its template shape) change
mk = np.zeros((1024, 1024), np.uint8); t = np.asarray(Image.open(d / "template" / "front" / "armB.upper.png"))[..., 3] > 127
ys, xs = np.nonzero(t); mk[ys.min():ys.min() + 30, :] = 255
buf = io.BytesIO(); Image.fromarray(mk).save(buf, "PNG")
before = np.asarray(Image.open(d / "parts" / "front" / "armB.upper.png")).copy()
ppt.fix_region("human.female", c[0], "front", "armB.upper", buf.getvalue())
after = np.asarray(Image.open(d / "parts" / "front" / "armB.upper.png"))
changed = np.any(after != before, -1)
assert not (changed & ~(mk > 0)).any() and (d / "parts_orig").exists()

# archive: never the approved one
try:
    ppt.archive("human.female", c[0]); raise SystemExit("no error")
except ValueError:
    pass
c2 = ppt.paint("human.female", n=1, seed=9)[0]
ppt.archive("human.female", c2)
assert not (ppt.out_dir("human.female") / c2).exists() and (ws.ARCHIVE / "workshop" / "paint" / "human.female" / c2).exists()
fake.stop(); shutil.rmtree(tmp)
print("ok")
