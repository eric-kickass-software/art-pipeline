"""AI sprite kit (v3): FLUX.2-klein draws the parts, the kit keeps them modular.

1. A base mannequin per build and facing is generated once (txt2img + pixel LoRA): a bald chibi in plain
   grey undershirt and shorts, standing in the kit pose.
2. Every part is an *edit* of that base ("give the character long silver hair. Change nothing else.").
   Edits keep the rest of the image nearly identical, so the changed pixels are the part.
3. The part is cleaned (diff threshold, slot area, speck removal), shrunk 8x to the 64 px cell with the
   base's alignment (feet on row 61), and its colours are mapped to recolourable channels + 5 shade
   steps, then written as a normal kit template (sprites/parts/<body>/<part>/...). From there the bake,
   Sprite Studio, compositor and Unity work unchanged.

    uv run hdart ai-base [--bodies slim] [--seed 21]
    uv run hdart ai-part torso.red_tunic --prompt "a red tunic with gold trim and a leather belt"
    uv run hdart ai-kit                      # every part in specs/ai_parts.json
"""
import colorsys
import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import comfy

ROOT = Path(__file__).resolve().parent.parent
AI_DIR = ROOT / "sprites" / "ai"
TEMPLATES = ROOT / "sprites" / "parts"
LAYERS = AI_DIR / "layers"   # extracted parts at full 512 px, the source for every resolution
BIG = 512
RESOLUTIONS = (32, 64, 128, 256)


def ground_row(res):
    return res - max(1, round(3 * res / 64))

STYLE = ("pixel art sprite, chibi JRPG character in the style of Octopath Traveler, full body, standing straight, "
         "arms relaxed at the sides, big head, small body, short stubby legs, game asset, centered, plain white background")
BUILDS = {
    "slim": "slender build",
    "broad": "broad-shouldered stocky build",
    "short": "short and round, dwarf-like build",
}
FACINGS = {
    "front": "three-quarter view facing left, looking left",
    "back": "seen from behind, three-quarter back view facing up and to the left, face not visible",
}
BASE_LOOK = "bald head, plain grey sleeveless undershirt and grey shorts, barefoot, neutral face"

# slot -> layer name (z) used for the template, and which channels its colours may map to
SLOT_LAYER = {"body": "body", "face": "face", "hair": "hair", "facial": "facial", "head": "headgear",
              "torso": "torso", "legs": "legwear", "feet": "footwear", "hands": "handwear", "back": "back_front",
              "mainhand": "mainhand", "offhand": "offhand"}
# rough area (in 512 px base coordinates, fractions of the sprite bbox) each slot may change
SLOT_AREA = {"hair": (0.0, 0.85), "face": (0.15, 0.5), "facial": (0.25, 0.6), "head": (0.0, 0.5),
             "torso": (0.35, 1.0), "legs": (0.7, 1.0), "feet": (0.8, 1.0), "hands": (0.4, 0.9), "back": (0.0, 1.0),
             "mainhand": (0.0, 1.0), "offhand": (0.0, 1.0)}


# ----------------------------------------------------------------------------- generation

def base_prompt(body, facing):
    return f"{STYLE}, {BUILDS[body]}, {FACINGS[facing]}, {BASE_LOOK}"


def base_path(body, facing):
    return AI_DIR / "base" / f"{body}.{facing}.png"


def make_base(body, facing, seed=21):
    p = comfy.run(comfy.build_txt2img(base_prompt(body, facing), BIG, BIG, seed=seed, lora_strength=1.0,
                                      prefix=f"hdart/aibase_{body}_{facing}"), AI_DIR / "raw")[0]
    dst = base_path(body, facing)
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.open(p).convert("RGB").save(dst)
    meta = alignment(dst)
    (dst.with_suffix(".json")).write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return dst


def edit_part(body, facing, prompt, seed=5, lora=0.6):
    base = base_path(body, facing)
    name = comfy.upload(base)
    full = (f"{prompt}. Keep exactly the same character, pose, framing, size and pixel art style. Change nothing else."
            if facing == "front" else
            f"{prompt}, seen from behind. Keep exactly the same character, pose, framing, size and pixel art style. "
            f"Change nothing else.")
    p = comfy.run(comfy.build_edit(name, full, seed=seed, lora_strength=lora, prefix="hdart/aipart",
                                   megapixels=BIG * BIG / 1e6), AI_DIR / "raw")[0]
    return Image.open(p).convert("RGB").resize((BIG, BIG), Image.NEAREST)


# ----------------------------------------------------------------------------- extraction

def foreground(img):
    a = np.asarray(img).astype(int)
    return np.abs(a - 255).sum(-1) > 45  # not the white background


