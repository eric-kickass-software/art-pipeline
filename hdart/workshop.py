"""Workshop: curated, reviewed AI sprite generation (backs the Studio's Workshop page).

Stages, each gated by an explicit approval:
  1. Base   - generate N front candidates of the mannequin from the style + build description, approve one;
              then N back candidates (edits of the approved front), approve one.
  2. Parts  - generate N candidates per facing as edits of the approved base (back uses the approved
              front part as a second reference), approve one per facing.
  3. Cut QA - the approved image is cut into a layer (adjustable sensitivity / speck size), shown as mask,
              layer, native-grid channel map and composite; approve -> templates for every grid size.
Nothing reaches the kit before stage 3 is approved. State: sprites/ai/workshop.json.
"""
import base64
import io
import json
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import ai_kit, comfy

ROOT = Path(__file__).resolve().parent.parent
STATE = ai_kit.AI_DIR / "workshop.json"
CAND = ai_kit.AI_DIR / "candidates"
STYLE = ROOT / "specs" / "ai_style.json"
ARCHIVE = ROOT / "archive"  # git-ignored, shared with the Dungeon Forge
ORDER = ("medium", "style", "proportions", "view", "rendering", "framing", "world")
QA_COLORS = {"hair": (214, 70, 200), "skin": (240, 190, 150), "eyes": (60, 200, 90), "cloth1": (70, 110, 230),
             "cloth2": (60, 200, 220), "leather": (140, 90, 50), "metal": (170, 170, 185), "wood": (220, 140, 40),
             "accent": (240, 210, 40), "dark": (20, 20, 25), "magic": (150, 80, 240), "white": (250, 250, 250)}


# ----------------------------------------------------------------------------- state & prompts

def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"builds": {}, "parts": {}}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def style():
    return json.loads(STYLE.read_text(encoding="utf-8"))


def save_style(groups=None, base=None, builds=None):
    st = style()
    if groups:
        for k, v in groups.items():
            if k in st["groups"]:
                st["groups"][k]["text"] = v
    if base is not None:
        st["base"]["text"] = base
    if builds:
        st["builds"].update(builds)
    STYLE.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
    return st


def style_text(st=None, groups=ORDER):
    st = st or style()
    return ", ".join(st["groups"][g]["text"] for g in groups if st["groups"][g]["text"].strip())


def base_prompt(build_desc, st=None):
    st = st or style()
    return f"{style_text(st)}, {build_desc}, {st['base']['text']}"


def part_prompt(slot, text, facing, st=None):
    st = st or style()
    verb = st["slot_verbs"].get(slot, "Give the character")
    look = style_text(st, ("medium", "rendering"))
    if facing == "front":
        return f"{verb} {text}. {st['part_suffix']} Pixel art style: {look}."
    return (f"This character is seen from behind. {verb} the same {text} as shown in image 2, seen from behind. "
            f"{st['part_suffix']} Pixel art style: {look}.")


def rel(p):
    return str(Path(p).resolve().relative_to(ai_kit.AI_DIR.resolve())).replace("\\", "/")


# ----------------------------------------------------------------------------- double-sided card: back from the front

BACK_PROMPT = ("This is the FRONT of a paper cut-out figure, mirrored. Redraw it as the BACK side of the same cut-out: "
               "show the character from behind (back of the head, back of the body and clothes, no face), keeping "
               "exactly the same outline, silhouette, size and position, so it lines up with the front perfectly. "
               "Same pixel art style.")


def mirror(img):
    return img.transpose(Image.FLIP_LEFT_RIGHT)


def enforce_silhouette(img, silhouette):
    """Make `img` occupy exactly `silhouette` (bool mask): white outside; any background-white holes inside
    are filled from the nearest drawn pixel so the outline matches the front pixel for pixel."""
    a = np.asarray(img.convert("RGB")).copy()
    inside = silhouette
    drawn = ai_kit.foreground(Image.fromarray(a)) & inside
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


