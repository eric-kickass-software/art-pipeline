"""Dungeon Forge generation: everything that paints (FLUX through hdart.core.comfy) or reworks pixels.

Tiles  - template-guided: a flat-shaded geometry template (hdart.tools.dungeon.geometry) is drawn at 8x the native
         size; FLUX paints over it with img2img and the result is clamped to the template outline, so every tile lines
         up and tiles seamlessly.
Props  - clean prompt -> single object on white -> cut out -> fitted to a footprint box -> downsampled.
Floor families - one seamless master tile, repainted per block size (master tile + repainted variants).
Wall families  - one flat front-view elevation (the wall master), repainted variants and doors, built into wall pieces.
The set material sheet, recipes, candidate edits (repaint, rescale) and the Studio job kinds that run all of it.
"""
import json
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from hdart.core import comfy, config, jobs
from hdart.imaging import imageops
from hdart.tools.dungeon import iso, patterns
from hdart.core.locks import DUNGEON as LOCK
from hdart.tools.dungeon.geometry import SCALE, THIN, atlas_n, block_mask, geometry, template, thin_boxes
from hdart.tools.dungeon.sets import (EVEN_TONE, SHEET_DEFAULT, WALL_UNITS, archive_candidate, drop_candidate,
    new_candidate, candidate_path, dun_dir, family_settings, load_set, master_rel, piece_of, resolved, save_set,
    save_state, set_dir, set_style, sheet, sheet_template, state, style, style_text, upsert_piece, wall_family_settings)
from hdart.tools.dungeon.colour import from_lab, to_lab, family_weight, image_lab_stats, match_palette


# ----------------------------------------------------------------------------- generation

def _i2i(init, prompt, seed, denoise, lora, steps=8):
    out = comfy.img2img(init, prompt, seed=seed, denoise=denoise, lora=lora, steps=steps, prefix="hdart/dun_tile")
    return out.resize(init.size, Image.NEAREST)


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
    from hdart.imaging.imageops import enforce_silhouette  # fill anything left white inside the outline, clear outside
    return enforce_silhouette(out, mask), mask


def gen_floor(piece, tile=64, seed=1, lora=0.0, st=None):
    """Floor-type tiles: a top-down square tile from FLUX, warped exactly onto the 2:1 iso diamond (always
    fills edge to edge, and neighbouring tiles meet cleanly). A 1 px darker rim gives the slab some depth."""
    st = st or style()
    prompt = (f"{style_text(st, ('medium', 'style', 'rendering', 'biome'))}, top-down orthographic view of a single "
              f"square dungeon floor tile, {piece['prompt']}, the tile fills the entire image edge to edge, "
              f"flat even lighting")
    p = comfy.run(comfy.build_txt2img(prompt, 512, 512, seed=seed, lora_strength=lora, prefix="hdart/dun_floor"),
                  dun_dir() / "tmp")[0]
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
                  dun_dir() / "tmp")[0]
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
REPAINT_DENOISE = 0.85  # default repaint strength for variants (piece `denoise` / the editor's denoise field)


def floor_texture_prompt(st, text):
    return (f"{style_text(st, ('medium', 'style', 'rendering', 'biome'))}, seamless tileable floor texture seen "
            f"straight from above, {text}, the pattern continues past every edge of the image, no border, "
            f"no frame, no walls, flat even lighting")


def repaint(img, mask, prompt, seed, denoise=1.0, lora=0.0, reference=True):
    """Masked FLUX repaint (comfy.build_repaint). `mask` is float 0..1 (0 = keep, 1 = repaint fully). The result
    is blended back with the same mask in pixel space, so kept areas stay exactly the input (no VAE drift)."""
    out = comfy.repaint(img, np.asarray(mask, dtype=np.float32), prompt, seed=seed, denoise=denoise, lora=lora,
                        reference=reference, prefix="hdart/dun_rp")
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


def _periodic_blur(a, sigma):
    """Gaussian blur of an (H, W[, C]) array that wraps around the edges (FFT), so a seamless image stays seamless."""
    H, W = a.shape[:2]
    fy, fx = np.fft.fftfreq(H)[:, None], np.fft.fftfreq(W)[None, :]
    g = np.exp(-2 * (np.pi * sigma) ** 2 * (fx ** 2 + fy ** 2))
    if a.ndim == 3:
        g = g[..., None]
    return np.real(np.fft.ifft2(np.fft.fft2(a, axes=(0, 1)) * g, axes=(0, 1)))


def even_tone(img, strength=0.8, scale=0.08):
    """Even out stone-sized differences in colour and brightness (one dark slab, a stained patch): the things the eye
    spots repeating every tile. The colour averaged over `scale` x the tile width is pulled toward the tile's mean,
    per channel; the detail inside each stone, the joints and the edges are kept. Wraps, so it stays seamless."""
    a = np.asarray(img.convert("RGB")).astype(np.float64)
    low = _periodic_blur(a, scale * a.shape[1])
    gain = np.clip(a.reshape(-1, 3).mean(0) / np.maximum(low, 1.0), 0.5, 2.0)
    return Image.fromarray(np.clip(a * (1 + strength * (gain - 1)), 0, 255).astype(np.uint8))


def _even(piece, img):
    k = float((piece.get("family") or {}).get("even_tone", EVEN_TONE))
    return even_tone(img, k) if k > 0 else img


def finalize_draft(name, pid, rel, seed=None, lora=0.0):
    """A full master from a draft candidate: same composition, redrawn at full resolution (a light img2img, 0.4)
    and made seamless like any master (the seam repaint; floors also even tone, walls the floor colour match).
    -> new candidate rel."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    old = state(name)["pieces"].get(pid, {}).get("meta", {}).get(rel, {})
    if not old.get("draft"):
        raise ValueError("that's not a draft")
    seed = int(seed if seed is not None else old.get("seed", 1))
    piece["prompt"] = old.get("prompt", piece.get("prompt", ""))
    src = Image.open(candidate_path(name, rel)).convert("RGB")
    if piece.get("method") == "wall_master":
        prompt = wall_texture_prompt(set_style(spec), piece["prompt"])
        gw = 640
        gh = int(round(gw * src.height / src.width / 16)) * 16
        img = _i2i(src.resize((gw, gh), Image.LANCZOS), prompt, seed, 0.4, lora).resize(src.size, Image.LANCZOS)
        out = match_to_floor(name, spec, make_seamless_x(img, prompt, seed))
    else:
        prompt = floor_texture_prompt(set_style(spec), piece["prompt"])
        img = _i2i(src.resize((1024, 1024), Image.LANCZOS), prompt, seed, 0.4, lora).resize((MASTER_PX, MASTER_PX), Image.LANCZOS)
        out = _even(piece, make_seamless(img, prompt, seed, lora))
    meta = {"seed": seed, "prompt": piece["prompt"], "finalized_from": rel}
    return new_candidate(name, pid, out, meta, f"full{seed}")


def even_master(name, pid, rel, strength=EVEN_TONE):
    """A master candidate with stone-sized tone differences evened out (even_tone), as a new candidate."""
    piece = resolved(load_set(name), pid)
    if piece.get("method") != "master":
        raise ValueError("even tone works on floor master tiles")
    old = state(name)["pieces"].get(pid, {}).get("meta", {}).get(rel, {})
    out = even_tone(Image.open(candidate_path(name, rel)).convert("RGB"), float(strength))
    meta = {"seed": old.get("seed"), "prompt": old.get("prompt", piece.get("prompt", "")), "evened_from": rel, "even": float(strength)}
    return new_candidate(name, pid, out, meta, f"even{int(float(strength) * 100)}")


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


def family_template(master):
    """The family's layout template ({pattern, params, palette, seed, strength}) or None."""
    t = (master.get("family") or {}).get("template")
    return t if t and t.get("pattern") and t["pattern"] != "none" else None


