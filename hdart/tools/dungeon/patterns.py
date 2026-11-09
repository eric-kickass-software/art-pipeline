"""Procedural layout templates for floor and wall materials: brick bonds, ashlar, flagstones, cobbles, tiles, planks.

Everything is laid out in tile units (1 tile = one board cell; heights in iso units convert with iso.KZ) and repeats
exactly once per tile across (and per tile down, for floors), so a template is seamless and at a known scale by
construction. Dungeon Forge uses a template three ways: as the starting image for a family's master (img2img keeps
the layout, FLUX paints the surface), as the blockout in the room preview before anything is generated, and for its
region map (which pixels are stone, which are mortar).

render(pattern, params, palette, width_tiles, height_tiles, per, seed, periodic_v) -> {"rgb", "height", "region"}
"""
import numpy as np
from PIL import Image

# defaults per pattern; sizes in tiles. "across" = units per tile width (an integer, so the pattern repeats per tile)
PATTERNS = {
    "running_bond": {"label": "brick: running bond", "across": 4, "course": 0.1, "mortar": 0.012, "bevel": 0.012},
    "stack_bond":   {"label": "brick: stack bond", "across": 4, "course": 0.1, "mortar": 0.012, "bevel": 0.012},
    "english_bond": {"label": "brick: English bond", "across": 4, "course": 0.1, "mortar": 0.012, "bevel": 0.012},
    "basketweave":  {"label": "brick: basketweave", "across": 4, "course": 0.0, "mortar": 0.010, "bevel": 0.010, "per_block": 2},
    "ashlar":       {"label": "stone: ashlar (random blocks)", "across": 3, "course": 0.16, "mortar": 0.014, "bevel": 0.02},
    "flagstone":    {"label": "stone: irregular flagstones", "across": 4, "course": 0.0, "mortar": 0.016, "bevel": 0.025, "jitter": 0.55},
    "cobble":       {"label": "stone: cobbles", "across": 9, "course": 0.0, "mortar": 0.012, "bevel": 0.03, "jitter": 0.55},
    "square_tiles": {"label": "tiles: square", "across": 4, "course": 0.0, "mortar": 0.010, "bevel": 0.006},
    "hex_tiles":    {"label": "tiles: hexagonal", "across": 5, "course": 0.0, "mortar": 0.010, "bevel": 0.008},
    "planks":       {"label": "wood: planks", "across": 1, "course": 0.12, "mortar": 0.006, "bevel": 0.004},
}
DEFAULT_PALETTE = {"stones": ["#7d7c80", "#6f6e73", "#87858a", "#76736f"], "mortar": "#3b3a3f", "variation": 0.08}


def _hex(c):
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def _hash(*ints):
    h = np.uint64(1469598103934665603)
    out = np.full(np.broadcast(*ints).shape, h, np.uint64)
    for v in ints:
        out ^= np.asarray(v).astype(np.int64).astype(np.uint64)
        out *= np.uint64(1099511628211)
    return out


def _rand(*ints):
    """Deterministic 0..1 per integer key."""
    return (_hash(*ints) % np.uint64(1000003)).astype(np.float64) / 1000003.0


def _rows(u, v, across, course, offset_fn, periodic_v, h_tiles):
    """Rect units in rows: (cell id, distance to the unit's edge in tiles)."""
    rows = max(1, round(1 / course)) if periodic_v else None
    ch = 1 / rows if periodic_v else course
    vb = (h_tiles - v) if not periodic_v else v  # walls: courses counted up from the base
    row = np.floor(vb / ch).astype(np.int64)
    width = 1.0 / across
    off = offset_fn(row % rows if periodic_v else row) * width  # offsets repeat with the rows
    x = u + off
    col = np.floor(x / width).astype(np.int64)
    fx, fy = x - col * width, vb - row * ch
    d = np.minimum.reduce([fx, width - fx, fy, ch - fy])
    rmod = row % rows if periodic_v else row
    return _hash(rmod, col % across), d


