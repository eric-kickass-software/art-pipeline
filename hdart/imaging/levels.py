"""Keying and cel levels, shared by hair and faces (Face tab), the skin (Paint bands, Ramps tab, Unity export) and the
Item Workshop. Levels maps are RGBA: R = cel level (254 = outline), G = channel / material, A = mask.

    keying     key_hue(rgb), hue_dist(h, kh), brightness_centres(v, n), nearest_level(v, cent): a colour key picks
               the pixels of one hue; their brightness, cut into n levels by 1D k-means, gives the cel levels
    smoothing  smooth_val, smooth_levels, spread: FLUX's soft gradients and hand-painted brushwork quantize into
               mottled islands, which look splotchy once a ramp colours them. So the brightness is blurred inside the
               area before it's cut into levels, small patches merge into their surroundings and a majority filter
               smooths the band edges
    skin       skin_mask, skin_key, skin_cut, skin_levels (any figure on white), head_skin_key, skin_dist, blank_face
    colouring  colour_levels(lev, colours): a levels map coloured with one ramp
    bands      is_band, keep_bands, write_bands: hand-edited bands (Workshop Editor)
"""
import numpy as np
from PIL import Image, ImageFilter

from hdart.imaging.imageops import foreground, hsv

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
    filled, got = spread(keep, lev[..., :2].copy(), keep | small, 200)
    take = small & got
    out[..., 0][take] = filled[..., 0][take]; out[..., 1][take] = filled[..., 1][take]
    return _mode(out, a)


def spread(mask_from: np.ndarray, values: np.ndarray, into: np.ndarray, steps: int = 120) -> tuple[np.ndarray, np.ndarray]:
    """Copy each pixel of `values` (H, W, C) outward from `mask_from` into `into`, nearest first (8 neighbours).
    Returns (the spread values, the mask of pixels that got one)."""
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


# ----------------------------------------------------------------------------- colour keys

def key_hue(rgb) -> float:
    """The hue (degrees) of one RGB colour."""
    return float(hsv(np.array([[rgb]], np.uint8))[0][0, 0])


def hue_dist(h: np.ndarray, kh: float) -> np.ndarray:
    """Per pixel: the distance (degrees, 0..180) from hue h to the key hue kh around the colour wheel."""
    return np.minimum(np.abs(h - kh), 360 - np.abs(h - kh))


def brightness_centres(v: np.ndarray, n: int) -> np.ndarray:
    """n level brightnesses for the values v (1D k-means from the 10th..90th percentiles), sorted dark -> light."""
    cent = np.percentile(v, np.linspace(10, 90, n))
    for _ in range(20):
        lab = np.abs(v[:, None] - cent[None]).argmin(1)
        cent = np.array([v[lab == i].mean() if (lab == i).any() else cent[i] for i in range(n)])
    cent.sort()
    return cent


def nearest_level(v: np.ndarray, cent) -> np.ndarray:
    """The index of the nearest level brightness in `cent` for each value of v."""
    return np.abs(v[:, None] - np.asarray(cent)[None]).argmin(1)


# ----------------------------------------------------------------------------- skin
# A skin tone is a palette ramp (specs/palettes.json, group "skin"): the art stores cel levels, the game colours them.

LUMA = np.array([0.299, 0.587, 0.114])


def skin_mask(a: np.ndarray) -> np.ndarray:
    """Skin pixels of an RGB array: warm (red over blue) or cool (green or blue over red: orcs and the like; human skin
    never is) and saturated enough, not the grey clothes or the outline."""
    a = a.astype(int)
    mx, mn = a.max(-1), a.min(-1)
    hue = (a[..., 0] > a[..., 2] + 18) | (np.maximum(a[..., 1], a[..., 2]) > a[..., 0] + 8)
    m = hue & ((mx - mn) > 0.16 * np.maximum(mx, 1)) & (mx > 70)
    lum = a.mean(-1)
    grey = ((mx - mn) < 0.12 * np.maximum(mx, 1)) & (lum < 200)
    near = np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)
    bg = ~foreground(Image.fromarray(a.astype(np.uint8)))
    return m | (near & ~bg & (lum > 90) & ~grey)  # light anti-aliased skin edges count too


def _skin_val(a, m):
    """Brightness of the skin pixels m, blurred inside the skin. The light anti-aliased rim (2 px) is left out of the
    blur and takes its inner neighbours' value: it would otherwise become a pale outline band."""
    core = m
    for _ in range(2):
        core = core & np.roll(core, 1, 0) & np.roll(core, -1, 0) & np.roll(core, 1, 1) & np.roll(core, -1, 1)
    return smooth_val(a.astype(float) @ LUMA, core if core.any() else m)


