"""Dungeon Forge geometry: the flat-shaded templates FLUX paints over (exact 2:1 iso diamonds, wall blocks, pillars,
thin walls and meshes), piece layout and mirroring, atlas sizes and the floor warp, and the output resolution
(downsampling to the board grid, pixel steps).

Native tile size: `tile` px wide (64 -> 64x32 diamond); heights are in tile units (1 unit = tile/2 px, matching
Prototype V's placeholders). Generation runs at SCALE x the native size.
"""
from pathlib import Path

import numpy as np
from PIL import Image

from hdart.core import config
from hdart.tools.dungeon import iso


SCALE = 8  # generation is done at 8x the native pixel size


# ----------------------------------------------------------------------------- geometry templates

def canvas_size(piece, tile):
    """Native (w, h) of a piece's sprite: tile wide, diamond + extrusion tall."""
    h_units = piece.get("height", 0.0)
    return tile, int(round(tile / 2 + h_units * tile / 2))


def template(piece, tile):
    """Flat-shaded geometry at SCALE: returns (RGB image, silhouette mask, face map 0 none/1 top/2 left/3 right)."""
    w, h = canvas_size(piece, tile)
    W, H = w * SCALE, h * SCALE
    kind = piece["shape"]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32) + 0.5
    face = np.zeros((H, W), np.uint8)

    def diamond(cx, cy, hw, hh):
        return (np.abs(xx - cx) / hw + np.abs(yy - cy) / hh) <= 1.0

    base_cy = H - W / 4  # centre of the floor diamond
    if kind in ("floor", "decal"):
        face[diamond(W / 2, base_cy, W / 2, W / 4)] = 1
    elif kind in ("block", "wall"):
        height = piece.get("height", 1.0) * (W / 2)  # one height unit = tile/2 native px
        top_cy = base_cy - height
        top = diamond(W / 2, top_cy, W / 2, W / 4)
        # side faces: the lower edges of the top diamond extruded down to the floor
        left = (xx <= W / 2) & (yy >= top_cy + xx * 0.5) & (yy <= base_cy + xx * 0.5)
        right = (xx > W / 2) & (yy >= top_cy + (W - xx) * 0.5) & (yy <= base_cy + (W - xx) * 0.5)
        face[left] = 2
        face[right] = 3
        face[top] = 1
    elif kind == "pillar":
        height = piece.get("height", 1.5) * (W / 2)
        r = W * 0.22
        body = (np.abs(xx - W / 2) <= r) & (yy <= base_cy) & (yy >= base_cy - height)
        cap_b = ((xx - W / 2) / r) ** 2 + ((yy - base_cy) / (r / 2)) ** 2 <= 1
        cap_t = ((xx - W / 2) / r) ** 2 + ((yy - (base_cy - height)) / (r / 2)) ** 2 <= 1
        face[diamond(W / 2, base_cy, W / 2, W / 4)] = 1  # floor under it
        face[body | cap_b] = np.where(xx[body | cap_b] < W / 2, 2, 3)
        face[cap_t] = 1
    m = face > 0
    shade = {0: (255, 255, 255), 1: (150, 148, 156), 2: (112, 110, 120), 3: (78, 76, 88)}
    img = np.zeros((H, W, 3), np.uint8)
    for k, c in shade.items():
        img[face == k] = c
    return Image.fromarray(img), m, face


THIN = ("run", "post", "mesh")  # thin-wall pieces (hdart/tools/dungeon/iso.py geometry)


def meshes_dir() -> Path:
    """<data>/sprites/dungeon/_meshes: cached Blender normal passes of the mesh pieces."""
    return config.data_dir() / "sprites" / "dungeon" / "_meshes"


