"""Character Workshop, shared core: the race mannequins (Race tab), the shared workshop state, style prompts and ramps.

    workshop.json (sprites/ai/)   shared state: race mannequin candidates and approvals ("builds"), read by the puppet,
                                  Paint, Face and Item Workshop modules through load_state() / edit_state()
    specs/ai_style.json           the style groups every race prompt starts from (style(), style_text())
    specs/races.json              races and their sexes, traits and prompts (races_doc(), gen_race(), approve_race())
    specs/palettes.json           skin / hair / eye / material ramps (palettes_doc(), save_ramp(), skin_key())

Mannequins: a race's unisex front is painted from the style + race text, its back from the approved front
(back_from_front: the front mirrored as a silhouette, repainted as the back side). Job kind "race".
The retired part board (per-build mannequins, parts, cut editor, sets) was removed in A10 (see git history).
"""
import base64
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import ai_kit, comfy, config, jobs, levels, store

ROOT = Path(__file__).resolve().parent.parent
STATE = ai_kit.AI_DIR / "workshop.json"
CAND = ai_kit.AI_DIR / "candidates"
STYLE = config.specs_dir() / "ai_style.json"
ARCHIVE = config.data_dir() / "archive"  # git-ignored, shared with the Dungeon Forge
ORDER = ("medium", "style", "proportions", "view", "rendering", "framing", "world")


# ----------------------------------------------------------------------------- state & prompts

def _state():
    return store.State(STATE, {"builds": {}, "parts": {}})


def load_state():
    return _state().load()


def save_state(s):
    _state().save(s)


def edit_state():
    """`with edit_state() as s:` changes workshop.json under its lock (Workshop, Paint, Face and Item Workshop share it)."""
    return _state().edit()


def style():
    return json.loads(STYLE.read_text(encoding="utf-8"))


def style_text(st=None, groups=ORDER):
    st = st or style()
    return ", ".join(st["groups"][g]["text"] for g in groups if st["groups"][g]["text"].strip())


def rel(p):
    return str(Path(p).resolve().relative_to(ai_kit.AI_DIR.resolve())).replace("\\", "/")


# ----------------------------------------------------------------------------- double-sided card: back from the front

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


def _i2i(init, refs, prompt, seed, lora, denoise, mask=None):
    """img2img; `mask` (0..255 array, or bool) limits the redraw to it and keeps everything else exactly."""
    out = comfy.img2img(init, prompt, seed=seed, denoise=denoise, lora=lora, refs=refs, mask=mask, prefix="hdart/ws_back")
    return np.asarray(out.resize((ai_kit.BIG, ai_kit.BIG), Image.NEAREST))


def _grow(mask, n):
    for _ in range(n):
        mask = mask | np.roll(mask, 1, 0) | np.roll(mask, -1, 0) | np.roll(mask, 1, 1) | np.roll(mask, -1, 1)
    return mask


def figure_mask(img):
    """The figure's silhouette: foreground, plus the enclosed 'background' holes above the chin (eye whites and
    highlights are pure white, so the foreground test misses them; left as holes they come back as white glints).
    Holes below the chin (between arm and body, between the legs) are real background and stay."""
    fg = ai_kit.foreground(img)
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
        nxt = _grow(reach, 1) & bg
        if (nxt == reach).all():
            break
        reach = nxt
    return fg | (bg & ~reach & (np.arange(fg.shape[0])[:, None] <= chin))


def _outline(a, mask, width=3, colour=(40, 32, 30)):
    """Draw a dark line just inside the mask edge: tells FLUX where the figure ends."""
    inner = mask.copy()
    for _ in range(width):
        inner &= np.roll(inner, 1, 0) & np.roll(inner, -1, 0) & np.roll(inner, 1, 1) & np.roll(inner, -1, 1)
    a = a.copy()
    a[mask & ~inner] = colour
    return a