def template_render(tpl, kind, per=MASTER_PX, height_units=WALL_UNITS):
    """A template as a floor square or a wall elevation: {"rgb", "height", "region"} (library image templates too)."""
    if tpl["pattern"] == "image":
        from hdart.tools.dungeon import library
        return library.image_template(tpl, kind, per, height_units * iso.KZ)
    if kind == "wall":
        return patterns.wall_template(tpl, height_units * iso.KZ, per)
    return patterns.floor_template(tpl, per)


DRAFT_PX, DRAFT_STEPS = 512, 4  # drafts: FLUX at a quarter of the pixels and half the steps, no seam repaint


def gen_master(piece, seed=1, lora=0.0, st=None, draft=False):
    """A seamless master floor tile (top-down square, MASTER_PX). With a family template: FLUX starts from the
    template (img2img at its strength), so the layout and scale are the template's and only the surface is painted.
    `draft`: a quick look (a few seconds): smaller, fewer steps, edges made periodic without the seam repaint;
    finalize_draft turns a chosen draft into a full master with the same composition."""
    st = st or style()
    prompt = floor_texture_prompt(st, piece["prompt"])
    tpl = family_template(piece)
    if draft:
        if tpl:
            init = template_render({**tpl, "seed": seed if tpl.get("reseed") else tpl.get("seed", 1)}, "floor", DRAFT_PX)["rgb"]
            src = _i2i(init, prompt, seed, float(tpl.get("strength", 0.7)), lora, steps=DRAFT_STEPS)
        else:
            p = comfy.run(comfy.build_txt2img(prompt, 576, 576, seed=seed, steps=DRAFT_STEPS, lora_strength=lora,
                                              prefix="hdart/dun_draft"), dun_dir() / "tmp")[0]
            src = Image.open(p).convert("RGB").crop((32, 32, 544, 544))
        return _even(piece, periodic_smooth(flatten_light(src.resize((MASTER_PX, MASTER_PX), Image.LANCZOS))))
    if tpl:
        init = template_render({**tpl, "seed": seed if tpl.get("reseed") else tpl.get("seed", 1)}, "floor", 1024)["rgb"]
        src = _i2i(init, prompt, seed, float(tpl.get("strength", 0.7)), lora).resize((MASTER_PX, MASTER_PX), Image.LANCZOS)
        return _even(piece, make_seamless(src, prompt, seed, lora))
    big = 1152  # FLUX likes a darker rim at the image edge: generate larger, keep the middle
    p = comfy.run(comfy.build_txt2img(prompt, big, big, seed=seed, lora_strength=lora, prefix="hdart/dun_master"),
                  dun_dir() / "tmp")[0]
    src = Image.open(p).convert("RGB")
    m = (big - 1024) // 2
    src = src.crop((m, m, m + 1024, m + 1024)).resize((MASTER_PX, MASTER_PX), Image.LANCZOS)
    return _even(piece, make_seamless(src, prompt, seed, lora))


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
    den = float(denoise) if denoise else REPAINT_DENOISE
    # No reference image: with one FLUX copies the tiled master almost exactly. Starting from it at 0.85 keeps the
    # master's stone scale but redraws the layout; 1.0 = freer (scale may drift), ~0.6 = subtle changes.
    mp = piece_of(spec, piece.get("from"))
    material = material_prompt(name, mp)
    feature = piece["prompt"][len(material):].strip(" ,") if piece["prompt"].startswith(material) else ""
    if N > 1 and feature and family_settings(mp).get("span", "once") == "once":
        # a block is one piece: its feature should happen once, not in every tile (FLUX copies the tiled master).
        # Pass 1 redraws the whole interior plainly (a new layout, so the block doesn't repeat the master); pass 2
        # paints the feature once, inside a round zone that crosses tile boundaries.
        # (tested on the Stylized floor: pass 1 at 0.85 invents shapes from style words like "moonlit"; 0.8 doesn't;
        # pass 2 under 1.0 sometimes paints nothing in the zone)
        plain = repaint(base, mask, floor_texture_prompt(st, f"{material}, {PLAIN_REROLL}"), seed, denoise=min(den, 0.8),
                        lora=lora, reference=False)
        zone = mask * floor_zone(N, per, seed)
        out = repaint(plain, zone.astype(np.float32), floor_texture_prompt(st, f"{material}, {feature}, just one, in a single place on this floor"),
                      seed + 1, denoise=1.0, lora=lora, reference=False)
    else:
        out = repaint(base, mask, floor_texture_prompt(st, piece["prompt"]), seed, denoise=den, lora=lora, reference=False)
    return lock_palette(spec, piece.get("from"), out, mask, master)


PLAIN_REROLL = "the same stones redrawn, different shapes and wear, nothing special, no shapes or patterns painted on the floor"


def floor_zone(N, per, seed, frac=0.65, ramp=0.4):
    """Repaint strength for an NxN floor block's feature: 1 in a round zone (radius frac x half the block, at least
    0.75 tiles, random centre), 0 outside."""
    rng = np.random.default_rng(seed)
    S = N * per
    yy, xx = (np.mgrid[0:S, 0:S] + 0.5) / per
    r = max(0.75, frac * N / 2)
    lo, hi = r * 0.6 + 0.2, N - r * 0.6 - 0.2
    cx, cy = rng.uniform(lo, hi, 2) if hi - lo > 0.05 else (N / 2, N / 2)
    return np.clip(1 - (np.hypot(xx - cx, yy - cy) - r) / ramp, 0, 1)


def lock_palette(spec, master_id, img, weight, master_img):
    """Palette lock (family setting, on by default): match a fresh repaint's interior to the master's colours."""
    m = next((p for p in spec["pieces"] if p["id"] == master_id), None)
    if not m or not family_settings(m).get("palette_lock", True):
        return img
    lab = to_lab(np.asarray(master_img.convert("RGB")).astype(np.float32).reshape(-1, 3))
    return match_palette(img, (lab.mean(0), lab.std(0)), weight, 1.0)


def gen_prop(piece, seed=1, lora=0.0, st=None, size=512):
    """Clean prompt -> object on white -> BiRefNet cut-out. Returns (RGB on white, mask)."""
    st = st or style()
    view = piece.get("view") or st["prop_view"]  # per-piece override, e.g. flat things lying on the ground
    # props swap the tileset medium for an object one ("tileset" makes FLUX put things on a floor slab)
    prompt = f"{st.get('prop_medium') or st['groups']['medium']['text']}, " \
             f"{style_text(st, ('style', 'rendering', 'biome') if piece.get('biome', True) else ('style', 'rendering'))}, " \
             f"{view}, {piece['prompt']}, {st['prop_framing']}"
    p = comfy.run(comfy.build_txt2img(prompt, size, size, seed=seed, lora_strength=lora, remove_bg=True,
                                      prefix="hdart/dun_prop"), dun_dir() / "tmp")[0]
    rgba = np.asarray(Image.open(p).convert("RGBA"))
    mask = imageops.clean(rgba[..., 3] > 127, min_area=300)
    rgb = rgba[..., :3].copy()
    rgb[~mask] = 255
    return Image.fromarray(rgb), mask


def generate(name, pid, n=3, seed=1, denoise=None, prompt=None, lora=None, variation=None, draft=False):
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
            img = match_to_floor(name, spec, gen_wall_master(spec, piece, sd, lora if lora is not None else 0.0, draft=draft))
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("method") == "wall_repaint":
            img = gen_wall_variant(name, spec, piece, sd, denoise or piece.get("denoise"), lora if lora is not None else 0.0)
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("method") == "door_repaint":
            img = gen_door(name, spec, piece, sd, denoise or piece.get("denoise"), lora if lora is not None else 0.0)
            mask = np.ones(img.size[::-1], bool)
        elif piece.get("method") == "master":
            img = gen_master(piece, sd, lora if lora is not None else 0.0, st=st, draft=draft)
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
        if draft and piece.get("method") in ("master", "wall_master"):
            meta[rel]["draft"] = True
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


