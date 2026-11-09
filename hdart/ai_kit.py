"""Shared paths and mask helpers for the AI character tools (Character Workshop, Item Workshop, Workshop Editor).

The name is historical: this was the AI sprite kit (v3), retired 2026-10-25 (its code is in git history before A10).
What the tools still share from it:
    ROOT     the tool's folder (specs/ live here)
    AI_DIR   the data folder of the character tools: <data>/sprites/ai (workshop.json, paint/, face/, items/, ...)
    BIG      the size (px) FLUX paints a 512-px mannequin image at
    foreground(img), clean(mask)   masks of AI images painted on white
"""
from pathlib import Path

import numpy as np

from . import config

ROOT = Path(__file__).resolve().parent.parent
AI_DIR = config.data_dir() / "sprites" / "ai"
BIG = 512


def foreground(img):
    """Pixels that are not the white background (AI images are painted on white)."""
    a = np.asarray(img).astype(int)
    return np.abs(a - 255).sum(-1) > 45  # not the white background


def clean(mask, min_area=24):
    """Drop small specks (connected components) and fill pinholes."""
    from collections import deque
    h, w = mask.shape
    seen = np.zeros_like(mask)
    out = np.zeros_like(mask)
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not seen[y, x]:
                comp, q = [], deque([(y, x)])
                seen[y, x] = True
                while q:
                    cy, cx = q.popleft()
                    comp.append((cy, cx))
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
                if len(comp) >= min_area:
                    ys, xs = zip(*comp)
                    out[list(ys), list(xs)] = True
    # close 1-2 px gaps
    grow = out.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        grow |= np.roll(out, (dy, dx), (0, 1))
    shrink = grow.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        shrink &= np.roll(grow, (dy, dx), (0, 1))
    return shrink | out