def _english(u, v, across, course, periodic_v, h_tiles):
    rows = max(2, round(1 / course / 2) * 2) if periodic_v else None
    ch = 1 / rows if periodic_v else course
    vb = (h_tiles - v) if not periodic_v else v
    row = np.floor(vb / ch).astype(np.int64)
    n = np.where(row % 2 == 0, across, across * 2)  # stretcher course, then header course
    width = 1.0 / n
    x = u + np.where(row % 2 == 0, 0, 0.5) * width
    col = np.floor(x / width).astype(np.int64)
    fx, fy = x - col * width, vb - row * ch
    d = np.minimum.reduce([fx, width - fx, fy, ch - fy])
    return _hash(row % rows if periodic_v else row, col % n), d


def _basket(u, v, across, per_block):
    s = 1.0 / across                      # block size
    bx, by = np.floor(u / s).astype(np.int64), np.floor(v / s).astype(np.int64)
    fx, fy = u - bx * s, v - by * s
    horiz = (bx + by) % 2 == 0
    t = s / per_block
    k = np.where(horiz, np.floor(fy / t), np.floor(fx / t)).astype(np.int64)
    a = np.where(horiz, fy - k * t, fx - k * t)
    b = np.where(horiz, fx, fy)
    d = np.minimum.reduce([a, t - a, b, s - b])
    return _hash(bx % across, by % across, k), d


def _ashlar(u, v, across, course, seed, periodic_v, h_tiles):
    """Rows of random heights (summing to 1 tile on floors), each row random block widths summing to 1 tile."""
    vb = (h_tiles - v) if not periodic_v else v
    span = 1.0 if periodic_v else h_tiles
    hs, acc, i = [], 0.0, 0
    while acc < span - 1e-9:
        hh = course * (0.65 + 0.7 * _rand(seed, 77, i))
        hs.append(min(hh, span - acc) if periodic_v and span - acc < course * 0.6 + hh else hh)
        acc += hs[-1]
        i += 1
    if periodic_v:
        hs = [h * span / sum(hs) for h in hs]
    edges = np.concatenate([[0], np.cumsum(hs)])
    vv = np.mod(vb, span) if periodic_v else vb
    row = np.clip(np.searchsorted(edges, vv, side="right") - 1, 0, len(hs) - 1)
    fy = vv - edges[row]
    ch = np.asarray(hs)[row]
    ids = np.zeros(u.shape, np.uint64)
    d = np.zeros(u.shape)
    for r in range(len(hs)):
        m = row == r
        if not m.any():
            continue
        n = max(1, int(round(across * (0.8 + 0.6 * _rand(seed, 91, r)))))
        ws = np.array([0.6 + 0.8 * _rand(seed, r, j) for j in range(n)])
        xs = np.concatenate([[0], np.cumsum(ws / ws.sum())])
        sh = _rand(seed, 13, r)
        x = np.mod(u[m] + sh, 1.0)
        c = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, n - 1)
        fx = x - xs[c]
        wcol = np.diff(xs)[c]
        d[m] = np.minimum.reduce([fx, wcol - fx, fy[m], ch[m] - fy[m]])
        ids[m] = _hash(r, c, seed)
    return ids, d


def _voronoi(u, v, points, periodic_v, h_tiles):
    """Periodic Voronoi (torus across, and down when periodic_v): cell id and half the gap to the second-nearest."""
    best = np.full(u.shape, np.inf)
    second = np.full(u.shape, np.inf)
    ids = np.zeros(u.shape, np.int64)
    shifts_v = (-1, 0, 1) if periodic_v else (0,)
    for i, (px, py) in enumerate(points):
        for dx in (-1, 0, 1):
            for dy in shifts_v:
                dd = (u - (px + dx)) ** 2 + (v - (py + dy) * (1 if periodic_v else 1)) ** 2
                closer = dd < best
                second = np.where(closer, best, np.minimum(second, dd))
                ids = np.where(closer, i, ids)
                best = np.where(closer, dd, best)
    d = (np.sqrt(second) - np.sqrt(best)) / 2
    return ids.astype(np.uint64), d


def _voronoi_points(kind, across, jitter, seed, periodic_v, h_tiles):
    pts = []
    rows_span = 1.0 if periodic_v else h_tiles
    if kind == "hex":
        cols = across
        rows = max(2, int(round(rows_span * across * 2 / np.sqrt(3) / 2)) * 2)
        for r in range(rows):
            for c in range(cols):
                pts.append(((c + 0.5 * (r % 2) + 0.25) / cols, (r + 0.5) / rows * rows_span))
        return pts
    rows = max(1, int(round(across * rows_span)))
    for r in range(rows):
        for c in range(across):
            jx, jy = (_rand(seed, r, c, 1) - 0.5) * jitter, (_rand(seed, r, c, 2) - 0.5) * jitter
            pts.append(((c + 0.5 + jx) / across, (r + 0.5 + jy) / rows * rows_span))
    return pts