def skin_cut(a: np.ndarray, m: np.ndarray, cent) -> np.ndarray:
    """Level map of the skin pixels m of RGB array a (-1 = not skin): the blurred brightness cut at the levels' (cent,
    RGB) brightnesses, then small patches merged and band edges smoothed. Hand-painted brushwork and FLUX's soft
    gradients otherwise quantize into splotches once a ramp colours them."""
    lev = np.full(m.shape, -1, np.int16)
    if not len(cent) or not m.any():
        return lev
    vs = _skin_val(a, m); cl = np.asarray(cent, float) @ LUMA
    L = np.zeros(m.shape + (4,), np.uint8)
    L[..., 0][m] = np.abs(vs[m][:, None] - cl[None]).argmin(1); L[..., 3][m] = 255
    island = max(1, int(ISLAND * (m.shape[0] / CANVAS) ** 2))   # ISLAND is for the rest-pose canvas
    lev[m] = smooth_levels(L, island)[..., 0][m]
    return lev


def skin_key(img: Image.Image, size: int = 5) -> list:
    """The skin's cel level colours: k-means on the skin colours, sorted dark -> light (RGB centres; [] = no skin)."""
    a = np.asarray(img.convert("RGB"))
    px = a[skin_mask(a)].astype(float)
    if len(px) < size:
        return []
    lum = px.sum(1); order0 = np.argsort(lum)  # start from evenly spread brightness percentiles (deterministic)
    cent = px[order0[(np.linspace(0.08, 0.92, size) * (len(px) - 1)).astype(int)]]
    for _ in range(30):
        lab = ((px[:, None] - cent[None]) ** 2).sum(-1).argmin(1)
        cent = np.array([px[lab == i].mean(0) if (lab == i).any() else cent[i] for i in range(size)])
    return [[int(round(c)) for c in cc] for cc in cent[np.argsort(cent.sum(1))]]


def skin_levels(img: Image.Image, size: int = 5, seed: int = 0) -> tuple[np.ndarray, list]:
    """Cel levels of the skin: the skin key (skin_key) and the level map cut from the blurred brightness and smoothed
    (skin_cut). Returns (level map: -1 = not skin, the level centres as RGB)."""
    a = np.asarray(img.convert("RGB"))
    cent = skin_key(img, size)
    if not cent:
        return np.full(a.shape[:2], -1, np.int16), []
    return skin_cut(a, skin_mask(a), cent), cent


def head_skin_key(head: np.ndarray) -> np.ndarray:
    """The skin cel levels of a head part (RGBA array; RGB centres dark -> light, a default tone without skin)."""
    a = head[..., 3] > 127
    bg = np.full(head.shape[:2] + (3,), 255, np.uint8); bg[a] = head[a, :3]
    cent = skin_key(Image.fromarray(bg), 5)
    return np.array(cent, float) if cent else np.array([[220, 170, 140]], float)


def skin_dist(rgb: np.ndarray, key: np.ndarray) -> np.ndarray:
    """Per pixel: the RGB distance to the nearest skin level of `key`."""
    return np.sqrt(((rgb[..., None, :].astype(float) - key[None, None]) ** 2).sum(-1)).min(-1)


def blank_face(head: np.ndarray, ellipse: np.ndarray, key: np.ndarray | None = None) -> np.ndarray:
    """The head part with its painted face removed: inside the face area, pixels that aren't skin (eyes, brows, mouth,
    their outlines) are filled from the skin around them, so a face layer can go on top of any build."""
    key = head_skin_key(head)[2:] if key is None else key   # lit skin levels: dark lines and pupils count as features
    a = head[..., 3] > 127; feat = ellipse & a & (skin_dist(head[..., :3], key) > 32)
    feat = np.asarray(Image.fromarray(np.where(feat, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 0
    feat &= a & ellipse
    ys, xs = np.nonzero(feat)
    if not len(ys):
        return head
    y0, y1, x0, x1 = max(0, ys.min() - 4), ys.max() + 5, max(0, xs.min() - 4), xs.max() + 5
    col = head[y0:y1, x0:x1, :3].astype(float); todo = feat[y0:y1, x0:x1].copy(); known = a[y0:y1, x0:x1] & ~todo
    for _ in range(400):
        if not (todo & ~known).any():
            break
        acc = np.zeros_like(col); cnt = np.zeros(todo.shape)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            k = np.roll(known, (dy, dx), (0, 1)); c = np.roll(col, (dy, dx), (0, 1))
            acc[k] += c[k]; cnt[k] += 1
        take = todo & ~known & (cnt > 0)
        col[take] = acc[take] / cnt[take][:, None]; known |= take
    out = head.copy(); out[y0:y1, x0:x1, :3] = np.clip(col, 0, 255).astype(np.uint8)
    return out


# ----------------------------------------------------------------------------- colouring

def colour_levels(lev: np.ndarray, colours, line=(40, 32, 30)) -> np.ndarray:
    """A levels map coloured with one ramp (`colours` dark -> light, RGB); outline pixels in the outline colour."""
    out = np.zeros(lev.shape, np.uint8); a = lev[..., 3] > 127; r = lev[..., 0]
    pal = np.array(colours, np.uint8)
    band = a & (r < 254); out[band, :3] = pal[np.clip(r[band], 0, len(pal) - 1)]
    out[a & (r == 254), :3] = line; out[..., 3] = np.where(a, 255, 0)
    return out
