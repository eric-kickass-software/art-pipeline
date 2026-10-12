"""Iso geometry for dungeon pieces: boxes -> template, ownership and exact normals.

Coordinates ("piece space"): x and y in tile units on the floor grid, z in height units (1 = tile/2 px,
Prototype V's convention). +x runs right-down on screen, +y left-down, z up. The visible faces of a box
are its top, its +y face ("left") and its +x face ("right").

A cell canvas is `tile` px wide and tile/2 + h*tile/2 tall with the floor diamond's centre at
(W/2, H - W/4), the sprite pivot. A run of L cells along +x is drawn on one wider canvas; cell k's own
canvas sits at offset (k*W/2, k*W/4) inside it, so slicing is a crop.

Unity's board axes differ: board (X, Y) directions map to piece space as  piece +x = board -Y,
piece +y = board -X  (see DungeonArt.cs).
"""
import math

import numpy as np
from PIL import Image, ImageDraw

SHADE = {1: 150, 2: 112, 3: 78}  # template grey per face: top, left (+y), right (+x)

# camera basis in piece space (true world, 30 deg elevation, looking from the +x+y side)
_R = np.array([1, -1, 0]) / math.sqrt(2)
_V = np.array([math.cos(math.radians(30)) / math.sqrt(2)] * 2 + [math.sin(math.radians(30))])
_U = np.cross(_V, _R)
_U = _U if _U[2] > 0 else -_U


def to_screen_normal(n):
    """Piece-space normal(s) [..., 3] -> sprite normal-map space (x right, y up, z towards the viewer)."""
    n = np.asarray(n, np.float32)
    out = np.stack([n @ _R, n @ _U, n @ _V], -1)
    return out / np.maximum(np.linalg.norm(out, axis=-1, keepdims=True), 1e-6)


FACE_N = {1: (0, 0, 1), 2: (0, 1, 0), 3: (1, 0, 0)}


def cell_canvas(tile, height, scale=1):
    """(W, H) of one cell's sprite at `scale` times the native size."""
    return int(tile * scale), int(round((tile / 2 + height * tile / 2) * scale))


def run_canvas(tile, height, length, scale=1):
    w, h = cell_canvas(tile, height, scale)
    return w + (length - 1) * w // 2, h + (length - 1) * w // 4


def project(W, H_cell, x, y, z):
    """Piece-space point -> pixel on a run canvas whose cell 0 matches a cell canvas (W, H_cell)."""
    ox, oy = W / 2, H_cell - W / 2
    return ox + (x - y) * W / 2, oy + (x + y) * W / 4 - z * W / 2


_KZ = (math.sqrt(2) / 2) / math.cos(math.radians(30))  # one height unit in world units


def render_boxes(boxes, size, W, H_cell, xyz=False):
    """boxes: [(x0, x1, y0, y1, z0, z1, owner)], z-buffered.
    Returns face map (0 none, 1 top, 2 left, 3 right) and owner map (-1 none); with `xyz` also the piece-space point
    (X, Y, Z arrays) under every pixel, so a texture can be mapped onto the faces (wall elevations)."""
    w, h = size
    face = np.zeros((h, w), np.uint8)
    own = np.full((h, w), -1, np.int32)
    depth = np.full((h, w), -np.inf, np.float32)
    X, Y, Z = (np.zeros((h, w), np.float32) for _ in range(3))
    ox, oy = W / 2, H_cell - W / 2
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    u = 2 * (xx - ox) / W  # = x - y on any surface
    for x0, x1, y0, y1, z0, z1, owner in boxes:
        P = lambda x, y, z: project(W, H_cell, x, y, z)
        faces = {
            2: [P(x0, y1, z0), P(x1, y1, z0), P(x1, y1, z1), P(x0, y1, z1)],  # +y face
            3: [P(x1, y0, z0), P(x1, y1, z0), P(x1, y1, z1), P(x1, y0, z1)],  # +x face
            1: [P(x0, y0, z1), P(x1, y0, z1), P(x1, y1, z1), P(x0, y1, z1)],  # top
        }
        for f, poly in faces.items():
            im = Image.new("L", size, 0)
            ImageDraw.Draw(im).polygon(poly, fill=1)
            m = np.asarray(im, bool)
            if not m.any():
                continue
            # invert the projection on the face's plane -> piece-space point -> distance towards the viewer
            if f == 1:
                s = 4 * (yy - oy + z1 * W / 2) / W  # x + y
                x, y, z = (s + u) / 2, (s - u) / 2, np.full_like(u, z1)
            elif f == 2:
                y = np.full_like(u, y1)
                x = u + y
                z = ((x + y) * W / 4 + oy - yy) * 2 / W
            else:
                x = np.full_like(u, x1)
                y = x - u
                z = ((x + y) * W / 4 + oy - yy) * 2 / W
            d = (x + y) * 0.6124 + z * _KZ * 0.5
            win = m & (d > depth + 1e-4)
            depth[win], face[win], own[win] = d[win], f, owner
            if xyz:
                X[win], Y[win], Z[win] = x[win], y[win], z[win]
    return (face, own, X, Y, Z) if xyz else (face, own)


def shade_image(face, normals=None, base=None):
    """Flat-shaded template from a face map (or from per-pixel normals for meshes)."""
    img = np.full(face.shape + (3,), 255, np.float32)
    if normals is not None:  # meshes: interpolate the three shades by normal direction
        n = np.abs(normals)
        w = n / np.maximum(n.sum(-1, keepdims=True), 1e-6)
        g = w[..., 2] * SHADE[1] + w[..., 1] * SHADE[2] + w[..., 0] * SHADE[3]
        img[face > 0] = g[face > 0, None]
    else:
        for f, g in SHADE.items():
            img[face == f] = g
    if base is not None:
        img[face > 0] = img[face > 0] * 0.6 + np.asarray(base, np.float32) * 0.4
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def face_normals(face):
    """Per-pixel piece-space normals from a face map."""
    n = np.zeros(face.shape + (3,), np.float32)
    for f, v in FACE_N.items():
        n[face == f] = v
    return n


def encode_normal(n_screen, alpha):
    """Screen-space normals -> RGBA normal map (Unity sprite _NormalMap convention)."""
    out = np.zeros(n_screen.shape[:2] + (4,), np.uint8)
    out[..., :3] = np.clip((n_screen * 0.5 + 0.5) * 255, 0, 255)
    out[..., 3] = alpha
    return Image.fromarray(out)


def detail_normals(n_screen, rgba, strength=0.9):
    """Add bump from the painted pixels' luminance (dark mortar/cracks read as grooves)."""
    a = np.asarray(rgba).astype(np.float32) / 255
    lum = a[..., :3] @ np.array([0.3, 0.59, 0.11], np.float32)
    m = a[..., 3] > 0.5
    lum = np.where(m, lum, np.nan)
    fill = np.nanmean(lum) if m.any() else 0.5
    lum = np.where(m, lum, fill)
    gx = (np.roll(lum, -1, 1) - np.roll(lum, 1, 1)) / 2
    gy = (np.roll(lum, -1, 0) - np.roll(lum, 1, 0)) / 2
    n = n_screen.copy()
    n[..., 0] -= gx * strength
    n[..., 1] += gy * strength
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
