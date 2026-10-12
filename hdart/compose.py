"""Paper-doll compositor: stacks indexed part layers by per-pixel depth and colours them from
palette ramps. Mirrors what the Unity CharacterComposer does at hero load.

Appearance (what a Saved Hero stores):
    {"body": "slim",
     "parts":  {"face": "face.kind", "hair": "hair.short", "torso": "torso.tunic", ...},
     "colors": {"skin": "skin.tan", "hair": "hair.black", "cloth1": "cloth.red", ...}}
"""
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image

from .palette import Palettes

TIE_BIAS = 2
SHADE_LO, SHADE_HI = 0.36, 0.98  # shade range spread over the ramp  # depth units (of 255): a later slot wins near-ties, so clothes sit on top of skin


def shifted(a, dy, dx, cell):
    """np.roll that never pulls pixels across sprite-cell borders (cells are cell=(w, h))."""
    out = np.roll(a, (dy, dx), axis=(0, 1))
    w, h = cell
    if dy:
        rows = np.arange(a.shape[0]) % h
        out[rows == (0 if dy > 0 else h - 1)] = 0
    if dx:
        cols = np.arange(a.shape[1]) % w
        out[:, cols == (0 if dx > 0 else w - 1)] = 0
    return out


class Kit:
    def __init__(self, render_dir, spec_path, palettes_path):
        self.dir = Path(render_dir)
        self.manifest = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))
        self.spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        self.pal = Palettes(palettes_path)
        self.channels = {c["id"]: c for c in self.spec["channels"]}
        self.slot_order = [s["name"] for s in self.spec["slots"]]
        self._cache = {}

    def sheet(self, body, part, view, kind):
        key = (body, part, view, kind)
        if key not in self._cache:
            p = self.dir / body / f"{part}.{view}.{kind}.png"
            self._cache[key] = np.asarray(Image.open(p).convert("RGBA")) if p.exists() else None
        return self._cache[key]

    def parts_for(self, slot, body=None):
        return sorted(p for p, e in self.manifest["parts"].items()
                      if e["slot"] == slot and (body is None or body in e["bodies"]))

    def default_colors(self):
        return {c["name"]: c["default"] for c in self.spec["channels"] if c.get("default")}

    def random_appearance(self, rng: random.Random):
        body = rng.choice(self.manifest["bodies"])
        parts = {}
        for s in self.spec["slots"]:
            opts = self.parts_for(s["name"], body)
            if not opts:
                continue
            if s["required"] or rng.random() < {"facial": 0.3, "back": 0.35, "offhand": 0.5, "head": 0.35,
                                                 "hands": 0.5}.get(s["name"], 0.85):
                parts[s["name"]] = rng.choice(opts)
        colors = {}
        for c in self.spec["channels"]:
            if c["name"] == "none":
                continue
            opts = self.pal.options(c["name"])
            colors[c["name"]] = rng.choice(opts) if c.get("customizable") and opts else c["default"]
        return {"body": body, "parts": parts, "colors": colors}

    def resolve_parts(self, app):
        parts = dict(app["parts"])
        hidden = set()
        for p in parts.values():
            hidden |= set(self.manifest["parts"].get(p, {}).get("hides", []))
        return [parts[s] for s in self.slot_order if s in parts and s not in hidden]

    def compose(self, app, view, outline=True, inner_lines=True):
        """Returns (color RGBA sheet, normal RGBA sheet) for the whole view sheet."""
        return self.compose_full(app, view, outline, inner_lines)[:2]

    def compose_full(self, app, view, outline=True, inner_lines=True):
        """Like compose, plus the winning layer depth per pixel (0 = nearest; outline pixels take the
        depth of the shape they surround). Used by the voxel-sheet renderer for relief."""
        body = app["body"]
        best = None
        for part in self.resolve_parts(app):
            d = self.sheet(body, part, view, "data")
            n = self.sheet(body, part, view, "normal")
            if d is None:
                continue
            if best is None:
                best_d = np.zeros_like(d)
                best_n = np.zeros_like(n)
                depth = np.full(d.shape[:2], 1e9)
                best = True
            z = np.where(d[..., 3] > 0, d[..., 2].astype(np.float32), 1e9)
            win = z <= depth + TIE_BIAS
            depth = np.where(win, np.minimum(z, depth), depth)
            best_d[win] = d[win]
            best_n[win] = n[win]
        if best is None:
            raise ValueError("no layers for appearance")
        cell = self.manifest["views"][view]["cell"]
        color = self.colorize(best_d, app["colors"], outline, inner_lines, cell=cell)
        depth = np.where(best_d[..., 3] > 0, best_d[..., 2], 255).astype(np.int16)
        near = depth.copy()  # outline pixels: take the nearest neighbouring shape's depth
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            near = np.minimum(near, shifted(np.where(depth < 255, depth, 999), dy, dx, cell).astype(np.int16) | 0)
        depth = np.where((color[..., 3] > 0) & (depth == 255), np.minimum(near, 254), depth)
        return color, best_n, depth

    def colorize(self, data, colors, outline=True, inner_lines=True, cell=(64, 64)):
        size = self.pal.size
        alpha = data[..., 3] > 0
        shade = data[..., 0].astype(np.float32) / 255
        chan = data[..., 1]
        depth = data[..., 2].astype(np.int16)
        idx = np.clip(np.floor((shade - SHADE_LO) / (SHADE_HI - SHADE_LO) * size), 0, size - 1).astype(np.int16)
        if inner_lines:  # darken the far side of depth breaks (sleeve over torso, hair over face)
            far = np.zeros_like(alpha)
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nd = shifted(depth, dy, dx, cell)
                na = shifted(alpha, dy, dx, cell)
                far |= na & (depth - nd > 12)
            idx = np.where(far & alpha, np.maximum(idx - 2, 0), idx)
        out = np.zeros(data.shape[:2] + (4,), np.uint8)
        for cid, c in self.channels.items():
            if cid == 0:
                continue
            m = alpha & (chan == cid)
            if not m.any():
                continue
            ramp_id = colors.get(c["name"]) or c.get("default")
            ramp = self.pal.ramps.get(ramp_id)
            if ramp is None:
                ramp = self.pal.ramps[c["default"]]
            out[m, :3] = ramp[idx[m]]
            out[m, 3] = 255
        if outline:
            edge = np.zeros_like(alpha)
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                edge |= shifted(alpha, dy, dx, cell)
            edge &= ~alpha
            out[edge, :3] = self.pal.outline
            out[edge, 3] = 255
        return out