def _gap_repaint(raw, sil, refs, prompt, seed, lora):
    """Repaint only where FLUX left background inside the silhouette (grown 8 px, soft edge), so those holes get
    real paint instead of the clamp's smear. Only the hole pixels are taken from the repaint."""
    from PIL import ImageFilter
    gap = sil & ~ai_kit.foreground(Image.fromarray(raw))
    if not gap.any():
        return raw
    soft = np.asarray(Image.fromarray(np.where(_grow(gap, 8) & sil, 255, 0).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(3)))
    pre = enforce_silhouette(Image.fromarray(raw), sil)
    fix = _i2i(pre, refs, prompt, seed + 100, lora, GAP_DENOISE, mask=np.where(sil, soft, 0))
    out = raw.copy()
    take = _grow(gap, 2) & sil
    out[take] = fix[take]
    return out


GAP_DENOISE = 0.9


def back_from_front(front_img, seed, lora=0.8, what="", denoise=BACK_DENOISE):
    """Mannequin back = reverse side of the front card: img2img from the blurred mirrored front (keeps the
    layout and colours, loses the face), mirrored front as appearance reference. Sampled only inside the
    silhouette (outside held white), with a dark outline along its edge so FLUX fills the shape to the line;
    holes it still leaves get a small repaint, then it's clamped to the silhouette.
    (Tested 2026-10-20: fill 89% -> 99.8%, no smears; eye-white holes filled = no glints on the head.)"""
    m = mirror(front_img.convert("RGB"))
    sil = figure_mask(m)
    a = _blur_into(m, sil)
    a[~sil] = 255
    a = _outline(a, sil)
    prompt = back_prompt(what or "back of the head and body")
    out = _i2i(Image.fromarray(a), [m], prompt, seed, lora, denoise, mask=sil)
    out = _gap_repaint(out, sil, [m], prompt, seed, lora)
    return enforce_silhouette(Image.fromarray(out), sil)


# ----------------------------------------------------------------------------- races (Workshop "Races" tab)
# A race = three A-pose mannequins: <race>.unisex (generated), <race>.male / <race>.female (edits of the unisex front that
# keep head, face, pose, scale and feet), each with a back made as an edit of its front (perspective-correct 3/4 back).

RACES = config.specs_dir() / "races.json"
SEXES = ("unisex", "male", "female")
# only the unisex has an A-pose mannequin (the Template's reference art); male and female are painted from the Template
# (Paint step, hdart/puppet_paint.py). Their old mannequins were archived on 2026-10-24.
MANNEQUIN_SEXES = ("unisex",)
RACE_TAIL = ("Same pixel art style, same colours, same size and position in the frame, plain white background, "
             "nothing beneath the feet.")


def races_doc():
    return json.loads(RACES.read_text(encoding="utf-8"))


def save_races(doc):
    for rid, r in doc.get("races", {}).items():
        if not rid.replace("_", "").isalnum() or not rid.islower():
            raise ValueError(f"Race ids are lowercase letters, digits and _: {rid!r}")
        if r.get("from") and r["from"] not in doc["races"]:
            raise ValueError(f"{rid}: `from` race {r['from']!r} doesn't exist")
    RACES.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return doc


def race_build(race, sex):
    return f"{race}.{sex}"


def _approved(s, build, facing):
    return s["builds"].get(build, {}).get(facing, {}).get("approved")


def race_needs(race, sex, facing, s=None, doc=None):
    """What a generation step starts from: ("txt2img", None) or ("edit", rel of the source image). Raises when the
    prerequisite isn't approved yet."""
    s = s or load_state(); doc = doc or races_doc()
    r = doc["races"][race]
    if sex not in MANNEQUIN_SEXES:
        raise ValueError(f"Only the unisex has a mannequin; {sex} is painted from the Template (Paint step).")
    if facing == "back":
        src = _approved(s, race_build(race, sex), "front")
        if not src:
            raise ValueError(f"Approve the {race} {sex} front first.")
        return "edit", src
    if sex == "unisex":
        if r.get("from"):
            src = _approved(s, race_build(r["from"], "unisex"), "front")
            if not src:
                raise ValueError(f"{race} starts from {r['from']}: approve the {r['from']} unisex front first.")
            return "edit", src
        return "txt2img", None
    src = _approved(s, race_build(race, "unisex"), "front")
    if not src:
        raise ValueError(f"Approve the {race} unisex front first.")
    return "edit", src


