"""Dungeon Forge colour harmonizing: the set's `harmonize` setting (0..1, Studio > Dungeon slider) pulls every tile
image's colour statistics (mean and spread in Lab) towards its family's reference, so the pieces of a family match.
Lab conversions and palette matching shared with generation.
"""
import numpy as np
from PIL import Image

from hdart.core.locks import HARMONY as _REF_CACHE
from hdart.tools.dungeon.geometry import atlas_n, block_mask
from hdart.tools.dungeon.sets import load_candidate, master_rel, set_dir, state


# ----------------------------------------------------------------------------- colour harmonizing
# The set's `harmonize` setting (0..1, Studio > Dungeon slider) pulls every tile image's colour statistics (mean and
# spread per CIELAB channel) toward its family's reference, so variants and pieces made in separate generations sit
# together. Families: floors, and walls (everything else that isn't a prop). Reference = the family's approved
# images (all its candidates while nothing is approved). Props are left alone: they're objects, not surfaces.

_M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
_WHITE = _M.sum(1)


def to_lab(rgb):
    c = rgb / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ _M.T / _WHITE
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def from_lab(lab):
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], -1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) / (24389 / 27)) * _WHITE
    c = xyz @ np.linalg.inv(_M).T
    c = np.where(c > 0.0031308, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055, 12.92 * c)
    return np.clip(c * 255, 0, 255)


def family(piece):
    if piece["kind"] in ("prop", "elevation"):
        return None
    return "floor" if piece.get("shape") == "floor" else "wall"


def _paint_mask(img, mask):
    """Pixels that are painted surface (not the white backdrop)."""
    if mask is not None and mask.shape == img.size[::-1]:
        return mask
    return np.asarray(img).min(-1) < 232


def _lab_stats(img, mask):
    lab = to_lab(np.asarray(img)[_paint_mask(img, mask)].astype(np.float32))
    if len(lab) < 16:
        return None
    return lab.mean(0), lab.std(0)


def _reference_rels(name, spec, fam):
    """The images a family's harmonize reference is measured over: its approved ones, else all its candidates."""
    s = state(name)
    rels = []
    for p in spec["pieces"]:
        if family(p) == fam and not p.get("retired"):
            ps = s["pieces"].get(p["id"], {})
            rels += [(p["id"], r) for r in ps.get("approved", [])]
    if not rels:
        for p in spec["pieces"]:
            if family(p) == fam and not p.get("retired"):
                rels += [(p["id"], r) for r in s["pieces"].get(p["id"], {}).get("candidates", [])]
    return rels


def harmony_key(name, spec, piece):
    """What harmonize() makes a piece's colours depend on (for caches): nothing, its family master, or the set of
    images its family's reference is measured over."""
    fam = family(piece)
    if not float(spec.get("harmonize") or 0) or fam is None or piece.get("method") == "master" or piece.get("built_from"):
        return ""
    if piece.get("method") == "repaint":
        return master_rel(name, piece.get("from")) or ""
    return str(hash(tuple(sorted(r for _, r in _reference_rels(name, spec, fam)))))


def _reference(name, spec, fam):
    """(mean, std) in Lab over the family's approved images (else all its candidates), or None."""
    rels = _reference_rels(name, spec, fam)
    if not rels:
        return None
    key = (name, fam, tuple(sorted(r for _, r in rels)))
    if key not in _REF_CACHE:
        stats = [st for st in (_lab_stats(*load_candidate(name, r)) for _, r in rels) if st]
        _REF_CACHE[key] = (np.mean([m for m, _ in stats], 0), np.mean([d for _, d in stats], 0)) if stats else None
    return _REF_CACHE[key]


def match_palette(img, ref, weight, strength=1.0):
    """Pull `img`'s colours (CIELAB mean and spread, measured over its interior: weight ~1) toward `ref` = (mean, std),
    applied per pixel with weight * strength. Weight 0 = untouched, so a floor family image's kept border (the
    master's exact edges) stays exact and the change fades in with the same soft ramp as the repaint."""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    inner = weight > 0.99
    if strength <= 0 or inner.sum() < 64:
        return img
    lab = to_lab(a.reshape(-1, 3)).reshape(a.shape)
    ms, ss = lab[inner].mean(0), lab[inner].std(0)
    mr, sr = ref
    fixed = (lab - ms) * np.clip(sr / np.maximum(ss, 1e-3), 0.5, 2.0) + mr
    w = (np.clip(weight, 0, 1) * strength)[..., None]
    out = a * (1 - w) + from_lab(fixed.reshape(-1, 3)).reshape(a.shape) * w
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def image_lab_stats(name, rel, interior=None):
    """Lab (mean, std) of a candidate (whole image, or where `interior` weight ~1), cached by file."""
    key = ("lab", name, rel, None if interior is None else interior.shape)
    if key not in _REF_CACHE:
        a = np.asarray(Image.open(set_dir(name) / rel).convert("RGB")).astype(np.float32)
        px = a[interior > 0.99] if interior is not None else a.reshape(-1, 3)
        lab = to_lab(px)
        _REF_CACHE[key] = (lab.mean(0), lab.std(0))
    return _REF_CACHE[key]


def family_weight(piece, size):
    """The repaint mask of a family image (0 on the kept border, ramping to 1 inside), for an image of `size` px."""
    N = atlas_n(piece)
    return block_mask(N, size // N, float(piece.get("border", 0.08)))


def harmonize(name, spec, piece, img, mask):
    """`img` with its colours pulled toward a reference by the set's `harmonize` strength. Floor family variants:
    only the interior, toward the active master's palette (their edges are the master's and must stay exact);
    masters are the palette, unchanged. Other pieces: the whole image toward their family's approved images."""
    k = float(spec.get("harmonize") or 0)
    fam = family(piece)
    if k <= 0 or fam is None or piece.get("method") == "master" or piece.get("built_from"):
        return img
    if piece.get("method") == "repaint":
        mrel = master_rel(name, piece.get("from"))
        return match_palette(img, image_lab_stats(name, mrel), family_weight(piece, img.width), k) if mrel else img
    ref = _reference(name, spec, fam)
    rgb = np.asarray(img).copy()
    paint = _paint_mask(img, mask)
    if ref is None or paint.sum() < 16:
        return img
    lab = to_lab(rgb[paint].astype(np.float32))  # painted pixels only, converted once
    (mr, sr), ms, ss = ref, lab.mean(0), lab.std(0)
    ratio = np.clip(sr / np.maximum(ss, 1e-3), 0.5, 2.0)  # never blow up a flat image or flatten a busy one
    rgb[paint] = from_lab((lab - ms) * (1 + k * (ratio - 1)) + ms + k * (mr - ms)).astype(np.uint8)
    return Image.fromarray(rgb)
