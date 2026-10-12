"""Mock dungeon room: tiles on the iso grid, characters on tiles, torch light from the normal maps.
A quick stand-in for the Unity board view so the art can be judged together."""
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image

ROOM = [  # W wall, . floor, P pillar, R rubble, B bones, D door (ne wall), d door (nw wall)
    "WWWWDWWWW",
    "W.......W",
    "W.P...P.W",
    "W...B...W",
    "d...R...W",
    "W.......W",
    "W.P.R.P.W",
    "W....B..W",
    "WWWWWWWWW",
]


def _load(p):
    return np.asarray(Image.open(p).convert("RGBA")).astype(np.float32) / 255


def render_room(tiles_dir, kit=None, apps=None, out=None, scale=2, torches=None, open_door=True, seed=3):
    tiles_dir = Path(tiles_dir)
    meta = json.loads((tiles_dir / "tiles.json").read_text())
    T = meta["tile"]
    cw, ch = meta["canvas"]
    piv_y = ch - meta["pivot"][1] * ch  # pivot y from the top of the sprite
    rng = random.Random(seed)
    sprites = {}

    def sprite(name):
        if name not in sprites:
            sprites[name] = (_load(tiles_dir / f"{name}.png"), _load(tiles_dir / f"{name}.normal.png"))
        return sprites[name]

    def pick(prefix):
        opts = [s for s in meta["sprites"] if s.startswith(prefix + ".") and s[len(prefix) + 1:].isdigit()]
        if not opts:
            raise KeyError(prefix)
        return rng.choice(opts)

    n = len(ROOM)
    # world +X -> screen right/down, +Y -> right/up; row r = +X, column c = +Y
    def screen(r, c):
        return (r + c) * T / 2, (r - c) * T / 4

    xs = [screen(r, c) for r in range(n) for c in range(n)]
    minx, maxx = min(x for x, _ in xs), max(x for x, _ in xs)
    miny, maxy = min(y for _, y in xs), max(y for _, y in xs)
    W = int(maxx - minx + cw + 40)
    H = int(maxy - miny + ch + 40)
    ox, oy = -minx + cw / 2 + 20, -miny + piv_y + 20
    albedo = np.zeros((H, W, 4), np.float32)
    normal = np.zeros((H, W, 3), np.float32)
    normal[..., 2] = 1

    def blit(img, nrm, x, y):
        h, w = img.shape[:2]
        x0, y0 = int(round(x)), int(round(y))
        a = img[..., 3:4]
        region = albedo[y0:y0 + h, x0:x0 + w]
        region[..., :3] = region[..., :3] * (1 - a) + img[..., :3] * a
        region[..., 3:] = np.maximum(region[..., 3:], a)
        if nrm is not None:
            nr = normal[y0:y0 + h, x0:x0 + w]
            nr[:] = nr * (1 - a) + (nrm[..., :3] * 2 - 1) * a

    order = sorted(((r, c) for r in range(n) for c in range(n)), key=lambda rc: (rc[0] - rc[1], rc[0] + rc[1]))
    actors = {}
    if kit and apps:
        free = [(r, c) for r, c in order if ROOM[r][c] == "."]
        rng.shuffle(free)
        for app, rc in zip(apps, free):
            actors[rc] = app
    for r, c in order:
        k = ROOM[r][c]
        sx, sy = screen(r, c)
        x, y = ox + sx - cw / 2, oy + sy - piv_y
        if k == "W":  # near walls (bottom/right edges of the screen) use the low cutaway version
            near = r == n - 1 or c == 0
            name = pick("tile.crypt_wall_low" if near else "tile.crypt_wall")
        elif k in "Dd":
            name = f"tile.crypt_door.{'open' if open_door else 'closed'}.{'ne' if k == 'D' else 'nw'}"
        else:
            name = pick({"P": "tile.crypt_pillar", "R": "tile.crypt_rubble", "B": "tile.crypt_bones"}.get(k, "tile.crypt_floor"))
        blit(*sprite(name), x, y)
        if (r, c) in actors:
            app = actors[(r, c)]
            color, nrm = kit.compose(app, "token")
            vc = kit.manifest["views"]["token"]
            row = next(rw for rw in vc["rows"] if rw["clip"] == "idle" and rw["dir"] == app.get("_dir", "sw"))
            w, h = vc["cell"]
            cell = color[row["row"] * h:(row["row"] + 1) * h, :w].astype(np.float32) / 255
            ncell = nrm[row["row"] * h:(row["row"] + 1) * h, :w].astype(np.float32) / 255
            blit(cell, ncell, ox + sx - w / 2, oy + sy - h * 0.86)

    # torch light from normals: ambient + warm point lights placed in screen space (z = height)
    torches = torches or [(ox + screen(2, 4)[0], oy + screen(2, 4)[1] - 30, 70, (1.0, 0.72, 0.42), 1.5),
                          (ox + screen(6, 2)[0], oy + screen(6, 2)[1] - 30, 70, (1.0, 0.65, 0.38), 1.3),
                          (ox + screen(4, 7)[0], oy + screen(4, 7)[1] - 30, 60, (0.55, 0.65, 1.0), 0.6)]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    nrm = normal / np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-6)
    light = np.full((H, W, 3), 0.42, np.float32)
    for lx, ly, lz, col, inten in torches:
        L = np.stack([lx - xx, -(ly - yy), np.full_like(xx, lz)], axis=-1)
        dist = np.linalg.norm(L, axis=-1, keepdims=True)
        L /= dist
        ndl = np.clip((nrm * L).sum(-1, keepdims=True), 0, 1)
        fall = np.clip(1 - dist / (T * 4.5), 0, 1) ** 2
        light += ndl * fall * np.array(col) * inten
    lit = np.clip(albedo[..., :3] * light, 0, 1)
    bg = np.array([0.05, 0.04, 0.06])
    a = albedo[..., 3:]
    img = (lit * a + bg * (1 - a)) * 255
    flat = (albedo[..., :3] * a + bg * (1 - a)) * 255
    both = np.concatenate([flat, img], axis=1).astype(np.uint8)
    im = Image.fromarray(both)
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    if out:
        im.save(out)
    return im
