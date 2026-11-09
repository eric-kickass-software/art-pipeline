"""Dungeon Forge cells: a candidate cut into the board's cells (sprite + normal map per tile), harmonized and
downsampled to the output resolution. Shared by the previews and the export.
"""
import json

import numpy as np
from PIL import Image, ImageOps

from hdart.tools.dungeon import iso
from hdart.tools.dungeon.geometry import (SQUARE_METHODS, THIN, canvas_size, downsample, geometry, layout, mirrored,
    output_tile, warp_floor)
from hdart.tools.dungeon.sets import GEOMETRY_KEYS, load_candidate, load_set, resolved
from hdart.tools.dungeon.colour import harmonize


_GEO_CACHE = {}


def _geometry_cached(piece, tile):
    key = (json.dumps({k: piece.get(k) for k in GEOMETRY_KEYS + ("kind",)}, sort_keys=True), tile)
    if key not in _GEO_CACHE:
        _GEO_CACHE[key] = geometry(piece, tile)
    return _GEO_CACHE[key]


def _sprite(img8, mask8, n8, w, h):
    """An 8x region -> (native RGBA, normal map RGBA). n8: screen-space normals at 8x."""
    rgba = downsample(img8, mask8, w, h)
    enc = Image.fromarray(np.clip((n8 * 0.5 + 0.5) * 255, 0, 255).astype(np.uint8))
    ns = np.asarray(downsample(enc, mask8, w, h))[..., :3].astype(np.float32) / 255 * 2 - 1
    ns /= np.maximum(np.linalg.norm(ns, axis=-1, keepdims=True), 1e-6)
    ns = iso.detail_normals(ns, rgba)
    return rgba, iso.encode_normal(ns, np.asarray(rgba)[..., 3])


def fill_white(img, mask, thresh=232, iters=24):
    """White backdrop bleeding in at the outline (img2img) -> grown in from the neighbouring painted pixels."""
    a = np.asarray(img).astype(np.float32)
    bad = mask & (a.min(-1) > thresh)
    if not bad.any():
        return img
    good = mask & ~bad
    for _ in range(iters):
        if not bad.any():
            break
        acc = np.zeros_like(a)
        cnt = np.zeros(a.shape[:2], np.float32)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            g = np.roll(good, (dy, dx), (0, 1))
            acc += np.roll(a, (dy, dx), (0, 1)) * g[..., None]
            cnt += g
        fix = bad & (cnt > 0)
        a[fix] = acc[fix] / cnt[fix, None]
        good, bad = good | fix, bad & ~fix
    return Image.fromarray(a.astype(np.uint8))


def _soften_edge(sp, left=True):
    """FLUX outlines the ends of a painted wall with a light highlight. Where pieces join that reads as a seam:
    replace the outermost opaque column of each row with its inner neighbour (colour and normal)."""
    rgba, nm = (np.asarray(im).copy() for im in sp)
    op = rgba[..., 3] > 0
    for y in np.nonzero(op.any(1))[0]:
        xs = np.nonzero(op[y])[0]
        x, nx = (xs[0], xs[0] + 1) if left else (xs[-1], xs[-1] - 1)
        if 0 <= nx < rgba.shape[1] and op[y, nx]:
            rgba[y, x], nm[y, x] = rgba[y, nx], nm[y, nx]
    return Image.fromarray(rgba), Image.fromarray(nm)


def _flat_normals(shape, piece_normal=(0, 0, 1)):
    return np.broadcast_to(iso.to_screen_normal(piece_normal), tuple(shape) + (3,)).copy()


def _mirror(rgba, nm):
    """Mirror a thin piece from the x axis onto the y axis (normal map: flip x)."""
    n = np.asarray(ImageOps.mirror(nm)).copy()
    n[..., 0] = 255 - n[..., 0]
    return ImageOps.mirror(rgba), Image.fromarray(n)