SIL_PROMPT = ("Image 1 is a flat grey silhouette of a character. Paint the BACK view of the character shown in image 2 "
              "(seen from behind: back of the head, back of the body and clothes, no face) so that it fills the grey "
              "silhouette exactly: same outline, same size, same position, the head inside the round top of the "
              "silhouette and the feet at the bottom. Detailed pixel art in the same style as image 2, plain white "
              "background.")


def silhouette_image(mask):
    a = np.full(mask.shape + (3,), 255, np.uint8)
    a[mask] = (150, 150, 155)
    edge = mask & ~(np.roll(mask, 1, 0) & np.roll(mask, -1, 0) & np.roll(mask, 1, 1) & np.roll(mask, -1, 1))
    a[edge] = (40, 40, 45)
    return Image.fromarray(a)


BACK_DENOISE = 0.78


def back_prompt(what):
    st = style()
    return (f"{style_text(st, ('medium', 'proportions', 'rendering'))}, the same character as the reference seen from "
            f"BEHIND, back view, no face visible, {what}, plain white background")


def _blur_into(img, mask, radius=10):
    from PIL import ImageFilter
    blur = np.asarray(img.filter(ImageFilter.GaussianBlur(radius)))
    a = np.asarray(img).copy()
    a[mask] = blur[mask]
    return a


def _i2i(init, refs, prompt, seed, lora, denoise):
    CAND.mkdir(parents=True, exist_ok=True)
    init.save(CAND / "_tmp_init.png")
    names = []
    for k, r in enumerate(refs):
        r.save(CAND / f"_tmp_ref{k}.png")
        names.append(comfy.upload(CAND / f"_tmp_ref{k}.png"))
    nodes = comfy.build_img2img(comfy.upload(CAND / "_tmp_init.png"), prompt, denoise=denoise, seed=seed,
                                lora_strength=lora, ref_images=names, prefix="hdart/ws_back")
    p = comfy.run(nodes, CAND / "tmp")[0]
    return np.asarray(Image.open(p).convert("RGB").resize((ai_kit.BIG, ai_kit.BIG), Image.NEAREST))


def back_from_front(front_img, seed, lora=0.8, what="", denoise=BACK_DENOISE):
    """Mannequin back = reverse side of the front card: img2img from the blurred mirrored front (keeps the
    layout and colours, loses the face), mirrored front as appearance reference, clamped to its silhouette."""
    m = mirror(front_img.convert("RGB"))
    sil = ai_kit.foreground(m)
    a = _blur_into(m, sil)
    a[~sil] = 255
    out = _i2i(Image.fromarray(a), [m], back_prompt(what or "back of the head and body"), seed, lora, denoise)
    return enforce_silhouette(Image.fromarray(out), sil)


BACK_BANDS = {"hair": (0.0, 0.46), "head": (0.0, 0.46), "torso": (0.38, 0.8), "back": (0.0, 1.0),
              "legs": (0.68, 1.0), "feet": (0.82, 1.0)}  # fraction of the figure height the back may repaint


def back_part(front_part_img, front_layer_alpha, back_base_img, seed, text, lora=0.8, denoise=BACK_DENOISE, slot=None):
    """Back of a part: only the region the part covers on the back is regenerated (mirrored front cut, plus a
    slot band: a hood covers the whole back of the head, a cape the whole back); the rest is copied from
    the back mannequin so the cut stays clean. Clamped to the mirrored front silhouette."""
    m = mirror(front_part_img.convert("RGB"))
    sil = ai_kit.foreground(m)
    region = np.asarray(mirror(Image.fromarray(front_layer_alpha))) > 0
    region |= sil & ~ai_kit.foreground(back_base_img)  # anything the part adds outside the bare figure
    if slot in BACK_BANDS:
        fg = ai_kit.foreground(back_base_img)
        ys = np.nonzero(fg)[0]
        y0, H = ys.min(), ys.max() - ys.min()
        lo, hi = BACK_BANDS[slot]
        rows = np.arange(fg.shape[0])[:, None]
        region |= fg & (rows >= y0 + lo * H) & (rows <= y0 + hi * H)
    for _ in range(6):
        region |= np.roll(region, 1, 0) | np.roll(region, -1, 0) | np.roll(region, 1, 1) | np.roll(region, -1, 1)
    bb = np.asarray(back_base_img.convert("RGB")).copy()
    init = bb.copy()
    init[region] = _blur_into(m, region)[region]
    init[~sil & ~ai_kit.foreground(back_base_img)] = 255
    gen = _i2i(Image.fromarray(init), [m, back_base_img.convert("RGB")], back_prompt(f"wearing {text}"), seed, lora,
               denoise)
    out = bb.copy()
    out[region] = gen[region]
    return enforce_silhouette(Image.fromarray(out), sil | ai_kit.foreground(back_base_img))


