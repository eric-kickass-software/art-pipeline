"""Image and mask operations shared by the character tools (Character Workshop, Item Workshop, Workshop Editor) and the
Dungeon Forge. Pure numpy / PIL, no tool state: masks are boolean (H, W) arrays, images RGB(A) uint8 arrays or PIL
images.

    foreground(img), clean(mask)        the figure of an AI image painted on white, without specks and pinholes
    components(mask, min_px)            keep only connected pieces of at least min_px pixels
    grow / erode                        square dilation / erosion by n px (PIL max / min filters)
    grow_cross / erode_cross            the same with 4-neighbour steps (np.roll: wraps at the edges)
    hsv(rgb)                            hue (degrees), saturation, value of an RGB array
    figure_mask(img), enforce_silhouette(img, mask)   a figure's silhouette; an image clamped to one
    save(im, path)                      PIL save that retries while Windows holds a fresh file
"""
import time
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def foreground(img: Image.Image | np.ndarray) -> np.ndarray:
    """Pixels that are not the white background (AI images are painted on white)."""
    a = np.asarray(img).astype(int)
    return np.abs(a - 255).sum(-1) > 45


def clean(mask: np.ndarray, min_area: int = 24) -> np.ndarray:
    """Drop small specks (connected pieces under min_area px, 4 neighbours) and fill 1-2 px pinholes."""
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
    grown = out.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        grown |= np.roll(out, (dy, dx), (0, 1))
    shrink = grown.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        shrink &= np.roll(grown, (dy, dx), (0, 1))
    return shrink | out


def components(mask: np.ndarray, min_px: int) -> np.ndarray:
    """The mask without its connected pieces (4 neighbours) smaller than min_px."""
    out = np.zeros_like(mask); seen = np.zeros_like(mask); H, W = mask.shape
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys, xs):
        if seen[y, x]:
            continue
        stack = [(y, x)]; seen[y, x] = True; comp = []
        while stack:
            cy, cx = stack.pop(); comp.append((cy, cx))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True; stack.append((ny, nx))
        if len(comp) >= min_px:
            cy_, cx_ = zip(*comp); out[list(cy_), list(cx_)] = True
    return out


def _filter(mask: np.ndarray, f) -> np.ndarray:
    return np.asarray(Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)).filter(f)) > 0


def grow(mask: np.ndarray, n: int) -> np.ndarray:
    """The mask dilated by n px (a (2n+1)-px square); n <= 0 returns a copy."""
    if n <= 0:
        return mask.copy()
    return _filter(mask, ImageFilter.MaxFilter(2 * n + 1))


def erode(mask: np.ndarray, n: int) -> np.ndarray:
    """The mask eroded by n px (a (2n+1)-px square); n <= 0 returns a copy (PIL's MinFilter(1) crashes)."""
    if n <= 0:
        return mask.copy()
    return _filter(mask, ImageFilter.MinFilter(2 * n + 1))


def grow_cross(mask: np.ndarray, n: int) -> np.ndarray:
    """The mask dilated n times by its 4 neighbours (wraps at the edges, like np.roll)."""
    for _ in range(n):
        mask = mask | np.roll(mask, 1, 0) | np.roll(mask, -1, 0) | np.roll(mask, 1, 1) | np.roll(mask, -1, 1)
    return mask


def erode_cross(mask: np.ndarray, n: int) -> np.ndarray:
    """The mask eroded n times by its 4 neighbours (wraps at the edges, like np.roll)."""
    a = mask.copy()
    for _ in range(n):
        a &= np.roll(a, 1, 0) & np.roll(a, -1, 0) & np.roll(a, 1, 1) & np.roll(a, -1, 1)
    return a


def hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(hue in degrees 0..360, saturation 0..1, value 0..1) per pixel of an RGB uint8 array."""
    a = rgb.astype(float) / 255; mx, mn = a.max(-1), a.min(-1); d = mx - mn + 1e-6
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    return h, np.where(mx > 0, d / (mx + 1e-6), 0), mx


def figure_mask(img: Image.Image) -> np.ndarray:
    """The figure's silhouette: foreground, plus the enclosed 'background' holes above the chin (eye whites and
    highlights are pure white, so the foreground test misses them; left as holes they come back as white glints).
    Holes below the chin (between arm and body, between the legs) are real background and stay."""
    fg = foreground(img)
    ys = np.nonzero(fg)[0]
    if not len(ys):
        return fg
    y0, H = ys.min(), ys.max() - ys.min()
    widths = fg.sum(1)
    lo, hi = int(y0 + 0.28 * H), int(y0 + 0.6 * H)
    chin = lo + int(np.argmin(widths[lo:hi]))  # the neck: narrowest row between the big head and the shoulders
    bg = ~fg
    reach = np.zeros_like(bg)  # background reachable from the image border
    reach[0], reach[-1], reach[:, 0], reach[:, -1] = bg[0], bg[-1], bg[:, 0], bg[:, -1]
    while True:
        nxt = grow_cross(reach, 1) & bg
        if (nxt == reach).all():
            break
        reach = nxt
    return fg | (bg & ~reach & (np.arange(fg.shape[0])[:, None] <= chin))


def enforce_silhouette(img: Image.Image, silhouette: np.ndarray) -> Image.Image:
    """Make `img` occupy exactly `silhouette` (bool mask): white outside; any background-white holes inside
    are filled from the nearest drawn pixel so the outline matches the front pixel for pixel."""
    a = np.asarray(img.convert("RGB")).copy()
    inside = silhouette
    drawn = foreground(Image.fromarray(a)) & inside
    hole = inside & ~drawn
    filled = a.copy()
    known = drawn.copy()
    for _ in range(400):  # grow known colours into holes
        if not (hole & ~known).any():
            break
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src = np.roll(known, (dy, dx), (0, 1))
            take = hole & ~known & src
            filled[take] = np.roll(filled, (dy, dx), (0, 1))[take]
            known |= take
    filled[~inside] = 255
    return Image.fromarray(filled)


def save(im: Image.Image, path: str | Path, **kw) -> None:
    """Save, retrying briefly: a viewer or virus scan can hold a fresh preview file for a moment (Errno 22 on Windows)."""
    for i in range(10):
        try:
            return im.save(path, **kw)
        except OSError:
            if i == 9:
                raise
            time.sleep(0.4)