def thin_boxes(piece, length, h):
    """Boxes of a thin-wall piece. Run halves are owned separately (2k + half) so a run slices into arms."""
    t = piece.get("thickness", 0.25)
    y0, y1 = 0.5 - t / 2, 0.5 + t / 2
    if piece["shape"] == "post":
        p = t * 0.6  # a touch fatter than the wall so it covers the arm ends
        return [(0.5 - p, 0.5 + p, 0.5 - p, 0.5 + p, 0, h + 0.15, 0)]
    if piece.get("opening") == "door":
        dh, dw = piece.get("door_height", 1.3), piece.get("door_width", 0.5)
        a, b, lt = 0.5 - dw / 2, 0.5 + dw / 2, t * 0.5
        boxes = [(0, a, y0, y1, 0, h, 0), (b, 1, y0, y1, 0, h, 0), (a, b, y0, y1, dh, h, 0),
                 (a, b, 0.5 - lt / 2, 0.5 + lt / 2, 0, dh, 1)]  # jambs, lintel, recessed door leaf (owner 1)
        return [bx for bx in boxes if bx[1] - bx[0] > 1e-6]  # a full-width door has no jambs
    return [(k + hh / 2, k + (hh + 1) / 2, y0, y1, 0, h, 2 * k + hh) for k in range(length) for hh in (0, 1)]


def mesh_pass(piece, tile, h, length):
    """Blender normal pass of a mesh piece (cached) -> (piece-space normals, mask)."""
    name = f"{piece['mesh']}_t{tile}_h{h:g}_l{length}"
    png = meshes_dir() / f"{name}.normal.png"
    if not png.exists():
        import subprocess
        extra = [a for k in ("opening", "spring") if k in piece for a in (f"--{k}", str(piece[k]))]
        subprocess.run([config.blender(), "-b", "-P", str(config.ROOT / "blender" / "mesh_template.py"), "--",
                        "--mesh", piece["mesh"], "--tile", str(tile), "--height", f"{h:g}", "--length", str(length),
                        "--scale", str(SCALE), "--thickness", str(piece.get("thickness", 0.25)), "--out", str(meshes_dir()),
                        *extra], check=True, capture_output=True)
    a = np.asarray(Image.open(png).convert("RGBA")).astype(np.float32) / 255
    mask = a[..., 3] > 0.5
    nb = a[..., :3] * 2 - 1  # Blender world normal -> piece space (y flipped)
    n = np.stack([nb[..., 0], -nb[..., 1], nb[..., 2]], -1)
    n[~mask] = 0
    return n, mask


def geometry(piece, tile):
    """Template + silhouette + ownership + piece-space normals at SCALE for any tile-like piece."""
    shape = piece.get("shape")
    if shape in THIN:
        h = piece.get("height", 2.0)
        L = 1 if shape == "post" else int(piece.get("length", 1))
        W, Hc = iso.cell_canvas(tile, h, SCALE)
        size = iso.run_canvas(tile, h, L, SCALE)
        if shape == "mesh":
            n, mask = mesh_pass(piece, tile, h, L)
            face = mask.astype(np.uint8)
            own = np.where(mask, 0, -1)
            img = iso.shade_image(face, normals=n)
        else:
            face, own = iso.render_boxes(thin_boxes(piece, L, h), size, W, Hc)
            mask, n = face > 0, iso.face_normals(face)
            img = iso.shade_image(face)
            if piece.get("opening") == "door":  # tint the leaf wood-brown so FLUX paints a door there
                a = np.asarray(img).astype(np.float32)
                leaf = mask & (own == 1)
                a[leaf] = a[leaf] * np.array([0.95, 0.68, 0.45]) * 0.85
                img = Image.fromarray(a.astype(np.uint8))
                own = np.where(mask, 0, -1)
        return {"img": img, "mask": mask, "own": own, "n": n, "length": L, "height": h, "cell": (W, Hc)}
    tpl, mask, face = template(piece, tile)
    return {"img": tpl, "mask": mask, "own": np.where(mask, 0, -1), "n": iso.face_normals(face), "length": 1,
            "height": piece.get("height", 0.0), "cell": mask.shape[::-1]}


def layout(piece):
    """How a piece is cut for the game: single sprite, run halves, or an NxN floor atlas."""
    if piece["kind"] == "prop":
        return "single"
    if piece.get("method") == "atlas" or piece.get("method") == "repaint" and atlas_n(piece) > 1:
        return "atlas"
    if piece.get("shape") == "run" and piece.get("opening") != "door":
        return "run"
    return "single"


def mirrored(piece):
    """Thin pieces along x also ship mirrored (= the same piece along y)."""
    return piece["kind"] != "prop" and piece.get("shape") in ("run", "mesh")
SQUARE_METHODS = ("atlas", "master", "repaint")


def atlas_n(piece):
    """Tiles per side of a floor piece's square: atlas N, repaint N (default 1), else 1."""
    m = piece.get("method")
    return int(piece.get("atlas", 4)) if m == "atlas" else int(piece.get("atlas", 1)) if m in ("repaint", "wall_repaint") else 1


