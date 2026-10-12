"""Colour harmonizing in hdart.dungeon on the crypt set (read-only). Run: uv run python tests/test_harmonize.py"""
import time
import numpy as np
from PIL import Image
import hdart.dungeon as d

rgb = np.random.default_rng(1).integers(0, 256, (64, 64, 3)).astype(np.float64)
assert np.abs(d._from_lab(d._to_lab(rgb)) - rgb).max() < 0.5, "Lab round trip"

spec = dict(d.load_set("crypt")); st = d.state("crypt")["pieces"]
floors = [(p, r) for p in spec["pieces"] if d.family(p) == "floor" for r in st.get(p["id"], {}).get("approved", [])]
assert len(floors) >= 2, "needs approved crypt floors"
p, rel = floors[-1]
img, mask = d._load("crypt", rel)
spec["harmonize"] = 0
assert d.harmonize("crypt", spec, p, img, mask) is img, "strength 0 = untouched"
spec["harmonize"] = 1
t = time.time(); ref = d._reference("crypt", spec, "floor"); t_ref = time.time() - t
t = time.time(); out = d.harmonize("crypt", spec, p, img, mask); t_one = time.time() - t
before, after = d._lab_stats(img, mask), d._lab_stats(out, mask)
gap = lambda s: np.abs(s[0] - ref[0]).sum()
print(f"floor mean gap to reference: before {gap(before):.2f}, after {gap(after):.2f}  (ref {t_ref:.2f}s once, {t_one*1000:.0f} ms/image)")
assert gap(after) < max(1.0, gap(before) * 0.25), "k=1 should land near the reference"
prop = next(q for q in spec["pieces"] if q["kind"] == "prop")
assert d.harmonize("crypt", spec, prop, img, mask) is img, "props untouched"
# cells() reads the set from disk, so run it with the setting patched in
real = d.load_set
d.load_set = lambda n: {**real(n), "harmonize": 1.0}
t = time.time(); a = d.cells("crypt", p["id"], rel, 64); t_on = time.time() - t
d.load_set = real
b = d.cells("crypt", p["id"], rel, 64)
assert any(np.asarray(a[k][0]).tolist() != np.asarray(b[k][0]).tolist() for k in a), "cells() should apply it"
print(f"cells with harmonize: {t_on*1000:.0f} ms ({p['id']}, {img.size[0]}x{img.size[1]})")
# floor families: a variant's kept border must stay exact (it's the master's edge); the interior moves to the master
fam_set, mid = "zz_master", "tile.zz_floor_master"
if fam_set in d.list_sets() and d.master_rel(fam_set, mid):
    fs = d.load_set(fam_set)
    var = next((p for p in d.family_pieces(fs, mid).values() if d.state(fam_set)["pieces"].get(p["id"], {}).get("approved")), None)
    if var:
        vrel = d.state(fam_set)["pieces"][var["id"]]["approved"][0]
        img = Image.open(d.set_dir(fam_set) / vrel).convert("RGB")
        out = d.harmonize(fam_set, {**fs, "harmonize": 1.0}, var, img, None)
        w = d.family_weight(var, img.width)
        a, b = np.asarray(img).astype(int), np.asarray(out).astype(int)
        assert np.abs(a - b)[w == 0].max() == 0, "family harmonize must leave the kept border exact"
        mm = d.image_lab_stats(fam_set, d.master_rel(fam_set, mid))[0]
        inner = w > 0.99
        gap = lambda x: np.linalg.norm(d._to_lab(x[inner].astype(np.float32)).mean(0) - mm)
        print(f"family variant interior dE to master: {gap(a):.1f} -> {gap(b):.1f}, border unchanged")
        assert gap(b) < gap(a) * 0.3 + 0.5
        assert d.harmonize(fam_set, {**fs, "harmonize": 1.0}, d.piece_of(fs, mid), img, None) is img, "masters are the palette"
print("harmonize tests passed")
