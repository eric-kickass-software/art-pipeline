"""Mannequin art cut up by the template: the template's parts are fitted onto a race's approved mannequin bone by bone
(torso and head first, then each limb swung from its parent joint), the art is cut by which fitted part is nearest, and
each part's pixels move back onto the template's bones. Joints and pivots stay the template's; the art keeps its own
muscles, outline work and ears. Each part gets a fit score, so art that is way out of place gets called out.

    fit(V, parts, img)        -> {part: Fit}, the mannequin's foreground
    cut(V, parts, img, fits)  -> {part: RGBA on the rest-pose canvas}; pixels the art doesn't reach stay transparent
    report(fits)              -> [(part, score)] of the parts under FLAG
"""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

from hdart.imaging import imageops
from hdart.tools.character import rig

FLAG = 0.8        # a part under this score is called out
RING = 14         # canvas px around a part, where the template says background, checked for art that shouldn't be there
STEP = 3          # sampling stride over the template masks while fitting
CLIP = 44         # canvas px a body part's art may leave its template shape (the head is free: ears, horns)
EDGE = 2          # art px of the mannequin's outline trimmed off: the Paint step draws its own, and a sliced line goes ragged
ANGLES = np.radians(np.arange(-70, 71, 3)); SCALES = np.arange(0.75, 1.36, 0.05)


@dataclass
class Fit:
    z: complex        # template -> mannequin: m = z * (c - t0) + m0, points as complex numbers
    t0: complex
    m0: complex
    score: float = 0.0

    def to_art(self, c):
        return self.z * (c - self.t0) + self.m0

    def to_canvas(self, m):
        return (m - self.m0) / self.z + self.t0


def foreground(img):
    """The mannequin's pixels: everything the white background doesn't reach from the image's edge."""
    a = np.asarray(img.convert("RGB")).astype(int)
    white = (a.min(-1) > 232) & (a.max(-1) - a.min(-1) < 24)
    bg = np.zeros(white.shape, bool); bg[0] = white[0]; bg[-1] = white[-1]; bg[:, 0] = white[:, 0]; bg[:, -1] = white[:, -1]
    for _ in range(2000):
        g = bg.copy(); g[1:] |= bg[:-1]; g[:-1] |= bg[1:]; g[:, 1:] |= bg[:, :-1]; g[:, :-1] |= bg[:, 1:]
        g &= white
        if (g == bg).all():
            break
        bg = g
    return ~bg


def _points(mask):
    ys, xs = np.nonzero(mask[::STEP, ::STEP])
    return xs * STEP + 1j * ys * STEP


def _score(fit, inside, ring, fg):
    """How well a fitted part sits on the art: its inside on art, and the background beside it free of art."""
    def on(p):
        m = fit.to_art(p); x = np.round(m.real).astype(int); y = np.round(m.imag).astype(int)
        ok = (x >= 0) & (y >= 0) & (x < fg.shape[1]) & (y < fg.shape[0])
        return ok & fg[np.clip(y, 0, fg.shape[0] - 1), np.clip(x, 0, fg.shape[1] - 1)]
    i = on(inside).mean() if len(inside) else 1.0
    r = 1 - on(ring).mean() if len(ring) else 1.0
    return float(0.65 * i + 0.35 * r)


def _search(t0, m0, s0, inside, ring, fg, shifts=(0,), angles=ANGLES, scales=SCALES):
    """The best rotation + scale about the joint t0 (at m0 on the art), optionally shifting the joint a little."""
    best = None
    for dx in shifts:
        for dy in shifts:
            for a in angles:
                for s in scales:
                    f = Fit(s * s0 * np.exp(1j * a), t0, m0 + complex(dx, dy))
                    v = _score(f, inside, ring, fg) - 0.06 * abs(np.log(s)) - 0.02 * abs(a)   # no reason to stray: stay near
                    if best is None or v > best[0]:
                        best = (v, f)
    best[1].score = _score(best[1], inside, ring, fg)
    return best[1]