def cells(name, pid, rel, tile=None):
    """A candidate cut for the game: {suffix: (RGBA, normal map)} at `tile`.
    single -> ""; run -> "k{cell}h{half}"; atlas -> "i{i}j{j}"; mirrored copies (y axis) end in "m"."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    T = spec["tile"]
    tile = tile or T
    img, mask = load_candidate(name, rel)
    img = harmonize(name, spec, piece, img, mask)
    lay = layout(piece)
    out = {}
    if piece["kind"] == "prop":
        rgba = fit_prop(img, mask, piece.get("footprint", 0.5), tile)
        n = iso.detail_normals(np.broadcast_to(np.array([0, 0, 1], np.float32), (rgba.height, rgba.width, 3)).copy(), rgba)
        return {"": (rgba, iso.encode_normal(n, np.asarray(rgba)[..., 3]))}
    w, h = canvas_size(piece, tile) if piece.get("shape") not in THIN else iso.cell_canvas(tile, piece.get("height", 2.0))
    if lay == "atlas":
        N = int(piece.get("atlas", 4))
        for i in range(N):
            for j in range(N):
                im8, m8 = warp_floor(img, T, i / N, (i + 1) / N, j / N, (j + 1) / N, rim=piece.get("rim", 0))
                out[f"i{i}j{j}"] = _sprite(im8, m8, _flat_normals(m8.shape), tile, tile // 2)
        return out
    if piece.get("method") in SQUARE_METHODS:  # a 1x1 top-down square (master, 1x1 repaint): warp it on cutting
        im8, m8 = warp_floor(img, T, rim=piece.get("rim", 0))
        return {"": _sprite(im8, m8, _flat_normals(m8.shape), tile, tile // 2)}
    if piece.get("shape") in ("floor", "decal"):
        return {"": _sprite(fill_white(img, mask), mask, _flat_normals(mask.shape), w, h)}
    g = _geometry_cached(piece, T)
    nscr = iso.to_screen_normal(g["n"])
    m_all = mask if mask is not None else g["mask"]
    img = fill_white(img, m_all)
    W, Hc = g["cell"]
    if lay == "run":
        L = g["length"]
        for k in range(L):
            ox, oy = k * W // 2, k * W // 4
            sl = (slice(oy, oy + Hc), slice(ox, ox + W))
            for hh in (0, 1):
                m = (g["own"][sl] == 2 * k + hh) & m_all[sl]
                sp = _sprite(img.crop((ox, oy, ox + W, oy + Hc)), m, nscr[sl], w, h)
                if k == 0 and hh == 0:  # where this run joins the previous one
                    sp = _soften_edge(sp, left=True)
                if k == L - 1 and hh == 1:
                    sp = _soften_edge(sp, left=False)
                out[f"k{k}h{hh}"] = sp
    else:
        sp = _sprite(img, m_all, nscr, w, h)
        if piece.get("shape") in ("run", "mesh"):  # doors and arches join walls on both sides
            sp = _soften_edge(_soften_edge(sp, left=True), left=False)
        out[""] = sp
    if mirrored(piece):
        for suf, (rgba, nm) in list(out.items()):
            out[suf + "m"] = _mirror(rgba, nm)
    return out


def native(name, pid, rel, tile=None):
    """A candidate at the native grid, assembled for review (a run in one piece, an atlas as its patch)."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    tile = tile or output_tile(spec)
    cs = {k: v[0] for k, v in cells(name, pid, rel, tile).items() if not k.endswith("m")}
    lay = layout(piece)
    if lay == "single":
        return cs[""]
    if lay == "run":
        L = max(int(k[1:k.index("h")]) for k in cs) + 1
        cw, ch = cs["k0h0"].size
        canvas = Image.new("RGBA", (cw + (L - 1) * tile // 2, ch + (L - 1) * tile // 4))
        for k in range(L):
            for hh in (0, 1):
                canvas.alpha_composite(cs[f"k{k}h{hh}"], (k * tile // 2, k * tile // 4))
        return canvas
    N = int(piece.get("atlas", 4))
    canvas = Image.new("RGBA", (N * tile, N * tile // 2))
    for i in range(N):
        for j in range(N):
            canvas.alpha_composite(cs[f"i{i}j{j}"], ((i - j) * tile // 2 + (N - 1) * tile // 2, (i + j) * tile // 4))
    return canvas


def fit_prop(img, mask, footprint, tile=64):
    """Crop to the object, scale so its width spans `footprint` tiles, sit its base on the canvas bottom."""
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    crop = np.asarray(img)[y0:y1, x0:x1]
    cm = mask[y0:y1, x0:x1]
    out_w = max(2, int(round(footprint * tile)))
    out_h = max(2, int(round(out_w * (y1 - y0) / (x1 - x0))))
    return downsample(Image.fromarray(crop), cm, out_w, out_h)
