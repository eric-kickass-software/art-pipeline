"""Preview sheets and GIFs of composed characters, for reviewing the kit by eye."""
import random

import numpy as np
from PIL import Image, ImageDraw

BG = (38, 34, 44)


def _cell(sheet, view_cfg, row, frame):
    w, h = view_cfg["cell"]
    return sheet[row * h:(row + 1) * h, frame * w:(frame + 1) * w]


def _upscale(a, k):
    return np.repeat(np.repeat(a, k, axis=0), k, axis=1)


def _on_bg(cell, bg=BG):
    rgb = np.empty(cell.shape[:2] + (3,), np.uint8)
    rgb[:] = bg
    m = cell[..., 3] > 0
    rgb[m] = cell[m, :3]
    return rgb


def lineup(kit, apps, view, path, scale=3, labels=True):
    """One row per appearance: first frame of each clip/direction, upscaled."""
    vc = kit.manifest["views"][view]
    w, h = vc["cell"]
    rows = vc["rows"]
    composed = [kit.compose(a, view)[0] for a in apps]
    pad = 4
    W = len(rows) * (w * scale + pad) + pad
    H = len(apps) * (h * scale + pad) + pad + (14 if labels else 0)
    img = Image.new("RGB", (W, H), (24, 20, 28))
    d = ImageDraw.Draw(img)
    for i, sheet in enumerate(composed):
        for j, r in enumerate(rows):
            f = min(r["frames"] - 1, r["frames"] // 2) if r["clip"] in ("attack", "cast") else 0
            tile = _on_bg(_upscale(_cell(sheet, vc, r["row"], f), scale))
            x, y = pad + j * (w * scale + pad), pad + (14 if labels else 0) + i * (h * scale + pad)
            img.paste(Image.fromarray(tile), (x, y))
    if labels:
        for j, r in enumerate(rows):
            d.text((pad + j * (w * scale + pad) + 2, 2), f"{r['clip']} {r['dir']}", fill=(200, 200, 200))
    img.save(path)
    return path


def crowd(kit, apps, view, path, cols=8, scale=3):
    """A grid of characters in one pose (first frame of the first row)."""
    vc = kit.manifest["views"][view]
    w, h = vc["cell"]
    rows_n = (len(apps) + cols - 1) // cols
    img = Image.new("RGB", (cols * w * scale, rows_n * h * scale), BG)
    for i, a in enumerate(apps):
        sheet = kit.compose(a, view)[0]
        tile = _on_bg(_upscale(_cell(sheet, vc, 0, 0), scale))
        img.paste(Image.fromarray(tile), ((i % cols) * w * scale, (i // cols) * h * scale))
    img.save(path)
    return path


def gif(kit, apps, view, path, clips=None, scale=3, cols=6):
    """Animated GIF: each character plays every clip/direction row of the view in turn."""
    vc = kit.manifest["views"][view]
    w, h = vc["cell"]
    rows = [r for r in vc["rows"] if clips is None or r["clip"] in clips]
    sheets = [kit.compose(a, view)[0] for a in apps]
    rows_n = (len(apps) + cols - 1) // cols
    frames, durations = [], []
    for r in rows:
        reps = 2 if r["loop"] else 1
        for _ in range(reps):
            for f in range(r["frames"]):
                img = np.empty((rows_n * h * scale, cols * w * scale, 3), np.uint8)
                img[:] = BG
                for i, s in enumerate(sheets):
                    tile = _on_bg(_upscale(_cell(s, vc, r["row"], f), scale))
                    y, x = (i // cols) * h * scale, (i % cols) * w * scale
                    img[y:y + h * scale, x:x + w * scale] = tile
                frames.append(Image.fromarray(img))
                durations.append(int(1000 / r["fps"]))
        durations[-1] += 250
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2)
    return path


def random_apps(kit, n, seed=1):
    rng = random.Random(seed)
    return [kit.random_appearance(rng) for _ in range(n)]