def render(pattern, params=None, palette=None, width_tiles=1, height_tiles=1.0, per=512, seed=1, periodic_v=True):
    """A template image (`per` px per tile): {"rgb": PIL image, "height": 0..1 (mortar 0), "region": 0 mortar / 1 unit}."""
    p = {**PATTERNS[pattern], **(params or {})}
    pal = {**DEFAULT_PALETTE, **(palette or {})}
    W, H = int(round(width_tiles * per)), int(round(height_tiles * per))
    yi, xi = np.mgrid[0:H, 0:W]
    yy, xx = yi + 0.5, xi + 0.5
    # coordinates from the pixel index within its tile, so every tile gets bit-identical numbers (exact repeats)
    um = (np.mod(xi, per) + 0.5) / per
    v = ((np.mod(yi, per) if periodic_v else yi) + 0.5) / per
    u = um
    across, course = int(p["across"]), float(p.get("course") or 0.1)
    if pattern in ("running_bond", "stack_bond", "square_tiles", "planks"):
        if pattern == "square_tiles":
            course = 1.0 / across
        shift = {"running_bond": lambda r: np.where(r % 2 == 1, 0.5, 0.0), "stack_bond": lambda r: 0.0 * r,
                 "square_tiles": lambda r: 0.0 * r, "planks": lambda r: _rand(seed, r % 97)}[pattern]
        ids, d = _rows(um, v, across, course, shift, periodic_v, height_tiles)
    elif pattern == "english_bond":
        ids, d = _english(um, v, across, course, periodic_v, height_tiles)
    elif pattern == "basketweave":
        ids, d = _basket(um, np.mod(v, 1.0) if periodic_v else v, across, int(p.get("per_block", 2)))
    elif pattern == "ashlar":
        ids, d = _ashlar(um, v, across, course, seed, periodic_v, height_tiles)
    else:
        kind = "hex" if pattern == "hex_tiles" else "voronoi"
        pts = _voronoi_points(kind, across, float(p.get("jitter", 0.0)), seed, periodic_v, height_tiles)
        ids, d = _voronoi(um, np.mod(v, 1.0) if periodic_v else v, pts, periodic_v, height_tiles)
    joint = max(float(p["mortar"]), 1.0 / per) if p["mortar"] > 0 else 0.0  # joints never thinner than a pixel
    mortar = d < joint / 2
    height = np.clip((d - joint / 2) / max(p["bevel"], 1e-6), 0, 1)
    stones = np.stack([_hex(c) for c in pal["stones"]])
    pick = (ids % np.uint64(len(stones))).astype(np.int64)
    tone = 1 + (_rand(ids, seed, 5) - 0.5) * 2 * pal["variation"]
    rgb = stones[pick] * tone[..., None]
    rgb *= (0.78 + 0.22 * height)[..., None]  # a bevel: darker towards the joints
    gx = np.floor(np.mod(xx, per) / 3).astype(np.int64)  # fine grain, repeating per tile like the layout
    gy = np.floor((np.mod(yy, per) if periodic_v else yy) / 3).astype(np.int64)
    grain = (_rand(gx, gy, seed) - 0.5) * 10
    rgb += grain[..., None]
    mort = _hex(pal["mortar"])
    rgb[mortar] = mort + grain[mortar][:, None] * 0.6
    return {"rgb": Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)), "height": np.where(mortar, 0.0, height),
            "region": (~mortar).astype(np.uint8)}


def floor_template(tpl, per=512):
    """A family template ({pattern, params, palette, seed}) as a one-tile floor square."""
    return render(tpl["pattern"], tpl.get("params"), tpl.get("palette"), 1, 1.0, per, int(tpl.get("seed", 1)), True)


def wall_template(tpl, height_tiles, per=512):
    """A family template as a one-tile-wide wall elevation (`height_tiles` tall; courses counted up from the base)."""
    return render(tpl["pattern"], tpl.get("params"), tpl.get("palette"), 1, height_tiles, per, int(tpl.get("seed", 1)), False)
