"""Master tile + repaint helpers in hdart.dungeon (no GPU). Run: uv run python tests/test_master.py"""
import numpy as np
from PIL import Image
import hdart.dungeon as d

S = 256
yy, xx = np.mgrid[0:S, 0:S] + 0.5
rng = np.random.default_rng(3)
detail = rng.normal(0, 12, (S, S, 1))

# periodic_smooth: a texture with a left-to-right ramp has a big wrap step; afterwards it wraps like any neighbour step
ramp = np.clip(90 + 0.4 * xx[..., None] + detail, 0, 255).repeat(3, -1).astype(np.uint8)
def wrap_vs_inner(a):
    a = a.astype(float)
    return np.abs(a[:, 0] - a[:, -1]).mean(), np.abs(np.diff(a, axis=1)).mean()
w0, i0 = wrap_vs_inner(ramp)
w1, i1 = wrap_vs_inner(np.asarray(d.periodic_smooth(Image.fromarray(ramp))))
assert w0 > 5 * i0 and w1 < 1.5 * i1, (w0, i0, w1, i1)

# flatten_light: a vignette (bright middle, dark rim, 60% falloff) goes away, the detail stays
vign = 1.3 - 0.6 * ((xx - S / 2) ** 2 + (yy - S / 2) ** 2) / (S / 2) ** 2
img = np.clip(120 * vign[..., None] + detail, 0, 255).repeat(3, -1).astype(np.uint8)
flat = np.asarray(d.flatten_light(Image.fromarray(img))).astype(float)
centre_vs_rim = lambda a: a[S // 2 - 20:S // 2 + 20, S // 2 - 20:S // 2 + 20].mean() - a[:12].mean()
assert abs(centre_vs_rim(flat)) < 0.05 * centre_vs_rim(img.astype(float)), (centre_vs_rim(img.astype(float)), centre_vs_rim(flat))
assert np.std(flat - np.asarray(Image.fromarray(flat.astype(np.uint8)).resize((8, 8)).resize((S, S)))) > 5  # detail kept

# masks: a block keeps an exact band at its outer edge and repaints everything else, including inner tile seams
m = d.block_mask(3, 100, 0.08)
assert m[:7].max() == 0 and m[:, :7].max() == 0 and m[-7:].max() == 0 and m[150, 150] == 1 and m[150, 100] == 1
c = d.seam_cross_mask(200)
assert c[100, 37] == 1 and c[100, 100] == 1 and c[20, 20] == 0

# pieces
assert d.layout({"kind": "tile", "shape": "floor", "method": "repaint", "atlas": 3}) == "atlas"
assert d.layout({"kind": "tile", "shape": "floor", "method": "repaint"}) == "single"
assert d.layout({"kind": "tile", "shape": "floor", "method": "master"}) == "single"
assert d.atlas_n({"method": "atlas"}) == 4 and d.atlas_n({"method": "repaint", "atlas": 2}) == 2 and d.atlas_n({"method": "master"}) == 1
print("master tests passed")