def fit(V, parts, img):
    fg = foreground(img); S = _canvas_scale(parts, V)
    masks = {n: parts[n][0][..., 3] > 127 for n in V["order"]}
    figure = np.any(list(masks.values()), 0)
    pts = {n: _points(m) for n, m in masks.items()}
    ring = {n: _points(imageops.grow(m, RING) & ~figure) for n, m in masks.items()}
    joint = lambda name: complex(*(np.array(rig.joint(V, name), float) * S))
    # the whole figure gives the starting scale and place
    ty, tx = np.nonzero(figure); my, mx = np.nonzero(fg)
    s0 = (my.max() - my.min()) / (ty.max() - ty.min())
    c0 = complex((tx.min() + tx.max()) / 2, (ty.min() + ty.max()) / 2); m0 = complex((mx.min() + mx.max()) / 2, (my.min() + my.max()) / 2)
    start = Fit(s0, c0, m0)
    out = {}
    t = joint("pelvis")
    out["torso"] = _search(t, start.to_art(t), s0, pts["torso"], ring["torso"], fg, shifts=range(-24, 25, 6),
                           angles=np.radians(np.arange(-15, 16, 5)), scales=np.arange(0.8, 1.25, 0.05))
    t = joint("neck")
    out["head"] = _search(t, out["torso"].to_art(t), s0, pts["head"], ring["head"], fg, shifts=range(-30, 31, 6),
                          angles=np.radians(np.arange(-15, 16, 5)), scales=np.arange(0.8, 1.25, 0.05))
    for k in sorted({n.split(".")[0] for n in V["limbs"]}):
        parent = out["torso"]; s_prev = abs(parent.z)
        for seg in ("upper", "lower", "hand"):
            n = f"{k}.{seg}"; L = V["limbs"][n]; t = joint(L["from"])
            end = seg == "hand"   # hands and feet are posed freely: a wider swing, and the joint may slide a little
            f = _search(t, parent.to_art(t), s_prev, pts[n], ring[n], fg, shifts=(0,) if seg == "lower" else range(-12, 13, 4),
                        angles=np.radians(np.arange(-130, 131, 5)) if end else ANGLES)
            out[n] = f; parent = f; s_prev = abs(f.z)
    return out, fg


def _canvas_scale(parts, V):
    return parts[V["order"][0]][0].shape[0] / _rig_px()


def _rig_px():
    return rig.PX


def labels(V, parts, fits, fg):
    """Every art pixel's part: the fitted template masks, grown through the art until all of it has an owner (ears
    and all). Later parts in the draw order win where fitted parts overlap."""
    H, W = fg.shape; yy, xx = np.mgrid[:H, :W]; m = xx + 1j * yy
    lab = np.full(fg.shape, -1, int)
    for i, n in enumerate(V["order"]):
        c = fits[n].to_canvas(m); A = parts[n][0][..., 3] > 127
        x = np.round(c.real).astype(int); y = np.round(c.imag).astype(int)
        ok = (x >= 0) & (y >= 0) & (x < A.shape[1]) & (y < A.shape[0])
        lab[ok & fg & A[np.clip(y, 0, A.shape[0] - 1), np.clip(x, 0, A.shape[1] - 1)]] = i
    for _ in range(400):
        todo = fg & (lab < 0)
        if not todo.any():
            break
        new = lab.copy()
        for ax, sh in ((0, 1), (0, -1), (1, 1), (1, -1)):
            r = np.roll(lab, sh, ax); take = todo & (new < 0) & (r >= 0); new[take] = r[take]
        lab = new
    return lab


def _bilinear(a, x, y):
    x = np.clip(x, 0, a.shape[1] - 1.001); y = np.clip(y, 0, a.shape[0] - 1.001)
    x0 = x.astype(int); y0 = y.astype(int); fx = (x - x0)[..., None]; fy = (y - y0)[..., None]
    return ((a[y0, x0] * (1 - fx) + a[y0, x0 + 1] * fx) * (1 - fy) + (a[y0 + 1, x0] * (1 - fx) + a[y0 + 1, x0 + 1] * fx) * fy)


def cut(V, parts, img, fits, fg):
    """Each part's art on the template's bones, as RGBA: its own pixels, plus any art under the template shape (so the
    joint ends overlap). Where the template has no art under it stays transparent, for the caller to fill."""
    a = np.asarray(img.convert("RGB")).astype(float); lab = labels(V, parts, fits, fg)
    core = imageops.erode_cross(fg, EDGE)
    N = parts[V["order"][0]][0].shape[0]; yy, xx = np.mgrid[:N, :N]; c = xx + 1j * yy
    out = {}
    for i, n in enumerate(V["order"]):
        m = fits[n].to_art(c); x = np.round(m.real).astype(int); y = np.round(m.imag).astype(int)
        ok = (x >= 0) & (y >= 0) & (x < fg.shape[1]) & (y < fg.shape[0])
        cx = np.clip(x, 0, fg.shape[1] - 1); cy = np.clip(y, 0, fg.shape[0] - 1)
        tmpl = parts[n][0][..., 3] > 127
        own = ok & core[cy, cx] & (lab[cy, cx] == i); under = ok & core[cy, cx] & tmpl
        if n != "head":
            own &= imageops.grow(tmpl, CLIP)
        keep = np.asarray(Image.fromarray(np.where(own | under, 255, 0).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2.5))) > 127
        rgba = np.zeros((N, N, 4), np.uint8)
        rgba[..., :3] = np.clip(_bilinear(a, m.real, m.imag), 0, 255).astype(np.uint8); rgba[..., 3] = np.where(keep, 255, 0)
        out[n] = rgba
    return out


def report(fits):
    return [(n, round(f.score, 2)) for n, f in fits.items() if f.score < FLAG]
