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
from .locks import DUNGEON as LOCK, HARMONY as _REF_CACHE
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
    if piece.get("method") == "atlas":
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


def generate(name, pid, n=3, seed=1, denoise=None, prompt=None, lora=None):
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
    if piece["kind"] == "prop":
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


def harmonize(name, spec, piece, img, mask):
    """`img` with its colours pulled toward the family reference by the set's `harmonize` strength."""
    k = float(spec.get("harmonize") or 0)
    fam = family(piece)
    if k <= 0 or fam is None:
        return img
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
        if p0.get("retired"):  # kept for reference (older geometry), not shipped
            continue
        piece = resolved(spec, p0["id"])
        approved = s["pieces"].get(piece["id"], {}).get("approved", [])
        if not approved:
            continue
        key = piece.get("board", piece["id"])  # the id the game board asks for
        of = piece.get("of")
        of = piece_of(spec, of).get("board", of) if of and any(p["id"] == of for p in spec["pieces"]) else of
        entry = meta["pieces"].setdefault(key, {"kind": piece["kind"], "of": of, "variants": []})
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


def plan_floor_blocks(cells, sizes, seed):
    """{(x, y): (block x, block y, size)}: square blocks of the given sizes (plus 1x1) over the cells. Greedy in
    scan order, largest size first, skipping a size at ~30% of anchors for variety."""
    floor = set(cells)
    big = sorted({s for s in sizes if s > 1}, reverse=True)
    plan = {}
    for x, y in sorted(floor, key=lambda c: (c[1], c[0])):
        if (x, y) in plan:
            continue
        size = 1
        for s in big:
            if (_fnv((x, y, (seed + s) & 0xFFFFFFFF)) % 100 >= FLOOR_SKIP_PERCENT
                    and all((x + dx, y + dy) in floor and (x + dx, y + dy) not in plan
                            for dy in range(s) for dx in range(s))):
                size = s
                break
        for dy in range(size):
            for dx in range(size):
                plan[(x + dx, y + dy)] = (x, y, size)
    return plan


def room_preview(name, tile=None, scale=3, include=None):
    """Compose approved pieces (native pixels) into a small iso room with thin, joined-up walls. `include` =
    {pid: rel} swaps in one candidate for a piece, to judge it in context before approving."""
    spec = load_set(name)
    tile = tile or output_tile(spec)
    s = state(name)
    approved = {pid: list(ps.get("approved", [])) for pid, ps in s["pieces"].items()}
    for pid, rel in (include or {}).items():
        approved[pid] = [rel]
    roles, by_key = _roles(spec, approved)
    cache = {}

    def variants(key):
        return by_key.get(key, []) if key else []

    def sprite(p, rel, suf):
        if (p["id"], rel) not in cache:
            cache[(p["id"], rel)] = {k: v[0] for k, v in cells(name, p["id"], rel, tile).items()}
        im = cache[(p["id"], rel)].get(suf)
        piv = [0.5, 0.1] if p["kind"] == "prop" else [0.5, (tile / 4) / im.height] if im else None
        return im, piv

    def pick(key, x, y, pred=lambda p: True):
        vs = [v for v in variants(key) if pred(v[0])]
        return vs[(x * 7 + y * 13) % len(vs)] if vs else None

    rows, cols = len(ROOM), len(ROOM[0])
    at = lambda x, y: ROOM[y][x] if 0 <= x < cols and 0 <= y < rows else " "
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
    plan = plan_floor_blocks([(x, y) for y in range(rows) for x in range(cols) if main and floor_key(x, y) == main],
                             [1] + [int(v[0].get("atlas", 4)) for v in atlas_vs], _fnv_str(main or "")) if atlas_vs else {}

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
    for layer, depth, sub, x, y, p, rel, suf in sorted(items, key=lambda t: (t[0], t[1], t[2])):
        im, piv = sprite(p, rel, suf)
        if im is None:
            continue
        sx, sy = ox + (x - y) * tile / 2, oy + (x + y) * tile / 4
        canvas.alpha_composite(im, (int(round(sx - piv[0] * im.width)), int(round(sy - (1 - piv[1]) * im.height))))
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
