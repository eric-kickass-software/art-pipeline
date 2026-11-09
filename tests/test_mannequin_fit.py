"""Template-guided mannequin cut (hdart.tools.character.mannequin_fit), no GPU: a fake mannequin drawn from the template with one arm swung,
a big extra ear and one hand missing. Run: uv run python tests/test_mannequin_fit.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import numpy as np
from PIL import Image
from hdart.imaging import imageops
from hdart.tools.character import mannequin_fit as mf, puppet_paint as ppt, puppet_template as pt, rig

spec = pt.load(); V = spec["views"]["front"]; parts = pt.render(spec, "front", ppt.S); order = V["order"]
N = parts["head"][0].shape[0]; SZ = 512; k = SZ / N
pose = {"armA.hand": None}   # left out of the art
swing = {"armB.upper", "armB.lower", "armB.hand"}
piv = complex(*(np.array(rig.joint(V, V["limbs"]["armB.upper"]["from"]), float) * ppt.S)); rot = np.exp(1j * np.radians(18))

art = np.full((SZ, SZ, 3), 255, np.uint8); yy, xx = np.mgrid[:SZ, :SZ]; c = (xx + 1j * yy) / k
for i, n in enumerate(order):
    if n in pose:
        continue
    q = (c - piv) / rot + piv if n in swing else c
    x = np.clip(q.real.astype(int), 0, N - 1); y = np.clip(q.imag.astype(int), 0, N - 1)
    art[parts[n][0][y, x, 3] > 127] = (60 + 8 * i, 150, 60)
head = parts["head"][0][..., 3] > 127; ys, xs = np.nonzero(head)
ear = (slice(int((ys.min() + ys.max()) / 2 * k), int((ys.min() + ys.max()) / 2 * k) + 14), slice(int(xs.max() * k) - 2, int(xs.max() * k) + 30))
art[ear] = (60, 150, 60)
img = Image.fromarray(art)

fits, fg = mf.fit(V, parts, img)
assert fg[ear].all() and not fg[0, 0]
for n in ("torso", "head", "armB.upper", "armB.lower", "legA.upper", "legB.upper"):
    assert fits[n].score >= mf.FLAG, (n, fits[n].score)
assert abs(np.degrees(np.angle(fits["armB.upper"].z / fits["torso"].z)) - 18) < 7, "the swung arm is found"
assert "armA.hand" in dict(mf.report(fits)), "a part with no art under it is called out"

# the template's joints never move: a part's joint on the art comes back to the same canvas point
t = complex(*(np.array(rig.joint(V, "neck"), float) * ppt.S)); f = fits["head"]
assert abs(f.to_canvas(f.to_art(t)) - t) < 1e-6

cut = mf.cut(V, parts, img, fits, fg)
A = {n: cut[n][..., 3] > 127 for n in order}
assert set(cut) == set(order) and all(a.shape == (N, N) for a in A.values())
assert (A["head"] & ~head).sum() > 300, "the head keeps art beyond the template (the ear)"
for n in order:   # body art stays near its template part; a stray piece can't fly off
    if n != "head":
        far = A[n] & ~imageops.grow(parts[n][0][..., 3] > 127, mf.CLIP + 4)
        assert not far.any(), n
print("ok")
