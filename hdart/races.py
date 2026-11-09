"""Character Workshop, Races tab: the race mannequins (specs/races.json), their backs and their skin ramp preview.

A race = one A-pose unisex mannequin, front and back: the front is painted from the style + race text (or edited from
another race's front, `from`), the back is an edit of the approved front. Candidates are PNGs under
sprites/ai/candidates/<race>/<sex>/, listed with their approval in workshop.json (hdart.workshop). Male and female
have no mannequin: the Paint step (hdart.puppet_paint) paints them from the Template. Job kind "race".

    races_doc / save_races, race_needs / race_prompt, gen_race, approve_race, race_state, archive_base, ramp_preview
    back_from_front(front_img, seed)   the older double-sided card: the back repainted from the mirrored front
"""
import base64
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import comfy, config, imageops, jobs, levels, palette, store, workshop as ws

BIG = 512   # the size (px) FLUX paints a mannequin at


# ----------------------------------------------------------------------------- races (Workshop "Races" tab)
# A race = three A-pose mannequins: <race>.unisex (generated), <race>.male / <race>.female (edits of the unisex front that
# keep head, face, pose, scale and feet), each with a back made as an edit of its front (perspective-correct 3/4 back).


def races_path() -> Path:
    """specs/races.json: the races and their mannequin prompts."""
    return config.specs_dir() / "races.json"


SEXES = ("unisex", "male", "female")
# only the unisex has an A-pose mannequin (the Template's reference art); male and female are painted from the Template
# (Paint step, hdart/puppet_paint.py). Their old mannequins were archived on 2026-10-24.
MANNEQUIN_SEXES = ("unisex",)
RACE_TAIL = ("Same pixel art style, same colours, same size and position in the frame, plain white background, "
             "nothing beneath the feet.")


def races_doc() -> dict:
    """specs/races.json: races (traits, prompts, `from`, flags), sexes, pose, base body."""
    return json.loads(races_path().read_text(encoding="utf-8"))


def save_races(doc: dict) -> dict:
    """Check and write specs/races.json (lowercase ids; a `from` race must exist)."""
    for rid, r in doc.get("races", {}).items():
        if not rid.replace("_", "").isalnum() or not rid.islower():
            raise ValueError(f"Race ids are lowercase letters, digits and _: {rid!r}")
        if r.get("from") and r["from"] not in doc["races"]:
            raise ValueError(f"{rid}: `from` race {r['from']!r} doesn't exist")
    races_path().write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return doc


def race_build(race: str, sex: str) -> str:
    """The build id of a race and sex: "<race>.<sex>"."""
    return f"{race}.{sex}"


def approved(s: dict, build: str, facing: str) -> str | None:
    """The approved mannequin image (relative to the AI folder) of a build and facing in workshop state s."""
    return s["builds"].get(build, {}).get(facing, {}).get("approved")


def race_needs(race, sex, facing, s=None, doc=None):
    """What a generation step starts from: ("txt2img", None) or ("edit", rel of the source image). Raises when the
    prerequisite isn't approved yet."""
    s = s or ws.load_state(); doc = doc or races_doc()
    r = doc["races"][race]
    if sex not in MANNEQUIN_SEXES:
        raise ValueError(f"Only the unisex has a mannequin; {sex} is painted from the Template (Paint step).")
    if facing == "back":
        src = approved(s, race_build(race, sex), "front")
        if not src:
            raise ValueError(f"Approve the {race} {sex} front first.")
        return "edit", src
    if sex == "unisex":
        if r.get("from"):
            src = approved(s, race_build(r["from"], "unisex"), "front")
            if not src:
                raise ValueError(f"{race} starts from {r['from']}: approve the {r['from']} unisex front first.")
            return "edit", src
        return "txt2img", None
    src = approved(s, race_build(race, "unisex"), "front")
    if not src:
        raise ValueError(f"Approve the {race} unisex front first.")
    return "edit", src


def race_prompt(race, sex, facing, doc=None, st=None):
    doc = doc or races_doc(); st = st or ws.style()
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
        groups = [g for g in doc.get("style_groups", ws.ORDER) if g in st["groups"]]
        parts = [ws.style_text(st, tuple(groups)), doc["pose"], r["traits"], r.get("description", ""),
                 doc["sexes"]["unisex"], body]
        return ", ".join(p.strip() for p in parts if p and p.strip())
    raise ValueError(f"Only the unisex has a mannequin; {sex} is painted from the Template (Paint step).")


def gen_race(race, sex, facing, n=4, seed=1):
    """Queue job: candidates for one race mannequin step. Each candidate records the image it was made from."""
    doc = races_doc(); s = ws.load_state()
    mode, src = race_needs(race, sex, facing, s, doc)
    prompt = race_prompt(race, sex, facing, doc)
    build = race_build(race, sex)
    b = s["builds"].setdefault(build, {"race": race, "sex": sex, "front": {}, "back": {}})
    out = ws.candidates_dir() / race / sex
    out.mkdir(parents=True, exist_ok=True)
    cands = []
    name = comfy.upload(ws.ai_dir() / src) if mode == "edit" else None
    for k in range(n):
        sd = seed + k
        if mode == "edit":
            nodes = comfy.build_edit(name, prompt, seed=sd, lora_strength=0.6, prefix=f"hdart/race_{race}_{sex}_{facing}")
        else:
            nodes = comfy.build_txt2img(prompt, BIG, BIG, seed=sd, lora_strength=1.0, prefix=f"hdart/race_{race}")
        dst = out / f"{facing}_s{sd}_{int(time.time())}.png"
        comfy.image(nodes).convert("RGB").resize((BIG, BIG), Image.LANCZOS).save(dst)
        r_ = ws.rel(dst)
        cands.append(r_)
        b[facing].setdefault("meta", {})[r_] = {"from": src, "seed": sd}
    with ws.edit_state() as s:   # merge: approvals may have happened while ComfyUI ran
        b2 = s["builds"].setdefault(build, {"race": race, "sex": sex, "front": {}, "back": {}})
        b2[facing]["candidates"] = cands + [c for c in b2[facing].get("candidates", []) if c not in cands]
        b2[facing].setdefault("meta", {}).update(b[facing]["meta"])
    return cands