def race_prompt(race, sex, facing, doc=None, st=None):
    doc = doc or races_doc(); st = st or style()
    r = doc["races"][race]
    body = doc.get("base_body", "")
    if facing == "back":
        return ("Show this exact character from behind: a three-quarter back view facing away to the right, seen slightly "
                "from above, back of the head, no face visible, the same A-pose (both arms straight out and down about 30 "
                "degrees from the body with white space between arm and torso, legs apart at shoulder width), the same "
                f"{body}, no ground shadow. " + RACE_TAIL)
    if sex == "unisex" and r.get("from"):
        return r["from_edit"] + " " + RACE_TAIL
    if sex == "unisex":
        groups = [g for g in doc.get("style_groups", ORDER) if g in st["groups"]]
        parts = [style_text(st, tuple(groups)), doc["pose"], r["traits"], r.get("description", ""),
                 doc["sexes"]["unisex"], body]
        return ", ".join(p.strip() for p in parts if p and p.strip())
    raise ValueError(f"Only the unisex has a mannequin; {sex} is painted from the Template (Paint step).")


def gen_race(race, sex, facing, n=4, seed=1):
    """Queue job: candidates for one race mannequin step. Each candidate records the image it was made from."""
    doc = races_doc(); s = load_state()
    mode, src = race_needs(race, sex, facing, s, doc)
    prompt = race_prompt(race, sex, facing, doc)
    build = race_build(race, sex)
    b = s["builds"].setdefault(build, {"race": race, "sex": sex, "front": {}, "back": {}})
    out = CAND / race / sex
    out.mkdir(parents=True, exist_ok=True)
    cands = []
    name = comfy.upload(ai_kit.AI_DIR / src) if mode == "edit" else None
    for k in range(n):
        sd = seed + k
        if mode == "edit":
            nodes = comfy.build_edit(name, prompt, seed=sd, lora_strength=0.6, prefix=f"hdart/race_{race}_{sex}_{facing}")
        else:
            nodes = comfy.build_txt2img(prompt, ai_kit.BIG, ai_kit.BIG, seed=sd, lora_strength=1.0, prefix=f"hdart/race_{race}")
        dst = out / f"{facing}_s{sd}_{int(time.time())}.png"
        comfy.image(nodes).convert("RGB").resize((ai_kit.BIG, ai_kit.BIG), Image.LANCZOS).save(dst)
        r_ = rel(dst)
        cands.append(r_)
        b[facing].setdefault("meta", {})[r_] = {"from": src, "seed": sd}
    with edit_state() as s:   # merge: approvals may have happened while ComfyUI ran
        b2 = s["builds"].setdefault(build, {"race": race, "sex": sex, "front": {}, "back": {}})
        b2[facing]["candidates"] = cands + [c for c in b2[facing].get("candidates", []) if c not in cands]
        b2[facing].setdefault("meta", {}).update(b[facing]["meta"])
    return cands


def approve_race(build, facing, file):
    """Approve one race mannequin image. A new front invalidates that build's back."""
    if build.split(".", 1)[-1] not in MANNEQUIN_SEXES:
        raise ValueError("Only the unisex has a mannequin.")
    with edit_state() as s:
        b = s["builds"][build]
        b[facing]["approved"] = file
        if facing == "front":
            b["back"].pop("approved", None)
    return b


def race_state(race):
    """For the Race tab: the unisex mannequin per facing (candidates, approval, whether it's stale = made from an image
    that is no longer the approved source). Male and female have no mannequin (painted from the Template)."""
    doc = races_doc(); s = load_state()
    out = {"race": race, "spec": doc, "builds": {}}
    for sex in MANNEQUIN_SEXES:
        build = race_build(race, sex)
        b = s["builds"].get(build, {"front": {}, "back": {}})
        row = {}
        for f in ("front", "back"):
            d = b.get(f, {})
            try:
                _, src = race_needs(race, sex, f, s, doc); ready = True
            except ValueError as e:
                src, ready = None, str(e)
            meta = d.get("meta", {})
            row[f] = {"candidates": d.get("candidates", []), "approved": d.get("approved"), "ready": ready,
                      "prompt": race_prompt(race, sex, f, doc), "mode": "edit" if (f == "back" or
                                                                                 doc["races"][race].get("from")) else "txt2img",
                      "stale": {c: bool(meta.get(c, {}).get("from")) and meta[c]["from"] != src for c in d.get("candidates", [])}}
        out["builds"][sex] = row
    pal = palettes_doc()
    out["ramps"] = {n: e for n, e in pal["groups"].get("skin", {}).items() if race in e.get("races", [])}
    return out


