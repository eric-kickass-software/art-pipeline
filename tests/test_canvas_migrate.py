"""hdart canvas-migrate: canvas images padded to 1152 at (64, 128) losslessly, crop-space images and previews left
alone, spec points and grip points shifted, unknown 1024 px files refused, a second run changes nothing; an old (512)
spec is refused by the loaders. Run: uv run python tests/test_canvas_migrate.py"""
import json, shutil, tempfile
from pathlib import Path
import numpy as np
from PIL import Image
from hdart import canvas_migrate as cm, puppet_template as pt, puppet_paint as ppt

tmp = Path(tempfile.mkdtemp())
ai, specs = tmp / "ai", tmp / "specs"
rng = np.random.default_rng(1)


def png(rel, size=1024, mode="RGBA", root=None):
    f = (root or ai) / rel; f.parent.mkdir(parents=True, exist_ok=True)
    a = rng.integers(0, 255, (size, size, 4 if mode == "RGBA" else 1), np.uint8)
    a[:4, :4] = 0   # a transparent / empty corner, as on the real canvas
    (Image.fromarray(a) if mode == "RGBA" else Image.fromarray(a[..., 0] > 127).convert("1")).save(f)
    return f


old = json.loads(json.dumps(json.loads((cm.ai_kit.ROOT / "specs" / "puppet_template.json").read_text(encoding="utf-8"))))
old["px"] = 512   # (the repo's spec is migrated: make an old one by moving it back)
for V in old["views"].values():
    for k, j in V["joints"].items():
        V["joints"][k] = {n: ([p[0] - 32, p[1] - 64] if isinstance(p, list) else p) for n, p in j.items()} if isinstance(j, dict) else [j[0] - 32, j[1] - 64]
    for sh in V["shapes"].values():
        sh["points"] = [[p[0] - 32, p[1] - 64] for p in sh["points"]]
        if sh.get("face"):
            fc = sh["face"]; fc["eyes"] = [[p[0] - 32, p[1] - 64] for p in fc["eyes"]]
            fc["mouth"] = [fc["mouth"][0] - 32, fc["mouth"][1] - 64]; fc["center"] = [[p[0] - 32, p[1] - 64] for p in fc["center"]]
    V["seams"]["waist"]["y"] -= 64
specs.mkdir(parents=True); (specs / "puppet_template.json").write_text(json.dumps(old, indent=1), encoding="utf-8")
cand = ai / "paint/human.male/s1_1"; cand.mkdir(parents=True); (cand / "template.json").write_text(json.dumps(old, indent=1), encoding="utf-8")

canvas = [png("paint/human.male/s1_1/parts/front/head.png"), png("paint/human.male/s1_1/bands/back/armA.upper.png"),
          png("face/human/hair/s2_2/layer_front.png"), png("face/human/hair/s2_2/levels_orig_back.png"),
          png("items/human/item.cloth_tunic/s3_3/plevels/front/torso.png"), png("items/human/item.cloth_tunic/s3_3/guide_back.png"),
          png("items/_blank.png"), png("items/human/_grip/human.male/s4_4/under_armA_front.png"),
          png("item_zones/item.x.front.png", mode="1", root=specs)]
crop = [png("paint/human.male/s1_1/sheet_front.png"), png("face/human/hair/s2_2/paint_front.png"), png("face/human/hair/s2_2/edit_back.png"),
        png("items/human/item.cloth_tunic/s3_3/complete_front.png"), png("items/human/_grip/human.male/s4_4/paint_armA_front.png"),
        png("face/human/hair/s2_2/tmp/hairback_1.png"), png("face/human/hair/s2_2/prev_male_front.png", 512)]
gm = ai / "items/human/_grip/human.male/s4_4/meta.json"
gm.write_text(json.dumps({"kind": "grip", "grip": {"armA": {"front": {"c": [100.0, 200.0], "u": [0.0, -1.0], "w": 30.0}}}}))
before = {f: np.asarray(Image.open(f)).copy() for f in canvas + crop}

# the plan: every file in its group, nothing unknown
p = cm.plan(ai, specs)
assert len(p["specs"]) == 2 and len(p["grip meta"]) == 1 and len(p["item zones"]) == 1 and not p["unknown"], p
assert sum(len(p[g]) for g, _ in cm.PAD_RULES) == len(canvas) - 1
assert cm.migrate(ai, specs, dry=True)["paint parts"] == 2

# an old spec is refused until migrated
try:
    ppt._spec_for(cand); raise SystemExit("old spec loaded")
except ValueError as e:
    assert "canvas-migrate" in str(e)

done = cm.migrate(ai, specs, skip_backup=True, previews=False)
assert done == {"specs": 2, "grip meta": 1, "paint parts": 2, "face layers": 2, "item parts": 1, "item layers": 1, "item blank": 1,
                "grip hands": 1, "item zones": 1}, done
for f in canvas:   # padded, the old pixels unchanged inside
    a = np.asarray(Image.open(f)); assert a.shape[:2] == (1152, 1152), f
    assert np.array_equal(a[128:128 + 1024, 64:64 + 1024], before[f]), f
    assert not a[:128].any() and not a[:, :64].any(), f
for f in crop:
    assert np.array_equal(np.asarray(Image.open(f)), before[f]), f
new = json.loads((specs / "puppet_template.json").read_text(encoding="utf-8"))
assert new == pt.checked(json.loads((cand / "template.json").read_text(encoding="utf-8")), "cand") and new["px"] == 576
assert new["views"] == json.loads((cm.ai_kit.ROOT / "specs" / "puppet_template.json").read_text(encoding="utf-8"))["views"]   # back to the repo's
g = json.loads(gm.read_text())["grip"]["armA"]["front"]
assert g["c"] == [164.0, 328.0] and g["u"] == [0.0, -1.0]

# a second run changes nothing
assert cm.migrate(ai, specs, skip_backup=True, previews=False) == {}

# a 1024 px image no rule covers stops the run
png("paint/human.male/s1_1/mystery.png")
try:
    cm.migrate(ai, specs, dry=True); raise SystemExit("unknown not refused")
except ValueError as e:
    assert "mystery.png" in str(e)
shutil.rmtree(tmp)
print("ok")
