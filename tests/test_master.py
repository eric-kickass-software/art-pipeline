"""Master tile + repaint helpers in hdart.dungeon (no GPU). Run: uv run python tests/test_master.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
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
# even tone: a 3x3 slab tile with one dark slab -> slabs even out, joints stay dark, still wraps (edges unchanged in kind)
rng = np.random.default_rng(3); S = 192; t = np.full((S, S, 3), 150.0) + rng.normal(0, 6, (S, S, 3))
t[64:128, 64:128] *= 0.6; t[::64] = 50; t[:, ::64] = 50
e = np.asarray(d.even_tone(Image.fromarray(np.clip(t, 0, 255).astype(np.uint8)), 1.0)).astype(float)
slab = lambda a, y, x: a[y + 8:y + 56, x + 8:x + 56].mean()
before, after = abs(slab(t, 64, 64) - slab(t, 0, 0)), abs(slab(e, 64, 64) - slab(e, 0, 0))
assert after < before * 0.4, (before, after)
assert e[::64].mean() < e[8:56, 8:56].mean() - 40  # joints still dark
wrap = np.abs(e[0] - e[-1]).mean(), np.abs(t[0] - t[-1]).mean(); assert wrap[0] < wrap[1] + 15, wrap
print(f"even tone: dark slab {before:.0f} -> {after:.0f} off the others")
# floor blocks: the feature zone crosses tile boundaries both ways (the block's kept border protects its edges)
for N in (2, 3, 4):
    for seed in range(20):
        z = d.floor_zone(N, 64, seed); ys, xs = np.nonzero(z > 0.99)
        assert xs.size and xs.max() // 64 > xs.min() // 64 and ys.max() // 64 > ys.min() // 64, (N, seed)
assert d.family_settings({'family': {}})['span'] == 'once'
print("master tests passed")