# ----------------------------------------------------------------------------- ramps (Workshop "Ramps" tab)
# A skin tone is a palette ramp (specs/palettes.json, group "skin"): the art stores cel levels, the game colours them.

PALETTES = config.specs_dir() / "palettes.json"


def skin_mask(a):
    """Skin pixels of an RGB array: warm (red over blue) or cool (green or blue over red: orcs and the like; human skin
    never is) and saturated enough, not the grey clothes or the outline."""
    a = a.astype(int)
    mx, mn = a.max(-1), a.min(-1)
    hue = (a[..., 0] > a[..., 2] + 18) | (np.maximum(a[..., 1], a[..., 2]) > a[..., 0] + 8)
    m = hue & ((mx - mn) > 0.16 * np.maximum(mx, 1)) & (mx > 70)
    lum = a.mean(-1)
    grey = ((mx - mn) < 0.12 * np.maximum(mx, 1)) & (lum < 200)
    near = np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)
    bg = ~ai_kit.foreground(Image.fromarray(a.astype(np.uint8)))
    return m | (near & ~bg & (lum > 90) & ~grey)  # light anti-aliased skin edges count too


LUMA = np.array([0.299, 0.587, 0.114])


def _skin_val(a, m):
    """Brightness of the skin pixels m, blurred inside the skin. The light anti-aliased rim (2 px) is left out of the
    blur and takes its inner neighbours' value: it would otherwise become a pale outline band."""
    core = m
    for _ in range(2):
        core = core & np.roll(core, 1, 0) & np.roll(core, -1, 0) & np.roll(core, 1, 1) & np.roll(core, -1, 1)
    return levels.smooth_val(a.astype(float) @ LUMA, core if core.any() else m)


def skin_cut(a, m, cent):
    """Level map of the skin pixels m of RGB array a (-1 = not skin): the blurred brightness cut at the levels' (cent,
    RGB) brightnesses, then small patches merged and band edges smoothed (hdart.levels). Hand-painted brushwork and
    FLUX's soft gradients otherwise quantize into splotches once a ramp colours them."""
    lev = np.full(m.shape, -1, np.int16)
    if not len(cent) or not m.any():
        return lev
    vs = _skin_val(a, m); cl = np.asarray(cent, float) @ LUMA
    L = np.zeros(m.shape + (4,), np.uint8)
    L[..., 0][m] = np.abs(vs[m][:, None] - cl[None]).argmin(1); L[..., 3][m] = 255
    island = max(1, int(levels.ISLAND * (m.shape[0] / levels.CANVAS) ** 2))   # ISLAND is for the rest-pose canvas
    lev[m] = levels.smooth_levels(L, island)[..., 0][m]
    return lev


def skin_key(img, size=5):
    """The skin's cel level colours: k-means on the skin colours, sorted dark -> light (RGB centres; [] = no skin)."""
    a = np.asarray(img.convert("RGB"))
    px = a[skin_mask(a)].astype(float)
    if len(px) < size:
        return []
    lum = px.sum(1); order0 = np.argsort(lum)  # start from evenly spread brightness percentiles (deterministic)
    cent = px[order0[(np.linspace(0.08, 0.92, size) * (len(px) - 1)).astype(int)]]
    for _ in range(30):
        lab = ((px[:, None] - cent[None]) ** 2).sum(-1).argmin(1)
        cent = np.array([px[lab == i].mean(0) if (lab == i).any() else cent[i] for i in range(size)])
    return [[int(round(c)) for c in cc] for cc in cent[np.argsort(cent.sum(1))]]