# ----------------------------------------------------------------------------- stage 1: base

def gen_base(build, facing, n=4, seed=1, description=None):
    st = style()
    s = load_state()
    b = s["builds"].setdefault(build, {"description": st["builds"].get(build, build), "front": {}, "back": {}})
    if description:
        b["description"] = description
    out = CAND / build / "_base"
    out.mkdir(parents=True, exist_ok=True)
    cands = []
    for k in range(n):
        sd = seed + k
        if facing == "front":
            nodes = comfy.build_txt2img(base_prompt(b["description"], st), ai_kit.BIG, ai_kit.BIG, seed=sd,
                                        lora_strength=1.0, prefix="hdart/ws_base")
        else:
            front = b["front"].get("approved")
            if not front:
                raise ValueError("Approve a front base first.")
            dst = out / f"{facing}_card_s{sd}_{int(time.time())}.png"
            back_from_front(Image.open(ai_kit.AI_DIR / front), sd, what=b["description"]).save(dst)
            cands.append(rel(dst))
            continue
        p = comfy.run(nodes, out / "tmp")[0]
        dst = out / f"{facing}_s{sd}_{int(time.time())}.png"
        Image.open(p).convert("RGB").resize((ai_kit.BIG, ai_kit.BIG), Image.NEAREST).save(dst)
        cands.append(rel(dst))
    b[facing]["candidates"] = cands + [c for c in b[facing].get("candidates", []) if c not in cands][:8]
    save_state(s)
    return cands


def approve_base(build, facing, file):
    s = load_state()
    b = s["builds"][build]
    b[facing]["approved"] = file
    if facing == "front":
        b["back"].pop("approved", None)  # a new front invalidates the back
    dst = ai_kit.base_path(build, facing)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ai_kit.AI_DIR / file, dst)
    dst.with_suffix(".json").write_text(json.dumps(ai_kit.alignment(dst), indent=1), encoding="utf-8")
    ai_kit._ANATOMY.clear()
    save_state(s)
    if b["front"].get("approved") and b["back"].get("approved"):
        for f in ("front", "back"):
            ai_kit.build_base_part(build, f)
    return b


# archive/workshop/<rel>: archived mannequin candidates, same relative path as under sprites/ai/ (nothing is deleted;
# delete archive/ by hand once you're sure)

def archive_base(build, facing, file=None):
    """Archive mannequin candidates of one build + facing: just `file`, or (no file) every candidate that isn't
    approved, plus the older files in the _base folder the board no longer lists. The approved one never moves.
    Returns how many files moved."""
    s = load_state()
    b = s["builds"].get(build, {}).get(facing, {})
    approved = b.get("approved")
    if file:
        if file == approved:
            raise ValueError("That's the approved mannequin. Approve another one first.")
        rels = {file}
    else:
        rels = {c for c in b.get("candidates", []) if c != approved}
        rels |= {rel(p) for p in (CAND / build / "_base").glob(f"{facing}_*.png")} - {approved}
    for r in rels:
        src = ai_kit.AI_DIR / r
        if src.exists():
            dst = ARCHIVE / "workshop" / r
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
    if b:
        b["candidates"] = [c for c in b.get("candidates", []) if c not in rels]
        save_state(s)
    return len(rels)