def approve_race(build, facing, file):
    """Approve one race mannequin image. A new front invalidates that build's back."""
    if build.split(".", 1)[-1] not in MANNEQUIN_SEXES:
        raise ValueError("Only the unisex has a mannequin.")
    with ws.edit_state() as s:
        b = s["builds"][build]
        b[facing]["approved"] = file
        if facing == "front":
            b["back"].pop("approved", None)
    return b


def race_state(race):
    """For the Race tab: the unisex mannequin per facing (candidates, approval, whether it's stale = made from an image
    that is no longer the approved source). Male and female have no mannequin (painted from the Template)."""
    doc = races_doc(); s = ws.load_state()
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
    pal = palette.palettes_doc()
    out["ramps"] = {n: e for n, e in pal["groups"].get("skin", {}).items() if race in e.get("races", [])}
    return out


def ramp_preview(build):
    """For the ramp editor: the approved mannequin front/back plus a level map per facing (PNG data URLs: R = level, A = skin)."""
    s = ws.load_state()
    b = s["builds"].get(build, {})
    out = {"build": build, "facings": {}}
    for f in ("front", "back"):
        rel_ = b.get(f, {}).get("approved")
        if not rel_:
            continue
        img = Image.open(ws.ai_dir() / rel_).convert("RGB")
        lev, cent = levels.skin_levels(img, palette.palettes_doc().get("rampSize", 5))
        enc = np.zeros(lev.shape + (4,), np.uint8)
        enc[..., 0] = np.clip(lev, 0, 255)
        enc[..., 3] = np.where(lev >= 0, 255, 0)
        buf = io.BytesIO(); Image.fromarray(enc).save(buf, "PNG")
        out["facings"][f] = {"art": rel_, "levels": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
                             "key": cent}
    return out


# archive/workshop/<rel>: archived mannequin candidates, same relative path as under sprites/ai/ (nothing is deleted;
# delete archive/ by hand once you're sure)

def archive_base(build, facing, file=None):
    """Archive mannequin candidates of one build + facing: just `file`, or (no file) every candidate that isn't
    approved, plus the older files in the _base folder the board no longer lists. The approved one never moves.
    Returns how many files moved."""
    with ws.edit_state() as s:
        b = s["builds"].get(build, {}).get(facing, {})
        approved = b.get("approved")
        if file:
            if file == approved:
                raise ValueError("That's the approved mannequin. Approve another one first.")
            rels = {file}
        else:
            rels = {c for c in b.get("candidates", []) if c != approved}
            rels |= {ws.rel(p) for p in (ws.candidates_dir() / build / "_base").glob(f"{facing}_*.png")} - {approved}
        for r in rels:
            if (ws.ai_dir() / r).exists():
                store.archive(ws.ai_dir() / r, config.archive_dir() / "workshop" / r)
        if b:
            b["candidates"] = [c for c in b.get("candidates", []) if c not in rels]
    return len(rels)


# ----------------------------------------------------------------------------- double-sided card: back from the front

def mirror(img: Image.Image) -> Image.Image:
    """The image flipped left to right."""
    return img.transpose(Image.FLIP_LEFT_RIGHT)


BACK_DENOISE = 0.78


def back_prompt(what: str) -> str:
    """The prompt of the double-sided card's back pass."""
    st = ws.style()
    return (f"{ws.style_text(st, ('medium', 'proportions', 'rendering'))}, the same character as the reference seen from "
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
    return np.asarray(out.resize((BIG, BIG), Image.NEAREST))


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
    gap = sil & ~imageops.foreground(Image.fromarray(raw))
    if not gap.any():
        return raw
    soft = np.asarray(Image.fromarray(np.where(imageops.grow_cross(gap, 8) & sil, 255, 0).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(3)))
    pre = imageops.enforce_silhouette(Image.fromarray(raw), sil)
    fix = _i2i(pre, refs, prompt, seed + 100, lora, GAP_DENOISE, mask=np.where(sil, soft, 0))
    out = raw.copy()
    take = imageops.grow_cross(gap, 2) & sil
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
    sil = imageops.figure_mask(m)
    a = _blur_into(m, sil)
    a[~sil] = 255
    a = _outline(a, sil)
    prompt = back_prompt(what or "back of the head and body")
    out = _i2i(Image.fromarray(a), [m], prompt, seed, lora, denoise, mask=sil)
    out = _gap_repaint(out, sil, [m], prompt, seed, lora)
    return imageops.enforce_silhouette(Image.fromarray(out), sil)


# ----------------------------------------------------------------------------- Studio job kinds (hdart.jobs)

@jobs.kind("race")
def _job_race(a):
    return gen_race(a["race"], a["sex"], a["facing"], int(a.get("n", 4)), int(a.get("seed", 1)))
