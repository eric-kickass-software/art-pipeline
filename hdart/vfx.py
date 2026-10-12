"""Procedural pixel-art spell effects (battle screen VFX flipbooks).

Each effect is an intensity field drawn at `ss` x resolution from deterministic particles and
shapes, box-reduced to the pixel grid, then mapped onto an element ramp (palettes.json `magic.*`
or any ramp id): brighter = higher ramp index. No AI involved: effects must loop and read cleanly
at 64 px, which procedural shapes do better than image models.
"""
import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image

from .palette import Palettes


class Canvas:
    def __init__(self, size, ss=4):
        self.n = size * ss
        self.ss = ss
        self.size = size
        y, x = np.mgrid[0:self.n, 0:self.n].astype(np.float32) / self.n  # 0..1 coords, y down
        self.x, self.y = x, y
        self.I = np.zeros((self.n, self.n), np.float32)

    def disc(self, cx, cy, r, val=1.0, soft=0.4):
        d = np.hypot(self.x - cx, self.y - cy)
        inner = r * (1 - soft)
        f = np.clip((r - d) / max(r - inner, 1e-6), 0, 1)
        core = 0.62 + 0.38 * np.clip(1 - d / max(r, 1e-6), 0, 1) ** 0.8  # bright centre, darker rim
        self.I = np.maximum(self.I, f * core * val)

    def ring(self, cx, cy, r, w, val=1.0):
        d = np.abs(np.hypot(self.x - cx, self.y - cy) - r)
        self.I = np.maximum(self.I, np.clip(1 - d / max(w, 1e-6), 0, 1) * val)

    def line(self, x0, y0, x1, y1, w, val=1.0):
        px, py = self.x - x0, self.y - y0
        dx, dy = x1 - x0, y1 - y0
        t = np.clip((px * dx + py * dy) / max(dx * dx + dy * dy, 1e-9), 0, 1)
        d = np.hypot(px - t * dx, py - t * dy)
        self.I = np.maximum(self.I, np.clip(1 - d / max(w, 1e-6), 0, 1) * val)

    def plus(self, cx, cy, r, val=1.0):
        w = r * 0.28
        self.line(cx - r, cy, cx + r, cy, w, val)
        self.line(cx, cy - r, cx, cy + r, w, val)

    def reduce(self):
        ss = self.ss
        return self.I.reshape(self.size, ss, self.size, ss).mean(axis=(1, 3))


def bolt(c, t, rng):
    hx = 0.15 + 0.7 * t
    for k in range(7):
        tt = k / 7
        c.disc(hx - tt * 0.28, 0.5 + math.sin(t * 20 + k) * 0.015, 0.075 * (1 - tt), 0.85 * (1 - tt) ** 1.3)
    c.disc(hx, 0.5, 0.1, 1.0, 0.55)
    for _ in range(4):
        a = rng.uniform(math.pi * 0.6, math.pi * 1.4)
        r = rng.uniform(0.06, 0.2)
        c.disc(hx + math.cos(a) * r, 0.5 + math.sin(a) * r, 0.018, rng.uniform(0.4, 0.9), 0.2)


def burst(c, t, rng):
    c.disc(0.5, 0.55, 0.22 * (1 - t) ** 0.7 + 0.02, 1.0 * (1 - t * 0.6), 0.6)
    c.ring(0.5, 0.55, 0.08 + 0.35 * t ** 0.7, 0.05 * (1 - t) + 0.01, 0.9 * (1 - t))
    for k in range(14):
        a = 2 * math.pi * k / 14 + rng.uniform(-0.2, 0.2)
        r = (0.1 + rng.uniform(0.25, 0.4) * t)
        c.disc(0.5 + math.cos(a) * r, 0.55 + math.sin(a) * r + 0.15 * t * t, 0.025 * (1 - t) + 0.008, 0.9 * (1 - t ** 2), 0.2)


def heal(c, t, rng):
    c.disc(0.5, 0.62, 0.28, 0.35 * math.sin(math.pi * t), 0.95)
    for k in range(12):
        ph = (t + k / 12) % 1.0
        x = 0.5 + math.sin(k * 2.4) * 0.22
        y = 0.9 - ph * 0.75
        v = math.sin(math.pi * ph)
        if k % 3 == 0:
            c.plus(x, y, 0.045 * v + 0.01, v)
        else:
            c.disc(x, y, 0.022 * v + 0.006, v * 0.9, 0.2)