# ----------------------------------------------------------------------------- stage 2: part candidates

def part_entry(s, part, build):
    e = s["parts"].setdefault(part, {"slot": part.split(".")[0], "text": "", "builds": {}})
    return e, e["builds"].setdefault(build, {"front": {}, "back": {}, "cut": {"thresh": 70, "speck": 24},
                                             "status": "new"})


def gen_part(part, build, facing, text, n=4, seed=5, lora=0.6):
    st = style()
    s = load_state()
    e, pb = part_entry(s, part, build)
    e["text"] = text
    slot = e["slot"]
    base = ai_kit.base_path(build, facing)
    if not base.exists():
        raise ValueError(f"Approve the {facing} base for '{build}' first.")
    out = CAND / build / part
    out.mkdir(parents=True, exist_ok=True)
    cands = []
    if facing == "back":  # reverse side of the approved front: mirrored, redrawn from behind, same silhouette
        front = pb["front"].get("approved")
        if not front:
            raise ValueError("Approve the front version of this part first.")
        layer_files = sorted((ai_kit.LAYERS / build / part).glob("front.*.png"))
        if not layer_files:
            cut(part, build, "front")
            layer_files = sorted((ai_kit.LAYERS / build / part).glob("front.*.png"))
        front_alpha = np.asarray(Image.open(layer_files[0]).convert("RGBA"))[..., 3]
        back_base_img = Image.open(base).convert("RGB")
        for k in range(n):
            sd = seed + k
            img = back_base_img.copy() if slot == "face" else back_part(
                Image.open(ai_kit.AI_DIR / front), front_alpha, back_base_img, sd, text, lora=max(lora, 0.8),
                            slot=slot)
            dst = out / f"{facing}_card_s{sd}_{int(time.time())}.png"
            img.save(dst)
            cands.append(rel(dst))
        pb[facing]["candidates"] = cands + [c for c in pb[facing].get("candidates", []) if c not in cands][:8]
        pb["status"] = "candidates"
        save_state(s)
        return cands
    extra = []
    name = comfy.upload(base)
    for k in range(n):
        sd = seed + k
        nodes = comfy.build_edit(name, part_prompt(slot, text, facing, st), seed=sd, lora_strength=lora,
                                 prefix="hdart/ws_part", megapixels=ai_kit.BIG ** 2 / 1e6, extra_images=extra)
        p = comfy.run(nodes, out / "tmp")[0]
        dst = out / f"{facing}_s{sd}_{int(time.time())}.png"
        Image.open(p).convert("RGB").resize((ai_kit.BIG, ai_kit.BIG), Image.NEAREST).save(dst)
        cands.append(rel(dst))
    pb[facing]["candidates"] = cands + [c for c in pb[facing].get("candidates", []) if c not in cands][:8]
    pb["status"] = "candidates"
    save_state(s)
    return cands


def approve_raw(part, build, facing, file):
    s = load_state()
    e, pb = part_entry(s, part, build)
    pb[facing]["approved"] = file
    if facing == "front":
        pb["back"].pop("approved", None)
    pb["status"] = "cut" if pb["front"].get("approved") and pb["back"].get("approved") else "raw"
    save_state(s)
    return cut(part, build, facing)


# ----------------------------------------------------------------------------- stage 3: cut + QA

MASKS = ai_kit.AI_DIR / "masks"  # user mask edits: L png, 255 = force keep, 128 = force remove


def edits_path(build, part, facing):
    return MASKS / build / part / f"{facing}.png"


def load_edits(build, part, facing):
    p = edits_path(build, part, facing)
    return np.asarray(Image.open(p).convert("L")) if p.exists() else np.zeros((ai_kit.BIG, ai_kit.BIG), np.uint8)


def auto_layer(part, build, facing):
    s = load_state()
    e, pb = part_entry(s, part, build)
    raw = pb[facing].get("approved")
    if not raw:
        raise ValueError(f"No approved {facing} image yet.")
    base = Image.open(ai_kit.base_path(build, facing)).convert("RGB")
    edited = Image.open(ai_kit.AI_DIR / raw).convert("RGB")
    layer = ai_kit.extract(base, edited, e["slot"], thresh=pb["cut"]["thresh"], speck=pb["cut"]["speck"])
    return layer, base, edited


