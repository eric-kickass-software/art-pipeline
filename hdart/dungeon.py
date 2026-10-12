"""Dungeon set generation: isometric tiles and props from prompts, then downsampled to the board grid.

Tiles  - template-guided: a flat-shaded geometry template (exact 2:1 iso diamond, wall block of N tiles,
         pillar, door frame, ...) is drawn at 8x the native size; FLUX paints over it with img2img and the
         result is clamped to the template outline, so every tile lines up and tiles seamlessly.
Props  - clean prompt -> single object on white -> cut out -> fitted to a footprint box -> downsampled.
Native tile size: `tile` px wide (64 -> 64x32 diamond); heights are in tile units (1 unit = tile/2 px,
matching Prototype V's placeholders).
"""
import json
import math
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from . import ai_kit, comfy, iso
from .locks import CELLS as _CELL_CACHE, DUNGEON as LOCK, HARMONY as _REF_CACHE
from .paths import REPO

ROOT = Path(__file__).resolve().parent.parent
DUN = ROOT / "sprites" / "dungeon"
ARCHIVE = ROOT / "archive"  # git-ignored: archived sets, candidates and spec history, for removal by hand later
STYLE = ROOT / "specs" / "dungeon_style.json"
SCALE = 8  # generation is done at 8x the native pixel size
ORDER = ("medium", "style", "view", "rendering", "framing", "biome")


def style():
    return json.loads(STYLE.read_text(encoding="utf-8"))


def style_text(st=None, groups=ORDER):
    st = st or style()
    return ", ".join(st["groups"][g]["text"] for g in groups if st["groups"][g]["text"].strip())


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


THIN = ("run", "post", "mesh")  # thin-wall pieces (hdart/iso.py geometry)
MESHES = DUN / "_meshes"


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
        return [(0, a, y0, y1, 0, h, 0), (b, 1, y0, y1, 0, h, 0), (a, b, y0, y1, dh, h, 0),
                (a, b, 0.5 - lt / 2, 0.5 + lt / 2, 0, dh, 1)]  # jambs, lintel, recessed door leaf (owner 1)
    return [(k + hh / 2, k + (hh + 1) / 2, y0, y1, 0, h, 2 * k + hh) for k in range(length) for hh in (0, 1)]


def mesh_pass(piece, tile, h, length):
    """Blender normal pass of a mesh piece (cached) -> (piece-space normals, mask)."""
    name = f"{piece['mesh']}_t{tile}_h{h:g}_l{length}"
    png = MESHES / f"{name}.normal.png"
    if not png.exists():
        import subprocess
        from .cli import BLENDER_DEFAULT
        extra = [a for k in ("opening", "spring") if k in piece for a in (f"--{k}", str(piece[k]))]
        subprocess.run([BLENDER_DEFAULT, "-b", "-P", str(ROOT / "blender" / "mesh_template.py"), "--",
                        "--mesh", piece["mesh"], "--tile", str(tile), "--height", f"{h:g}", "--length", str(length),
                        "--scale", str(SCALE), "--thickness", str(piece.get("thickness", 0.25)), "--out", str(MESHES),
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


# ----------------------------------------------------------------------------- generation

def _i2i(init, prompt, seed, denoise, lora):
    DUN.mkdir(parents=True, exist_ok=True)
    tmp = DUN / "_tmp_init.png"
    init.save(tmp)
    nodes = comfy.build_img2img(comfy.upload(tmp), prompt, denoise=denoise, seed=seed, lora_strength=lora,
                                prefix="hdart/dun_tile")
    p = comfy.run(nodes, DUN / "tmp")[0]
    return Image.open(p).convert("RGB").resize(init.size, Image.NEAREST)


def gen_tile(piece, tile=64, seed=1, denoise=0.72, lora=0.0, st=None):
    """Template img2img -> clamped to the template silhouette. Returns the 8x image (RGB, white bg)."""
    st = st or style()
    g = geometry(piece, tile)
    tpl, mask = g["img"], g["mask"]
    # give the template a little noise + the piece's base colour so img2img has something to grow from
    base = np.array(piece.get("tint", (128, 126, 134)), np.float32)
    a = np.asarray(tpl).astype(np.float32)
    a[mask] = a[mask] * 0.6 + base * 0.4 + np.random.default_rng(seed).normal(0, 10, (mask.sum(), 3))
    init = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5))
    prompt = f"{style_text(st)}, {piece['prompt']}"
    out = _i2i(init, prompt, seed, denoise, lora)
    from .workshop import enforce_silhouette  # fill anything left white inside the outline, clear outside
    return enforce_silhouette(out, mask), mask


def gen_floor(piece, tile=64, seed=1, lora=0.0, st=None):
    """Floor-type tiles: a top-down square tile from FLUX, warped exactly onto the 2:1 iso diamond (always
    fills edge to edge, and neighbouring tiles meet cleanly). A 1 px darker rim gives the slab some depth."""
    st = st or style()
    prompt = (f"{style_text(st, ('medium', 'style', 'rendering', 'biome'))}, top-down orthographic view of a single "
              f"square dungeon floor tile, {piece['prompt']}, the tile fills the entire image edge to edge, "
              f"flat even lighting")
    p = comfy.run(comfy.build_txt2img(prompt, 512, 512, seed=seed, lora_strength=lora, prefix="hdart/dun_floor"),
                  DUN / "tmp")[0]
    src = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
    S = src.shape[0]
    _, mask, _ = template({"shape": "floor"}, tile)
    H, W = mask.shape
    top = H - W / 2
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32) + 0.5
    a = ((xx - W / 2) / (W / 2) + (yy - top) / (W / 4)) / 2
    b = ((yy - top) / (W / 4) - (xx - W / 2) / (W / 2)) / 2
    inset = 0.035  # FLUX likes to draw a thin frame round the tile: sample just inside it
    sx = np.clip(((inset + a * (1 - 2 * inset)) * S).astype(int), 0, S - 1)
    sy = np.clip(((inset + b * (1 - 2 * inset)) * S).astype(int), 0, S - 1)
    out = np.full((H, W, 3), 255, np.float32)
    out[mask] = src[sy[mask], sx[mask]]
    rim = mask & ((a > 0.97) | (b > 0.97))  # the two front edges
    out[rim] *= 0.55
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), mask


def gen_atlas(piece, seed=1, lora=0.0, st=None, size=1024):
    """Large floor area: one top-down texture covering atlas x atlas tiles, made seamless so the block
    repeats without a visible edge. Returns the square RGB source (cut into iso tiles by `cells`)."""
    st = st or style()
    n = piece.get("atlas", 4)
    prompt = (f"{style_text(st, ('medium', 'style', 'rendering', 'biome'))}, seamless tileable floor texture seen "
              f"straight from above, {piece['prompt']}, the pattern continues past every edge of the image, no border, "
              f"no frame, no walls, flat even lighting")
    big = int(size * 1.125) // 16 * 16
    p = comfy.run(comfy.build_txt2img(prompt, big, big, seed=seed, lora_strength=lora, prefix="hdart/dun_atlas"),
                  DUN / "tmp")[0]
    src = Image.open(p).convert("RGB")
    m = (src.width - size) // 2  # FLUX still likes a darker rim at the image edge: drop it
    src = src.crop((m, m, m + size, m + size))
    return make_tileable(src, prompt, seed, lora)


