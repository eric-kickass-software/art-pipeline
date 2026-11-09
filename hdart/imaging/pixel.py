"""Turning high-res AI images into game-ready pixel art, and texture helpers."""
import numpy as np
from PIL import Image, ImageFilter


def alpha_bbox(img, threshold=16):
    a = np.asarray(img.getchannel("A")) > threshold
    ys, xs = np.nonzero(a)
    if len(xs) == 0:
        return (0, 0, img.width, img.height)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def pixelize(img, size=32, colors=16, outline=(27, 20, 33), margin=1, alpha_cut=0.5):
    """Crop to content, fit into size x size (keeping aspect), downscale with area averaging,
    hard alpha, reduce to `colors` colours, then add a 1px outline."""
    img = img.convert("RGBA")
    x0, y0, x1, y1 = alpha_bbox(img)
    img = img.crop((x0, y0, x1, y1))
    inner = size - 2 * margin
    s = inner / max(img.width, img.height)
    w, h = max(1, round(img.width * s)), max(1, round(img.height * s))
    # premultiplied area downscale so transparent pixels don't bleed colour
    a = np.asarray(img).astype(np.float32) / 255
    a[..., :3] *= a[..., 3:4]
    small = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((w, h), Image.BOX)).astype(np.float32) / 255
    al = small[..., 3]
    rgb = np.where(al[..., None] > 0, small[..., :3] / np.maximum(al[..., None], 1e-6), 0)
    mask = al >= alpha_cut
    canvas = np.zeros((size, size, 4), np.uint8)
    ox, oy = (size - w) // 2, (size - h) // 2
    canvas[oy:oy + h, ox:ox + w, :3] = np.clip(rgb * 255, 0, 255)
    canvas[oy:oy + h, ox:ox + w, 3] = mask * 255
    out = quantize(Image.fromarray(canvas), colors)
    if outline is not None:
        out = add_outline(out, outline)
    return out


def quantize(img, colors=16, palette=None):
    """Reduce opaque pixels to a small palette (median cut, or a fixed palette image/array)."""
    img = img.convert("RGBA")
    arr = np.asarray(img).copy()
    mask = arr[..., 3] > 0
    if not mask.any():
        return img
    rgb = Image.fromarray(arr[..., :3])
    if palette is None:
        q = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    else:
        pal_img = Image.new("P", (1, 1))
        flat = np.asarray(palette, np.uint8).reshape(-1, 3)
        pal = list(flat.ravel()) + [0] * (768 - flat.size)
        pal_img.putpalette(pal)
        q = rgb.quantize(palette=pal_img, dither=Image.Dither.NONE)
    arr[..., :3] = np.asarray(q.convert("RGB"))
    arr[..., 3] = mask * 255
    return Image.fromarray(arr)


def add_outline(img, color=(27, 20, 33)):
    arr = np.asarray(img.convert("RGBA")).copy()
    a = arr[..., 3] > 0
    edge = np.zeros_like(a)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        edge |= np.roll(a, (dy, dx), axis=(0, 1))
    edge &= ~a
    arr[edge, :3] = color
    arr[edge, 3] = 255
    return Image.fromarray(arr)


def make_tileable(img, blend=0.25):
    """Seamless wrap: offset by half and cross-fade each seam with the unshifted image using a
    smooth mask, so opposite edges match. Fine for stone, dirt, cloth, leather."""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    h, w = a.shape[:2]
    rolled = np.roll(a, (h // 2, w // 2), axis=(0, 1))
    yy = np.abs(np.linspace(-1, 1, h))[:, None]
    xx = np.abs(np.linspace(-1, 1, w))[None, :]
    # weight of the original: 1 in the centre, 0 at the borders (where the rolled copy is seamless)
    wy = np.clip((1 - yy) / blend, 0, 1)
    wx = np.clip((1 - xx) / blend, 0, 1)
    wgt = (wy * wx)[..., None]
    wgt = wgt * wgt * (3 - 2 * wgt)
    # the rolled copy wraps cleanly at the borders (its seam is in the centre, where the original wins)
    out = a * wgt + rolled * (1 - wgt)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def height_to_normal(img, strength=2.0):
    """Tangent-space normal map from luminance (tileable: wraps at the edges)."""
    g = np.asarray(img.convert("L").filter(ImageFilter.GaussianBlur(1))).astype(np.float32) / 255
    dx = (np.roll(g, -1, axis=1) - np.roll(g, 1, axis=1)) * strength
    dy = (np.roll(g, -1, axis=0) - np.roll(g, 1, axis=0)) * strength
    n = np.stack([-dx, dy, np.ones_like(g)], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return Image.fromarray(((n * 0.5 + 0.5) * 255).astype(np.uint8))