def cut(part, build, facing, thresh=None, speck=None, res=64):
    s = load_state()
    e, pb = part_entry(s, part, build)
    if thresh is not None:
        pb["cut"]["thresh"] = int(thresh)
    if speck is not None:
        pb["cut"]["speck"] = int(speck)
    save_state(s)
    layer, base, edited = auto_layer(part, build, facing)
    ed = load_edits(build, part, facing)
    keep = ((layer[..., 3] > 0) | (ed == 255)) & (ed != 128)
    layer[..., 3] = keep * 255
    ai_kit.save_layer(build, part, facing, ai_kit.SLOT_LAYER.get(e["slot"], "torso"), layer)
    return qa(part, build, facing, layer, base, edited, res)


def cut_data(part, build, facing):
    """What the cut editor needs: the approved image, the automatic mask and the user's edits."""
    layer, base, edited = auto_layer(part, build, facing)
    s = load_state()
    e, pb = part_entry(s, part, build)
    auto = Image.fromarray(((layer[..., 3] > 0) * 255).astype(np.uint8))
    return {"image": url(edited), "base": url(base), "auto": url(auto),
            "edits": url(Image.fromarray(load_edits(build, part, facing))), "cut": pb["cut"]}


def save_edits(part, build, facing, data_url, res=64):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    im = Image.open(io.BytesIO(raw)).convert("L").resize((ai_kit.BIG, ai_kit.BIG), Image.NEAREST)
    a = np.asarray(im)
    out = np.where(a > 190, 255, np.where(a > 60, 128, 0)).astype(np.uint8)
    p = edits_path(build, part, facing)
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(p)
    return cut(part, build, facing, res=res)


# ----------------------------------------------------------------------------- fix a region with a prompt

def fix_region(target, build, facing, image_rel, mask_url, prompt, seed=7, part=None):
    """Regenerate the image with a corrective prompt and keep the result only inside the painted
    selection. The fixed image becomes a new candidate (base or part)."""
    st = style()
    src = Image.open(ai_kit.AI_DIR / image_rel).convert("RGB")
    raw = base64.b64decode(mask_url.split(",", 1)[1])
    sel = np.asarray(Image.open(io.BytesIO(raw)).convert("L").resize(src.size, Image.NEAREST)) > 127
    grow = sel.copy()
    for dy, dx in ((0, 2), (0, -2), (2, 0), (-2, 0)):
        grow |= np.roll(sel, (dy, dx), (0, 1))
    name = comfy.upload(ai_kit.AI_DIR / image_rel)
    full = (f"{prompt}. Only change that; keep everything else exactly the same: same character, pose, framing, "
            f"size and pixel art style ({style_text(st, ('medium', 'rendering'))}).")
    nodes = comfy.build_edit(name, full, seed=seed, lora_strength=0.6, prefix="hdart/ws_fix",
                             megapixels=ai_kit.BIG ** 2 / 1e6)
    out_dir = CAND / build / (part or "_base")
    p = comfy.run(nodes, out_dir / "tmp")[0]
    fixed = np.asarray(Image.open(p).convert("RGB").resize(src.size, Image.NEAREST))
    res = np.where(grow[..., None], fixed, np.asarray(src)).astype(np.uint8)
    dst = out_dir / f"{facing}_fix_s{seed}_{int(time.time())}.png"
    Image.fromarray(res).save(dst)
    s = load_state()
    if target == "part":
        e, pb = part_entry(s, part, build)
        pb[facing].setdefault("candidates", []).insert(0, rel(dst))
    else:
        b = s["builds"][build]
        b[facing].setdefault("candidates", []).insert(0, rel(dst))
    save_state(s)
    return rel(dst)


# ----------------------------------------------------------------------------- board