def block_mask(N, per, band, ramp=0.10):
    """0 in an outer band `band` tiles wide (kept: the master's edges), ramping to 1 over `ramp` tiles inside."""
    S = N * per
    yy, xx = np.mgrid[0:S, 0:S] + 0.5
    d = np.minimum.reduce([xx, yy, S - xx, S - yy]) / per
    return np.clip((d - band) / ramp, 0, 1)


def warp_floor(src, tile, u0=0.0, u1=1.0, v0=0.0, v1=1.0, rim=0.55):
    """A square region of a top-down texture -> the iso floor diamond at SCALE (RGB on white, mask)."""
    src = np.asarray(src).astype(np.float32)
    S = src.shape[0]
    _, mask, _ = template({"shape": "floor"}, tile)
    H, W = mask.shape
    top = H - W / 2
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32) + 0.5
    a = ((xx - W / 2) / (W / 2) + (yy - top) / (W / 4)) / 2  # piece x within the cell
    b = ((yy - top) / (W / 4) - (xx - W / 2) / (W / 2)) / 2  # piece y within the cell
    sx = np.clip(((u0 + a * (u1 - u0)) * S).astype(int), 0, S - 1)
    sy = np.clip(((v0 + b * (v1 - v0)) * S).astype(int), 0, S - 1)
    out = np.full((H, W, 3), 255, np.float32)
    out[mask] = src[sy[mask], sx[mask]]
    if rim:
        edge = mask & ((a > 0.97) | (b > 0.97))  # the two front edges
        out[edge] *= rim
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), mask


# ----------------------------------------------------------------------------- downsample

def downsample(img, mask, out_w, out_h, colors=None):
    """Area-average into the output grid with hard alpha (premultiplied, so white never bleeds in). Box sums come
    from integral images, so full-resolution output (hundreds of px per tile) is as quick as the pixel-art sizes."""
    a = np.asarray(img.convert("RGB")).astype(np.float64) / 255
    m = mask.astype(np.float64)
    H, W = m.shape
    ky, kx = H / out_h, W / out_w
    ys = np.array([int(round(i * ky)) for i in range(out_h + 1)])
    xs = np.array([int(round(i * kx)) for i in range(out_w + 1)])

    def boxes(v):  # sums of v over every output cell [ys[i], ys[i+1]) x [xs[j], xs[j+1])
        s = np.zeros((H + 1, W + 1) + v.shape[2:])
        s[1:, 1:] = v.cumsum(0).cumsum(1)
        r = s[ys]
        return r[1:, xs[1:]] - r[1:, xs[:-1]] - r[:-1, xs[1:]] + r[:-1, xs[:-1]]

    sm = boxes(m)
    area = np.outer(np.diff(ys), np.diff(xs))
    al = sm / np.maximum(area, 1)
    rgb = boxes(a * m[..., None]) / np.maximum(sm, 1e-12)[..., None]
    rgb[sm == 0] = 0
    out = np.zeros((out_h, out_w, 4), np.uint8)
    out[..., :3] = np.clip(rgb * 255, 0, 255)
    out[..., 3] = (al >= 0.5) * 255
    im = Image.fromarray(out)
    if colors:
        from hdart.imaging.pixel import quantize
        im = quantize(im, colors)
    return im


# ----------------------------------------------------------------------------- output resolution (pixelation)

def full_tile(spec):
    """Pixels per tile at full resolution: what FLUX paints (SCALE x the set's grid size)."""
    return SCALE * int(spec["tile"])


def output_tile(spec):
    """Pixels per tile in previews and the Unity export. The set's `pixel` setting (Studio > Dungeon slider)
    downsamples to that many px per tile for the pixel-art look; unset = full resolution."""
    px = int(spec.get("pixel") or 0)
    return px if px else full_tile(spec)


def pixel_steps(spec):
    """Slider stops, chunkiest first. Multiples of 4 so half and quarter tiles stay whole pixels."""
    T = int(spec["tile"])
    steps = {max(4, int(round(T * f / 4)) * 4) for f in (0.25, 0.375, 0.5, 0.75, 1, 1.5, 2, 3, 4)}
    return sorted(px for px in steps if px < full_tile(spec))