def alignment(base_file):
    fg = foreground(Image.open(base_file).convert("RGB"))
    ys, xs = np.nonzero(fg)
    return {"bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]}


def placement(meta, res):
    """Downscale factor and offset that put the base's feet on the ground row, centred, in a res cell."""
    x0, y0, x1, y1 = meta["bbox"]
    k = BIG // res
    return k, int(round((res - 1) / 2 - (x0 + x1) / 2 / k)), int(round(ground_row(res) - y1 / k))


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


class WholeCharacterEdit(ValueError):
    """The prompt changed most of the character outside its slot (e.g. 'a strong woman'): not a part."""


def _largest(mask):
    from collections import deque
    h, w = mask.shape
    seen = np.zeros_like(mask)
    best = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        comp, q = [], deque([(y, x)])
        seen[y, x] = True
        while q:
            cy, cx = q.popleft()
            comp.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(comp) > len(best):
            best = comp
    out = np.zeros_like(mask)
    if best:
        ys, xs = zip(*best)
        out[list(ys), list(xs)] = True
    return out


_ANATOMY = {}


def anatomy(base_img):
    """Landmarks of the base mannequin (512 px): face core (skin + eyes from the eye line down), chin,
    and the sprite's vertical extent. Used to keep each slot to its own region."""
    key = id(base_img)
    if key in _ANATOMY:
        return _ANATOMY[key]
    a = np.asarray(base_img).astype(np.float32) / 255
    fg = foreground(base_img)
    ys, xs = np.nonzero(fg)
    y0, y1 = ys.min(), ys.max()
    H = y1 - y0
    mx, mn = a.max(-1), a.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    skin = fg & (a[..., 0] > a[..., 2] + 0.08) & (sat > 0.12) & (sat < 0.7) & (mx > 0.45)
    rows = np.arange(a.shape[0])[:, None]
    cols = np.arange(a.shape[1])[None, :]
    # neck = the narrowest silhouette row between the big head and the shoulders
    widths = fg.sum(1)
    lo, hi = int(y0 + 0.28 * H), int(y0 + 0.6 * H)
    chin = lo + int(np.argmin(widths[lo:hi]))
    head = _largest(skin & (rows < chin))
    hy, hx = np.nonzero(head)
    top = hy.min()
    # head interior: the head silhouette per row, shrunk so the outline itself never counts as an eye
    inner = np.zeros_like(fg)
    for y in range(top, chin):
        xr = np.nonzero(fg[y] & (np.arange(a.shape[1]) >= hx.min() - 4) & (np.arange(a.shape[1]) <= hx.max() + 4))[0]
        if len(xr) > 24:
            inner[y, xr.min() + 10:xr.max() - 10] = True
    dark = fg & (mx < 0.35) & inner & (rows > top + 0.35 * (chin - top))
    eyes = _largest(dark) | _largest(dark & ~_largest(dark))  # the two eyes
    ey, ex = np.nonzero(eyes)
    eye_top = ey.min() if len(ey) else top + 0.5 * (chin - top)
    eye_bot = ey.max() if len(ey) else top + 0.65 * (chin - top)
    x_far = (ex.max() + 14) if len(ex) else hx.max()
    face = np.zeros_like(fg)
    for y in range(int(eye_top) - 8, int(chin) + 1):
        xr = np.nonzero((head | eyes)[y])[0]
        if len(xr):
            face[y, xr.min():min(xr.max(), x_far) + 1] = True
    face &= fg
    res = {"fg": fg, "y0": y0, "y1": y1, "H": H, "face": face, "eye_top": eye_top, "eye_bot": eye_bot,
           "chin": chin, "head_x": (hx.min(), hx.max())}
    _ANATOMY[key] = res
    return res


def slot_mask(slot, an, shape):
    """Where a part of this slot may change pixels."""
    rows = np.arange(shape[0])[:, None]
    cols = np.arange(shape[1])[None, :]
    face, H, y0 = an["face"], an["H"], an["y0"]
    allm = np.ones(shape, bool)
    if slot == "face":
        m = face.copy()
        for dy, dx in ((0, 3), (0, -3), (3, 0), (-3, 0)):
            m |= np.roll(face, (dy, dx), (0, 1))
        return m
    if slot == "facial":
        x0, x1 = an["head_x"]
        return (rows > an["eye_bot"] + 4) & (rows < an["chin"] + 0.14 * H) & (cols > x0 - 8) & (cols < x1 + 8)
    keep_face = ~face
    if slot in ("hair", "head", "back", "mainhand", "offhand"):
        return keep_face & allm
    if slot in ("torso", "hands"):
        return keep_face & (rows >= an["chin"] - 8)
    if slot == "legs":
        return rows >= y0 + 0.7 * H
    if slot == "feet":
        return rows >= y0 + 0.82 * H
    return keep_face & allm


def extract(base_img, edited, slot, thresh=70, guard=False, speck=24):
    """Changed pixels within the slot's area -> RGBA layer at 512."""
    b = np.asarray(base_img).astype(np.int16)
    e = np.asarray(edited).astype(np.int16)
    # tolerant diff: a pixel only counts as changed if no similar colour sits within ~1 native pixel in the
    # base (edits redraw outlines a pixel off, which a plain diff would pick up as thin noise)
    r = BIG // 64
    best = np.full(b.shape[:2], 10 ** 6, np.int32)
    for dy in range(-r, r + 1, max(1, r // 2)):
        for dx in range(-r, r + 1, max(1, r // 2)):
            sh = np.roll(b, (dy, dx), (0, 1))
            best = np.minimum(best, np.abs(e - sh).sum(-1))
    diff = (best > thresh) & foreground(edited)
    allowed = slot_mask(slot, anatomy(base_img), diff.shape)
    if guard:
        outside = (diff & ~allowed).sum()
        if diff.sum() > 4000 and outside > 0.45 * diff.sum():
            raise WholeCharacterEdit(
                f"This prompt changed most of the character outside the '{slot}' slot "
                f"({outside * 100 // diff.sum()}% of the changes), so it isn't a part. Describe one thing to add, "
                f"or use 'New build' / the style field for whole-character looks.")
    mask = clean(diff & allowed, min_area=speck)
    out = np.zeros((BIG, BIG, 4), np.uint8)
    out[..., :3] = e
    out[..., 3] = mask * 255
    return out


def to_cell(rgba512, meta, res=64):
    """512 RGBA -> res x res cell with the base alignment (premultiplied area average, hard alpha)."""
    a = rgba512.astype(np.float32) / 255
    a[..., :3] *= a[..., 3:4]
    k, dx, dy = placement(meta, res)
    N = res
    s = a.reshape(N, k, N, k, 4).mean(axis=(1, 3))
    alpha = s[..., 3]
    rgb = np.where(alpha[..., None] > 0, s[..., :3] / np.maximum(alpha[..., None], 1e-6), 0)
    cell = np.zeros((N, N, 4), np.float32)
    cell[..., :3] = rgb
    cell[..., 3] = despeckle(alpha >= 0.45, min_px=max(2, res // 32))
    cell = np.roll(cell, (dy, dx), (0, 1))
    return cell


def despeckle(m, min_px=2):
    """Native-resolution cleanup: drop tiny islands, fill single-pixel holes."""
    filled = m.copy()
    nb = sum(np.roll(m, s, a).astype(int) for s, a in ((1, 0), (-1, 0), (1, 1), (-1, 1)))
    filled |= ~m & (nb >= 3)
    return clean(filled, min_area=min_px)


# ----------------------------------------------------------------------------- colours -> channels

def classify(rgb, slot):
    """Per-pixel channel name for a part's colours (heuristic, by hue/saturation/value)."""
    h, s, v = np.vectorize(colorsys.rgb_to_hsv)(rgb[..., 0], rgb[..., 1], rgb[..., 2])
    hue = h * 360
    ch = np.full(rgb.shape[:2], "cloth1", object)
    dark = v < 0.2
    if slot == "hair" or slot == "facial":
        ch[:] = "hair"
    elif slot == "body":
        skin = (hue < 50) & (s > 0.12) & (s < 0.75) & (v > 0.4)
        ch[:] = "cloth2"
        ch[skin] = "skin"
    elif slot == "face":
        ch[:] = "skin"
        ch[(v < 0.45) | (s > 0.5) & ((hue > 180) | (hue < 20))] = "eyes"
        ch[(s < 0.12) & (v > 0.85)] = "white"
    else:
        grey = s < 0.16
        ch[grey & (v > 0.38)] = "metal"
        ch[(hue >= 15) & (hue < 45) & (s >= 0.25) & (v < 0.55)] = "leather"
        ch[(hue >= 38) & (hue < 62) & (s > 0.45) & (v > 0.6)] = "accent"
        if slot in ("mainhand", "offhand"):
            ch[(hue >= 15) & (hue < 45) & (s >= 0.25) & (v >= 0.35) & (v < 0.75)] = "wood"
            ch[(s > 0.45) & ((hue > 170) & (hue < 320))] = "magic"
        rest = ch == "cloth1"
        if rest.any():  # split the remaining colours into main cloth and second cloth by hue
            hues = hue[rest]
            main = np.median(hues)
            dist = np.minimum(np.abs(hue - main), 360 - np.abs(hue - main))
            ch[rest & (dist > 45)] = "cloth2"
    ch[dark] = "dark"
    return ch


def to_template(cell, slot):
    """64 cell RGBA float -> {channel, shade idx} arrays."""
    alpha = cell[..., 3] > 0.5
    ch = classify(np.clip(cell[..., :3], 0, 1), slot)
    lum = (cell[..., :3] @ np.array([0.3, 0.59, 0.11], np.float32))
    idx = np.zeros(alpha.shape, np.int16)
    for name in set(ch[alpha]):
        m = alpha & (ch == name)
        vals = lum[m]
        if name == "dark":
            idx[m] = 1
            continue
        lo, hi = np.percentile(vals, 5), np.percentile(vals, 95)
        idx[m] = np.clip(np.round((vals - lo) / max(hi - lo, 1e-3) * 3.6 + 0.2), 0, 4)
    return alpha, ch, idx


def write_template(body, part, facing, alpha, ch, idx, layer, res=64):
    from .sprite_kit import CH, index_palette, SPEC_PATH
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    for c in spec["channels"]:
        CH[c["name"]] = c["id"]
    pal = index_palette()
    img = np.zeros(alpha.shape + (4,), np.uint8)
    for name in set(ch[alpha]):
        for i in range(5):
            m = alpha & (ch == name) & (idx == i)
            if m.any():
                img[m, :3] = pal[(CH[name], i)]
    img[alpha, 3] = 255
    d = TEMPLATES / f"r{res}" / body / part
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"{facing}.*.png"):
        old.unlink()
    Image.fromarray(img).save(d / f"{facing}.down-down.{layer}.png")
    return d


def save_layer(body, part, facing, layer, rgba512):
    d = LAYERS / body / part
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"{facing}.*.png"):
        old.unlink()
    Image.fromarray(rgba512).save(d / f"{facing}.{layer}.png")


def templates_from_layers(body, part, resolutions=RESOLUTIONS):
    """(Re)build the index-palette templates of one AI part for every resolution."""
    slot = part.split(".")[0]
    for f in sorted((LAYERS / body / part).glob("*.png")):
        facing, layer, _ = f.name.split(".")
        meta = json.loads(base_path(body, facing).with_suffix(".json").read_text(encoding="utf-8"))
        rgba = np.asarray(Image.open(f).convert("RGBA"))
        for res in resolutions:
            alpha, ch, idx = to_template(to_cell(rgba, meta, res), slot)
            write_template(body, part, facing, alpha, ch, idx, layer, res)


def all_templates(resolutions=RESOLUTIONS):
    for bdir in LAYERS.iterdir() if LAYERS.exists() else []:
        for pdir in bdir.iterdir():
            templates_from_layers(bdir.name, pdir.name, resolutions)


def build_base_part(body, facing, resolutions=RESOLUTIONS):
    """body.base from the mannequin itself."""
    base = Image.open(base_path(body, facing)).convert("RGB")
    rgba = np.zeros((BIG, BIG, 4), np.uint8)
    rgba[..., :3] = np.asarray(base)
    rgba[..., 3] = foreground(base) * 255
    save_layer(body, "body.base", facing, "body", rgba)
    templates_from_layers(body, "body.base", resolutions)
    return LAYERS / body / "body.base"


def reextract(body="slim", parts=None, resolutions=RESOLUTIONS):
    """Re-run extraction from the saved raw edits (no generation), e.g. after tuning extract()."""
    for raw in sorted((AI_DIR / "raw").glob(f"*.{body}.*.png")):
        part, _, facing = raw.stem.rsplit(".", 2)
        if parts and part not in parts:
            continue
        slot = part.split(".")[0]
        base = Image.open(base_path(body, facing)).convert("RGB")
        edited = Image.open(raw).convert("RGB")
        save_layer(body, part, facing, SLOT_LAYER.get(slot, "torso"), extract(base, edited, slot))
    for pdir in (LAYERS / body).iterdir():
        if pdir.name != "body.base" and (not parts or pdir.name in parts):
            templates_from_layers(body, pdir.name, resolutions)


def make_part(part, prompt, body="slim", facings=("front", "back"), seed=5, lora=0.6, resolutions=RESOLUTIONS):
    slot = part.split(".")[0]
    for facing in facings:
        base = Image.open(base_path(body, facing)).convert("RGB")
        edited = edit_part(body, facing, prompt, seed, lora)
        (AI_DIR / "raw").mkdir(parents=True, exist_ok=True)
        edited.save(AI_DIR / "raw" / f"{part}.{body}.{facing}.png")
        save_layer(body, part, facing, SLOT_LAYER.get(slot, "torso"), extract(base, edited, slot, guard=True))
    templates_from_layers(body, part, resolutions)
    return LAYERS / body / part