def board():
    """Parts x builds coverage: status + best thumbnail per cell."""
    s = load_state()
    st = style()
    lib = json.loads((ROOT / "specs" / "ai_parts.json").read_text(encoding="utf-8"))["parts"]
    builds = list(st["builds"])
    parts = sorted(set(lib) | set(s["parts"]) - {"body.base"})
    cells = {}
    for p in parts:
        for b in builds:
            pb = s["parts"].get(p, {}).get("builds", {}).get(b)
            status = pb["status"] if pb else "missing"
            thumb = None
            lay = ai_kit.LAYERS / b / p
            if pb and lay.exists():
                f = next(iter(sorted(lay.glob("front.*.png"))), None)
                thumb = rel(f) if f else None
            if pb and not thumb:
                c = pb["front"].get("approved") or next(iter(pb["front"].get("candidates", [])), None)
                thumb = c
            n_cand = len(pb["front"].get("candidates", [])) + len(pb["back"].get("candidates", [])) if pb else 0
            stage = status
            back_ok = bool(pb) and "_card_" in (pb["back"].get("approved") or "_card_")
            if pb and status not in ("rejected",) and pb["back"].get("approved") and not back_ok:
                stage = "redo-back"
            elif pb and status not in ("approved", "rejected", "unreviewed"):
                if pb["front"].get("approved") and pb["back"].get("approved"):
                    stage = "needs-cut"
                elif pb["front"].get("approved"):
                    stage = "needs-back"
                elif n_cand:
                    stage = "pick"
            cells[f"{p}|{b}"] = {"status": stage, "thumb": thumb, "candidates": n_cand}
    bases = {b: {"front": bool(s["builds"].get(b, {}).get("front", {}).get("approved")),
                 "back": bool(s["builds"].get(b, {}).get("back", {}).get("approved")),
                 "card": "_card_" in (s["builds"].get(b, {}).get("back", {}).get("approved") or "")} for b in builds}
    return {"parts": parts, "builds": builds, "cells": cells, "bases": bases,
            "text": {p: (s["parts"].get(p, {}).get("text") or lib.get(p, {}).get("prompt", "")) for p in parts}}