def skin_levels(img, size=5, seed=0):
    """Cel levels of the skin: the skin key (skin_key) and the level map cut from the blurred brightness and smoothed
    (skin_cut). Returns (level map: -1 = not skin, the level centres as RGB)."""
    a = np.asarray(img.convert("RGB"))
    cent = skin_key(img, size)
    if not cent:
        return np.full(a.shape[:2], -1, np.int16), []
    return skin_cut(a, skin_mask(a), cent), cent


def ramp_preview(build):
    """For the ramp editor: the approved mannequin front/back plus a level map per facing (PNG data URLs: R = level, A = skin)."""
    s = load_state()
    b = s["builds"].get(build, {})
    out = {"build": build, "facings": {}}
    for f in ("front", "back"):
        rel_ = b.get(f, {}).get("approved")
        if not rel_:
            continue
        img = Image.open(ai_kit.AI_DIR / rel_).convert("RGB")
        lev, cent = skin_levels(img, json.loads(PALETTES.read_text(encoding="utf-8")).get("rampSize", 5))
        enc = np.zeros(lev.shape + (4,), np.uint8)
        enc[..., 0] = np.clip(lev, 0, 255)
        enc[..., 3] = np.where(lev >= 0, 255, 0)
        buf = io.BytesIO(); Image.fromarray(enc).save(buf, "PNG")
        out["facings"][f] = {"art": rel_, "levels": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
                             "key": cent}
    return out


def palettes_doc():
    return json.loads(PALETTES.read_text(encoding="utf-8"))


def save_ramp(group, name, base=None, tune=None, colors=None, races=None, old_name=None):
    """Create or update one ramp in specs/palettes.json. `colors` (resolved hex, darkest first) is what the game uses;
    `base` and `tune` are kept so the ramp editor can reopen it."""
    if not name or not all(ch.isalnum() or ch in "_-" for ch in name):
        raise ValueError("Ramp names are letters, digits, _ and - only.")
    doc = palettes_doc()
    grp = doc["groups"].setdefault(group, {})
    if old_name and old_name != name:
        if name in grp:
            raise ValueError(f"{group}.{name} already exists.")
        grp = {(name if k == old_name else k): v for k, v in grp.items()}
        doc["groups"][group] = grp
    e = grp.setdefault(name, {})
    if base:
        e["base"] = base
    if tune:
        e["tune"] = tune
    if colors:
        if len(colors) != doc.get("rampSize", 5):
            raise ValueError(f"A ramp has {doc.get('rampSize', 5)} colours.")
        e["colors"] = colors
    if races is not None:
        if races:
            e["races"] = races
        else:
            e.pop("races", None)
    PALETTES.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return doc


def delete_ramp(group, name):
    doc = palettes_doc()
    doc["groups"].get(group, {}).pop(name, None)
    PALETTES.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return doc


# archive/workshop/<rel>: archived mannequin candidates, same relative path as under sprites/ai/ (nothing is deleted;
# delete archive/ by hand once you're sure)

def archive_base(build, facing, file=None):
    """Archive mannequin candidates of one build + facing: just `file`, or (no file) every candidate that isn't
    approved, plus the older files in the _base folder the board no longer lists. The approved one never moves.
    Returns how many files moved."""
    with edit_state() as s:
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
            if (ai_kit.AI_DIR / r).exists():
                store.archive(ai_kit.AI_DIR / r, ARCHIVE / "workshop" / r)
        if b:
            b["candidates"] = [c for c in b.get("candidates", []) if c not in rels]
    return len(rels)


# ----------------------------------------------------------------------------- images for the pages

def url(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def image(relpath):
    p = (ai_kit.AI_DIR / relpath).resolve()
    if ai_kit.AI_DIR.resolve() not in p.parents:
        raise ValueError("outside the AI folder")
    return p.read_bytes()


# ----------------------------------------------------------------------------- Studio job kinds (hdart.jobs)

@jobs.kind("race")
def _job_race(a):
    return gen_race(a["race"], a["sex"], a["facing"], int(a.get("n", 4)), int(a.get("seed", 1)))

