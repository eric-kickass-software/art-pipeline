"""Cel levels shared by the Item Workshop and the skin (Workshop Ramps tab, Unity export): FLUX's soft gradients and
hand-painted brushwork quantize into mottled islands, which look splotchy once a ramp colours them. So the brightness is
blurred inside the area before it's cut into levels, small patches merge into their surroundings and a majority filter
smooths the band edges.

Levels maps are RGBA: R = level (254 = outline), G = channel / material, A = mask."""
import numpy as np
from PIL import Image, ImageFilter

CANVAS = 1152  # the rest-pose canvas ISLAND is measured on
ISLAND = 400   # level patches smaller than this (px, the 1152 px rest-pose canvas: 2 px per rig px) merge into their surroundings: no splotchy highlights
VAL_BLUR = 3   # px: the brightness is blurred (inside the item, outline excluded) before it's cut into cel levels


def _components_of(cls):
    """Connected regions of equal class (4 neighbours) by min-label propagation, on the bounding box of cls >= 0.
    Returns (labels, sizes): labels -1 where cls < 0."""
    valid = cls >= 0; lab = np.full(cls.shape, -1, np.int64)
    if not valid.any():
        return lab, np.zeros(0, np.int64)
    ys, xs = np.nonzero(valid); y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    c = cls[y0:y1, x0:x1]; v = valid[y0:y1, x0:x1]; big = c.size + 1
    L = np.where(v, np.arange(c.size).reshape(c.shape), big)
    for _ in range(5000):
        old = L
        L = L.copy()
        for sl_a, sl_b in (((slice(1, None), slice(None)), (slice(None, -1), slice(None))), ((slice(None, -1), slice(None)), (slice(1, None), slice(None))),
                           ((slice(None), slice(1, None)), (slice(None), slice(None, -1))), ((slice(None), slice(None, -1)), (slice(None), slice(1, None)))):
            same = v[sl_a] & v[sl_b] & (c[sl_a] == c[sl_b])
            L[sl_a] = np.where(same, np.minimum(L[sl_a], L[sl_b]), L[sl_a])
        if (L == old).all():
            break
    _, inv, sizes = np.unique(np.where(v, L, big), return_inverse=True, return_counts=True)
    inv = inv.reshape(c.shape); lab[y0:y1, x0:x1] = np.where(v, inv, -1)
    return lab, sizes


def smooth_val(val, m, r=VAL_BLUR):
    """Brightness blurred inside mask m only (normalized: the dark outline and the background don't bleed in)."""
    def box(x, k):   # a box blur of width 2k+1 along both axes (cumulative sums)
        for ax in (0, 1):
            x = np.swapaxes(x, 0, ax); c = np.cumsum(np.pad(x, ((k + 1, k), (0, 0)), mode="edge"), 0)
            x = np.swapaxes((c[2 * k + 1:] - c[:-2 * k - 1]) / (2 * k + 1), 0, ax)
        return x
    f = lambda x: box(box(box(x.astype(float), r), r), r)   # three boxes ~ a Gaussian
    w = f(m.astype(np.float32)); v = f(np.where(m, val, 0).astype(np.float32))
    return np.where(w > 1e-3, v / np.maximum(w, 1e-3), val)


def _mode(lev, a, r=2):
    """A majority filter on the levels (per material) inside a: ragged band edges become smooth."""
    cls = np.where(a, lev[..., 1].astype(np.int64) * 16 + lev[..., 0], -1); vals = np.unique(cls[a])
    if len(vals) < 2:
        return lev
    best = np.zeros(a.shape); arg = cls.copy()
    for c in vals:
        cnt = np.asarray(Image.fromarray(np.where(cls == c, 255, 0).astype(np.uint8)).filter(ImageFilter.BoxBlur(r)), float)
        win = a & (cnt > best); best = np.where(win, cnt, best); arg = np.where(win, c, arg)
    out = lev.copy(); out[..., 1][a] = (arg[a] // 16).astype(np.uint8); out[..., 0][a] = (arg[a] % 16).astype(np.uint8)
    return out


def smooth_levels(lev, island=ISLAND):
    """Merge small patches of one cel level (per material) into the levels around them: FLUX's soft gradients between
    its flat tones otherwise quantize into mottled islands, which look splotchy once a ramp colours them. Big bands and
    long thin highlights (large connected regions) stay; outline pixels (254) are left alone."""
    a = (lev[..., 3] > 127) & (lev[..., 0] < 254)
    if not a.any():
        return lev
    cls = np.where(a, lev[..., 1].astype(np.int64) * 16 + lev[..., 0], -1)
    lab, sizes = _components_of(cls)
    small = a & (sizes[np.clip(lab, 0, None)] < island)
    if not small.any():
        return _mode(lev, a)
    keep = a & ~small
    out = lev.copy()
    filled, got = _spread(keep, lev[..., :2].copy(), keep | small, 200)
    take = small & got
    out[..., 0][take] = filled[..., 0][take]; out[..., 1][take] = filled[..., 1][take]
    return _mode(out, a)


def _spread(mask_from, values, into, steps=120):
    """Copy each pixel of `values` (H, W, C) outward from `mask_from` into `into`, nearest first (8 neighbours)."""
    known = mask_from.copy(); out = values.copy()
    for _ in range(steps):
        todo = into & ~known
        if not todo.any():
            break
        took = np.zeros_like(known)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            k = np.roll(known, (dy, dx), (0, 1)) & todo & ~took
            out[k] = np.roll(values if False else out, (dy, dx), (0, 1))[k]
            took |= k
        if not took.any():
            break
        known |= took
    return out, known


# ----------------------------------------------------------------------------- hand-edited bands (Workshop Editor)

def is_band(lev):
    """The pixels of a levels map that carry a cel level (not outline, not empty)."""
    return (lev[..., 3] > 127) & (lev[..., 0] < 254)


def keep_bands(old, new):
    """`new` levels with the hand-edited levels of `old` kept wherever both have a band of the same channel (a repaint
    of a layer whose bands were edited by hand keeps those edits; newly painted pixels get their own levels)."""
    if old is None or old.shape != new.shape:
        return new
    keep = is_band(old) & is_band(new) & (old[..., 1] == new[..., 1])
    out = new.copy(); out[..., 0][keep] = old[..., 0][keep]
    return out


def write_bands(lev, png_rgba, n_levels):
    """A levels map with the bands painted in the editor (RGBA, R = level, A = painted) written over its band pixels;
    outline, material and coverage stay."""
    out = lev.copy(); m = is_band(lev) & (png_rgba[..., 3] > 127)
    out[..., 0][m] = np.minimum(png_rgba[..., 0][m], n_levels - 1)
    return out