def qa(part, build, facing, layer, base, edited, res):
    slot = part.split(".")[0]
    meta = json.loads(ai_kit.base_path(build, facing).with_suffix(".json").read_text(encoding="utf-8"))
    m = layer[..., 3] > 0
    over = np.asarray(edited).astype(np.float32) * 0.35
    over[m] = np.asarray(edited)[m] * 0.6 + np.array([255, 40, 160]) * 0.4
    cell = ai_kit.to_cell(layer, meta, res)
    alpha, ch, idx = ai_kit.to_template(cell, slot)
    chan = np.zeros((res, res, 4), np.uint8)
    for name in set(ch[alpha]):
        mm = alpha & (ch == name)
        c = np.array(QA_COLORS.get(name, (255, 0, 255)), np.float32)
        chan[mm, :3] = np.clip(c[None] * (0.55 + idx[mm, None] * 0.12), 0, 255)
    chan[alpha, 3] = 255
    # composite on the base body (native grid, default colours)
    braw = np.zeros((ai_kit.BIG, ai_kit.BIG, 4), np.uint8)
    braw[..., :3] = np.asarray(base)
    braw[..., 3] = ai_kit.foreground(base) * 255
    bcell = ai_kit.to_cell(braw, meta, res)
    comp = np.zeros((res, res, 4), np.float32)
    comp[..., :3] = bcell[..., :3]
    comp[..., 3] = bcell[..., 3]
    comp[alpha, :3] = cell[alpha, :3]
    comp[alpha, 3] = 1
    up = lambda a, k: Image.fromarray(a).resize((a.shape[1] * k, a.shape[0] * k), Image.NEAREST)
    k = max(1, 256 // res)
    stats = {"pixels": int(alpha.sum()), "channels": {n: int((alpha & (ch == n)).sum()) for n in set(ch[alpha])}}
    return {"raw": url(edited.resize((256, 256), Image.NEAREST)), "base": url(base.resize((256, 256), Image.NEAREST)),
            "mask": url(Image.fromarray(over.astype(np.uint8)).resize((256, 256), Image.NEAREST)),
            "layer": url(Image.fromarray(layer).resize((256, 256), Image.NEAREST)),
            "channels": url(up(chan, k)), "composite": url(up((comp * 255).astype(np.uint8), k)),
            "stats": stats, "res": res}


def approve_cut(part, build):
    s = load_state()
    e, pb = part_entry(s, part, build)
    ai_kit.templates_from_layers(build, part)
    pb["status"] = "approved"
    save_state(s)


def reject(part, build):
    s = load_state()
    e, pb = part_entry(s, part, build)
    shutil.rmtree(ai_kit.LAYERS / build / part, ignore_errors=True)
    for r in ai_kit.RESOLUTIONS:
        shutil.rmtree(ai_kit.TEMPLATES / f"r{r}" / build / part, ignore_errors=True)
    pb["status"] = "rejected"
    save_state(s)


def import_existing(build="slim"):
    """One-off: register parts made before the Workshop as 'unreviewed' with their raw edits as candidates."""
    s = load_state()
    lib = json.loads((ROOT / "specs" / "ai_parts.json").read_text(encoding="utf-8"))["parts"]
    b = s["builds"].setdefault(build, {"description": style()["builds"].get(build, build), "front": {}, "back": {}})
    for f in ("front", "back"):
        if ai_kit.base_path(build, f).exists():
            dst = CAND / build / "_base" / f"{f}_original.png"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ai_kit.base_path(build, f), dst)
            b[f].setdefault("candidates", [rel(dst)])
            b[f].setdefault("approved", rel(dst))
    for raw in sorted((ai_kit.AI_DIR / "raw").glob(f"*.{build}.*.png")):
        part, _, facing = raw.stem.rsplit(".", 2)
        e, pb = part_entry(s, part, build)
        e["text"] = e["text"] or lib.get(part, {}).get("prompt", "")
        pb[facing].setdefault("candidates", [rel(raw)])
        pb[facing].setdefault("approved", rel(raw))
        if pb["status"] == "new":
            pb["status"] = "unreviewed"
    save_state(s)
    return s


# ----------------------------------------------------------------------------- sets & characters

SETS = ROOT / "sprites" / "sets"
CHARS = ROOT / "sprites" / "characters"


def safe(name):
    n = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()
    if not n:
        raise ValueError("Give it a name.")
    return n


def save_set(name, include_unreviewed=False):
    """Snapshot of the approved parts per build: what the kit should contain."""
    s = load_state()
    parts = {}
    for pid, e in s["parts"].items():
        ok = [b for b, pb in e["builds"].items()
              if pb.get("status") == "approved" or (include_unreviewed and pb.get("status") == "unreviewed")]
        if ok:
            parts[pid] = sorted(ok)
    builds = sorted(b for b, bd in s["builds"].items() if bd["front"].get("approved") and bd["back"].get("approved"))
    data = {"name": name, "created": time.strftime("%Y-%m-%d %H:%M"), "builds": builds, "parts": parts,
            "style": style()["groups"]}
    SETS.mkdir(parents=True, exist_ok=True)
    (SETS / f"{safe(name)}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def list_sets():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SETS.glob("*.json"))] if SETS.exists() else []


def load_set(name):
    p = SETS / f"{safe(name)}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_character(name, appearance, note=""):
    CHARS.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "saved": time.strftime("%Y-%m-%d %H:%M"), "note": note, "appearance": appearance}
    (CHARS / f"{safe(name)}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def list_characters():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CHARS.glob("*.json"))] if CHARS.exists() else []


def delete_character(name):
    (CHARS / f"{safe(name)}.json").unlink(missing_ok=True)


def url(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def image(relpath):
    p = (ai_kit.AI_DIR / relpath).resolve()
    if ai_kit.AI_DIR.resolve() not in p.parents:
        raise ValueError("outside the AI folder")
    return p.read_bytes()