def make_tileable(src, prompt, seed, lora, band=0.16, denoise=0.7):
    """Roll the texture by half, repaint the seam cross with img2img, blend only a feathered band, roll back."""
    a = np.asarray(src).astype(np.float32)
    S = a.shape[0]
    rolled = np.roll(a, (S // 2, S // 2), (0, 1))
    fixed = np.asarray(_i2i(Image.fromarray(rolled.astype(np.uint8)), prompt, seed + 1000, denoise, lora)
                       .convert("RGB")).astype(np.float32)
    yy, xx = np.mgrid[0:S, 0:S] + 0.5
    dist = np.minimum(np.abs(xx - S / 2), np.abs(yy - S / 2))
    half = band * S / 2
    w = np.clip(1 - (dist - half * 0.5) / half, 0, 1)[..., None]
    out = rolled * (1 - w) + fixed * w
    return Image.fromarray(np.clip(np.roll(out, (-(S // 2), -(S // 2)), (0, 1)), 0, 255).astype(np.uint8))


# ----------------------------------------------------------------------------- master tile + repainted variants
# A floor family made from one seamless master tile, so every piece shares its edges and any piece can sit next to
# any other (FloorBlocks mixes their sizes on the board):
#   method "master"   one seamless top-down tile: generated, lighting flattened, edges made periodic, then the seam
#                     cross repainted with a soft mask. Ships as a 1x1 floor tile.
#   method "repaint"  `from` = the master piece, `atlas` = N (1 = a 1x1 tile, 2-4 = an NxN block): the approved master
#                     tiled NxN, everything but an outer band (`border`, in tiles) repainted with the piece prompt.
#                     Its outer edges are the master's, so it fits next to the master and every other variant.
# Both are stored as top-down squares (like atlases) and warped onto the iso diamond when cut.

MASTER_PX = 512    # source px per tile (= the full-resolution output for a 64 grid)
BLOCK_MAX = 1536   # largest repaint canvas FLUX gets (a 4x4 block runs at 384 px per tile)
SQUARE_METHODS = ("atlas", "master", "repaint")
REPAINT_DENOISE = 0.85  # default repaint strength for variants (piece `denoise` / the editor's denoise field)


def atlas_n(piece):
    """Tiles per side of a floor piece's square: atlas N, repaint N (default 1), else 1."""
    m = piece.get("method")
    return int(piece.get("atlas", 4)) if m == "atlas" else int(piece.get("atlas", 1)) if m in ("repaint", "wall_repaint") else 1


def floor_texture_prompt(st, text):
    return (f"{style_text(st, ('medium', 'style', 'rendering', 'biome'))}, seamless tileable floor texture seen "
            f"straight from above, {text}, the pattern continues past every edge of the image, no border, "
            f"no frame, no walls, flat even lighting")


def repaint(img, mask, prompt, seed, denoise=1.0, lora=0.0, reference=True):
    """Masked FLUX repaint (comfy.build_repaint). `mask` is float 0..1 (0 = keep, 1 = repaint fully). The result
    is blended back with the same mask in pixel space, so kept areas stay exactly the input (no VAE drift)."""
    tmp = DUN / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    stem = f"_rp_{time.time_ns()}"
    img.save(tmp / f"{stem}.png")
    Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8)).save(tmp / f"{stem}.mask.png")
    try:
        nodes = comfy.build_repaint(comfy.upload(tmp / f"{stem}.png"), comfy.upload(tmp / f"{stem}.mask.png"),
                                    prompt, denoise=denoise, seed=seed, lora_strength=lora, prefix="hdart/dun_rp",
                                    reference=reference)
        p = comfy.run(nodes, tmp)[0]
    finally:
        for f in (tmp / f"{stem}.png", tmp / f"{stem}.mask.png"):
            f.unlink(missing_ok=True)
    out = Image.open(p).convert("RGB")
    p.unlink(missing_ok=True)
    if out.size != img.size:
        out = out.resize(img.size, Image.LANCZOS)
    m = np.clip(mask, 0, 1)[..., None]
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    return Image.fromarray(np.clip(a * (1 - m) + np.asarray(out).astype(np.float32) * m, 0, 255).astype(np.uint8))


def flatten_light(img, strength=1.0):
    """Divide out smooth lighting across the image (FLUX's vignette, a gradient from one side) and keep the detail:
    fit a quadratic surface (1, x, y, x², y², xy) to the brightness and even it out. Anything stone-sized or smaller
    is far too wiggly for that surface, so it's left alone."""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    H, W = a.shape[:2]
    lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    u, v = (xx / W - 0.5).ravel(), (yy / H - 0.5).ravel()
    basis = np.stack([np.ones_like(u), u, v, u * u, v * v, u * v], 1)
    fit = (basis @ np.linalg.lstsq(basis, lum.ravel(), rcond=None)[0]).reshape(H, W)
    gain = 1 + strength * (lum.mean() / np.maximum(fit, 1.0) - 1)
    return Image.fromarray(np.clip(a * gain[..., None], 0, 255).astype(np.uint8))


def periodic_smooth(img):
    """Moisan's periodic + smooth decomposition: remove the smooth image that explains the jump between opposite
    edges, so the texture wraps without a step (fixes 'almost tileable' colour/brightness edges exactly)."""
    a = np.asarray(img.convert("RGB")).astype(np.float64)
    H, W = a.shape[:2]
    v = np.zeros_like(a)
    v[0] += a[-1] - a[0]
    v[-1] += a[0] - a[-1]
    v[:, 0] += a[:, -1] - a[:, 0]
    v[:, -1] += a[:, 0] - a[:, -1]
    q = np.cos(2 * np.pi * np.arange(H) / H)[:, None]
    r = np.cos(2 * np.pi * np.arange(W) / W)[None, :]
    den = 2 * q + 2 * r - 4
    den[0, 0] = 1
    s = np.real(np.fft.ifft2(np.fft.fft2(v, axes=(0, 1)) / den[..., None], axes=(0, 1)))
    return Image.fromarray(np.clip(a - s, 0, 255).astype(np.uint8))


def seam_cross_mask(S, band=0.10, ramp=0.06):
    """Soft mask over the cross where a half-rolled texture's old edges meet (1 on the seam, 0 away from it)."""
    yy, xx = np.mgrid[0:S, 0:S] + 0.5
    d = np.minimum(np.abs(xx - S / 2), np.abs(yy - S / 2)) / S
    return np.clip(1 - (d - band / 2) / ramp, 0, 1)


def make_seamless(img, prompt, seed, lora=0.0, band=0.10):
    """Lighting flattened, edges made periodic, then the seam cross repainted (rolled to the middle so FLUX sees
    both sides of it) and rolled back. The result tiles with itself."""
    a = periodic_smooth(flatten_light(img))
    S = a.size[0]
    rolled = Image.fromarray(np.roll(np.asarray(a), (S // 2, S // 2), (0, 1)))
    fixed = repaint(rolled, seam_cross_mask(S, band), prompt, seed + 1000, denoise=0.75, lora=lora)
    return Image.fromarray(np.roll(np.asarray(fixed), (-(S // 2), -(S // 2)), (0, 1)))


def gen_master(piece, seed=1, lora=0.0, st=None):
    """A seamless master floor tile (top-down square, MASTER_PX)."""
    st = st or style()
    prompt = floor_texture_prompt(st, piece["prompt"])
    big = 1152  # FLUX likes a darker rim at the image edge: generate larger, keep the middle
    p = comfy.run(comfy.build_txt2img(prompt, big, big, seed=seed, lora_strength=lora, prefix="hdart/dun_master"),
                  DUN / "tmp")[0]
    src = Image.open(p).convert("RGB")
    m = (big - 1024) // 2
    src = src.crop((m, m, m + 1024, m + 1024)).resize((MASTER_PX, MASTER_PX), Image.LANCZOS)
    return make_seamless(src, prompt, seed, lora)


def seam_preview(name, pid, rel, size=768):
    """Top-down seam check for a master or repaint candidate: a master tiled 3x3; a variant (1x1 or NxN block) in a
    ring of master tiles. Edges that don't match show up as lines."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    img = Image.open(set_dir(name) / rel).convert("RGB")
    if piece.get("method") in ("wall_master", "wall_repaint"):
        a = np.tile(np.asarray(img), (1, 2, 1))
    elif piece.get("method") == "door_repaint":
        full, _ = wall_master_image(name, spec, piece.get("from"))
        sl = np.asarray(full)[:, :img.width]
        a = np.concatenate([sl, np.asarray(img), sl], 1)
    elif piece.get("method") == "master":
        a = np.tile(np.asarray(img), (3, 3, 1))
    else:
        N = atlas_n(piece)
        per = img.width // N
        m = np.asarray(master_image(name, spec, piece).resize((per, per), Image.LANCZOS))
        a = np.tile(m, (N + 2, N + 2, 1))
        a[per:per * (N + 1), per:per * (N + 1)] = np.asarray(img)
    out = Image.fromarray(a)
    return out.resize((size, size), Image.LANCZOS) if out.width > size else out


def block_mask(N, per, band, ramp=0.10):
    """0 in an outer band `band` tiles wide (kept: the master's edges), ramping to 1 over `ramp` tiles inside."""
    S = N * per
    yy, xx = np.mgrid[0:S, 0:S] + 0.5
    d = np.minimum.reduce([xx, yy, S - xx, S - yy]) / per
    return np.clip((d - band) / ramp, 0, 1)


def master_image(name, spec, piece, rel=None):
    if rel:
        return Image.open(set_dir(name) / rel).convert("RGB")
    mid = piece.get("from")
    if not mid or not any(p["id"] == mid for p in spec["pieces"]):
        raise ValueError(f"{piece['id']}: pick the master it repaints (from)")
    appr = state(name)["pieces"].get(mid, {}).get("approved", [])
    if not appr:
        raise ValueError(f"approve a candidate of {mid} first: it's the master {piece['id']} repaints")
    return Image.open(set_dir(name) / appr[0]).convert("RGB")


def gen_repaint(name, spec, piece, seed=1, denoise=None, lora=0.0, st=None):
    """The approved master tiled NxN with everything inside an outer band repainted from the piece prompt."""
    st = st or style()
    N = atlas_n(piece)
    per = min(MASTER_PX, BLOCK_MAX // N) // 16 * 16
    master = master_image(name, spec, piece).resize((per, per), Image.LANCZOS)
    base = Image.fromarray(np.tile(np.asarray(master), (N, N, 1)))
    mask = block_mask(N, per, float(piece.get("border", 0.08)))
    # No reference image: with one FLUX copies the tiled master almost exactly. Starting from it at 0.85 keeps the
    # master's stone scale but redraws the layout; 1.0 = freer (scale may drift), ~0.6 = subtle changes.
    out = repaint(base, mask, floor_texture_prompt(st, piece["prompt"]), seed,
                  denoise=float(denoise) if denoise else REPAINT_DENOISE, lora=lora, reference=False)
    return lock_palette(spec, piece.get("from"), out, mask, master)


def lock_palette(spec, master_id, img, weight, master_img):
    """Palette lock (family setting, on by default): match a fresh repaint's interior to the master's colours."""
    m = next((p for p in spec["pieces"] if p["id"] == master_id), None)
    if not m or not family_settings(m).get("palette_lock", True):
        return img
    lab = _to_lab(np.asarray(master_img.convert("RGB")).astype(np.float32).reshape(-1, 3))
    return match_palette(img, (lab.mean(0), lab.std(0)), weight, 1.0)


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


def gen_prop(piece, seed=1, lora=0.0, st=None, size=512):
    """Clean prompt -> object on white -> BiRefNet cut-out. Returns (RGB on white, mask)."""
    st = st or style()
    view = piece.get("view") or st["prop_view"]  # per-piece override, e.g. flat things lying on the ground
    # props swap the tileset medium for an object one ("tileset" makes FLUX put things on a floor slab)
    prompt = f"{st.get('prop_medium') or st['groups']['medium']['text']}, " \
             f"{style_text(st, ('style', 'rendering', 'biome') if piece.get('biome', True) else ('style', 'rendering'))}, " \
             f"{view}, {piece['prompt']}, {st['prop_framing']}"
    p = comfy.run(comfy.build_txt2img(prompt, size, size, seed=seed, lora_strength=lora, remove_bg=True,
                                      prefix="hdart/dun_prop"), DUN / "tmp")[0]
    rgba = np.asarray(Image.open(p).convert("RGBA"))
    mask = ai_kit.clean(rgba[..., 3] > 127, min_area=300)
    rgb = rgba[..., :3].copy()
    rgb[~mask] = 255
    return Image.fromarray(rgb), mask


def cut_white(img, thresh=40):
    """Prop silhouette: non-white pixels, largest connected blob(s), small holes filled."""
    fg = ai_kit.foreground(img)
    return ai_kit.clean(fg, min_area=400)


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
        from .pixel import quantize
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


# ----------------------------------------------------------------------------- sets: candidates, approval, export

SETS = ROOT / "specs" / "dungeon_sets"
EXPORT = REPO / "Assets" / "StreamingAssets" / "Art" / "Dungeon"


def load_set(name):
    return json.loads((SETS / f"{name}.json").read_text(encoding="utf-8"))


def set_dir(name):
    return DUN / name


def state(name):
    p = set_dir(name) / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"pieces": {}}


def save_state(name, s):
    set_dir(name).mkdir(parents=True, exist_ok=True)
    (set_dir(name) / "state.json").write_text(json.dumps(s, indent=1), encoding="utf-8")


def piece_of(spec, pid):
    p = next((p for p in spec["pieces"] if p["id"] == pid), None)
    if p is None:
        raise KeyError(f"piece {pid} is not in set {spec.get('id')}")
    return p


def generate(name, pid, n=3, seed=1, denoise=None, prompt=None, lora=None, variation=None):
    """n candidates for one piece. Returns relative paths (under sprites/dungeon/<set>/)."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    if prompt:
        piece["prompt"] = prompt
    out = set_dir(name) / "candidates" / pid
    out.mkdir(parents=True, exist_ok=True)
    st = set_style(spec)
    base = None
    if piece["kind"] == "wall_decor" and piece.get("of"):  # paint the decoration onto an approved base wall
        appr = state(name)["pieces"].get(piece["of"], {}).get("approved", [])
        if appr:
            bf = set_dir(name) / appr[0]
            base = (Image.open(bf).convert("RGB"), np.load(bf.with_suffix(".npy")))
    made, meta = [], {}
    for k in range(n):
        sd = seed + k
        if piece["kind"] == "prop":
            img, mask = gen_prop(piece, sd, lora=lora if lora is not None else 0.0, st=st)
        elif piece.get("method") == "wall_master":
            img = gen_wall_master(spec, piece, sd, lora if lora is not None else 0.0)
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("method") == "wall_repaint":
            img = gen_wall_variant(name, spec, piece, sd, denoise or piece.get("denoise"), lora if lora is not None else 0.0)
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("method") == "door_repaint":
            img = gen_door(name, spec, piece, sd, denoise or piece.get("denoise"), lora if lora is not None else 0.0)
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("method") == "master":
            img = gen_master(piece, sd, lora if lora is not None else 0.0, st=st)
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("method") == "repaint":
            img = gen_repaint(name, spec, piece, sd, denoise or piece.get("denoise"), lora if lora is not None else 0.0, st=st)
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("method") == "atlas":
            img = gen_atlas(piece, sd, lora if lora is not None else 0.0, st=st)
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("shape") == "floor" and piece.get("method", "warp") == "warp":
            img, mask = gen_floor(piece, spec["tile"], sd, lora if lora is not None else 0.0, st=st)
        elif base is not None:
            img, mask = gen_on_base(piece, base, sd, denoise or piece.get("denoise", 0.6),
                                    lora if lora is not None else 0.0, st=st)
        else:
            img, mask = gen_tile(piece, spec["tile"], sd, denoise or piece.get("denoise", 0.72),
                                 lora if lora is not None else 0.0, st=st)
        f = out / f"s{sd}_{int(time.time())}.png"
        img.save(f)
        np.save(f.with_suffix(".npy"), mask)
        rel = str(f.relative_to(set_dir(name))).replace("\\", "/")
        made.append(rel)
        meta[rel] = {"seed": sd, "prompt": piece["prompt"], "denoise": denoise, "lora": lora,
                     "on_base": base is not None}
        if piece.get("method") in ("repaint", "wall_repaint", "door_repaint"):  # its master (switching makes it stale)
            meta[rel]["master"] = master_rel(name, piece.get("from"))
        if variation:  # floor family: the variation entry it was made for
            meta[rel]["variation"] = variation
    with LOCK:  # re-read: approvals and archiving may have happened while this was generating
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        ps.setdefault("meta", {}).update(meta)
        ps["candidates"] = made + ps["candidates"]
        ps["prompt"] = piece["prompt"]
        save_state(name, s)
    return made


GEOMETRY_KEYS = ("shape", "length", "height", "thickness", "opening", "mesh")


def resolved(spec, pid):
    """A piece with its geometry inherited from `of` (wall decor is painted on that wall's exact shape)."""
    piece = dict(piece_of(spec, pid))
    if piece["kind"] == "wall_decor" and piece.get("of") and any(p["id"] == piece["of"] for p in spec["pieces"]):
        base = piece_of(spec, piece["of"])
        for k in GEOMETRY_KEYS:
            if k in base:
                piece[k] = base[k]
    return piece


def set_style(spec):
    """Shared dungeon descriptors with the set's own biome text swapped in."""
    st = style()
    if spec.get("biome"):
        st["groups"]["biome"]["text"] = spec["biome"]
    return st


def gen_on_base(piece, base, seed, denoise, lora, st):
    """wall_decor: img2img over an approved base wall so stone, colour and outline match the plain wall."""
    img, mask = base
    out = _i2i(img, f"{style_text(st)}, {piece['prompt']}", seed, denoise, lora)
    from .workshop import enforce_silhouette
    out = np.asarray(enforce_silhouette(out, mask)).copy()
    white = mask & (out.min(-1) > 225)  # img2img bleeds the white backdrop in at the outline: use the base there
    out[white] = np.asarray(img)[white]
    return Image.fromarray(out), mask


_GEO_CACHE = {}


def _geometry_cached(piece, tile):
    key = (json.dumps({k: piece.get(k) for k in GEOMETRY_KEYS + ("kind",)}, sort_keys=True), tile)
    if key not in _GEO_CACHE:
        _GEO_CACHE[key] = geometry(piece, tile)
    return _GEO_CACHE[key]


def _load(name, rel):
    f = set_dir(name) / rel
    mp = f.with_suffix(".npy")
    return Image.open(f).convert("RGB"), (np.load(mp) if mp.exists() else None)


# ----------------------------------------------------------------------------- colour harmonizing
# The set's `harmonize` setting (0..1, Studio > Dungeon slider) pulls every tile image's colour statistics (mean and
# spread per CIELAB channel) toward its family's reference, so variants and pieces made in separate generations sit
# together. Families: floors, and walls (everything else that isn't a prop). Reference = the family's approved
# images (all its candidates while nothing is approved). Props are left alone: they're objects, not surfaces.

_M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
_WHITE = _M.sum(1)


def _to_lab(rgb):
    c = rgb / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ _M.T / _WHITE
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def _from_lab(lab):
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], -1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) / (24389 / 27)) * _WHITE
    c = xyz @ np.linalg.inv(_M).T
    c = np.where(c > 0.0031308, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055, 12.92 * c)
    return np.clip(c * 255, 0, 255)


def family(piece):
    if piece["kind"] in ("prop", "elevation"):
        return None
    return "floor" if piece.get("shape") == "floor" else "wall"


def _paint_mask(img, mask):
    """Pixels that are painted surface (not the white backdrop)."""
    if mask is not None and mask.shape == img.size[::-1]:
        return mask
    return np.asarray(img).min(-1) < 232


def _lab_stats(img, mask):
    lab = _to_lab(np.asarray(img)[_paint_mask(img, mask)].astype(np.float32))
    if len(lab) < 16:
        return None
    return lab.mean(0), lab.std(0)


def _reference(name, spec, fam):
    """(mean, std) in Lab over the family's approved images (else all its candidates), or None."""
    s = state(name)
    rels = []
    for p in spec["pieces"]:
        if family(p) == fam and not p.get("retired"):
            ps = s["pieces"].get(p["id"], {})
            rels += [(p["id"], r) for r in ps.get("approved", [])]
    if not rels:
        for p in spec["pieces"]:
            if family(p) == fam and not p.get("retired"):
                rels += [(p["id"], r) for r in s["pieces"].get(p["id"], {}).get("candidates", [])]
    if not rels:
        return None
    key = (name, fam, tuple(sorted(r for _, r in rels)))
    if key not in _REF_CACHE:
        stats = [st for st in (_lab_stats(*_load(name, r)) for _, r in rels) if st]
        _REF_CACHE[key] = (np.mean([m for m, _ in stats], 0), np.mean([d for _, d in stats], 0)) if stats else None
    return _REF_CACHE[key]


def match_palette(img, ref, weight, strength=1.0):
    """Pull `img`'s colours (CIELAB mean and spread, measured over its interior: weight ~1) toward `ref` = (mean, std),
    applied per pixel with weight * strength. Weight 0 = untouched, so a floor family image's kept border (the
    master's exact edges) stays exact and the change fades in with the same soft ramp as the repaint."""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    inner = weight > 0.99
    if strength <= 0 or inner.sum() < 64:
        return img
    lab = _to_lab(a.reshape(-1, 3)).reshape(a.shape)
    ms, ss = lab[inner].mean(0), lab[inner].std(0)
    mr, sr = ref
    fixed = (lab - ms) * np.clip(sr / np.maximum(ss, 1e-3), 0.5, 2.0) + mr
    w = (np.clip(weight, 0, 1) * strength)[..., None]
    out = a * (1 - w) + _from_lab(fixed.reshape(-1, 3)).reshape(a.shape) * w
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def image_lab_stats(name, rel, interior=None):
    """Lab (mean, std) of a candidate (whole image, or where `interior` weight ~1), cached by file."""
    key = ("lab", name, rel, None if interior is None else interior.shape)
    if key not in _REF_CACHE:
        a = np.asarray(Image.open(set_dir(name) / rel).convert("RGB")).astype(np.float32)
        px = a[interior > 0.99] if interior is not None else a.reshape(-1, 3)
        lab = _to_lab(px)
        _REF_CACHE[key] = (lab.mean(0), lab.std(0))
    return _REF_CACHE[key]


def family_weight(piece, size):
    """The repaint mask of a family image (0 on the kept border, ramping to 1 inside), for an image of `size` px."""
    N = atlas_n(piece)
    return block_mask(N, size // N, float(piece.get("border", 0.08)))


def harmonize(name, spec, piece, img, mask):
    """`img` with its colours pulled toward a reference by the set's `harmonize` strength. Floor family variants:
    only the interior, toward the active master's palette (their edges are the master's and must stay exact);
    masters are the palette, unchanged. Other pieces: the whole image toward their family's approved images."""
    k = float(spec.get("harmonize") or 0)
    fam = family(piece)
    if k <= 0 or fam is None or piece.get("method") == "master" or piece.get("built_from"):
        return img
    if piece.get("method") == "repaint":
        mrel = master_rel(name, piece.get("from"))
        return match_palette(img, image_lab_stats(name, mrel), family_weight(piece, img.width), k) if mrel else img
    ref = _reference(name, spec, fam)
    rgb = np.asarray(img).copy()
    paint = _paint_mask(img, mask)
    if ref is None or paint.sum() < 16:
        return img
    lab = _to_lab(rgb[paint].astype(np.float32))  # painted pixels only, converted once
    (mr, sr), ms, ss = ref, lab.mean(0), lab.std(0)
    ratio = np.clip(sr / np.maximum(ss, 1e-3), 0.5, 2.0)  # never blow up a flat image or flatten a busy one
    rgb[paint] = _from_lab((lab - ms) * (1 + k * (ratio - 1)) + ms + k * (mr - ms)).astype(np.uint8)
    return Image.fromarray(rgb)


def _sprite(img8, mask8, n8, w, h):
    """An 8x region -> (native RGBA, normal map RGBA). n8: screen-space normals at 8x."""
    rgba = downsample(img8, mask8, w, h)
    enc = Image.fromarray(np.clip((n8 * 0.5 + 0.5) * 255, 0, 255).astype(np.uint8))
    ns = np.asarray(downsample(enc, mask8, w, h))[..., :3].astype(np.float32) / 255 * 2 - 1
    ns /= np.maximum(np.linalg.norm(ns, axis=-1, keepdims=True), 1e-6)
    ns = iso.detail_normals(ns, rgba)
    return rgba, iso.encode_normal(ns, np.asarray(rgba)[..., 3])


def fill_white(img, mask, thresh=232, iters=24):
    """White backdrop bleeding in at the outline (img2img) -> grown in from the neighbouring painted pixels."""
    a = np.asarray(img).astype(np.float32)
    bad = mask & (a.min(-1) > thresh)
    if not bad.any():
        return img
    good = mask & ~bad
    for _ in range(iters):
        if not bad.any():
            break
        acc = np.zeros_like(a)
        cnt = np.zeros(a.shape[:2], np.float32)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            g = np.roll(good, (dy, dx), (0, 1))
            acc += np.roll(a, (dy, dx), (0, 1)) * g[..., None]
            cnt += g
        fix = bad & (cnt > 0)
        a[fix] = acc[fix] / cnt[fix, None]
        good, bad = good | fix, bad & ~fix
    return Image.fromarray(a.astype(np.uint8))


def _soften_edge(sp, left=True):
    """FLUX outlines the ends of a painted wall with a light highlight. Where pieces join that reads as a seam:
    replace the outermost opaque column of each row with its inner neighbour (colour and normal)."""
    rgba, nm = (np.asarray(im).copy() for im in sp)
    op = rgba[..., 3] > 0
    for y in np.nonzero(op.any(1))[0]:
        xs = np.nonzero(op[y])[0]
        x, nx = (xs[0], xs[0] + 1) if left else (xs[-1], xs[-1] - 1)
        if 0 <= nx < rgba.shape[1] and op[y, nx]:
            rgba[y, x], nm[y, x] = rgba[y, nx], nm[y, nx]
    return Image.fromarray(rgba), Image.fromarray(nm)


def _flat_normals(shape, piece_normal=(0, 0, 1)):
    return np.broadcast_to(iso.to_screen_normal(piece_normal), tuple(shape) + (3,)).copy()


def _mirror(rgba, nm):
    """Mirror a thin piece from the x axis onto the y axis (normal map: flip x)."""
    n = np.asarray(ImageOps.mirror(nm)).copy()
    n[..., 0] = 255 - n[..., 0]
    return ImageOps.mirror(rgba), Image.fromarray(n)


def cells(name, pid, rel, tile=None):
    """A candidate cut for the game: {suffix: (RGBA, normal map)} at `tile`.
    single -> ""; run -> "k{cell}h{half}"; atlas -> "i{i}j{j}"; mirrored copies (y axis) end in "m"."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    T = spec["tile"]
    tile = tile or T
    img, mask = _load(name, rel)
    img = harmonize(name, spec, piece, img, mask)
    lay = layout(piece)
    out = {}
    if piece["kind"] == "prop":
        rgba = fit_prop(img, mask, piece.get("footprint", 0.5), tile)
        n = iso.detail_normals(np.broadcast_to(np.array([0, 0, 1], np.float32), (rgba.height, rgba.width, 3)).copy(), rgba)
        return {"": (rgba, iso.encode_normal(n, np.asarray(rgba)[..., 3]))}
    w, h = canvas_size(piece, tile) if piece.get("shape") not in THIN else iso.cell_canvas(tile, piece.get("height", 2.0))
    if lay == "atlas":
        N = int(piece.get("atlas", 4))
        for i in range(N):
            for j in range(N):
                im8, m8 = warp_floor(img, T, i / N, (i + 1) / N, j / N, (j + 1) / N, rim=piece.get("rim", 0))
                out[f"i{i}j{j}"] = _sprite(im8, m8, _flat_normals(m8.shape), tile, tile // 2)
        return out
    if piece.get("method") in SQUARE_METHODS:  # a 1x1 top-down square (master, 1x1 repaint): warp it on cutting
        im8, m8 = warp_floor(img, T, rim=piece.get("rim", 0))
        return {"": _sprite(im8, m8, _flat_normals(m8.shape), tile, tile // 2)}
    if piece.get("shape") in ("floor", "decal"):
        return {"": _sprite(fill_white(img, mask), mask, _flat_normals(mask.shape), w, h)}
    g = _geometry_cached(piece, T)
    nscr = iso.to_screen_normal(g["n"])
    m_all = mask if mask is not None else g["mask"]
    img = fill_white(img, m_all)
    W, Hc = g["cell"]
    if lay == "run":
        L = g["length"]
        for k in range(L):
            ox, oy = k * W // 2, k * W // 4
            sl = (slice(oy, oy + Hc), slice(ox, ox + W))
            for hh in (0, 1):
                m = (g["own"][sl] == 2 * k + hh) & m_all[sl]
                sp = _sprite(img.crop((ox, oy, ox + W, oy + Hc)), m, nscr[sl], w, h)
                if k == 0 and hh == 0:  # where this run joins the previous one
                    sp = _soften_edge(sp, left=True)
                if k == L - 1 and hh == 1:
                    sp = _soften_edge(sp, left=False)
                out[f"k{k}h{hh}"] = sp
    else:
        sp = _sprite(img, m_all, nscr, w, h)
        if piece.get("shape") in ("run", "mesh"):  # doors and arches join walls on both sides
            sp = _soften_edge(_soften_edge(sp, left=True), left=False)
        out[""] = sp
    if mirrored(piece):
        for suf, (rgba, nm) in list(out.items()):
            out[suf + "m"] = _mirror(rgba, nm)
    return out


def native(name, pid, rel, tile=None):
    """A candidate at the native grid, assembled for review (a run in one piece, an atlas as its patch)."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    tile = tile or output_tile(spec)
    cs = {k: v[0] for k, v in cells(name, pid, rel, tile).items() if not k.endswith("m")}
    lay = layout(piece)
    if lay == "single":
        return cs[""]
    if lay == "run":
        L = max(int(k[1:k.index("h")]) for k in cs) + 1
        cw, ch = cs["k0h0"].size
        canvas = Image.new("RGBA", (cw + (L - 1) * tile // 2, ch + (L - 1) * tile // 4))
        for k in range(L):
            for hh in (0, 1):
                canvas.alpha_composite(cs[f"k{k}h{hh}"], (k * tile // 2, k * tile // 4))
        return canvas
    N = int(piece.get("atlas", 4))
    canvas = Image.new("RGBA", (N * tile, N * tile // 2))
    for i in range(N):
        for j in range(N):
            canvas.alpha_composite(cs[f"i{i}j{j}"], ((i - j) * tile // 2 + (N - 1) * tile // 2, (i + j) * tile // 4))
    return canvas


def approve(name, pid, rel, on=True):
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        if on and rel not in ps["approved"]:
            ps["approved"].append(rel)
        if not on and rel in ps["approved"]:
            ps["approved"].remove(rel)
        save_state(name, s)
        return ps


def export(name, tiles=(32, 64)):
    """Approved variants -> Assets/StreamingAssets/Art/Dungeon/<set>/r<tile>/<key>.<v>[.<suffix>].png (+ .n.png
    normal maps) and set.json (format 2). Several pieces with the same `board` key become variants of it
    (door styles, wall runs of different lengths, floor atlases).
    The images are at the set's output resolution (`output_tile`: pixelated or full) in every r<tile> folder the
    game asks for; set.json's `ppu` (pixels per tile = Unity pixels per unit) and `filter` say how to show them."""
    spec = load_set(name)
    s = state(name)
    root = EXPORT / spec["id"]
    out_t = output_tile(spec)
    meta = {"id": spec["id"], "name": spec.get("name", spec["id"]), "format": 2, "tiles": list(tiles),
            "ppu": out_t, "filter": "point" if spec.get("pixel") else "bilinear", "pieces": {}}
    for t in tiles:  # variant counts can shrink: start each grid folder clean
        for old in (root / f"r{t}").glob("*.png"):
            old.unlink()
    for p0 in spec["pieces"]:
        if p0.get("retired") or p0.get("kind") == "elevation":  # retired: kept for reference; elevations: sources
            continue
        piece = resolved(spec, p0["id"])
        approved = s["pieces"].get(piece["id"], {}).get("approved", [])
        if not approved:
            continue
        key = piece.get("board", piece["id"])  # the id the game board asks for
        of = piece.get("of")
        of = piece_of(spec, of).get("board", of) if of and any(p["id"] == of for p in spec["pieces"]) else of
        entry = meta["pieces"].setdefault(key, {"kind": piece["kind"], "of": of, "variants": []})
        if piece.get("method") == "master":  # floor family: block size weights for the board's planner
            entry["blockWeights"] = family_settings(piece)["weights"]
        for rel in approved:
            vi = len(entry["variants"])
            var = {"source": piece["id"], "layout": layout(piece), "mirror": mirrored(piece),
                   "length": int(piece.get("length", 1)) if layout(piece) == "run" else 1,
                   "atlas": int(piece.get("atlas", 4)) if layout(piece) == "atlas" else 0}
            cs = cells(name, piece["id"], rel, out_t)
            h = next(iter(cs.values()))[0].height
            var["sprites"] = sorted(cs)
            for t in tiles:
                d = root / f"r{t}"
                d.mkdir(parents=True, exist_ok=True)
                for suf, (rgba, nm) in cs.items():
                    stem = f"{key}.{vi}" + (f".{suf}" if suf else "")
                    rgba.save(d / f"{stem}.png")
                    nm.save(d / f"{stem}.n.png")
                var[f"pivot{t}"] = [0.5, 0.1] if piece["kind"] == "prop" else [0.5, (out_t / 4) / h]
            entry["variants"].append(var)
    root.mkdir(parents=True, exist_ok=True)
    (root / "set.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return root


def fit_prop(img, mask, footprint, tile=64):
    """Crop to the object, scale so its width spans `footprint` tiles, sit its base on the canvas bottom."""
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    crop = np.asarray(img)[y0:y1, x0:x1]
    cm = mask[y0:y1, x0:x1]
    out_w = max(2, int(round(footprint * tile)))
    out_h = max(2, int(round(out_w * (y1 - y0) / (x1 - x0))))
    return downsample(Image.fromarray(crop), cm, out_w, out_h)


# ----------------------------------------------------------------------------- tooling (Studio > Dungeon)

def list_sets():
    return sorted(p.stem for p in SETS.glob("*.json"))


def save_style(groups=None, prop_view=None, prop_framing=None, prop_medium=None):
    st = style()
    if prop_medium is not None:
        st["prop_medium"] = prop_medium
    for g, text in (groups or {}).items():
        if g in st["groups"]:
            st["groups"][g]["text"] = text
    if prop_view is not None:
        st["prop_view"] = prop_view
    if prop_framing is not None:
        st["prop_framing"] = prop_framing
    STYLE.write_text(json.dumps(st, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return st


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def _backup_spec(path):
    """Keep the last 40 versions of each set spec in archive/history/<set>/, so a lost piece can be recovered."""
    if not path.exists():
        return
    hist = ARCHIVE / "history" / path.stem
    hist.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, hist / f"{_stamp()}_{time.time_ns() % 1000000:06d}.json")
    for old in sorted(hist.glob("*.json"))[:-40]:
        old.unlink()


def save_set(name, spec):
    """Replace a set spec (ids must be unique). Keeps `_doc`; the previous version goes to archive/history/."""
    ids = [p["id"] for p in spec["pieces"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate piece ids")
    with LOCK:
        path = SETS / f"{name}.json"
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        out = {"_doc": old.get("_doc", spec.get("_doc", "")), **{k: v for k, v in spec.items() if k != "_doc"}, "id": name}
        _backup_spec(path)
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return out


def new_set(name, title=None, biome="", copy_from="crypt"):
    """A new biome set. Pieces are copied from `copy_from` with the biome word swapped in their ids; each keeps
    a `board` id so Prototype V (which asks for the crypt content ids) can show the new art."""
    if not name.replace("_", "").isalnum():
        raise ValueError("set id: letters, digits and _ only")
    with LOCK:
        if (SETS / f"{name}.json").exists():
            raise ValueError(f"set {name} exists")
        src = load_set(copy_from)
        pieces = []
        for p in src["pieces"]:
            q = dict(p)
            q["board"] = p.get("board", p["id"])
            q["id"] = p["id"].replace(f"{copy_from}_", f"{name}_")
            if q.get("of"):
                q["of"] = q["of"].replace(f"{copy_from}_", f"{name}_")
            pieces.append(q)
        return save_set(name, {"_doc": src.get("_doc", ""), "id": name, "name": title or name.title(),
                               "tile": src.get("tile", 64),
                               "biome": biome or set_style(src)["groups"]["biome"]["text"], "pieces": pieces})


def update_set(name, fields):
    """Set-level fields: name, biome, tile, pixel (px per tile; 0/empty = full resolution), harmonize (0..1)."""
    with LOCK:
        spec = load_set(name)
        for k in ("name", "biome", "tile"):
            if k in fields:
                spec[k] = int(fields[k]) if k == "tile" else fields[k]
        if "pixel" in fields:
            px = int(fields["pixel"] or 0)
            if px and px not in pixel_steps(spec):
                raise ValueError(f"pixel: one of {pixel_steps(spec)} px per tile, or 0 for full resolution")
            if px:
                spec["pixel"] = px
            else:
                spec.pop("pixel", None)
        if "harmonize" in fields:
            k = round(min(max(float(fields["harmonize"] or 0), 0.0), 1.0), 2)
            if k:
                spec["harmonize"] = k
            else:
                spec.pop("harmonize", None)
        return save_set(name, spec)


def upsert_piece(name, piece, old_id=None):
    with LOCK:
        spec = load_set(name)
        old_id = old_id or piece["id"]
        clean = {k: v for k, v in piece.items() if v not in (None, "")}
        i = next((k for k, p in enumerate(spec["pieces"]) if p["id"] == old_id), None)
        if i is None:
            spec["pieces"].append(clean)
        else:
            spec["pieces"][i] = clean
        out = save_set(name, spec)  # first: it refuses duplicate ids, and then the state must not move either
        if old_id != piece["id"]:  # renamed: carry its candidates/approvals along
            s = state(name)
            if old_id in s["pieces"]:
                s["pieces"][piece["id"]] = s["pieces"].pop(old_id)
                save_state(name, s)
        return out


# ----------------------------------------------------------------------------- archive (nothing is deleted here)
# archive/dungeon/<set>/<rel>       archived candidates (png + npy mask), same relative path as under the set folder
# archive/sets/<set>_<stamp>/       archived sets: spec.json + the set's sprites folder
# archive/history/<set>/            previous versions of each set spec
# Delete archive/ (or parts of it) by hand once you're sure.

def _archive_candidate(name, rel):
    src = candidate_path(name, rel)
    dst = ARCHIVE / "dungeon" / name / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    for f in (src, src.with_suffix(".npy")):
        if f.exists():
            shutil.move(str(f), str(dst.with_suffix(f.suffix)))


def _drop(ps, rels):
    for k in ("candidates", "approved"):
        ps[k] = [r for r in ps.get(k, []) if r not in rels]
    for r in rels:
        ps.get("meta", {}).pop(r, None)


def discard(name, pid, rel):
    """Archive one candidate: its files move to archive/dungeon/<set>/ and it leaves the piece."""
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        _archive_candidate(name, rel)
        _drop(ps, {rel})
        save_state(name, s)
        return ps


def archive_unapproved(name, pid):
    """Archive every candidate of a piece that isn't approved. Returns the piece state and how many moved."""
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        rels = {r for r in ps.get("candidates", []) if r not in ps.get("approved", [])}
        for r in rels:
            _archive_candidate(name, r)
        _drop(ps, rels)
        save_state(name, s)
        return ps, len(rels)


def _archive_piece_state(name, s, pid):
    ps = s["pieces"].pop(pid, None) or {}
    for r in set(ps.get("candidates", [])) | set(ps.get("approved", [])):
        _archive_candidate(name, r)


def delete_piece(name, pid):
    """Remove a piece from the set and archive all of its generated images."""
    with LOCK:
        spec = load_set(name)
        spec["pieces"] = [p for p in spec["pieces"] if p["id"] != pid]
        out = save_set(name, spec)
        s = state(name)
        if pid in s["pieces"]:
            _archive_piece_state(name, s, pid)
            save_state(name, s)
        return out


def orphans(name):
    """Pieces that have generated images but are no longer in the set spec: {id: {"count", "prompt"}}."""
    ids = {p["id"] for p in load_set(name)["pieces"]}
    out = {}
    for pid, ps in state(name)["pieces"].items():
        n = len(set(ps.get("candidates", [])) | set(ps.get("approved", [])))
        if pid not in ids and n:
            out[pid] = {"count": n, "prompt": ps.get("prompt", "")}
    return out


def restore_piece(name, pid):
    """Put an orphan back into the set: from the style catalog when it came from there, else a plain floor tile or
    prop, with the prompt its images were generated with."""
    with LOCK:
        spec = load_set(name)
        if any(p["id"] == pid for p in spec["pieces"]):
            return spec
        ps = state(name)["pieces"].get(pid, {})
        cat = next((dict(c) for c in style().get("catalog", []) if c["id"].replace("{set}", name) == pid), None)
        if cat:
            cat.pop("group", None)
            cat["id"] = pid
            if cat.get("of"):
                cat["of"] = cat["of"].replace("{set}", name)
            piece = cat
        elif pid.startswith("prop."):
            piece = {"id": pid, "kind": "prop", "footprint": 0.5}
        else:
            piece = {"id": pid, "kind": "tile", "shape": "floor", "variants": 2}
        if ps.get("prompt"):
            piece["prompt"] = ps["prompt"]
        spec["pieces"].append(piece)
        return save_set(name, spec)


def archive_orphan(name, pid):
    with LOCK:
        s = state(name)
        _archive_piece_state(name, s, pid)
        save_state(name, s)


def archive_set(name):
    """Move a whole set (spec + generated images + state) to archive/sets/<set>_<stamp>/. Its Unity export, if any,
    stays in the Hearthdelve project. Returns the archive folder."""
    with LOCK:
        others = [n for n in list_sets() if n != name]
        if not others:
            raise ValueError("can't archive the only set")
        path = SETS / f"{name}.json"
        if not path.exists():
            raise ValueError(f"no set {name}")
        dst = ARCHIVE / "sets" / f"{name}_{_stamp()}"
        dst.mkdir(parents=True)
        shutil.move(str(path), str(dst / "spec.json"))
        if set_dir(name).exists():
            shutil.move(str(set_dir(name)), str(dst / "sprites"))
        return dst


def candidate_path(name, rel):
    p = (set_dir(name) / rel).resolve()
    if set_dir(name).resolve() not in p.parents:
        raise ValueError("outside the set folder")
    return p


def png_bytes(im, scale=1):
    import io
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# The mock room for the preview, in piece space (rows = +y, columns = +x). Walls are thin and join up by
# neighbours exactly like Prototype V does it: a post where walls meet or end, half-cell arms towards each
# neighbouring wall, long straight stretches cut into the longest wall runs available.
# W wall, T wall decor (on the room side), D door, A archway, P pillar, R rubble, X trap, a..l props.
ROOM = [
    "WWTWWWDWWTWW",
    "W.....W....W",
    "T.a...W..b.W",
    "W.....A....W",
    "W.c..WWW...W",
    "D....X...d.W",
    "W.e.....P..T",
    "W..f..R..g.W",
    "W.h......i.W",
    "WWWWWWWWWWWW",
]
WALLISH = "WTDA"


def _roles(spec, approved):
    """Room letter -> board key -> list of (source piece, rel, layout info) to draw with."""
    by_key = {}
    for p0 in spec["pieces"]:
        if p0.get("retired"):
            continue
        p = resolved(spec, p0["id"])
        for rel in approved.get(p["id"], []):
            by_key.setdefault(p.get("board", p["id"]), []).append((p, rel))

    def first_key(pred):
        for key, vs in by_key.items():
            if any(pred(p) for p, _ in vs):
                return key
        return None

    def word(p, *ws):
        return any(w in p["id"] for w in ws)

    floor = lambda p: p.get("shape") == "floor" and p["kind"] == "tile"
    wall = lambda p: p.get("shape") == "run" and p["kind"] == "tile" and p.get("opening") != "door"
    roles = {
        ".": first_key(lambda p: floor(p) and p.get("method") == "atlas")
             or first_key(lambda p: floor(p) and not word(p, "rubble", "trap")),
        "R": first_key(lambda p: floor(p) and word(p, "rubble")),
        "X": first_key(lambda p: floor(p) and word(p, "trap")),
        "W": first_key(wall),
        "post": first_key(lambda p: p.get("shape") == "post"),
        "D": first_key(lambda p: p.get("opening") == "door"),
        "A": first_key(lambda p: p.get("shape") == "mesh"),
        "P": first_key(lambda p: p.get("shape") == "pillar"),
    }
    decor = [k for k, vs in by_key.items() if vs[0][0]["kind"] == "wall_decor"]
    props = [k for k, vs in by_key.items() if vs[0][0]["kind"] == "prop"]
    for k, ch in enumerate("abcdefghijkl"):
        roles[ch] = props[k % len(props)] if props else None
    roles["decor"] = decor
    for k, fallback in (("R", "."), ("X", ".")):
        roles[k] = roles[k] or roles[fallback]
    return roles, by_key


def _runs(cells, along_x, lengths):
    """Straight wall stretches along one axis -> {(x, y): (length of the chosen run, index within it)}.
    Greedy longest-first, so long walls get long, non-repeating runs."""
    out = {}
    lengths = sorted(set(lengths), reverse=True) or [1]
    lines = {}
    for (x, y) in cells:
        lines.setdefault(y if along_x else x, []).append(x if along_x else y)
    for line, vals in lines.items():
        vals.sort()
        seg = [[vals[0]]]
        for v in vals[1:]:
            (seg[-1].append(v) if v == seg[-1][-1] + 1 else seg.append([v]))
        for s in seg:
            i = 0
            while i < len(s):
                L = next((L for L in lengths if L <= len(s) - i), 1)
                for k in range(L):
                    v = s[i + k]
                    out[(v, line) if along_x else (line, v)] = (L, k, s[i])
                i += L
    return out


# ----------------------------------------------------------------------------- editing candidates (image panel)

def _new_candidate(name, pid, img, meta, tag):
    """Save an image as a new candidate of a piece (full mask), with its meta, at the top of the list."""
    out = set_dir(name) / "candidates" / pid
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{tag}_{time.time_ns() // 1000000}.png"
    img.save(f)
    np.save(f.with_suffix(".npy"), np.ones(img.size[::-1], bool))
    rel = str(f.relative_to(set_dir(name))).replace("\\", "/")
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        ps["candidates"].insert(0, rel)
        ps.setdefault("meta", {})[rel] = meta
        save_state(name, s)
    return rel


def edit_candidate(name, pid, rel, prompt, denoise=0.5, seed=1):
    """Repaint a master or variant from itself (not from the master): its kept border stays exactly, so a variant
    still fits the family and a master still tiles. `prompt` = the full prompt to repaint with (the panel pre-fills
    the image's own). Saved as a new candidate with the same variation/master tags."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    method = piece.get("method")
    if method not in ("master", "repaint") + ELEV_METHODS:
        raise ValueError("edit works on family images (floor and wall masters, variants, doors)")
    old = state(name)["pieces"].get(pid, {}).get("meta", {}).get(rel, {})
    img = Image.open(set_dir(name) / rel).convert("RGB")
    N = atlas_n(piece)
    per = img.width // N
    text = prompt.strip() or old.get("prompt", piece.get("prompt", ""))
    if method in ELEV_METHODS:
        if method == "door_repaint":
            fam = wall_family_settings(piece_of(spec, piece["from"]))
            x0, x1, y0, _ = door_rect(fam, per, iso._KZ * per, img.height)
            xx, yy = np.arange(img.width) + 0.5, np.arange(img.height) + 0.5
            weight = (np.clip(np.minimum(xx - x0, x1 - xx) / (0.04 * per), 0, 1)[None, :]
                      * np.clip((yy - y0) / (0.04 * per), 0, 1)[:, None])
            full = door_texture_prompt(set_style(spec), material_prompt(name, piece_of(spec, piece["from"])), text)
        else:
            weight = elev_keep_mask(img.width, img.height, per, iso._KZ * per, top=0.0 if method == "wall_master" else 0.15,
                                    bottom=0.0 if method == "wall_master" else 0.08)
            full = wall_texture_prompt(set_style(spec), text)
        out = repaint(img, weight, full, seed, denoise=float(denoise), reference=False)
        if method != "door_repaint":
            mid = piece["id"] if method == "wall_master" else piece.get("from")
            mrel = rel if method == "wall_master" else master_rel(name, mid)
            with Image.open(set_dir(name) / mrel) as mi:
                out = lock_palette(spec, mid, out, weight, mi)
        meta = {**{k: v for k, v in old.items() if k in ("variation", "master")}, "seed": seed, "prompt": text,
                "denoise": float(denoise), "edited_from": rel}
        return _new_candidate(name, pid, out, meta, f"e{seed}")
    weight = block_mask(N, per, float(piece.get("border", 0.08)))
    out = repaint(img, weight, floor_texture_prompt(set_style(spec), text), seed, denoise=float(denoise), reference=False)
    # palette lock: a variant toward the active master, a master toward itself (an edit shouldn't shift its palette)
    mid = piece["id"] if piece.get("method") == "master" else piece.get("from")
    mrel = rel if piece.get("method") == "master" else master_rel(name, mid)
    if mrel:
        with Image.open(set_dir(name) / mrel) as mi:
            out = lock_palette(spec, mid, out, weight, mi)
    meta = {**{k: v for k, v in old.items() if k in ("variation", "master")}, "seed": seed, "prompt": text,
            "denoise": float(denoise), "edited_from": rel}
    return _new_candidate(name, pid, out, meta, f"e{seed}")


def rescale_master(name, pid, rel, factor, redetail=False, seed=1):
    """A master candidate at another texture scale (factor < 1 = smaller stones, > 1 = bigger), as a new candidate.
    1/factor an integer: the tile repeated and shrunk, exactly seamless. Otherwise: resampled from a 3x3 tiling and
    made seamless again. `redetail` repaints the whole tile lightly (0.35) first, to sharpen an upscale."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    if piece.get("method") != "master":
        raise ValueError("rescale works on master tiles (variants are repainted from the master)")
    factor = float(factor)
    if not 0.2 <= factor <= 4:
        raise ValueError("scale factor: 0.2 to 4")
    old = state(name)["pieces"].get(pid, {}).get("meta", {}).get(rel, {})
    prompt = old.get("prompt", piece.get("prompt", ""))
    full = floor_texture_prompt(set_style(spec), prompt)
    S = MASTER_PX
    src = np.asarray(Image.open(set_dir(name) / rel).convert("RGB").resize((S, S), Image.LANCZOS))
    k = round(1 / factor)
    if factor < 1 and abs(1 / factor - k) < 1e-6:
        out = Image.fromarray(np.tile(src, (k, k, 1))).resize((S, S), Image.LANCZOS)
    else:
        big = Image.fromarray(np.tile(src, (3, 3, 1)))  # 3S x 3S
        side, c = S / factor, 1.5 * S                    # source px that become one tile, around the centre
        out = big.crop((round(c - side / 2), round(c - side / 2), round(c + side / 2), round(c + side / 2))).resize((S, S), Image.LANCZOS)
        if redetail:
            out = repaint(out, np.ones((S, S), np.float32), full, seed, denoise=0.35, reference=True)
        out = make_seamless(out, full, seed)
    meta = {"seed": seed, "prompt": prompt, "rescaled_from": rel, "scale": factor, "redetail": bool(redetail)}
    return _new_candidate(name, pid, out, meta, f"x{factor:g}".replace(".", "_"))



# ----------------------------------------------------------------------------- wall families (Studio > wall board)
# A wall family is the floor family idea for walls: one flat front-view ELEVATION of the wall (the master: 3 units
# tall, one tile wide, repeating sideways every tile, because the game drops runs anywhere and each run starts its
# texture at its own edge), wall variations N tiles wide for runs of N cells (repainted from the master tiled N
# times, keeping its edges, like floor blocks), and doors repainted into a one-tile slice of it (only the 2-unit opening changes, so a door keeps the wall's own stone and colours). "Build"
# projects the approved elevations onto the exact thin-wall geometry (runs of 1-4 cells, the post, the doorway): every
# wall piece shares one texture, so joins line up and the palette carries over between walls and doors. The
# projections are ordinary candidates of ordinary wall pieces, so cutting, export and Unity are unchanged.
# Elevation pieces have kind "elevation": they are sources, never exported or drawn themselves.

WALL_UNITS, DOOR_UNITS, DOOR_WIDTH = 3.0, 2.0, 0.5   # GDD 5.1 scale: walls 3 units, doors 2 (hero ~1.5)
DOOR_DENOISE = 0.8                                    # doors repaint a pre-filled opening (see gen_door)
ELEV_PERIOD = 1                                       # the master repeats every tile (runs start anywhere)
ELEV_METHODS = ("wall_master", "wall_repaint", "door_repaint")


def elev_size(h=WALL_UNITS, tiles=ELEV_PERIOD, per=MASTER_PX):
    """(w, h) px of an elevation: `per` px per tile along the wall, true proportions up it (1 unit = iso._KZ tiles)."""
    return tiles * per, int(round(h * iso._KZ * per / 16)) * 16


def project_elevation(piece, tile, elev, period=ELEV_PERIOD, h=None):
    """A thin-wall piece (run / post / doorway) at SCALE, textured with `elev` (front view, `period` tiles wide):
    the front faces sample it by (x along the wall, z up it), the end faces continue around the corner, the top cap
    takes the elevation's top rows. Face shading as the geometry templates. -> (RGB image, mask)."""
    h = h or float(piece.get("height", WALL_UNITS))
    L = 1 if piece["shape"] == "post" else int(piece.get("length", 1))
    W, Hc = iso.cell_canvas(tile, h, SCALE)
    size = iso.run_canvas(tile, h, L, SCALE)
    face, own, X, Y, Z = iso.render_boxes(thin_boxes(piece, L, h), size, W, Hc, xyz=True)
    E = np.asarray(elev.convert("RGB")).astype(np.float32)
    eh, ew = E.shape[:2]
    per = ew / period
    t = float(piece.get("thickness", 0.25))
    u = np.where(face == 3, X + (Y - (0.5 - t / 2)), X)            # end faces: wrap the texture round the corner
    col = np.clip(((u % period) * per).astype(int), 0, ew - 1)
    row = np.clip(((h - Z) / h * eh).astype(int), 0, eh - 1)
    cap = np.clip(((Y - (0.5 - t / 2)) / t * eh * 0.03).astype(int), 0, eh - 1)  # top: the elevation's top rows
    row = np.where(face == 1, cap, row)
    out = np.full(face.shape + (3,), 255, np.float32)
    m = face > 0
    out[m] = E[row[m], col[m]]
    shade = np.ones(face.shape, np.float32)
    shade[face == 3], shade[face == 1] = 0.72, 1.12
    out[m] *= shade[m][:, None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), m


def wall_texture_prompt(st, text):
    return (f"{style_text(st, ('medium', 'style', 'rendering', 'biome'))}, flat front view of a dungeon wall, "
            f"orthographic elevation, {text}, the wall fills the whole image from its base at the bottom edge to a "
            f"stone cap at the top edge, no floor, no ceiling, no perspective, seamless horizontally, the pattern "
            f"continues past the left and right edges, flat even lighting")


def door_texture_prompt(st, material, text):
    return (f"{style_text(st, ('medium', 'style', 'rendering', 'biome'))}, flat front view of a dungeon wall of "
            f"{material}, orthographic elevation, {text} set into the wall in the middle, the door's base on the "
            f"bottom edge, no perspective, flat even lighting")


def flatten_light_x(img, strength=1.0):
    """flatten_light across the wall only: removes side-to-side gradients and vignette, keeps the vertical
    shading (a wall can be darker at its base)."""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    H, W = a.shape[:2]
    lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
    col = lum.mean(0)
    u = np.arange(W) / W - 0.5
    basis = np.stack([np.ones_like(u), u, u * u], 1)
    fit = basis @ np.linalg.lstsq(basis, col, rcond=None)[0]
    gain = 1 + strength * (col.mean() / np.maximum(fit, 1.0) - 1)
    return Image.fromarray(np.clip(a * gain[None, :, None], 0, 255).astype(np.uint8))


def periodic_smooth_x(img):
    """periodic_smooth for the left/right edges only (an elevation repeats sideways, not up)."""
    a = np.asarray(img.convert("RGB")).astype(np.float64)
    H, W = a.shape[:2]
    v = np.zeros_like(a)
    v[:, 0] += a[:, -1] - a[:, 0]
    v[:, -1] += a[:, 0] - a[:, -1]
    q = np.cos(2 * np.pi * np.arange(H) / H)[:, None]
    r = np.cos(2 * np.pi * np.arange(W) / W)[None, :]
    den = 2 * q + 2 * r - 4
    den[0, 0] = 1
    s = np.real(np.fft.ifft2(np.fft.fft2(v, axes=(0, 1)) / den[..., None], axes=(0, 1)))
    return Image.fromarray(np.clip(a - s, 0, 255).astype(np.uint8))


def make_seamless_x(img, prompt, seed, band=0.10, ramp=0.06):
    """An elevation that repeats sideways: lighting flattened across, left/right edges made periodic, then the
    vertical seam repainted (rolled to the middle so FLUX sees both sides) and rolled back."""
    a = periodic_smooth_x(flatten_light_x(img))
    w = a.size[0]
    rolled = Image.fromarray(np.roll(np.asarray(a), w // 2, 1))
    xx = (np.arange(w) + 0.5) / w
    col = np.clip(1 - (np.abs(xx - 0.5) - band / 2) / ramp, 0, 1)
    mask = np.tile(col[None, :], (a.size[1], 1))
    fixed = repaint(rolled, mask, prompt, seed + 1000, denoise=0.75)
    return Image.fromarray(np.roll(np.asarray(fixed), -(w // 2), 1))


def gen_wall_master(spec, piece, seed=1, lora=0.0):
    fam = wall_family_settings(piece)
    w, h = elev_size(fam["height"])  # one tile wide, 3 units tall (512 x 1248)
    prompt = wall_texture_prompt(set_style(spec), piece["prompt"])
    gw, gh = 640, int(round(640 * h / w / 16)) * 16  # FLUX gets ~1 MP, then it's scaled to the elevation
    p = comfy.run(comfy.build_txt2img(prompt, gw, gh, seed=seed, lora_strength=lora, prefix="hdart/dun_wallm"), DUN / "tmp")[0]
    img = Image.open(p).convert("RGB").resize((w, h), Image.LANCZOS)
    return make_seamless_x(img, prompt, seed)


def elev_keep_mask(w, h, per, unit_px, side=0.08, top=0.15, bottom=0.08, ramp=0.10):
    """Repaint mask of a wall variation: keeps a band at the left/right edges (where it meets other pieces), along
    the top (the cap line) and the base, so every variation joins every other one."""
    xx = (np.arange(w) + 0.5) / per
    yy = (np.arange(h) + 0.5) / unit_px  # units from the top
    H = h / unit_px
    mx = np.clip((np.minimum(xx, w / per - xx) - side) / ramp, 0, 1)
    my = np.clip((np.minimum(yy - top, H - yy - bottom)) / (ramp * 2), 0, 1)
    return mx[None, :] * my[:, None]


def wall_master_image(name, spec, master_id):
    rel = master_rel(name, master_id)
    if not rel:
        raise ValueError(f"pick the active wall master of {master_id} first (Use as master)")
    return Image.open(set_dir(name) / rel).convert("RGB"), rel


def gen_wall_variant(name, spec, piece, seed=1, denoise=None, lora=0.0):
    """A wall variation for runs of N cells: the master tiled N times sideways, everything but the kept edges
    (left/right where it meets other pieces, the cap line and the base) repainted. Canvas <= BLOCK_MAX wide."""
    master_id = piece.get("from")
    m, _ = wall_master_image(name, spec, master_id)
    fam = wall_family_settings(piece_of(spec, master_id))
    N = atlas_n(piece)
    per = min(m.width, BLOCK_MAX // N) // 16 * 16
    m = m.resize((per, int(round(m.height * per / m.width))), Image.LANCZOS)
    base = Image.fromarray(np.tile(np.asarray(m), (1, N, 1)))
    w, h = base.size
    mask = elev_keep_mask(w, h, per, iso._KZ * per)
    out = repaint(base, mask, wall_texture_prompt(set_style(spec), piece["prompt"]), seed,
                  denoise=float(denoise) if denoise else REPAINT_DENOISE, lora=lora, reference=False)
    return lock_palette(spec, master_id, out, mask, m) if fam["palette_lock"] else out


def door_rect(fam, per, unit_px, h_px):
    """Door opening in a one-tile elevation slice: (x0, x1, y0, y1) px."""
    a, b = (0.5 - fam["door_width"] / 2) * per, (0.5 + fam["door_width"] / 2) * per
    return a, b, h_px - fam["door_height"] * unit_px, h_px


def gen_door(name, spec, piece, seed=1, denoise=None, lora=0.0):
    """A one-tile slice of the active wall master with only the door opening (plus a little frame) repainted."""
    master_id = piece.get("from")
    master = piece_of(spec, master_id)
    fam = wall_family_settings(master)
    base, _ = wall_master_image(name, spec, master_id)
    per = base.width
    unit_px = iso._KZ * per
    x0, x1, y0, y1 = door_rect(fam, per, unit_px, base.height)
    frame, ramp = 0.06 * per, 0.04 * per
    xx, yy = np.arange(per) + 0.5, np.arange(base.height) + 0.5
    mx = np.clip((np.minimum(xx - (x0 - frame), (x1 + frame) - xx)) / ramp, 0, 1)
    my = np.clip((yy - (y0 - frame)) / ramp, 0, 1)
    mask = mx[None, :] * my[:, None]
    # Pre-fill the exact opening with dark wood (from the floor up to the door height): left alone, FLUX paints a
    # nice door floating mid-wall; started from this shape at 0.8 it paints the door where the doorway geometry is.
    init = np.asarray(base).astype(np.float32).copy()
    r = np.random.default_rng(seed)
    X0, X1, Y0 = int(round(x0)), int(round(x1)), int(round(y0))
    plank = (np.sin((np.arange(X1 - X0) / max(X1 - X0, 1)) * np.pi * 8) * 10)[None, :, None]
    init[Y0:, X0:X1] = np.array([96, 64, 42], np.float32) + plank + r.normal(0, 6, (base.height - Y0, X1 - X0, 1))
    return repaint(Image.fromarray(np.clip(init, 0, 255).astype(np.uint8)), mask,
                   door_texture_prompt(set_style(spec), material_prompt(name, master), piece["prompt"]),
                   seed, denoise=float(denoise) if denoise else DOOR_DENOISE, lora=lora, reference=False)


def set_wall_boards(spec):
    """The board ids the game asks for (wall, post, door), read from the set's existing wall pieces."""
    def first(pred, default):
        p = next((p for p in spec["pieces"] if pred(p) and not p.get("built_from")), None)
        return p.get("board", p["id"]) if p else default
    return {"wall": first(lambda p: p.get("shape") == "run" and not p.get("opening") and int(p.get("length", 1)) == 1, "tile.crypt_wall"),
            "post": first(lambda p: p.get("shape") == "post", "tile.crypt_wall.post"),
            "door": first(lambda p: p.get("opening") == "door", "tile.crypt_door")}


def wall_family_settings(master, spec=None):
    f = family_settings(master)
    f["doors"] = [v if isinstance(v, dict) else {"id": f"d{i + 1}", "text": v}
                  for i, v in enumerate((master.get("family") or {}).get("doors",
                      ["a closed arched wooden door with iron bands", "a heavy riveted black iron door",
                       "a rusty iron portcullis grate", "closed double wooden doors with carved panels"]))]
    f["height"] = float(f.get("height", WALL_UNITS))
    f["door_height"] = float(f.get("door_height", DOOR_UNITS))
    f["door_width"] = float(f.get("door_width", DOOR_WIDTH))
    f["boards"] = {**(set_wall_boards(spec) if spec else {"wall": "tile.crypt_wall", "post": "tile.crypt_wall.post", "door": "tile.crypt_door"}),
                   **(master.get("family") or {}).get("boards", {})}
    return f


def wall_family_pieces(spec, master_id):
    """{"variant": elevation piece, "door": elevation piece, "run1".."run4", "post", "doorway": wall pieces}."""
    out = {}
    for p in spec["pieces"]:
        if p.get("from") != master_id or p.get("retired"):
            continue
        if p.get("method") == "wall_repaint":
            out.setdefault(f"var{atlas_n(p)}", p)
        elif p.get("method") == "door_repaint":
            out.setdefault("door", p)
        elif p.get("built_from") == master_id or p.get("from") == master_id and p.get("shape") in THIN:
            key = "post" if p["shape"] == "post" else "doorway" if p.get("opening") == "door" else f"run{int(p.get('length', 1))}"
            out.setdefault(key, p)
    return out


def ensure_wall_pieces(name, master_id):
    """The family's elevation pieces (variant, door) and wall pieces (runs 1-4, post, doorway), created on first use
    at the family's heights. Returns wall_family_pieces."""
    with LOCK:
        spec = load_set(name)
        master = piece_of(spec, master_id)
        fam = wall_family_settings(master, spec)
        have = wall_family_pieces(spec, master_id)
        base = {"from": master_id, "variants": 1}
        want = {
            **{f"var{L}": {"id": f"{master_id}_var{L}", "kind": "elevation", "shape": "elevation", "method": "wall_repaint", "atlas": L,
                           "prompt": master.get("prompt", "")} for L in (1, 2, 3, 4)},
            "door": {"id": f"{master_id}_doors", "kind": "elevation", "shape": "elevation", "method": "door_repaint", "prompt": fam["doors"][0]["text"] if fam["doors"] else "a wooden door"},
            "post": {"id": f"{master_id}_post", "kind": "tile", "shape": "post", "height": fam["height"], "board": fam["boards"]["post"], "built_from": master_id},
            "doorway": {"id": f"{master_id}_doorway", "kind": "tile", "shape": "run", "opening": "door", "length": 1, "height": fam["height"],
                        "door_height": fam["door_height"], "door_width": fam["door_width"], "thickness": 0.25, "board": fam["boards"]["door"], "built_from": master_id},
        }
        for L in (1, 2, 3, 4):
            want[f"run{L}"] = {"id": f"{master_id}_run{L}", "kind": "tile", "shape": "run", "length": L, "height": fam["height"],
                               "thickness": 0.25, "board": fam["boards"]["wall"], "built_from": master_id}
        for key, piece in want.items():
            if key not in have:
                upsert_piece(name, {**base, **piece})
        return wall_family_pieces(load_set(name), master_id)


def build_walls(name, master_id):
    """Project the active wall master + approved wall variations onto runs 1-4 (and the master onto the post), and
    every approved door onto the doorway. Earlier builds are archived; the new projections become the approved
    candidates of those pieces. Returns {piece: number of projections}."""
    pcs = ensure_wall_pieces(name, master_id)
    spec = load_set(name)
    fam = wall_family_settings(piece_of(spec, master_id), spec)
    boards = set(fam["boards"].values())
    with LOCK:  # older wall pieces on the same boards would ship next to the new walls: retire them (reversible)
        sp = load_set(name)
        retired = [p["id"] for p in sp["pieces"] if p.get("kind") == "tile" and p.get("shape") in THIN
                   and not p.get("built_from") and not p.get("retired") and p.get("board", p["id"]) in boards]
        for p in sp["pieces"]:
            if p["id"] in retired:
                p["retired"] = True
        if retired:
            save_set(name, sp)
    spec = load_set(name)
    s = state(name)["pieces"]
    master, mrel = wall_master_image(name, spec, master_id)
    load = lambda r: Image.open(set_dir(name) / r).convert("RGB")
    doors = [(r, load(r), 1) for r in s.get(pcs["door"]["id"], {}).get("approved", [])]
    jobs = {}
    for L in (1, 2, 3, 4):  # a run of L cells: the master repeated L times, plus every approved L-wide variation
        jobs[f"run{L}"] = [(mrel, master, 1)] + [(r, load(r), L) for r in s.get(pcs[f"var{L}"]["id"], {}).get("approved", [])
                                                if s[pcs[f"var{L}"]["id"]].get("meta", {}).get(r, {}).get("master") == mrel]
    jobs["post"] = [(mrel, master, 1)]
    jobs["doorway"] = doors
    out = {}
    for key, items in jobs.items():
        piece = resolved(load_set(name), pcs[key]["id"])
        old = state(name)["pieces"].get(piece["id"], {})
        made = []
        for src_rel, elev, period in items:
            img, m = project_elevation(piece, spec["tile"], elev, period)
            f = set_dir(name) / "candidates" / piece["id"] / f"b_{pathlib_stem(src_rel)}_{time.time_ns() // 1000000}.png"
            f.parent.mkdir(parents=True, exist_ok=True)
            img.save(f)
            np.save(f.with_suffix(".npy"), m)
            made.append(str(f.relative_to(set_dir(name))).replace("\\", "/"))
        with LOCK:
            st = state(name)
            ps = st["pieces"].setdefault(piece["id"], {"candidates": [], "approved": []})
            for r in list(ps.get("candidates", [])):  # earlier builds are derived: archive them
                if ps.get("meta", {}).get(r, {}).get("built_from"):
                    _archive_candidate(name, r)
                    _drop(ps, {r})
            ps["candidates"] = made + ps["candidates"]
            ps["approved"] = list(made)
            for r, (src_rel, _, _) in zip(made, items):
                ps.setdefault("meta", {})[r] = {"built_from": src_rel, "master": mrel}
            save_state(name, st)
        out[piece["id"]] = len(made)
    return {"built": out, "retired": retired}


def pathlib_stem(rel):
    return Path(rel).stem


# ----------------------------------------------------------------------------- floor families (Studio > family board)
# A family = one master piece (method "master") + one repaint piece per block size, made from its approved candidate.
# Settings live on the master piece as `family`: prompt versions for the master shootout, variation phrases, target
# counts and planner weights per size. Starred master candidates and variation trials live in the master's state.

FAMILY_SIZES = (1, 2, 3, 4)
DEFAULT_WEIGHT = 70  # % of anchors where a block size that fits is used (FloorBlocks; 70 = the old 30% skip)
DEFAULT_VARIATIONS = ["one large crack running across", "moss growing in the joints", "a few sunken and chipped stones",
                      "a shallow puddle", "scattered dust and small rubble", "a worn, smoother patch"]


def family_settings(master):
    f = dict(master.get("family") or {})
    f.setdefault("prompts", [master.get("prompt", "")])
    f.setdefault("variations", list(DEFAULT_VARIATIONS))
    # entries {id, text}: the id tags the candidates made from it (meta.variation), so renaming keeps them together
    f["variations"] = [v if isinstance(v, dict) else {"id": f"v{i + 1}", "text": v} for i, v in enumerate(f["variations"])]
    f["sizes"] = [int(n) for n in f.get("sizes", FAMILY_SIZES)]
    f["palette_lock"] = bool(f.get("palette_lock", True))
    f["targets"] = {str(n): int(v) for n, v in {"1": 3, "2": 2, "3": 1, "4": 1, **f.get("targets", {})}.items()}
    f["weights"] = {str(n): int(v) for n, v in {"2": DEFAULT_WEIGHT, "3": DEFAULT_WEIGHT, "4": DEFAULT_WEIGHT,
                                                **f.get("weights", {})}.items()}
    return f


def family_pieces(spec, master_id):
    """{N: repaint piece} made from this master (the first per size)."""
    out = {}
    for p in spec["pieces"]:
        if p.get("method") == "repaint" and p.get("from") == master_id and not p.get("retired"):
            out.setdefault(atlas_n(p), p)
    return out


def master_rel(name, master_id):
    appr = state(name)["pieces"].get(master_id, {}).get("approved", [])
    return appr[0] if appr else None


def material_prompt(name, master):
    """What the family is made of: the prompt of the active master candidate, else the master piece's prompt."""
    rel = master_rel(name, master["id"])
    meta = state(name)["pieces"].get(master["id"], {}).get("meta", {}).get(rel or "", {})
    return meta.get("prompt") or master.get("prompt", "")


def variant_prompt(material, variation):
    return f"{material}, {variation}" if variation.strip() else material


def ensure_variant_piece(name, master_id, N):
    """The family's repaint piece for NxN blocks (created on first use). Returns its id."""
    with LOCK:
        spec = load_set(name)
        have = family_pieces(spec, master_id)
        if N in have:
            return have[N]["id"]
        master = piece_of(spec, master_id)
        pid = f"{master_id}_v{N}"
        target = family_settings(master)["targets"].get(str(N), 1)
        upsert_piece(name, {"id": pid, "kind": "tile", "shape": "floor", "method": "repaint", "from": master_id,
                            "atlas": N, "board": master.get("board", master_id), "variants": max(1, target),
                            "prompt": master.get("prompt", "")})
        return pid


def star(name, pid, rel, on=True):
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        st = ps.setdefault("starred", [])
        if on and rel not in st:
            st.append(rel)
        if not on and rel in st:
            st.remove(rel)
        save_state(name, s)
        return ps


def promote(name, pid, rel):
    """Make one candidate the family's active master (the only approved one)."""
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        ps["approved"] = [rel]
        save_state(name, s)
        return ps


def trial(name, master_id, rel, variation, seed=1):
    """A quick 1x1 variation repainted from one master candidate (starred or not), to see how a master takes
    variations before choosing it. Saved under candidates/<master>/trials/, listed in the master's state."""
    spec = load_set(name)
    master = piece_of(spec, master_id)
    meta = state(name)["pieces"].get(master_id, {}).get("meta", {}).get(rel, {})
    material = meta.get("prompt") or master.get("prompt", "")
    base = Image.open(set_dir(name) / rel).convert("RGB").resize((MASTER_PX, MASTER_PX), Image.LANCZOS)
    weight = block_mask(1, MASTER_PX, 0.08)
    img = repaint(base, weight, floor_texture_prompt(set_style(spec), variant_prompt(material, variation)),
                  seed, denoise=REPAINT_DENOISE, reference=False)
    img = lock_palette(spec, master_id, img, weight, base)
    out = set_dir(name) / "candidates" / master_id / "trials"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"t{seed}_{int(time.time())}.png"
    img.save(f)
    trel = str(f.relative_to(set_dir(name))).replace("\\", "/")
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(master_id, {"candidates": [], "approved": []})
        ps.setdefault("trials", {}).setdefault(rel, []).insert(0, {"rel": trel, "variation": variation})
        save_state(name, s)
    return trel


def fresh_approved(name, master_id, pid):
    """Approved variants of a family piece that were made from the active master (others are stale: their edges
    are the old master's, or unknown for variants made before this was recorded)."""
    active, ps = master_rel(name, master_id), state(name)["pieces"].get(pid, {})
    return [r for r in ps.get("approved", []) if ps.get("meta", {}).get(r, {}).get("master") == active]


def variant_jobs(name, master_id, var_ids=None, sizes=None, n=1, doors=False):
    """Generation jobs for the chosen variations (default all) at the chosen sizes (default the family's enabled
    sizes), n candidates each: prompt = the active master's prompt + the variation text, tagged with its id.
    Wall families: sizes are run lengths (1-4); `doors` = the door rows instead (prompt = the door text)."""
    import random
    spec = load_set(name)
    master = piece_of(spec, master_id)
    if not master_rel(name, master_id):
        raise ValueError(f"pick the active master of {master_id} first (Use as master)")
    wall = master.get("method") == "wall_master"
    fam = wall_family_settings(master, spec) if wall else family_settings(master)
    material = material_prompt(name, master)
    jobs = []
    if doors:
        pid = ensure_wall_pieces(name, master_id)["door"]["id"]
        for v in [v for v in fam["doors"] if (var_ids is None or v["id"] in var_ids) and v["text"].strip()]:
            for _ in range(max(1, int(n))):
                jobs.append({"set": name, "piece": pid, "n": 1, "seed": random.randint(1, 99999), "prompt": v["text"],
                             "variation": v["id"]})
        return jobs
    vs = [v for v in fam["variations"] if (var_ids is None or v["id"] in var_ids) and v["text"].strip()]
    pcs = ensure_wall_pieces(name, master_id) if wall else None
    for N in sizes or fam["sizes"]:
        pid = pcs[f"var{int(N)}"]["id"] if wall else ensure_variant_piece(name, master_id, int(N))
        for v in vs:
            for _ in range(max(1, int(n))):
                jobs.append({"set": name, "piece": pid, "n": 1, "seed": random.randint(1, 99999),
                             "prompt": variant_prompt(material, v["text"]), "variation": v["id"]})
    return jobs


def family_palette(name, master_id):
    """Colour of every image in the family (interior mean) and its distance from the active master (CIE76 ΔE: ~2 is
    barely visible, 5+ obvious, 10+ a different colour), so drifting variants stand out."""
    spec = load_set(name)
    mrel = master_rel(name, master_id)
    if not mrel:
        return {"master": None, "items": []}
    to_rgb = lambda lab: "#%02x%02x%02x" % tuple(int(v) for v in _from_lab(np.asarray(lab, np.float32)[None])[0])
    mm = image_lab_stats(name, mrel)[0]
    s = state(name)["pieces"]
    items = []
    mp = piece_of(spec, master_id)
    groups = ({int(k[3:]): p for k, p in wall_family_pieces(spec, master_id).items() if k.startswith("var")}
              if mp.get("method") == "wall_master" else family_pieces(spec, master_id))
    for N, p in groups.items():
        ps = s.get(p["id"], {})
        for rel in ps.get("candidates", []):
            with Image.open(set_dir(name) / rel) as im:
                size = im.size
            weight = (elev_keep_mask(size[0], size[1], size[0] // N, iso._KZ * size[0] // N)
                      if p.get("method") == "wall_repaint" else family_weight(p, size[0]))
            mean = image_lab_stats(name, rel, weight)[0]
            meta = ps.get("meta", {}).get(rel, {})
            items.append({"piece": p["id"], "rel": rel, "n": N, "rgb": to_rgb(mean), "dE": round(float(np.linalg.norm(mean - mm)), 1),
                          "approved": rel in ps.get("approved", []), "variation": meta.get("variation"),
                          "stale": meta.get("master") != mrel})
    items.sort(key=lambda it: -it["dE"])
    return {"master": {"rel": mrel, "rgb": to_rgb(mm)}, "items": items}


def family_info(name, spec, master):
    """What the family board needs about a family: kind, settings, its pieces by size, material, built state."""
    if master.get("method") == "wall_master":
        pcs = wall_family_pieces(spec, master["id"])
        built = state(name)["pieces"].get(pcs["run1"]["id"], {}).get("approved") if "run1" in pcs else None
        return {"kind": "wall", "settings": wall_family_settings(master, spec), "material": material_prompt(name, master),
                "pieces": {int(k[3:]): p["id"] for k, p in pcs.items() if k.startswith("var")},
                "door": pcs["door"]["id"] if "door" in pcs else None,
                "built": [pcs[k]["id"] for k in pcs if k.startswith("run") or k in ("post", "doorway")] if built else []}
    return {"kind": "floor", "settings": family_settings(master), "material": material_prompt(name, master),
            "pieces": {n: q["id"] for n, q in family_pieces(spec, master["id"]).items()}}


def family_layout(name, master_id, seed=0, W=16, H=12):
    """A planner-laid floor for the live preview: each cell's sprite (piece, candidate, cut suffix) at the set's
    output resolution, from the approved family (the same pieces, weights and rules as the game)."""
    spec = load_set(name)
    master = piece_of(spec, master_id)
    s = state(name)["pieces"]
    tiles = {}
    rel = master_rel(name, master_id)
    if rel:
        tiles[1] = [(master_id, rel)]
    for N, p in family_pieces(spec, master_id).items():
        tiles.setdefault(N, []).extend((p["id"], r) for r in s.get(p["id"], {}).get("approved", []))
    if not tiles:
        raise ValueError("no active master yet")
    holes = {(W // 3, H // 3), (2 * W // 3, H // 3), (W // 3, 2 * H // 3), (2 * W // 3, 2 * H // 3)}
    cells_ = [(x, y) for y in range(H) for x in range(W) if (x, y) not in holes]
    weights = {int(k): v for k, v in family_settings(master)["weights"].items()}
    plan = plan_floor_blocks(cells_, sorted(tiles), (_fnv_str(master.get("board", master_id)) + seed) & 0xFFFFFFFF, weights)
    out = []
    for (x, y), (bx, by, n) in sorted(plan.items(), key=lambda kv: (kv[0][0] + kv[0][1], kv[0][0])):
        pid, r = tiles[n][_fnv((bx, by, seed)) % len(tiles[n])]
        out.append({"x": x, "y": y, "bx": bx, "by": by, "n": n, "piece": pid, "rel": r,
                    "suf": f"i{x - bx}j{y - by}" if n > 1 else ""})
    return {"tile": output_tile(spec), "pixel": bool(spec.get("pixel")), "W": W, "H": H,
            "holes": sorted(holes), "cells": out}


def cell_sprites(name, pid, rel, tile=None, spec=None):
    """A candidate's cut sprites {suffix: RGBA} (what the export ships), cached across requests: the live previews
    and the room preview ask for the same ones over and over."""
    spec = spec or load_set(name)
    tile = tile or output_tile(spec)
    key = (name, pid, rel, tile, spec.get("pixel"), spec.get("harmonize"))
    if key not in _CELL_CACHE:
        if len(_CELL_CACHE) > 160:
            _CELL_CACHE.clear()
        _CELL_CACHE[key] = {k: v[0] for k, v in cells(name, pid, rel, tile).items()}
    return _CELL_CACHE[key]


def cell_png(name, pid, rel, suf, tile=None):
    return png_bytes(cell_sprites(name, pid, rel, tile)[suf])


def family_tiles(name, spec, master_id):
    """{N: [top-down squares]}: the active master and the approved variants of each size."""
    s = state(name)["pieces"]
    rel = master_rel(name, master_id)
    out = {1: [Image.open(set_dir(name) / rel).convert("RGB")]} if rel else {}
    for N, p in family_pieces(spec, master_id).items():
        for r in s.get(p["id"], {}).get("approved", []):
            out.setdefault(N, []).append(Image.open(set_dir(name) / r).convert("RGB"))
    return out


def family_preview(name, master_id, seed=0, outline=False, view="iso", W=14, H=10, per=64):
    """A W x H floor laid out by the planner from the family (same rules and weights as the game), top-down or iso.
    `outline` draws the blocks (white 1x1, blue 2x2, yellow 3x3, red 4x4)."""
    from PIL import ImageDraw
    spec = load_set(name)
    master = piece_of(spec, master_id)
    tiles = family_tiles(name, spec, master_id)
    if not tiles:
        raise ValueError("no active master yet")
    weights = {int(k): v for k, v in family_settings(master)["weights"].items()}
    holes = {(W // 3, H // 3), (2 * W // 3, H // 3), (W // 3, 2 * H // 3), (2 * W // 3, 2 * H // 3)}
    cells = [(x, y) for y in range(H) for x in range(W) if (x, y) not in holes]
    plan = plan_floor_blocks(cells, sorted(tiles), _fnv_str(master.get("board", master_id)) + seed, weights)
    top = Image.new("RGB", (W * per, H * per), (24, 20, 30))
    blocks = sorted(set(plan.values()))
    for bx, by, n in blocks:
        imgs = tiles[n]
        top.paste(imgs[_fnv((bx, by, seed)) % len(imgs)].resize((n * per, n * per), Image.LANCZOS), (bx * per, by * per))
    if outline:
        dr = ImageDraw.Draw(top)
        colour = {1: (255, 255, 255), 2: (90, 200, 255), 3: (255, 200, 60), 4: (255, 90, 120)}
        for bx, by, n in blocks:
            dr.rectangle([bx * per + 1, by * per + 1, (bx + n) * per - 2, (by + n) * per - 2],
                         outline=colour.get(n, (200, 200, 200)), width=3 if n > 1 else 1)
    if view != "iso":
        return top
    a = per // 2  # iso: the top-down squares warped onto the 2:1 diamond grid (what cutting does per cell)
    X0, k = H * a, per / a
    return top.transform(((W + H) * a, (W + H) * a // 2), Image.AFFINE,
                         (k / 2, k, -X0 * k / 2, -k / 2, k, X0 * k / 2), Image.BICUBIC, fillcolor=(24, 20, 30))


# ----------------------------------------------------------------------------- floor blocks (mixed atlas sizes)
# Same rules and hash as the game's FloorBlocks.cs (Presentation/Board), so the room preview matches the board.

FLOOR_SKIP_PERCENT = 30


def _fnv(vals):
    """FNV-1a over ints as little-endian uint32 (FloorBlocks.Hash(int x, int y, uint seed))."""
    h = 2166136261
    for v in vals:
        for k in range(4):
            h = ((h ^ ((v & 0xFFFFFFFF) >> (8 * k) & 0xFF)) * 16777619) & 0xFFFFFFFF
    return h


def _fnv_str(s):
    """FNV-1a over UTF-16 code units (FloorBlocks.Hash(string))."""
    h = 2166136261
    for c in s:
        h = ((h ^ ord(c)) * 16777619) & 0xFFFFFFFF
    return h


def plan_floor_blocks(cells, sizes, seed, weights=None):
    """{(x, y): (block x, block y, size)}: square blocks of the given sizes (plus 1x1) over the cells. Greedy in
    scan order, largest size first; at each anchor a size that fits is used with `weights[size]` % (default 70)."""
    floor = set(cells)
    big = sorted({s for s in sizes if s > 1}, reverse=True)
    plan = {}
    for x, y in sorted(floor, key=lambda c: (c[1], c[0])):
        if (x, y) in plan:
            continue
        size = 1
        for s in big:
            if (_fnv((x, y, (seed + s) & 0xFFFFFFFF)) % 100 >= 100 - (weights or {}).get(s, 100 - FLOOR_SKIP_PERCENT)
                    and all((x + dx, y + dy) in floor and (x + dx, y + dy) not in plan
                            for dy in range(s) for dx in range(s))):
                size = s
                break
        for dy in range(size):
            for dx in range(size):
                plan[(x + dx, y + dy)] = (x, y, size)
    return plan


def room_grid(W=14, H=10):
    """A preview room: back walls along the top row and left column, each with a door, a free-standing wall
    across the far side (both faces and post ends show), floor everywhere else."""
    g = [["." for _ in range(W)] for _ in range(H)]
    for x in range(W):
        g[0][x] = "W"
    for y in range(H):
        g[y][0] = "W"
    g[0][W // 2], g[H // 2][0] = "D", "D"
    for x in range(W // 2, W - 1):
        g[2 * H // 3][x] = "W"
    return ["".join(r) for r in g]


def room_preview(name, tile=None, scale=3, include=None, grid=None, floor_board=None, as_layout=False):
    """Compose approved pieces (native pixels) into a small iso room with thin, joined-up walls. `include` =
    {pid: rel} swaps in one candidate for a piece, to judge it in context before approving. `grid` replaces the
    mock room (see room_grid), `floor_board` picks the main floor. `as_layout` returns the draw list instead of an
    image (the Studio's live preview draws it): {w, h, ox, oy, tile, items: [{piece, rel, suf, x, y}], blocks}."""
    spec = load_set(name)
    tile = tile or output_tile(spec)
    s = state(name)
    approved = {pid: list(ps.get("approved", [])) for pid, ps in s["pieces"].items()}
    for pid, rel in (include or {}).items():
        approved[pid] = [rel]
    roles, by_key = _roles(spec, approved)
    if floor_board:
        roles["."] = roles["R"] = roles["X"] = floor_board
    cache = {}

    def variants(key):
        return by_key.get(key, []) if key else []

    def sprite(p, rel, suf):
        if (p["id"], rel) not in cache:
            cache[(p["id"], rel)] = cell_sprites(name, p["id"], rel, tile, spec)
        im = cache[(p["id"], rel)].get(suf)
        piv = [0.5, 0.1] if p["kind"] == "prop" else [0.5, (tile / 4) / im.height] if im else None
        return im, piv

    def pick(key, x, y, pred=lambda p: True):
        vs = [v for v in variants(key) if pred(v[0])]
        return vs[(x * 7 + y * 13) % len(vs)] if vs else None

    grid = grid or ROOM
    rows, cols = len(grid), len(grid[0])
    at = lambda x, y: grid[y][x] if 0 <= x < cols and 0 <= y < rows else " "
    wallish = {(x, y) for y in range(rows) for x in range(cols) if at(x, y) in WALLISH}
    nb = {c: {"+x": (c[0] + 1, c[1]) in wallish, "-x": (c[0] - 1, c[1]) in wallish,
              "+y": (c[0], c[1] + 1) in wallish, "-y": (c[0], c[1] - 1) in wallish} for c in wallish}
    run_lengths = [v[0].get("length", 1) for v in variants(roles["W"])]
    xs = _runs([c for c in wallish if nb[c]["+x"] or nb[c]["-x"]], True, run_lengths)
    ys = _runs([c for c in wallish if nb[c]["+y"] or nb[c]["-y"]], False, run_lengths)

    W = int((rows + cols) * tile / 2 + tile)
    H = int((rows + cols) * tile / 4 + tile * 2.5)
    ox, oy = rows * tile / 2 + tile / 2, tile * 2.0
    canvas = Image.new("RGBA", (W, H), (22, 18, 28, 255))
    items = []  # (layer, depth, sub, x, y, piece, rel, suffix)

    def add(layer, x, y, sub, pv, suf):
        if pv:
            items.append((layer, x + y, sub, x, y, pv[0], pv[1], suf))

    def floor_key(x, y):
        """The floor piece key under a cell, or None (thin walls only need floor on the side the camera sees)."""
        ch = at(x, y)
        if ch in WALLISH and ch not in "DA" and not any(at(x + dx, y + dy) in ".RXP" + "abcdefghijkl"
                                                        for dx, dy in ((1, 0), (0, 1), (1, 1))):
            return None
        return roles[ch] if ch in "RX" else roles["."]

    main = roles["."]
    atlas_vs = [v for v in variants(main) if layout(v[0]) == "atlas"]
    singles = [v for v in variants(main) if layout(v[0]) != "atlas"]
    mp = next((p for p in spec["pieces"] if p.get("method") == "master" and p.get("board", p["id"]) == main), None)
    plan = plan_floor_blocks([(x, y) for y in range(rows) for x in range(cols) if main and floor_key(x, y) == main],
                             [1] + [int(v[0].get("atlas", 4)) for v in atlas_vs], _fnv_str(main or ""),
                             {int(k): v for k, v in family_settings(mp)["weights"].items()} if mp else None) if atlas_vs else {}

    for y in range(rows):
        for x in range(cols):
            ch = at(x, y)
            fkey = floor_key(x, y)
            fv = pick(fkey, x, y) if fkey else None
            if fv and (x, y) in plan:  # mixed atlas sizes: this cell's tile of its block (preview axes: i = x, j = y)
                bx, by, n = plan[(x, y)]
                same = [v for v in atlas_vs if int(v[0].get("atlas", 4)) == n]
                if n > 1:
                    add(0, x, y, 0, same[(bx * 7 + by * 13) % len(same)], f"i{x - bx}j{y - by}")
                    fv = None
                elif singles:
                    add(0, x, y, 0, singles[(x * 7 + y * 13) % len(singles)], "")
                    fv = None
            if fv:
                p = fv[0]
                if layout(p) == "atlas":
                    N = int(p.get("atlas", 4))
                    fv = [v for v in variants(fkey) if layout(v[0]) == "atlas"][0]
                    add(0, x, y, 0, fv, f"i{x % N}j{y % N}")
                else:
                    add(0, x, y, 0, fv, "")
            c = (x, y)
            if ch in WALLISH:
                n = nb[c]
                straight_x = (n["+x"] or n["-x"]) and not (n["+y"] or n["-y"])
                straight_y = (n["+y"] or n["-y"]) and not (n["+x"] or n["-x"])
                m = "" if straight_x or not straight_y else "m"
                if ch in "DA" and roles[ch]:
                    add(1, x, y, 2, pick(roles[ch], x, y), m)
                    continue
                decor = None
                if ch == "T" and roles["decor"] and (straight_x or straight_y):
                    decor = pick(roles["decor"][(x + y) % len(roles["decor"])], x, y)
                for axis, run, mark in (("x", xs, ""), ("y", ys, "m")):
                    if c not in run:
                        continue
                    L, k, start = run[c]
                    pv = decor or pick(roles["W"], (c[1] if axis == "x" else c[0]), start,
                                       lambda p, L=L: int(p.get("length", 1)) == L)
                    if decor:
                        k = 0
                    if not pv:
                        continue
                    if n[f"-{axis}"]:
                        add(1, x, y, 0, pv, f"k{k}h0{mark}")
                    if n[f"+{axis}"]:
                        add(1, x, y, 3, pv, f"k{k}h1{mark}")
                if not ((n["+x"] and n["-x"] and not (n["+y"] or n["-y"])) or
                        (n["+y"] and n["-y"] and not (n["+x"] or n["-x"]))):
                    add(1, x, y, 1, pick(roles["post"], x, y), "")
            elif ch == "P" or ch.islower():
                add(1, x, y, 1, pick(roles[ch], x, y), "")
    draws = []
    for layer, depth, sub, x, y, p, rel, suf in sorted(items, key=lambda t: (t[0], t[1], t[2])):
        im, piv = sprite(p, rel, suf)
        if im is None:
            continue
        sx, sy = ox + (x - y) * tile / 2, oy + (x + y) * tile / 4
        draws.append((p["id"], rel, suf, int(round(sx - piv[0] * im.width)), int(round(sy - (1 - piv[1]) * im.height)), im))
    if as_layout:
        return {"w": W, "h": H, "ox": ox, "oy": oy, "tile": tile, "pixel": bool(spec.get("pixel")), "W": cols, "H": rows,
                "items": [{"piece": d[0], "rel": d[1], "suf": d[2], "x": d[3], "y": d[4]} for d in draws],
                "blocks": sorted(set(plan.values())), "walls": [[x, y] for (x, y) in sorted(wallish)]}
    for d in draws:
        canvas.alpha_composite(d[5], (d[3], d[4]))
    out = canvas.convert("RGB")
    return out.resize((out.width * scale, out.height * scale), Image.NEAREST)


UNITY = Path(r"C:\Program Files\Unity\Hub\Editor\6000.3.26f1\Editor\Unity.exe")
CAPTURES = ROOT / "work" / "protov"


def capture(name, tiles=(32, 64)):
    """Export the set and run Prototype V's batch capture with it. Returns the screenshot file names."""
    import subprocess
    export(name, tiles)
    CAPTURES.mkdir(parents=True, exist_ok=True)
    log = CAPTURES / "capture.log"
    r = subprocess.run([str(UNITY), "-batchmode", "-projectPath", str(REPO), "-executeMethod",
                        "Hearthdelve.Editor.PrototypeVSetup.Capture", "-reviewOut", str(CAPTURES), "-artSet", name,
                        "-logFile", str(log), "-quit"], timeout=1800)
    if r.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:] if log.exists() else []
        raise RuntimeError(f"Unity exited {r.returncode} (is this project open in the editor?)\n" + "\n".join(tail))
    return sorted(p.name for p in CAPTURES.glob("v0*.png"))