def ward(c, t, rng):
    pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi)
    c.ring(0.5, 0.62, 0.36, 0.03 + 0.015 * pulse, 0.8 + 0.2 * pulse)
    c.ring(0.5, 0.62, 0.3, 0.012, 0.45 * pulse)
    for k in range(6):  # shimmer glints running round the dome
        a = 2 * math.pi * (k / 6 + t * 0.5)
        c.disc(0.5 + math.cos(a) * 0.36, 0.62 + math.sin(a) * 0.36, 0.03, 1.0, 0.3)
    c.I *= (c.y < 0.75)  # cut the dome at the floor


def drain(c, t, rng):
    for k in range(16):
        ph = (t + k / 16) % 1.0
        a = k * 2.4 + ph * 4.0
        r = 0.42 * (1 - ph)
        c.disc(0.5 + math.cos(a) * r, 0.5 + math.sin(a) * r * 0.8, 0.02 + 0.015 * (1 - ph), 0.4 + 0.6 * ph, 0.2)
    c.disc(0.5, 0.5, 0.06 + 0.03 * math.sin(t * 6.28), 0.9, 0.5)


def lightning(c, t, rng):
    if t > 0.85:
        return
    x, y = 0.5 + rng.uniform(-0.1, 0.1), 0.0
    pts = [(x, y)]
    while y < 0.85:
        y += rng.uniform(0.06, 0.12)
        x += rng.uniform(-0.09, 0.09)
        pts.append((x, y))
    v = 1.0 if int(t * 8) % 2 == 0 else 0.7
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        c.line(x0, y0, x1, y1, 0.012, v)
        c.line(x0, y0, x1, y1, 0.04, v * 0.45)
        if rng.random() < 0.3:
            c.line(x1, y1, x1 + rng.uniform(-0.15, 0.15), y1 + rng.uniform(0.04, 0.12), 0.008, v * 0.7)
    c.disc(pts[-1][0], 0.88, 0.12, 0.6 * v, 0.8)


KINDS = {"bolt": bolt, "burst": burst, "heal": heal, "ward": ward, "drain": drain, "lightning": lightning}


def render_spell(spec, pal: Palettes, size=64, ss=4):
    frames = spec.get("frames", 8)
    ramp = pal.ramps[spec["ramp"]]
    out = np.zeros((size, size * frames, 4), np.uint8)
    for f in range(frames):
        rng = random.Random(f * 7919 + hash_str(spec["id"]))
        c = Canvas(size, ss)
        KINDS[spec["kind"]](c, f / frames, rng)
        I = c.reduce()
        a = I > spec.get("cutoff", 0.14)
        idx = np.clip((I * len(ramp) * 1.12).astype(int), 1, len(ramp) - 1)
        cell = out[:, f * size:(f + 1) * size]
        cell[a, :3] = ramp[idx[a]]
        cell[a, 3] = 255
    return Image.fromarray(out)


def hash_str(s):
    return sum((i + 1) * ord(ch) for i, ch in enumerate(s)) & 0xFFFF


def render_all(spec_path, palettes_path, out_dir, size=64):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    pal = Palettes(palettes_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta, sheets = {"cell": [size, size], "spells": {}}, []
    for s in spec["spells"]:
        img = render_spell(s, pal, size)
        img.save(out / f"{s['id']}.png")
        meta["spells"][s["id"]] = {"frames": s.get("frames", 8), "fps": s.get("fps", 12), "kind": s["kind"],
                                   "loop": s["kind"] in ("heal", "ward", "drain")}
        sheets.append((s["id"], img, s.get("frames", 8)))
        print(f"[vfx] {s['id']}")
    (out / "spells.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return sheets


def preview_gif(sheets, path, size=64, scale=3, cols=4):
    n = max(f for _, _, f in sheets)
    rows = (len(sheets) + cols - 1) // cols
    frames = []
    for f in range(n):
        canvas = Image.new("RGB", (cols * size * scale, rows * size * scale), (30, 26, 36))
        for i, (_, img, fr) in enumerate(sheets):
            cell = img.crop(((f % fr) * size, 0, (f % fr + 1) * size, size)).resize((size * scale, size * scale), Image.NEAREST)
            canvas.paste(cell, ((i % cols) * size * scale, (i // cols) * size * scale), cell)
        frames.append(canvas)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=90, loop=0)
    return path
