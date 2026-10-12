"""Colour harmonizing in hdart.dungeon on the crypt set (read-only). Run: uv run python tests/test_harmonize.py"""
import time
import numpy as np
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
print("harmonize tests passed")