def gen_on_base(piece, base, seed, denoise, lora, st):
    """wall_decor: img2img over an approved base wall so stone, colour and outline match the plain wall."""
    img, mask = base
    out = _i2i(img, f"{style_text(st)}, {piece['prompt']}", seed, denoise, lora)
    from hdart.imaging.imageops import enforce_silhouette
    out = np.asarray(enforce_silhouette(out, mask)).copy()
    white = mask & (out.min(-1) > 225)  # img2img bleeds the white backdrop in at the outline: use the base there
    out[white] = np.asarray(img)[white]
    return Image.fromarray(out), mask


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
            x0, x1, y0, _ = door_rect(fam, per, iso.KZ * per, img.height)
            xx, yy = np.arange(img.width) + 0.5, np.arange(img.height) + 0.5
            weight = (np.clip(np.minimum(xx - x0, x1 - xx) / (0.04 * per), 0, 1)[None, :]
                      * np.clip((yy - y0) / (0.04 * per), 0, 1)[:, None])
            full = door_texture_prompt(set_style(spec), material_prompt(name, piece_of(spec, piece["from"])), text)
        else:
            weight = elev_keep_mask(img.width, img.height, per, iso.KZ * per, top=0.0 if method == "wall_master" else 0.15,
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
        return new_candidate(name, pid, out, meta, f"e{seed}")
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
    return new_candidate(name, pid, out, meta, f"e{seed}")


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
    return new_candidate(name, pid, out, meta, f"x{factor:g}".replace(".", "_"))
                                                      # fill the cell (Eric: no side frame, just the header above)
DOOR_DENOISE = 0.8                                    # doors repaint a pre-filled opening (see gen_door)
ELEV_PERIOD = 1                                       # the master repeats every tile (runs start anywhere)
ELEV_METHODS = ("wall_master", "wall_repaint", "door_repaint")


def elev_size(h=WALL_UNITS, tiles=ELEV_PERIOD, per=MASTER_PX):
    """(w, h) px of an elevation: `per` px per tile along the wall, true proportions up it (1 unit = iso.KZ tiles)."""
    return tiles * per, int(round(h * iso.KZ * per / 16)) * 16


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


WALL_SEAMLESS = "seamless horizontally, the pattern continues past the left and right edges, "


def wall_texture_prompt(st, text):
    return (f"{style_text(st, ('medium', 'style', 'rendering', 'biome'))}, flat front view of a dungeon wall, "
            f"orthographic elevation, {text}, the wall fills the whole image from its base at the bottom edge to a "
            f"stone cap at the top edge, no floor, no ceiling, no perspective, {WALL_SEAMLESS}flat even lighting")


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


def gen_wall_master(spec, piece, seed=1, lora=0.0, draft=False):
    fam = wall_family_settings(piece)
    w, h = elev_size(fam["height"])  # one tile wide, 3 units tall (512 x 1248)
    prompt = wall_texture_prompt(set_style(spec), piece["prompt"])
    tpl = family_template(piece)
    if draft:  # see gen_master: small, few steps, edges made periodic without the seam repaint
        dw = 320
        dh = int(round(dw * h / w / 16)) * 16
        if tpl:
            init = template_render({**tpl, "seed": seed if tpl.get("reseed") else tpl.get("seed", 1)}, "wall", dw, fam["height"])["rgb"]
            img = _i2i(init.resize((dw, dh), Image.LANCZOS), prompt, seed, float(tpl.get("strength", 0.7)), lora, steps=DRAFT_STEPS)
        else:
            p = comfy.run(comfy.build_txt2img(prompt, dw, dh, seed=seed, steps=DRAFT_STEPS, lora_strength=lora,
                                              prefix="hdart/dun_wdraft"), dun_dir() / "tmp")[0]
            img = Image.open(p).convert("RGB")
        return periodic_smooth_x(flatten_light_x(img.resize((w, h), Image.LANCZOS)))
    if tpl:  # start from the layout template (img2img), like floor masters
        init = template_render({**tpl, "seed": seed if tpl.get("reseed") else tpl.get("seed", 1)}, "wall", 640, fam["height"])["rgb"]
        img = _i2i(init, prompt, seed, float(tpl.get("strength", 0.7)), lora).resize((w, h), Image.LANCZOS)
        return make_seamless_x(img, prompt, seed)
    gw, gh = 640, int(round(640 * h / w / 16)) * 16  # FLUX gets ~1 MP, then it's scaled to the elevation
    p = comfy.run(comfy.build_txt2img(prompt, gw, gh, seed=seed, lora_strength=lora, prefix="hdart/dun_wallm"), dun_dir() / "tmp")[0]
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


KEEP_TOP, KEEP_BOTTOM = 0.25, 0.10  # wall rescale: units kept as they are at the top (cap) and the base (plinth)


def _count_joints(sig, minsep, wrap=False, ref=100):
    """Joints along an edge-strength profile: peaks standing well above the typical level (median + 40% of the
    way to the top, or to the `ref` percentile), at least `minsep` apart (a bevelled joint's two edges count once). Returns their positions."""
    sig = np.convolve(np.pad(sig, 2, mode="wrap" if wrap else "edge"), np.ones(5) / 5, "valid")
    top = np.percentile(sig, ref)  # ref < 100: one very strong line (a cap's underside) can't hide the rest
    if top - np.median(sig) < 1.0:  # flat: no joints at all
        return []
    thr = np.median(sig) + 0.4 * (top - np.median(sig))
    order = np.argsort(-sig)
    picked = []
    n = len(sig)
    for i in order:
        if sig[i] < thr:
            break
        dist = lambda j: min(abs(i - j), n - abs(i - j)) if wrap else abs(i - j)
        if all(dist(j) >= minsep for j in picked):
            picked.append(int(i))
    return sorted(picked)


def measure_elevation(img, height_units=WALL_UNITS, keep_top=KEEP_TOP, keep_bottom=KEEP_BOTTOM):
    """Count the courses of a wall elevation's field (between the kept cap and base): {"rows" (courses in the field),
    "course_units" (their average height), "across" (bricks per tile, averaged over the courses)}. Joints are edges
    (whatever the tone of the bricks either side): horizontal ones from each row's vertical edge strength, then
    inside each course the vertical ones (wrapping: the elevation repeats sideways). None when nothing clear."""
    a = np.asarray(img.convert("L")).astype(np.float64)
    h, w = a.shape
    unit = h / height_units
    f = a[int(round(keep_top * unit)):h - int(round(keep_bottom * unit))]
    out = {"rows": None, "course_units": None, "across": None}
    if len(f) < 32:
        return out
    prof = np.abs(np.diff(f, axis=0, append=f[-1:])).mean(1)
    joints = _count_joints(prof, max(6, int(0.04 * unit)))
    if len(joints) < 2:
        return out
    gaps = np.diff(joints)
    course = float(np.median(gaps))
    out.update(rows=round(len(f) / course, 1), course_units=round(course / unit, 3))
    counts = []
    for y0, y1 in zip(joints, joints[1:]):
        if y1 - y0 < 0.6 * course:
            continue
        strip = f[y0 + (y1 - y0) // 4:y1 - (y1 - y0) // 4]
        col = np.abs(strip - np.roll(strip, 1, 1)).mean(0)
        counts.append(len(_count_joints(col, max(6, w // 24), wrap=True)))
    if counts:
        out["across"] = round(float(np.median(counts)), 1)
    return out


def rescale_wall_master(name, pid, rel, fx=1.0, fy=1.0, keep_top=KEEP_TOP, keep_bottom=KEEP_BOTTOM, redetail=False, seed=1,
                        snap=True):
    """A wall master with its field (everything between `keep_top` units at the top and `keep_bottom` at the base)
    rescaled: `fx` scales the bricks' width (0.5 = twice as many per tile), `fy` their height (0.5 = twice as many
    rows). The cap and base stay exactly as they were. New courses are counted up from the base; the joins (cap,
    base, and where the field had to be stacked to fill a smaller scale) are repainted, then the sideways seam is
    fixed where anything changed. 1/fx a whole number and fy = 1 keeps the bricks exact. -> new candidate rel."""
    spec = load_set(name)
    piece = resolved(spec, pid)
    if piece.get("method") != "wall_master":
        raise ValueError("this rescale works on wall masters")
    fx, fy = float(fx), float(fy)
    if not (1 / 3 - 1e-6 <= fx <= 3 and 0.25 <= fy <= 3):
        raise ValueError("brick width ×0.34-3, height ×0.25-3")
    fam = wall_family_settings(piece, spec)
    old = state(name)["pieces"].get(pid, {}).get("meta", {}).get(rel, {})
    prompt = old.get("prompt", piece.get("prompt", ""))
    full = wall_texture_prompt(set_style(spec), prompt)
    src = np.asarray(Image.open(candidate_path(name, rel)).convert("RGB"))
    h, w = src.shape[:2]
    unit = h / fam["height"]
    t, b = int(round(max(0.0, keep_top) * unit)), int(round(max(0.0, keep_bottom) * unit))
    if t + b > h - 64:
        raise ValueError("the kept top and bottom leave no wall to rescale")
    field = src[t:h - b]
    hf = len(field)
    # whole courses: the window starts on the field's lowest joint and spans a whole number of (scaled) courses, so
    # no sliver course is left under the cap (fy moves a little to fit; it's reported back)
    ww, wh, core = w / fx, hf / fy, field
    if snap:
        m = measure_elevation(Image.fromarray(src), fam["height"], keep_top, keep_bottom)
        if m["course_units"]:
            course = m["course_units"] * unit
            fl = np.asarray(Image.fromarray(field).convert("L")).astype(np.float64)
            jn = _count_joints(np.abs(np.diff(fl, axis=0, append=fl[-1:])).mean(1), max(6, int(0.04 * unit)))
            low, high = [j for j in jn if j >= hf - 1.2 * course], [j for j in jn if j <= 1.2 * course]
            if low and high and max(low) - min(high) > 2 * course:  # stack only whole courses (joint to joint)
                core = field[min(high):max(low)]
            n = max(1, round(wh / course))
            wh = n * course
            fy = hf / wh
    # source window: ww x wh px of the field's whole courses, tiled 3 across and stacked up from the base
    hc = len(core)
    copies = int(np.ceil(wh / hc)) + 1
    big = np.tile(core, (copies, 3, 1))
    Y1 = len(big)
    Y0 = Y1 - wh
    x0 = 1.5 * w - ww / 2
    win = Image.fromarray(big).crop((round(x0), round(Y0), round(x0 + ww), round(Y1)))
    new = np.asarray(win.resize((w, hf), Image.LANCZOS))
    out = src.copy()
    out[t:h - b] = new
    exact_x = fx < 1 and abs(1 / fx - round(1 / fx)) < 1e-6
    # joins to repaint: cap / field, field / base, and the stacked copies of the field
    joins = ([t] if t else []) + ([h - b] if b else [])
    for k in range(1, copies):  # where stacked copies of the field meet, inside the window
        yb = len(big) - k * hc
        if Y0 + 1 < yb < Y1 - 1:
            joins.append(int(round(t + (yb - Y0) / wh * hf)))
    yy = np.arange(h)[:, None].astype(np.float64)
    band, ramp = 0.06 * unit, 0.05 * unit
    mask = np.zeros((h, w), np.float32)
    for j in joins:
        mask = np.maximum(mask, np.clip(1 - (np.abs(yy - j) - band) / ramp, 0, 1) * np.ones((1, w)))
    img = Image.fromarray(out)
    if redetail and (fx > 1 or fy > 1):  # an upscale goes soft: repaint the field lightly
        fieldw = np.clip(np.minimum(yy - t, h - b - yy) / ramp, 0, 1) * np.ones((1, w))
        img = repaint(img, fieldw.astype(np.float32), full, seed + 7, denoise=0.35, reference=True)
    if joins:
        img = repaint(img, mask, full, seed, denoise=0.65)
    changed = np.clip(np.minimum(yy - (t - band - ramp) if t else yy + 1, (h - b + band + ramp) - yy if b else h - yy) / ramp, 0, 1)[:, 0]
    if not exact_x or joins:  # the sideways seam, only where pixels changed (the cap and base already repeat)
        a0 = np.asarray(img).astype(np.float32)
        sm = np.asarray(periodic_smooth_x(img)).astype(np.float32)
        a1 = a0 * (1 - changed[:, None, None]) + sm * changed[:, None, None]
        rolled = Image.fromarray(np.roll(np.clip(a1, 0, 255).astype(np.uint8), w // 2, 1))
        xx = (np.arange(w) + 0.5) / w
        col = np.clip(1 - (np.abs(xx - 0.5) - 0.05) / 0.06, 0, 1)
        fixed = repaint(rolled, (col[None, :] * changed[:, None]).astype(np.float32), full, seed + 1000, denoise=0.75)
        img = Image.fromarray(np.roll(np.asarray(fixed), -(w // 2), 1))
    res = np.asarray(img).copy()
    keep = changed < 1e-6  # outside the changed rows the cap and base are the source's own pixels, exactly
    res[keep] = src[keep]
    meta = {"seed": seed, "prompt": prompt, "rescaled_from": rel, "scale": [round(fx, 3), round(fy, 3)], "keep": [keep_top, keep_bottom],
            "redetail": bool(redetail)}
    return new_candidate(name, pid, Image.fromarray(res), meta, f"w{fx:g}x{fy:g}".replace(".", "_"))


def elevation_joints(img, height_units=WALL_UNITS):
    """The horizontal joints of a whole wall elevation, in units from the top (for the keep-band guides to snap to),
    and a suggested keep top / bottom where the regular courses stop: walking out from the middle of the wall, the
    last joint before the spacing breaks (a cap or plinth course is taller or shorter than the field's) or the
    band's tone changes. 0 when the courses run right to the edge."""
    rgb = np.asarray(img.convert("RGB")).astype(np.float64)
    a = rgb.mean(-1)
    h = a.shape[0]
    unit = h / height_units
    prof = np.abs(np.diff(a, axis=0, append=a[-1:])).mean(1)
    edge = max(4, int(0.02 * unit))  # the image's own top and bottom rows aren't joints
    js = [j for j in _count_joints(prof, max(6, int(0.04 * unit))) if edge <= j <= h - edge]
    if len(js) < 4:  # one very strong line (a hard cap edge) hid the courses: measure against the 98th percentile
        js = [j for j in _count_joints(prof, max(6, int(0.04 * unit)), ref=98) if edge <= j <= h - edge]
    # an edge peak sits on one side of the mortar line: move each joint to the line's middle (its darkest row
    # nearby), so cuts and keep lines go through the mortar, not along a brick's edge
    lum = np.convolve(np.pad(a.mean(1), 2, mode="edge"), np.ones(5) / 5, "valid")
    r = max(3, int(0.02 * unit))
    js = sorted({int(max(edge, j - r) + np.argmin(lum[max(edge, j - r):min(h - edge, j + r + 1)])) for j in js})
    out = {"height": height_units, "joints": [round(j / unit, 3) for j in js], "suggest": {"top": 0.0, "bottom": 0.0}}
    if len(js) < 4:
        return out
    course = float(np.median(np.diff(js)))
    kept = [js[0]]
    for j in js[1:]:  # a crack or chip reads as a joint a fraction of a course from a real one: drop it
        if j - kept[-1] >= 0.6 * course:
            kept.append(j)
    bounds = [0] + kept + [h]
    gaps = np.diff(bounds)
    tone = [rgb[y0 + 1:y1 - 1].mean((0, 1)) if y1 - y0 > 3 else rgb[y0:y1 + 1].mean((0, 1)) for y0, y1 in zip(bounds, bounds[1:])]
    field_tone = np.median(np.array(tone[1:-1]), 0)
    # a gap that's a whole number of courses is a missed joint, not a cap; a cap course is a fraction off
    off = lambda g: g < 0.65 * course or abs(g / course - round(g / course)) > 0.3
    # at the image's edge a long gap is the cap or plinth itself (no joint can hide there): any size off counts
    odd = lambda i: (abs(gaps[i] - course) > 0.35 * course if i in (0, len(gaps) - 1) else off(gaps[i])) \
        or np.abs(tone[i] - field_tone).max() > 30
    n = len(gaps)
    c = n // 2
    i = c
    while i > 0 and not odd(i - 1):
        i -= 1
    out["suggest"]["top"] = round(bounds[i] / unit, 3) if i > 0 else 0.0
    i = c
    while i < n - 1 and not odd(i + 1):
        i += 1
    out["suggest"]["bottom"] = round((h - bounds[i + 1]) / unit, 3) if i < n - 1 else 0.0
    for k in ("top", "bottom"):  # a 'cap' deeper than 0.8 units is a misread, not a cap
        if out["suggest"][k] > 0.8:
            out["suggest"][k] = 0.0
    return out


FIT_WARN = 0.15  # overall stretch past which brick detail starts to look smeared (one course may change 2x this)


def fit_courses(img, height_units=WALL_UNITS, top=0.0, bottom=0.0, mode="keep"):
    """Whole courses between two lines (units from the top / from the base), each course resampled so they share
    the space evenly: no partial course at either end, and the stretch is spread over every course instead of one
    global stretch (uneven rows come out even). A course that spans a missed joint counts as two.
    mode "keep": the bands above `top` and below `bottom` stay as they are (cap, plinth); the courses between are
      remapped to fill the space between them.
    mode "crop": everything outside the lines is dropped; the courses fill the whole wall height.
    Vertical only (the width stays one tile, so it still repeats sideways). -> (image, info)
    info: {"courses", "stretch": [per course, +0.06 = 6% taller], "max" (largest per course), "overall" (the whole
    span), "warn": overall past FIT_WARN or a course past twice that}."""
    src = img.convert("RGB")
    a = np.asarray(src)
    h = a.shape[0]
    unit = h / height_units
    y0, y1 = int(round(top * unit)), h - int(round(bottom * unit))
    if y1 - y0 < 0.3 * unit:
        raise ValueError("the lines leave too little wall to fit")
    js = [int(round(u * unit)) for u in elevation_joints(src, height_units)["joints"]]
    minsep = max(6, int(0.04 * unit))
    inner = [j for j in js if y0 + minsep <= j <= y1 - minsep]
    bounds = [y0] + inner + [y1]
    if len(bounds) < 3:
        raise ValueError("no joints between the lines")
    gaps = np.diff(bounds).astype(np.float64)
    course = float(np.median(gaps))
    kept = [bounds[0]]  # cracks a fraction of a course from a real joint aren't joints
    for b in bounds[1:-1]:
        if b - kept[-1] >= 0.6 * course and bounds[-1] - b >= 0.6 * course:
            kept.append(b)
    bounds = kept + [bounds[-1]]
    gaps = np.diff(bounds).astype(np.float64)
    weight = np.maximum(1, np.round(gaps / course))  # a missed joint: that segment is two courses
    out_y0, out_y1 = (y0, y1) if mode == "keep" else (0, h)
    target = np.diff(np.round(out_y0 + np.concatenate([[0], np.cumsum(weight)]) / weight.sum() * (out_y1 - out_y0))).astype(int)
    strips = [src.crop((0, b0, a.shape[1], b1)).resize((a.shape[1], int(t)), Image.LANCZOS)
              for b0, b1, t in zip(bounds, bounds[1:], target) if t > 0]
    field = np.concatenate([np.asarray(s) for s in strips], 0)
    res = a.copy() if mode == "keep" else np.zeros_like(a)
    res[out_y0:out_y0 + len(field)] = field
    stretch = [round(float(t / g - 1), 3) for t, g in zip(target, gaps)]
    mx = max(abs(v) for v in stretch)
    overall = round(float((out_y1 - out_y0) / (bounds[-1] - bounds[0]) - 1), 3)
    return Image.fromarray(res), {"courses": int(weight.sum()), "stretch": stretch, "max": round(mx, 3), "overall": overall,
                                  "warn": abs(overall) > FIT_WARN or mx > 2 * FIT_WARN}


def fit_wall_master(name, pid, rel, top, bottom, mode="keep", redetail=False, seed=1):
    """fit_courses on a wall master candidate, saved as a new candidate (optional light re-detail). -> rel"""
    spec = load_set(name)
    piece = resolved(spec, pid)
    if piece.get("method") != "wall_master":
        raise ValueError("fit courses works on wall masters")
    fam = wall_family_settings(piece, spec)
    img, info = fit_courses(Image.open(candidate_path(name, rel)), fam["height"], float(top), float(bottom), mode)
    old = state(name)["pieces"].get(pid, {}).get("meta", {}).get(rel, {})
    prompt = old.get("prompt", piece.get("prompt", ""))
    if redetail:  # stretched courses go a little soft: a light repaint of what was resampled, edges kept
        h, w = img.height, img.width
        unit = h / fam["height"]
        y0, y1 = (int(top * unit), h - int(bottom * unit)) if mode == "keep" else (0, h)
        yy = np.arange(h)[:, None].astype(np.float64)
        m = np.clip(np.minimum(yy - y0, y1 - yy) / (0.05 * unit), 0, 1) * np.ones((1, w))
        xx = (np.arange(w) + 0.5) / w
        m *= np.clip(np.minimum(xx, 1 - xx) / 0.06, 0, 1)[None, :]  # keep the left/right edges: it still repeats
        img = repaint(img, m.astype(np.float32), wall_texture_prompt(set_style(spec), prompt), seed, denoise=0.3, reference=True)
    meta = {"seed": seed, "prompt": prompt, "fitted_from": rel, "fit": {"mode": mode, "top": top, "bottom": bottom, **info},
            "redetail": bool(redetail)}
    return new_candidate(name, pid, img, meta, f"fit{info['courses']}")


def elevation_bands(name, rel, height_units=WALL_UNITS, keep_top=KEEP_TOP, keep_bottom=KEEP_BOTTOM, repeat=2):
    """The elevation repeated sideways with the kept cap and base tinted, to judge the keep bands before rescaling."""
    a = np.asarray(Image.open(candidate_path(name, rel)).convert("RGB")).astype(np.float32)
    h = a.shape[0]
    unit = h / height_units
    t, b = int(round(keep_top * unit)), int(round(keep_bottom * unit))
    tint = np.array([90, 160, 255], np.float32)
    if t:
        a[:t] = a[:t] * 0.6 + tint * 0.4
    if b:
        a[h - b:] = a[h - b:] * 0.6 + tint * 0.4
    for y in (t, h - b):
        if 0 < y < h:
            a[max(0, y - 2):y + 2] = (255, 210, 60)
    return Image.fromarray(np.tile(np.clip(a, 0, 255).astype(np.uint8), (1, repeat, 1)))


def feature_zone(w, per, N, seed, outside=0.3, ramp=0.35):
    """Per-column repaint strength for an N-cell variation: 1 in one band (random centre, 45-80% of the run, at
    least 1.2 cells, so it crosses a cell boundary), easing to `outside` (a light repaint) elsewhere."""
    rng = np.random.default_rng(seed)
    xx = (np.arange(w) + 0.5) / per
    width = max(1.2, rng.uniform(0.45, 0.8) * N)
    c = rng.uniform(width / 2 + 0.15, N - width / 2 - 0.15) if N - width > 0.3 else N / 2
    return outside + (1 - outside) * np.clip(1 - (np.abs(xx - c) - width / 2) / ramp, 0, 1)


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
    mask = elev_keep_mask(w, h, per, iso.KZ * per)
    prompt = wall_texture_prompt(set_style(spec), piece["prompt"])
    if N > 1 and fam.get("span", "once") == "once":
        # the run is one piece: its feature should happen once across it, not once per cell (FLUX copies the
        # master tiled N times). Strong repaint in one zone spanning cells, light elsewhere, and no "repeating"
        mask = mask * feature_zone(w, per, N, seed)[None, :]
        prompt = prompt.replace(WALL_SEAMLESS, "").replace(piece["prompt"], f"{piece['prompt']}, in a single place on this wall section")
    out = repaint(base, mask.astype(np.float32), prompt, seed,
                  denoise=float(denoise) if denoise else REPAINT_DENOISE, lora=lora, reference=False)
    return lock_palette(spec, master_id, out, mask, m) if fam["palette_lock"] else out


def door_rect(fam, per, unit_px, h_px):
    """Door opening in a one-tile elevation slice: (x0, x1, y0, y1) px."""
    a, b = (0.5 - fam["door_width"] / 2) * per, (0.5 + fam["door_width"] / 2) * per
    return a, b, h_px - fam["door_height"] * unit_px, h_px


def gen_door(name, spec, piece, seed=1, denoise=None, lora=0.0):
    """A one-tile door slice of the active wall master: only the opening (plus a little frame when the door is
    narrower than the cell) is repainted. FLUX gets the slice in the middle of three master tiles, so it sees wall
    on both sides of the door (alone, a full-width opening reads as a plain panel), and the middle tile is kept."""
    master_id = piece.get("from")
    master = piece_of(spec, master_id)
    fam = wall_family_settings(master)
    base, _ = wall_master_image(name, spec, master_id)
    per, H = base.width, base.height
    x0, x1, y0, _ = door_rect(fam, per, iso.KZ * per, H)
    X0, X1, Y0 = per + int(round(x0)), per + int(round(x1)), int(round(y0))
    frame, ramp = 0.06 * per, 0.04 * per
    left, right = max(per, X0 - frame), min(2 * per, X1 + frame)
    xx, yy = np.arange(3 * per) + 0.5, np.arange(H) + 0.5
    mx = np.clip(np.minimum(xx - left, right - xx) / ramp, 0, 1)
    mx[(xx >= per) & (xx < 2 * per) & (xx >= left) & (xx < right)] = np.maximum(
        mx[(xx >= per) & (xx < 2 * per) & (xx >= left) & (xx < right)], (left == per) * 1.0)  # full width: hard edges
    if X1 - X0 >= per - 1:
        mx = ((xx >= per) & (xx < 2 * per)).astype(np.float32)
    mask = mx[None, :] * np.clip((yy - (Y0 - frame)) / ramp, 0, 1)[:, None]
    # Pre-fill the exact opening with a door cue (dark planks, gaps, two iron bands) from the floor up to the door
    # height: left alone, FLUX paints a door floating mid-wall or a plain panel.
    init = np.tile(np.asarray(base).astype(np.float32), (1, 3, 1))
    r = np.random.default_rng(seed)
    w = X1 - X0
    planks = np.where((np.arange(w) % max(w // 6, 1)) < 3, -38.0, 0.0)[None, :, None]
    door = np.array([92, 60, 38], np.float32) + planks + r.normal(0, 6, (H - Y0, w, 1))
    for band in (0.25, 0.75):
        y = int((H - Y0) * band)
        door[y:y + max(int(0.03 * per), 4)] = np.array([46, 44, 46], np.float32)
    init[Y0:, X0:X1] = door
    out = repaint(Image.fromarray(np.clip(init, 0, 255).astype(np.uint8)), mask,
                  door_texture_prompt(set_style(spec), material_prompt(name, master), piece["prompt"]),
                  seed, denoise=float(denoise) if denoise else DOOR_DENOISE, lora=lora, reference=False)
    return out.crop((per, 0, 2 * per, H))


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
    dw = pcs["doorway"]
    if (dw.get("door_width"), dw.get("door_height"), dw.get("height")) != (fam["door_width"], fam["door_height"], fam["height"]):
        upsert_piece(name, {**dw, "door_width": fam["door_width"], "door_height": fam["door_height"], "height": fam["height"]})
        pcs = wall_family_pieces(load_set(name), master_id)
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
                    archive_candidate(name, r)
                    drop_candidate(ps, {r})
            ps["candidates"] = made + ps["candidates"]
            ps["approved"] = list(made)
            for r, (src_rel, _, _) in zip(made, items):
                ps.setdefault("meta", {})[r] = {"built_from": src_rel, "master": mrel}
            save_state(name, st)
        out[piece["id"]] = len(made)
    return {"built": out, "retired": retired}


def pathlib_stem(rel):
    return Path(rel).stem


def family_pieces(spec, master_id):
    """{N: repaint piece} made from this master (the first per size)."""
    out = {}
    for p in spec["pieces"]:
        if p.get("method") == "repaint" and p.get("from") == master_id and not p.get("retired"):
            out.setdefault(atlas_n(p), p)
    return out


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


def fill_jobs(name, master_id, n=1):
    """Generation jobs for the family's gaps only: every (variation, size) cell, and for walls every door, that has
    no candidate yet from the active master (archived ones don't count). n per gap."""
    spec = load_set(name)
    master = piece_of(spec, master_id)
    mrel = master_rel(name, master_id)
    if not mrel:
        raise ValueError(f"pick the active master of {master_id} first (Use as master)")
    if state(name)["pieces"].get(master_id, {}).get("meta", {}).get(mrel, {}).get("draft"):
        raise ValueError("the active master is a draft: finalize it first (variants keep the master's edges)")
    wall = master.get("method") == "wall_master"
    jobs = variant_jobs(name, master_id, None, None, n) + (variant_jobs(name, master_id, None, None, n, doors=True) if wall else [])
    s = state(name)["pieces"]
    have = set()
    for pid, ps in s.items():
        for r in ps.get("candidates", []):
            m = ps.get("meta", {}).get(r, {})
            if m.get("master") == mrel and m.get("variation"):
                have.add((pid, m["variation"]))
    return [j for j in jobs if (j["piece"], j["variation"]) not in have]


def review_list(name, master_id):
    """The family's candidates waiting for a decision: from the active master, not approved, not archived
    (variants of every size, then doors). [{pid, rel}]"""
    spec = load_set(name)
    master = piece_of(spec, master_id)
    mrel = master_rel(name, master_id)
    s = state(name)["pieces"]
    if master.get("method") == "wall_master":
        pcs = wall_family_pieces(spec, master_id)
        pids = [pcs[k]["id"] for k in ("var1", "var2", "var3", "var4", "door") if k in pcs]
    else:
        pids = [p["id"] for _, p in sorted(family_pieces(spec, master_id).items())]
    out = []
    for pid in pids:
        ps = s.get(pid, {})
        out += [{"pid": pid, "rel": r} for r in ps.get("candidates", [])
                if r not in ps.get("approved", []) and ps.get("meta", {}).get(r, {}).get("master") == mrel]
    return out


def family_palette(name, master_id):
    """Colour of every image in the family (interior mean) and its distance from the active master (CIE76 ΔE: ~2 is
    barely visible, 5+ obvious, 10+ a different colour), so drifting variants stand out."""
    spec = load_set(name)
    mrel = master_rel(name, master_id)
    if not mrel:
        return {"master": None, "items": []}
    to_rgb = lambda lab: "#%02x%02x%02x" % tuple(int(v) for v in from_lab(np.asarray(lab, np.float32)[None])[0])
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
            weight = (elev_keep_mask(size[0], size[1], size[0] // N, iso.KZ * size[0] // N)
                      if p.get("method") == "wall_repaint" else family_weight(p, size[0]))
            mean = image_lab_stats(name, rel, weight)[0]
            meta = ps.get("meta", {}).get(rel, {})
            items.append({"piece": p["id"], "rel": rel, "n": N, "rgb": to_rgb(mean), "dE": round(float(np.linalg.norm(mean - mm)), 1),
                          "approved": rel in ps.get("approved", []), "variation": meta.get("variation"),
                          "stale": meta.get("master") != mrel})
    items.sort(key=lambda it: -it["dE"])
    return {"master": {"rel": mrel, "rgb": to_rgb(mm)}, "items": items}


def _blockout_file(name, pid, make, key):
    """A blockout image for a piece (outside its candidate list), made by `make()` -> (img, mask) only when the
    content key is new. Returns its rel."""
    import hashlib
    h = hashlib.sha1(key.encode()).hexdigest()[:12]
    f = set_dir(name) / "candidates" / pid / "_blockout" / f"{h}.png"
    if not f.exists():
        img, mask = make()
        f.parent.mkdir(parents=True, exist_ok=True)
        img.save(f)
        np.save(f.with_suffix(".npy"), mask)
    return str(f.relative_to(set_dir(name))).replace("\\", "/")


def blockout_include(name, floor_id=None, wall_id=None):
    """room_preview `include` that shows the families' templates instead of generated art: the floor master as a
    template tile (its variants left out), the wall template projected onto runs 1-4, the post and the doorway (a
    flat door fill), and the set's other pieces on those boards left out. Families without a template keep their art."""
    import json as _json
    spec = load_set(name)
    inc = {}
    if floor_id:
        fm = piece_of(spec, floor_id)
        tpl = family_template(fm)
        if tpl:
            def floor_img():
                r = template_render(tpl, "floor", MASTER_PX)
                return r["rgb"], np.ones(r["region"].shape, bool)
            inc[floor_id] = _blockout_file(name, floor_id, floor_img, "f" + _json.dumps(tpl, sort_keys=True))
            for q in family_pieces(spec, floor_id).values():
                inc[q["id"]] = None
            board = fm.get("board", floor_id)
            for q in spec["pieces"]:
                if q["id"] != floor_id and q.get("board", q["id"]) == board and q["id"] not in inc:
                    inc[q["id"]] = None
    if wall_id:
        wm = piece_of(spec, wall_id)
        tpl = family_template(wm)
        if tpl:
            fam = wall_family_settings(wm, spec)
            pcs = ensure_wall_pieces(name, wall_id)
            spec = load_set(name)
            cache = {}

            def elev(door=False):
                if "e" not in cache:
                    cache["e"] = template_render(tpl, "wall", MASTER_PX, fam["height"])["rgb"]
                e = cache["e"]
                if not door:
                    return e
                a = np.asarray(e).copy()
                x0, x1, y0, _ = door_rect(fam, e.width, iso.KZ * e.width, e.height)
                a[int(y0):, int(x0):int(x1)] = (88, 58, 38)  # a flat door fill
                return Image.fromarray(a)
            key = _json.dumps(tpl, sort_keys=True) + str((fam["height"], fam["door_width"], fam["door_height"]))
            for k in ("run1", "run2", "run3", "run4", "post", "doorway"):
                piece = resolved(spec, pcs[k]["id"])
                inc[piece["id"]] = _blockout_file(name, piece["id"], lambda piece=piece, k=k: project_elevation(
                    piece, spec["tile"], elev(k == "doorway"), 1), k + key)
            boards = set(fam["boards"].values())
            for q in spec["pieces"]:
                if q.get("kind") == "tile" and q.get("board", q["id"]) in boards and q["id"] not in inc:
                    inc[q["id"]] = None
    return inc


def template_preview(name, master_id):
    """A family template on its own: 2 x 2 tiles of floor, or 2 tiles of wall elevation."""
    spec = load_set(name)
    m = piece_of(spec, master_id)
    tpl = family_template(m)
    if not tpl:
        raise ValueError("no template")
    if m.get("method") == "wall_master":
        fam = wall_family_settings(m, spec)
        e = template_render(tpl, "wall", 256, fam["height"])["rgb"]
        return Image.fromarray(np.tile(np.asarray(e), (1, 2, 1)))
    f = template_render(tpl, "floor", 256)["rgb"]
    return Image.fromarray(np.tile(np.asarray(f), (2, 2, 1)))


def link_family(spec, master, on=True):
    """Link (or unlink) a family's template to the sheet. A family with no template gets the kind's default pattern."""
    fam = dict(master.get("family") or {})
    tpl = dict(fam.get("template") or {})
    kind = "wall" if master.get("method") == "wall_master" else "floor"
    if on:
        if tpl.get("pattern") in (None, "none"):
            tpl = {"pattern": "running_bond" if kind == "wall" else "flagstone", "strength": 0.7, "seed": 1}
        tpl = sheet_template({**tpl, "link": True}, kind, sheet(spec))
    else:
        tpl.pop("link", None)
    fam["template"] = tpl
    return {**master, "family": fam}


def update_sheet(name, fields):
    """Save sheet fields and rewrite every linked family's template from it. Returns the spec."""
    with LOCK:
        spec = load_set(name)
        sh = {**(spec.get("sheet") or {})}
        for k, v in fields.items():
            if k not in SHEET_DEFAULT:
                raise ValueError(f"unknown sheet field {k}")
            sh[k] = v if k in ("palette", "words", "floor") else float(v)
        spec["sheet"] = sh
        full = sheet(spec)
        for i, p in enumerate(spec["pieces"]):
            tpl = (p.get("family") or {}).get("template") or {}
            if p.get("method") in ("master", "wall_master") and tpl.get("link"):
                kind = "wall" if p["method"] == "wall_master" else "floor"
                spec["pieces"][i] = {**p, "family": {**p["family"], "template": sheet_template(tpl, kind, full)}}
        return save_set(name, spec)


def sheet_from_floor(name, master_id):
    """Fill the sheet's palette from a floor family's active master (k-means of its stones, its darkest tone as the
    joint) and its stone size from the family's template, then link that family. Returns the spec."""
    from hdart.tools.dungeon import library
    spec = load_set(name)
    m = piece_of(spec, master_id)
    rel = master_rel(name, master_id)
    fields = {"floor": master_id}
    if rel:
        fields["palette"] = library.extract_palette(Image.open(set_dir(name) / rel).convert("RGB"))
    tpl = family_template(m)
    if tpl and tpl.get("pattern") != "image":
        fields["floor_stone"] = round(1 / max(1, int((tpl.get("params") or {}).get("across", 3))), 3)
    update_sheet(name, fields)
    with LOCK:
        spec = load_set(name)
        return upsert_piece(name, link_family(spec, piece_of(spec, master_id), True))


def sheet_floor_master(name, spec):
    """(floor family id, active master rel) the walls match, or (None, None)."""
    sh = sheet(spec)
    ids = ([sh["floor"]] if sh["floor"] else []) + [p["id"] for p in spec["pieces"] if p.get("method") == "master"]
    for fid in ids:
        rel = master_rel(name, fid) if any(p["id"] == fid for p in spec["pieces"]) else None
        if rel:
            return fid, rel
    return None, None


def match_to_floor(name, spec, img):
    """A wall master pulled toward the floor master's colours (sheet "match"), lightness offset by "wall_light"."""
    sh = sheet(spec)
    k = float(sh["match"])
    fid, rel = sheet_floor_master(name, spec)
    if k <= 0 or not rel:
        return img
    mean, std = image_lab_stats(name, rel)
    mean = np.array(mean, np.float32) + np.array([float(sh["wall_light"]), 0, 0], np.float32)
    return match_palette(img, (mean, std), np.ones(img.size[::-1], np.float32), k)


def matching_wall_family(name, floor_id, slug=None):
    """A wall family made to go with a floor family: the sheet filled from the floor (if it has none yet), a linked
    running-bond template, the floor's material words in the prompt. Returns the new master's id."""
    spec = load_set(name)
    if not spec.get("sheet"):
        sheet_from_floor(name, floor_id)
        spec = load_set(name)
    elif not sheet(spec)["floor"]:
        update_sheet(name, {"floor": floor_id})
        spec = load_set(name)
    floor = piece_of(spec, floor_id)
    base = re.sub(r"[^a-z0-9_]+", "_", (slug or floor_id.replace("tile.", "").replace(f"{name}_", "").replace("_master", "")).lower()).strip("_")
    base = re.sub(r"_?floors?$", "", base) or "stone"
    wid = f"tile.{name}_{base}_wall_master"
    n = 2
    while any(p["id"] == wid for p in spec["pieces"]):
        wid, n = f"tile.{name}_{base}{n}_wall_master", n + 1
    words = material_prompt(name, floor).split(",")[0].strip() or "stone"
    prompt = f"a wall of {words}, blocks in courses with mortar, a flat stone cap along the top"
    piece = {"id": wid, "kind": "elevation", "shape": "elevation", "method": "wall_master", "variants": 1, "prompt": prompt,
             "family": {"prompts": [prompt], "template": {"pattern": "running_bond", "strength": 0.7, "seed": 1}}}
    upsert_piece(name, link_family(spec, piece, True))
    return wid


# ----------------------------------------------------------------------------- recipes
# library/recipes/<id>.json (in git, shared by every set): a family's whole setup (prompts, variations, doors,
# template, switches) or a set's floor + wall families together, optionally with the set's material sheet. Start new
# families from one instead of from scratch. Board ids and images are never part of a recipe.

def recipes_dir() -> Path:
    """library/recipes: saved family recipes (in git)."""
    return config.library_dir() / "recipes"

_RECIPE_DROP = ("boards",)  # set-specific


def _family_recipe(master):
    kind = "wall" if master.get("method") == "wall_master" else "floor"
    fam = {k: v for k, v in (master.get("family") or {}).items() if k not in _RECIPE_DROP}
    return {"kind": kind, "slug": re.sub(r"^tile\.[^_]+_|_(wall_)?master$", "", master["id"]),
            "prompt": master.get("prompt", ""), "family": fam}


def save_recipe(name, master_ids, title, with_sheet=True, notes=""):
    """Save one or more families of a set (and its sheet) as a recipe. Returns the recipe."""
    spec = load_set(name)
    rid = re.sub(r"[^a-z0-9_]+", "_", title.lower()).strip("_") or "recipe"
    rec = {"id": rid, "title": title, "notes": notes, "from_set": name, "saved": time.strftime("%Y-%m-%d"),
           "families": [_family_recipe(piece_of(spec, m)) for m in master_ids]}
    if with_sheet and spec.get("sheet"):
        rec["sheet"] = {k: v for k, v in sheet(spec).items() if k != "floor"}
    recipes_dir().mkdir(parents=True, exist_ok=True)
    (recipes_dir() / f"{rid}.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return rec


def list_recipes():
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(recipes_dir().glob("*.json"))] if recipes_dir().exists() else []


def apply_recipe(name, rid, use_sheet=True):
    """New families in a set from a recipe (ids made unique), and its sheet when the set has none yet (or
    use_sheet="replace"). Linked templates take the set's sheet. Returns the new master ids."""
    f = recipes_dir() / f"{re.sub(r'[^a-z0-9_]+', '_', rid)}.json"
    if not f.exists():
        raise ValueError(f"no recipe {rid}")
    rec = json.loads(f.read_text(encoding="utf-8"))
    spec = load_set(name)
    if rec.get("sheet") and use_sheet and (use_sheet == "replace" or not spec.get("sheet")):
        update_sheet(name, rec["sheet"])
    made = []
    for fr in rec["families"]:
        spec = load_set(name)
        wall = fr["kind"] == "wall"
        base = f"tile.{name}_{fr['slug'] or ('wall' if wall else 'floor')}"
        pid, n = f"{base}_{'wall_' if wall and not base.endswith('wall') else ''}master", 2
        while any(p["id"] == pid for p in spec["pieces"]):
            pid, n = f"{base}{n}_{'wall_' if wall and not base.endswith('wall') else ''}master", n + 1
        piece = {"id": pid, "prompt": fr["prompt"], "variants": 1, "family": dict(fr["family"]),
                 **({"kind": "elevation", "shape": "elevation", "method": "wall_master"} if wall else
                    {"kind": "tile", "shape": "floor", "method": "master", "board": f"tile.{name}_floor"})}
        upsert_piece(name, piece)
        made.append(pid)
    if made and not sheet(load_set(name))["floor"]:
        floor = next((m for m, fr in zip(made, rec["families"]) if fr["kind"] == "floor"), None)
        if floor:
            update_sheet(name, {"floor": floor})
    return made


def apply_material(name, master_id, mid, use=("template", "words"), strength=0.65):
    """Use a library material on a family: its texture as the layout template, its palette on the family's procedural
    template, its words in the master prompt. Records the material on the family (floor and wall families can share
    one). Returns the set spec."""
    from hdart.tools.dungeon import library
    mat = library.load(mid)
    with LOCK:
        spec = load_set(name)
        m = piece_of(spec, master_id)
        fam = dict(m.get("family") or {})
        tpl = dict(fam.get("template") or {})
        if "template" in use:
            tpl = {"pattern": "image", "material": mat["id"], "repeats": mat.get("repeats", 1), "strength": strength, "seed": 1}
        if "palette" in use:
            if tpl.get("pattern") in (None, "none", "image"):
                tpl = {"pattern": "running_bond", "strength": strength, "seed": 1}
            tpl["palette"] = mat["palette"]
        if tpl:
            fam["template"] = tpl
        if "words" in use and mat.get("descriptors"):
            prompts = list(fam.get("prompts") or [m.get("prompt", "")])
            base = prompts[0] if prompts else ""
            extra = f"{mat['name'].lower()}, {mat['descriptors']}"
            prompts[0] = base if extra in base else (f"{base}, {extra}" if base.strip() else extra)
            fam["prompts"] = prompts
            m["prompt"] = prompts[0]
        fam["material"] = mat["id"]
        return upsert_piece(name, {**m, "family": fam})


# ----------------------------------------------------------------------------- Studio job kinds (hdart.core.jobs)

@jobs.kind("dun")
def _job_generate(a):
    return generate(a["set"], a["piece"], int(a.get("n", 3)), int(a.get("seed", 1)), float(a["denoise"]) if a.get("denoise") not in (None, "") else None, a.get("prompt") or None,
                    float(a["lora"]) if a.get("lora") not in (None, "") else None, a.get("variation"), bool(a.get("draft")))


@jobs.kind("dun_trial")
def _job_trial(a):
    return trial(a["set"], a["piece"], a["rel"], a.get("variation", ""), int(a.get("seed", 1)))


@jobs.kind("dun_edit")
def _job_edit(a):
    return edit_candidate(a["set"], a["piece"], a["rel"], a.get("prompt", ""), float(a.get("denoise", 0.5)),
                          int(a.get("seed", 1)))


@jobs.kind("dun_rescale")
def _job_rescale(a):
    if a.get("fit"):  # wall master: fit courses between the keep lines
        return fit_wall_master(a["set"], a["piece"], a["rel"], float(a.get("keep_top", 0)), float(a.get("keep_bottom", 0)),
                               a.get("mode", "keep"), bool(a.get("redetail")), int(a.get("seed", 1)))
    if "fx" in a:  # wall master: brick width / height, cap and base kept
        return rescale_wall_master(a["set"], a["piece"], a["rel"], float(a["fx"]), float(a["fy"]),
                                   float(a.get("keep_top", KEEP_TOP)), float(a.get("keep_bottom", KEEP_BOTTOM)),
                                   bool(a.get("redetail")), int(a.get("seed", 1)))
    return rescale_master(a["set"], a["piece"], a["rel"], float(a["factor"]), bool(a.get("redetail")), int(a.get("seed", 1)))


@jobs.kind("dun_finalize")
def _job_finalize(a):
    return finalize_draft(a["set"], a["piece"], a["rel"], a.get("seed"))


@jobs.kind("dun_build")
def _job_build(a):
    return build_walls(a["set"], a["piece"])
