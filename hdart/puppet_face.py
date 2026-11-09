"""Face tab: racial hair and faces on the painted puppets (2026-10-24, v1).

Male and female are painted on the same generic Template, so their heads have the same shape in the same place: a
hairstyle or face made on one build fits every build of the race. Layers are 1152 px RGBA in the rest pose (the paint
parts' canvas) and ride on the head bone.

  hair   FLUX paints hair around the head of the race's painted puppet (approved Paint of the chosen build, else its
         newest): the mask is the head grown outward plus the band above it, minus the face (from the Template's face
         guides). The hair layer = the pixels that changed (hair over the scalp and beyond the head's outline), cleaned.
         Front first, then the back with the painted front as reference.
  face   FLUX repaints the face area (an ellipse around the eyes and mouth from the face guides), front only; the face
         layer keeps only the feature pixels (eyes, brows, nose, mouth), so the skin underneath still takes the ramps.

Candidates: sprites/ai/face/<race>/<kind>/<id>/ (layer_<view>.png, prev_<sex>_<view>.png, meta.json). A race's hair
set and face set (state: builds["<race>.unisex"]["face_sets"]) are the candidates added to them, like skin tone sets.
"""
import copy, json, shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import config, comfy, imageops, jobs, memo, palette, rig, store, workshop as ws, puppet as pp, puppet_template as pt, puppet_paint as ppt
from .levels import (blank_face, brightness_centres, colour_levels, head_skin_key, hue_dist, keep_bands, key_hue,
                     nearest_level, skin_dist, write_bands)

SPEC = config.specs_dir() / "face.json"
KINDS = ("hair", "face")
SEXES = ("male", "female")
S = rig.S
N = rig.N
D_HAIR, D_FACE = 0.85, 0.7


HAIR_BACK_EDIT = ("Show this exact character from behind: a three-quarter back view facing away to the right, seen slightly "
                  "from above, the back of the head with no face visible, the same {hair} in the same flat vivid green colour "
                  "key, shaded with darker and lighter green, clean dark outlines, the same size and position in the frame, "
                  "plain white background.")


def spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def save_spec(doc):
    old = spec()
    for v in ("front", "back"):
        if "{hair}" not in str((doc.get("hair") or {}).get(v, "")):
            raise ValueError(f"hair.{v} must contain {{hair}}")
    if "{face}" not in str(doc.get("face", "")):
        raise ValueError("face must contain {face}")
    races = doc.get("races") or {}
    for r, e in races.items():
        if not isinstance(e, dict) or not all(isinstance(e.get(k, ""), str) for k in KINDS):
            raise ValueError(f"races.{r} needs text fields hair and face")
    key = lambda k, d_: str(doc.get(k) or old.get(k) or d_)   # a race text save from the tab doesn't send the keys
    for k in ("hair_key", "eye_key"):
        v = key(k, "")
        if v and not (len(v) == 7 and v.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in v[1:])):
            raise ValueError(f"{k} must be a #rrggbb colour")
    out = {"_doc": old.get("_doc", ""), "hair_key": key("hair_key", "#22c822"), "eye_key": key("eye_key", "#d020d0"),
           "hair": doc["hair"], "hair_back_edit": str(doc.get("hair_back_edit") or old.get("hair_back_edit") or HAIR_BACK_EDIT),
           "face": doc["face"], "races": races}
    SPEC.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    return out


def out_dir(race, kind):
    return ws.AI_DIR / "face" / race / kind


def composed(race, sex, doc=None):
    """The prompts a hair / face generation on <race>.<sex> sends."""
    doc = doc or spec(); r = doc["races"].get(race, {})
    body, clothes = ppt.prompts(f"{race}.{sex}"); style = ppt.style_text()
    fill = lambda t: (t.replace("{style}", style).replace("{body}", body).replace("{hair}", r.get("hair", "").strip() or "hair")
                      .replace("{face}", r.get("face", "").strip() or "a face"))
    return {"hair": {v: fill(doc["hair"][v]) for v in ("front", "back")}, "hair_back_edit": fill(doc.get("hair_back_edit", HAIR_BACK_EDIT)),
            "face": fill(doc["face"])}


# ----------------------------------------------------------------------------- the painted puppet to work on

def base_cid(build):
    """The paint a build's items and faces sit on: its approved paint candidate, else the newest finished one, else None."""
    ap = ws.load_state()["builds"].get(build, {}).get("paint", {}).get("approved")
    if ap:   # the usual case, without listing every candidate's files
        return ap
    st = ppt.state(build)
    cands = sorted((c for c in st["candidates"] if not c["progress"]), key=lambda c: c["meta"].get("made", 0))
    return st["approved"] or (cands[-1]["id"] if cands else None)


_BASE = memo.LRU(4)   # build -> (key, (cid, spec, parts)); ~75 MB per build


def base_key(build, cid=None):
    """What base(build) depends on: the paint id and its part files / template (changes when they're edited or re-saved)."""
    cid = cid or base_cid(build)
    if not cid:
        return None
    d = ppt.out_dir(build) / cid
    return (build, cid, memo.stamp(d / "parts" / "front", d / "parts" / "back", d / "template.json", pt.SPEC))


def base(build, cid=None):
    """(candidate id, its template spec, {view: {part: RGBA}}) of a build's approved paint (else newest; `cid` = another
    paint, e.g. the Workshop Editor's). Cached in memory until the paint changes; the arrays are read-only (copy before
    drawing on one) and the dicts are the caller's own."""
    default = base_cid(build); cid = cid or default
    if not cid:
        raise ValueError(f"{build} has no paint yet: paint it first (Race → Paint).")
    slot = build if cid == default else f"{build}|{cid}"
    key = base_key(build, cid); hit = _BASE.get(slot)
    if not hit or hit[0] != key:
        d = ppt.out_dir(build) / cid; sp = ppt.template_for(d); out = {}
        for v in ("front", "back"):
            out[v] = {n: memo.frozen(np.asarray(Image.open(d / "parts" / v / f"{n}.png").convert("RGBA")))
                      for n in sp["views"][v]["order"]}
        hit = _BASE.put(slot, (key, (cid, sp, out)))
    cid, sp, out = hit[1]
    return cid, copy.deepcopy(sp), {v: dict(p) for v, p in out.items()}


def compose(sp, view, parts, layers=(), blank=False):
    """The rest pose with extra layers drawn right over the head (before the near arm); `blank` draws the head with its
    own face removed (for a face layer)."""
    cv = Image.new("RGBA", (N, N))
    for n in sp["views"][view]["order"]:
        im = parts[n]
        if n == "head" and blank and view == "front":   # a little wider than the new face (old lashes go too), never the outline
            im = blank_face(im, rig.face_ellipse(sp, 1.2) & rig.inner_head(im))
        cv.alpha_composite(Image.fromarray(im))
        if n == "head":
            for L in layers:
                if L is not None:
                    cv.alpha_composite(Image.fromarray(L))
    return cv


def face_guide(rgba, fc):
    """A strong, drawn face guide where the Template's face guides say (a simple face for FLUX to refine): almond eye
    whites with a thick upper lid line, magenta-key irises with a dark pupil and a highlight, brows, a nose tick and a
    mouth. The eye on the far side of the 3/4 face is drawn a little narrower."""
    im = Image.fromarray(rgba).convert("RGBA"); hi = Image.new("RGBA", im.size); d = ImageDraw.Draw(hi)
    h_ = spec().get("eye_key", "#d020d0").lstrip("#"); key = tuple(int(h_[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    dark, line = (34, 26, 24, 255), (60, 40, 34, 255)
    e0, e1 = np.array(fc["eyes"][0]) * S, np.array(fc["eyes"][1]) * S; w = np.linalg.norm(e1 - e0)
    m = np.array(fc["mouth"]) * S; lw = max(3, int(0.035 * w))
    for e, far in ((e0, e0[0] < e1[0]), (e1, e0[0] >= e1[0])):   # faces look left: the left eye is the far one
        rx, ry = (0.23 if far else 0.27) * w, 0.24 * w
        d.ellipse([e[0] - rx, e[1] - ry, e[0] + rx, e[1] + ry], fill=(246, 244, 240, 255))                    # white
        ir = 0.18 * w
        d.ellipse([e[0] - ir * (0.8 if far else 1.0), e[1] - ir, e[0] + ir * (0.8 if far else 1.0), e[1] + ir], fill=key)  # iris
        d.ellipse([e[0] - 0.4 * ir, e[1] - 0.45 * ir, e[0] + 0.4 * ir, e[1] + 0.45 * ir], fill=dark)                 # pupil
        d.ellipse([e[0] - 0.55 * ir, e[1] - 0.75 * ir, e[0] - 0.1 * ir, e[1] - 0.3 * ir], fill=(255, 255, 255, 255))  # highlight
        d.arc([e[0] - rx, e[1] - ry, e[0] + rx, e[1] + ry], 190, 350, fill=dark, width=lw * 2)                       # upper lid
        d.arc([e[0] - rx, e[1] - ry, e[0] + rx, e[1] + ry], 30, 150, fill=line, width=max(2, lw // 2))               # lower lid
        by = e[1] - 0.48 * w                                                                                        # brow
        d.line([(e[0] - rx * 1.05, by + 0.05 * w), (e[0], by - 0.02 * w), (e[0] + rx * 1.05, by + 0.03 * w)], fill=dark, width=lw)
    mid = (e0 + e1) / 2; nose = np.array([mid[0] - 0.08 * w, (mid[1] + m[1]) / 2])
    d.line([(nose[0], nose[1] - 0.05 * w), (nose[0] - 0.04 * w, nose[1] + 0.03 * w), (nose[0] + 0.02 * w, nose[1] + 0.04 * w)], fill=line, width=max(2, lw // 2))
    d.arc([m[0] - 0.14 * w, m[1] - 0.08 * w, m[0] + 0.14 * w, m[1] + 0.05 * w], 20, 160, fill=line, width=max(2, lw // 2 + 1))
    a = np.asarray(hi)[..., 3] > 0; out = np.asarray(im).copy(); out[a] = np.asarray(hi)[a]; out[..., 3] = rgba[..., 3]
    return out


def _paint(init, mask, prompt, seed, denoise, tag, refs=()):
    return ppt.run_pass(init, mask, prompt, seed, denoise, tag, refs)


def key_rgb():
    h = spec().get("hair_key", "#22c822").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def key_layer(before, after, region, head, levels=5):
    """Colour-key cut-out of the hair: pixels near the key hue (saturated enough) + the dark outline pixels touching
    them, only pieces that touch the head. Returns (preview RGBA as painted, levels RGBA: R = cel level 0..levels-1 by
    brightness, 254 = outline, A = hair)."""
    h, sat, val = imageops.hsv(after)
    dh = hue_dist(h, key_hue(key_rgb()))
    hair = region & (dh < 35) & (sat > 0.28) & (val > 0.12)
    hair = np.asarray(Image.fromarray(np.where(hair, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))) > 0
    hair &= region
    near = np.asarray(Image.fromarray(np.where(hair, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))) > 0
    changed = np.abs(after.astype(int) - before.astype(int)).max(-1) > 38
    line = region & near & ~hair & changed & (val < 0.35)
    m = imageops.components(hair | line, 60)
    if head is not None:   # hair grows from the head: drop pieces that don't touch it
        hn = np.asarray(Image.fromarray(np.where(head, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 0
        keep = m & hn
        for _ in range(3000):
            g_ = (keep | np.roll(keep, 1, 0) | np.roll(keep, -1, 0) | np.roll(keep, 1, 1) | np.roll(keep, -1, 1)) & m
            if (g_ == keep).all():
                break
            keep = g_
        m = keep
    hair &= m; line &= m
    L = np.zeros((N, N, 4), np.uint8); L[m, :3] = after[m]; L[m, 3] = 255
    lev = np.zeros((N, N, 4), np.uint8)
    if hair.any():
        vs = np.asarray(Image.fromarray((val * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(5)), float) / 255
        v_ = vs[hair]   # smoothed: no speckled levels
        lev[..., 0][hair] = nearest_level(v_, brightness_centres(v_, levels))
    lev[..., 0][line] = 254; lev[..., 3][m] = 255
    return L, lev


def iris_levels(layer, levels=5, eyes=None):
    """The irises of a face layer by colour key (`eye_key`, magenta), only near the eye guides (blush is pinkish too):
    levels RGBA (R = cel level by brightness, A = iris)."""
    h_ = spec().get("eye_key", "#d020d0").lstrip("#")
    h, sat, val = imageops.hsv(layer[..., :3])
    dh = hue_dist(h, key_hue([int(h_[i:i + 2], 16) for i in (0, 2, 4)]))
    iris = (layer[..., 3] > 127) & (dh < 32) & (sat > 0.42) & (val > 0.1)   # blush is paler (lower saturation)
    if eyes:
        e0, e1 = np.array(eyes[0]) * S, np.array(eyes[1]) * S; r = 0.85 * np.linalg.norm(e1 - e0)
        yy, xx = np.mgrid[:layer.shape[0], :layer.shape[1]]
        iris &= (np.hypot(xx - e0[0], yy - e0[1]) < r) | (np.hypot(xx - e1[0], yy - e1[1]) < r)
    # the iris edge (anti-aliased magenta next to the core) belongs to it too
    near = np.asarray(Image.fromarray(np.where(iris, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    iris |= near & (layer[..., 3] > 127) & (dh < 55) & (sat > 0.18)
    lev = np.zeros(layer.shape, np.uint8)
    if iris.any():
        v_ = val[iris]
        lev[..., 0][iris] = nearest_level(v_, brightness_centres(v_, levels)); lev[..., 3][iris] = 255
    return lev


def group_ramp(group, name=None):
    """The colours (dark -> light, RGB) of a ramp of a palettes group; its first ramp by default."""
    from .palette import ramp_from_base
    doc = palette.palettes_doc(); grp = doc["groups"].get(group, {})
    e = grp.get(name) if name else None
    e = e or (next(iter(grp.values())) if grp else {"base": "#7a4a2b"})
    if e.get("colors"):
        return [tuple(int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for c in e["colors"]]
    return ramp_from_base(e["base"], doc.get("rampSize", 5))


def hair_ramp(name=None):
    """The colours (dark -> light, RGB) of a hair ramp (palettes group "hair"); the first one by default."""
    from .palette import ramp_from_base
    doc = palette.palettes_doc(); grp = doc["groups"].get("hair", {})
    e = grp.get(name) if name else None
    e = e or (next(iter(grp.values())) if grp else {"base": "#7a4a2b"})
    if e.get("colors"):
        return [tuple(int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for c in e["colors"]]
    return ramp_from_base(e["base"], doc.get("rampSize", 5))   # a base-only ramp, as the Ramps tab shows it


def _layer(before, after, region, thresh, min_px, key=None, skin_far=40, touch=None, open_=True):
    """The pixels FLUX changed inside `region` as an RGBA layer (rest-pose canvas); with a skin key, only pixels that aren't
    skin (so re-shaded skin stays out of the layer); with `touch`, only pieces that touch that mask (hair grows from
    the head)."""
    d = np.abs(after.astype(int) - before.astype(int)).max(-1)
    ch = (d > thresh) & region
    if key is not None:
        ch &= skin_dist(after, key) > skin_far
    if open_:   # drop specks (faces skip this: their lashes and outlines are 1-2 px lines)
        ch = np.asarray(Image.fromarray(np.where(ch, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))) > 0
    ch = imageops.components(ch & region, min_px)
    if touch is not None:   # drop pieces that don't touch the head (stray shapes FLUX drew beside it)
        near = np.asarray(Image.fromarray(np.where(touch, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 0
        keep = np.zeros_like(ch); seen = ch & near
        ys, xs = np.nonzero(seen)
        if len(ys):   # flood from the touching pixels through the layer
            keep = seen.copy()
            for _ in range(2000):
                g_ = (keep | np.roll(keep, 1, 0) | np.roll(keep, -1, 0) | np.roll(keep, 1, 1) | np.roll(keep, -1, 1)) & ch
                if (g_ == keep).all():
                    break
                keep = g_
        ch = keep
    L = np.zeros((N, N, 4), np.uint8); L[ch, :3] = after[ch]; L[ch, 3] = 255
    return L


# ----------------------------------------------------------------------------- generation

def crop_pass(comp: Image.Image, region, box: tuple, prompt: str, seed: int, denoise: float, tag: str, refs=()) -> tuple:
    """Paint `region` of the composite inside `box` (resized to the 1024 px FLUX sheet). Returns (the composite on white
    as RGB, the painted full-size RGB, the sheet init, FLUX's output)."""
    bg = Image.new("RGBA", (N, N), (255, 255, 255, 255)); bg.alpha_composite(comp)
    full = bg.convert("RGB"); side = box[2] - box[0]
    init = full.crop(box).resize((ppt.SHEET, ppt.SHEET), Image.LANCZOS)
    mk = Image.fromarray(np.where(region, 255, 0).astype(np.uint8)).crop(box).resize((ppt.SHEET, ppt.SHEET), Image.NEAREST)
    out = _paint(init, np.asarray(mk) > 0, prompt, seed, denoise, tag, refs)
    painted = np.asarray(full).copy()
    painted[box[1]:box[3], box[0]:box[2]] = np.asarray(out.resize((side, side), Image.LANCZOS))
    return np.asarray(full), painted, init, out


def _draw_back_guide(comp, head):
    """Key-green cap over the back of the skull (shaded like the head), crown, strands, darker underside, outline."""
    key = np.array(key_rgb(), float); ys, xs = np.nonzero(head)
    hy0, hy1, hx0, hx1 = ys.min(), ys.max(), xs.min(), xs.max(); hh, hw = hy1 - hy0, hx1 - hx0
    grow_ = lambda m, k: np.asarray(Image.fromarray(np.where(m, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * k + 1))) > 0
    a = np.asarray(comp).copy(); yy = np.arange(N)[:, None]
    cap = grow_(head, 4) & (yy < hy0 + 0.86 * hh)
    lum = a[..., :3].astype(float).mean(-1, keepdims=True) / 200.0
    a[cap, :3] = np.clip(key * (0.72 + 0.35 * lum[cap]), 0, 255).astype(np.uint8); a[cap, 3] = 255
    a[cap & (yy > hy0 + 0.7 * hh), :3] = np.clip(key * 0.62, 0, 255).astype(np.uint8)
    im = Image.fromarray(a); dr = ImageDraw.Draw(im)
    crown = (hx0 + 0.62 * hw, hy0 + 0.14 * hh); dark = tuple(int(c) for c in key * 0.5) + (255,)
    for t in np.linspace(0.05, 0.95, 11):
        tx = hx0 + t * hw; ty = hy0 + 0.86 * hh - 0.25 * hh * abs(t - 0.5)
        dr.line([crown, ((crown[0] + tx) / 2 + 0.04 * hw, (crown[1] + ty) / 2), (tx, ty)], fill=dark, width=3)
    dr.ellipse([crown[0] - 6, crown[1] - 6, crown[0] + 6, crown[1] + 6], fill=dark)
    out = np.asarray(im).copy()
    rim = cap & ~(np.asarray(Image.fromarray(np.where(cap, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(7))) > 0)
    out[rim, :3] = (34, 26, 24)
    return out


def _hair_back(race, fid, sp, vparts, P, sd, d):
    """The back hair (AB2): a FLUX edit of the just-painted front head into a back view (keeps the hairstyle, e.g. a
    braid), mapped onto the back head and cut by the key; the guide = the drawn back cap underneath + that hair on top;
    one masked pass on the whole puppet (figure framing, mask = around the head + the upper back for long hair, never
    the arms) with the front as reference; cut by the key."""
    grow_ = lambda m, k: np.asarray(Image.fromarray(np.where(m, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * k + 1))) > 0
    head = vparts["head"][..., 3] > 127; ys, xs = np.nonzero(head)
    hy0, hy1, hx0, hx1 = ys.min(), ys.max(), xs.min(), xs.max(); hh, hw = hy1 - hy0, hx1 - hx0; cx = (hx0 + hx1) // 2
    hbox, _ = rig.head_box(vparts)
    comp0 = compose(sp, "back", vparts)
    a0 = np.asarray(comp0); al = a0[..., 3:4] / 255.0; bald = (a0[..., :3] * al + 255 * (1 - al)).astype(np.uint8)
    around = grow_(head, int(0.16 * (hbox[2] - hbox[0])))
    body = np.zeros_like(head); arms = np.zeros_like(head)
    for n_, p_ in vparts.items():
        if n_ != "head":
            body |= p_[..., 3] > 127
        if n_.startswith("arm"):
            arms |= p_[..., 3] > 127
    lane = np.zeros_like(head); lane[max(0, hy1 - int(0.1 * hh)): hy1 + int(0.9 * hh), cx - int(0.38 * hw): cx + int(0.38 * hw)] = True
    region = ((around & ~(body & ~around)) | lane) & ~arms
    # 1. the hairstyle from behind: an edit of the painted front
    name = comfy.upload(d / "paint_front.png")
    ed = comfy.image(comfy.build_edit(name, P["hair_back_edit"], seed=sd, lora_strength=0.6, prefix="hdart/hairback"))
    ed = ed.convert("RGB").resize((ppt.SHEET, ppt.SHEET), Image.LANCZOS); ed.save(d / "edit_back.png")
    side = hbox[2] - hbox[0]; mapped = bald.copy()
    mapped[hbox[1]:hbox[3], hbox[0]:hbox[2]] = np.asarray(ed.resize((side, side), Image.LANCZOS))
    Lb, _ = key_layer(bald, mapped, region | grow_(head, 30), head)
    # 2. the guide: the drawn cap (coverage) + the edited hair (style)
    g = _draw_back_guide(comp0, head); m = Lb[..., 3] > 0; g[m] = Lb[m]
    # 3. one masked pass on the whole puppet
    box = rig.figure_box(comp0)
    _, after, _, out = crop_pass(Image.fromarray(g), region, box, P["hair"]["back"], sd, 0.7, f"{race}_{fid}_hair_back",
                                  [d / "paint_front.png"])
    out.save(d / "paint_back.png")
    return key_layer(bald, after, region, head)


def generate(race, kind, sex="male", n=1, seed=1):
    """Queue job: n hair or face candidates for a race, painted on <race>.<sex>'s puppet; previews on male and female."""
    if kind not in KINDS:
        raise ValueError(f"kind is one of {KINDS}")
    build = f"{race}.{sex}"; cid, sp, parts = base(build); P = composed(race, sex)
    made = []
    for i in range(n):
        sd = seed + i; fid, d = store.new(out_dir(race, kind), sd)
        try:
            _make_one(race, kind, sex, build, cid, sp, parts, P, d, fid, sd)
        except Exception as e:
            store.progress(d, 0, 3, "failed", sd, str(e)); raise
        (d / "progress.json").unlink(missing_ok=True)
        made.append(fid)
    return made


def _make_one(race, kind, sex, build, cid, sp, parts, P, d, fid, sd):
    """One hair / face candidate, reporting its progress."""
    of = 3 if kind == "hair" else 2
    if True:
        layers, levels = {}, {}
        if kind == "hair":
            ref = None
            for v in ("front", "back"):
                store.progress(d, 1 if v == "front" else 2, of, f"hair ({v})", sd)
                if v == "back":   # AB2 (2026-10-25 test): edit of the front -> cap + that hair as the guide -> framed pass
                    layers[v], levels[v] = _hair_back(race, fid, sp, parts[v], P, sd, d)
                    continue
                box, head = rig.head_box(parts[v])
                region = np.zeros((N, N), bool); region[box[1]:box[3], box[0]:box[2]] = True
                g = int(0.16 * (box[2] - box[0]))   # hair volume: a band around the head (no free-floating shapes above it)
                around = np.asarray(Image.fromarray(np.where(head, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * g + 1))) > 0
                ys = np.nonzero(head)[0]
                region &= around & ~(rig.face_ellipse(sp, 1.05) if v == "front" else np.zeros_like(head))
                body = np.zeros((N, N), bool)   # never repaint the rest of the body (only the head and around it)
                for nm, p_ in parts[v].items():
                    if nm != "head":
                        body |= p_[..., 3] > 127
                region &= ~(body & ~around)
                key = head_skin_key(parts[v]["head"])
                # the hair guide: a hair-coloured cap (above the brows in front, the whole back of the head behind), shaded
                # like the head, so FLUX paints a full head of hair instead of keeping the bald skull
                hy0, hy1 = ys.min(), ys.max(); hh = hy1 - hy0
                gs = int(0.04 * (box[2] - box[0]))   # the guide hugs the head (FLUX may grow the hair into `around`)
                around_guide = np.asarray(Image.fromarray(np.where(head, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * gs + 1))) > 0
                if v == "front":
                    fc = sp["views"]["front"]["shapes"]["head"]["face"]
                    e0, e1 = np.array(fc["eyes"][0]) * S, np.array(fc["eyes"][1]) * S
                    brow = (e0[1] + e1[1]) / 2 - 0.42 * np.linalg.norm(e1 - e0)
                    cap = (around_guide & (np.arange(N)[:, None] < brow)) & ~rig.face_ellipse(sp, 1.05)
                else:
                    cap = around_guide & (np.arange(N)[:, None] < hy0 + 0.82 * hh)
                hc = np.array(key_rgb(), float)   # colour key: the hair is painted in one key colour, cut out by hue
                comp = compose(sp, v, parts[v]); ca = np.asarray(comp).copy()
                lum = ca[..., :3].astype(float).mean(-1, keepdims=True) / 200.0
                ca[cap, :3] = np.clip(hc * (0.75 + 0.35 * lum[cap]), 0, 255).astype(np.uint8); ca[cap, 3] = 255
                comp = Image.fromarray(ca)
                before, after, init, out = crop_pass(comp, region, box, P["hair"][v], sd, D_HAIR, f"{race}_{fid}_hair_{v}",
                                                      [ref] if ref else [])
                before = np.asarray(compose(sp, v, parts[v]).convert("RGBA"))   # the layer = what differs from the bald head
                bgw = np.full((N, N, 3), 255, np.uint8); al = before[..., 3:4] / 255.0
                before = (before[..., :3] * al + bgw * (1 - al)).astype(np.uint8)
                out.save(d / f"paint_{v}.png")
                if v == "front":
                    ref = d / "paint_front.png"
                layers[v], levels[v] = key_layer(before, after, region, head)
        else:
            store.progress(d, 1, of, "face", sd)
            v = "front"; region = rig.face_ellipse(sp) & rig.inner_head(parts[v]["head"])   # off the jaw / chin outline
            key = head_skin_key(parts[v]["head"])
            comp = compose(sp, v, parts[v], blank=True)   # the whole faceless puppet as context
            box = rig.figure_box(comp)                       # framed like the Paint step's figure pass
            ca = np.asarray(comp).copy(); fc = sp["views"]["front"]["shapes"]["head"]["face"]
            comp = Image.fromarray(face_guide(ca, fc))    # a strong drawn face guide for FLUX to refine
            before, after, init, out = crop_pass(comp, region, box, P["face"], sd, D_FACE, f"{race}_{fid}_face")
            blank_ = np.asarray(compose(sp, v, parts[v], blank=True)); al = blank_[..., 3:4] / 255.0   # the layer = what differs
            before = (blank_[..., :3] * al + 255 * (1 - al)).astype(np.uint8)                          # from the blank head
            out.save(d / "paint_front.png")
            layers[v] = _layer(before, after, region, 22, 6, key[2:], 30, open_=False)   # lit skin levels only (dark lines stay in)
            levels[v] = iris_levels(layers[v], eyes=sp["views"]["front"]["shapes"]["head"]["face"]["eyes"])   # the irises (colour key) as cel levels for the eye ramps
        for v, L in layers.items():
            Image.fromarray(L).save(d / f"layer_{v}.png")
        for v, lv in levels.items():   # hair: its cel levels (R = level 0..4, 254 = outline; A = hair), coloured by a hair ramp
            Image.fromarray(lv).save(d / f"levels_{v}.png")
        keyed = {v: int((lv[..., 3] > 0).sum()) for v, lv in levels.items()}   # pixels the colour key found (0 = not recolourable)
        clipped = [v for v, L in layers.items() if (L[:2, :, 3] > 0).any()]    # cut flat by the canvas top (shown with a warning)
        prompt = P[kind] if kind == "face" else P["hair"]
        store.write_meta(d, kind, seed=sd, prompts={kind: prompt}, inputs={"paint": cid}, params={"sex": sex},
                         race=race, kind=kind, on=build, paint=cid, keyed=keyed, prompt=prompt, **({"clipped": clipped} if clipped else {}))
        store.progress(d, of, of, "previews on male and female", sd)
        previews(race, kind, fid)


def paint(build: str, n: int = 1, seed: int = 1) -> list:
    """The Paint step's job: n paint candidates of a build (puppet_paint.paint), each painted face harvested into a face
    candidate of the race (face_from_paint). Returns the paint candidate ids."""
    return ppt.paint(build, n, seed, face_harvest=lambda cid: face_from_paint(build.split(".", 1)[0], build, cid))


def face_from_paint(race, build, cid):
    """Harvest the face a paint candidate painted (face guide "keyed" or "marks") into a face candidate of the race:
    the non-skin pixels of its head's face area (kept off the outline) = the face layer, its magenta-key irises = the
    iris levels. The face was painted as part of the whole character, so it matches the race's style and lighting."""
    d0 = ppt.out_dir(build) / cid; sp = ppt.template_for(d0)
    head = np.asarray(Image.open(d0 / "parts" / "front" / "head.png").convert("RGBA"))
    region = rig.face_ellipse(sp, 1.2) & rig.inner_head(head)
    key = head_skin_key(head)[2:]
    feat = region & (skin_dist(head[..., :3], key) > 30)
    L = np.zeros_like(head); L[feat, :3] = head[feat, :3]; L[feat, 3] = 255
    lev = iris_levels(L, eyes=sp["views"]["front"]["shapes"]["head"]["face"]["eyes"])
    fid = f"p_{cid}"; d = out_dir(race, "face") / fid; d.mkdir(parents=True, exist_ok=True)
    Image.fromarray(L).save(d / "layer_front.png"); Image.fromarray(lev).save(d / "levels_front.png")
    bg = Image.new("RGBA", head.shape[1::-1], (255, 255, 255, 255)); bg.alpha_composite(Image.fromarray(head))
    box, _ = rig.head_box({"head": head}, 0.05, 0.05, 0.05)
    bg.convert("RGB").crop(box).resize((ppt.SHEET, ppt.SHEET), Image.LANCZOS).save(d / "paint_front.png")
    store.write_meta(d, "face", seed=store.read_meta(d0).get("seed"), inputs={"paint": cid}, params={"from_paint": True},
                     race=race, kind="face", on=build, paint=cid, from_paint=True, keyed={"front": int((lev[..., 3] > 0).sum())})
    previews(race, "face", fid)
    return fid


def previews(race, kind, fid, ramp=None, save=True):
    """prev_<sex>_<view>.png (rig size, 576 px: the layer on that sex's painted puppet) and head close-ups; hair is coloured with
    `ramp` (a hair ramp name; default the first of the hair group). save=False returns data URLs instead (Face tab's
    hair colour picker)."""
    import base64, io
    d = out_dir(race, kind) / fid; urls = {}
    colours = hair_ramp(ramp) if kind == "hair" else group_ramp("eyes", ramp)
    def put(im, name):
        if save:
            imageops.save(im, d / name)
        else:
            b = io.BytesIO(); im.save(b, "PNG"); urls[name] = "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    for sex in SEXES:
        try:
            _, sp, parts = base(f"{race}.{sex}")
        except ValueError:
            continue
        for v in ("front", "back"):
            f, fl = d / f"layer_{v}.png", d / f"levels_{v}.png"
            if kind == "hair" and fl.exists():
                L = colour_levels(np.asarray(Image.open(fl).convert("RGBA")), colours)
            else:
                L = np.asarray(Image.open(f).convert("RGBA")).copy() if f.exists() else None
                if L is not None and fl.exists():   # face: the irises coloured with the eye ramp
                    ir = colour_levels(np.asarray(Image.open(fl).convert("RGBA")), colours); m_ = ir[..., 3] > 0
                    L[m_, :3] = ir[m_, :3]
            comp = compose(sp, v, parts[v], [L], blank=(kind == "face"))
            put(pp.on_bg(np.asarray(comp.resize((pp.PX, pp.PX), Image.LANCZOS))), f"prev_{sex}_{v}.png")
            if v == "front":
                box, _ = rig.head_box(parts[v], 0.25, 0.15, 0.1)
                put(pp.on_bg(np.asarray(comp.crop(box).resize((pp.PX, pp.PX), Image.LANCZOS))), f"head_{sex}.png")
    return urls


# ----------------------------------------------------------------------------- candidates and sets

def face_sets(s: dict, race: str) -> dict:
    """A race's hair and face sets in workshop state s ({"hair": [ids], "face": [ids]}), created when missing."""
    return s["builds"].setdefault(f"{race}.unisex", {}).setdefault("face_sets", {"hair": [], "face": []})


def state(race):
    s = ws.load_state(); sets = face_sets(s, race); doc = spec()
    out = {"race": race, "spec": doc, "sets": sets, "composed": {sex: composed(race, sex, doc) for sex in SEXES}, "kinds": {}}
    bases = {}
    for sex in SEXES:
        try:
            cid, _, _ = base(f"{race}.{sex}"); bases[sex] = cid
        except ValueError:
            bases[sex] = None
    out["bases"] = bases
    for kind in KINDS:
        dd = out_dir(race, kind); lst = []
        if dd.exists():
            for c in sorted(store.folders(dd), key=lambda p: p.stat().st_mtime):
                meta = store.read_meta(c)
                prog = json.loads((c / "progress.json").read_text(encoding="utf-8")) if (c / "progress.json").exists() else None
                lst.append({"id": c.name, "dir": ws.rel(c), "meta": meta, "files": sorted(f.name for f in c.glob("*.*")), "progress": prog,
                            "stamp": int(max((f.stat().st_mtime for f in c.glob("*.*")), default=0)), "in_set": c.name in sets[kind]})
        out["kinds"][kind] = lst
    return out


def set_member(race, kind, fid, on=True):
    if not (out_dir(race, kind) / fid).exists():
        raise ValueError(f"No {kind} candidate {fid}.")
    with ws.edit_state() as s:
        store.member(face_sets(s, race)[kind], fid, on)
    return state(race)


def archive(race, kind, fid):
    with store.lock(ws.STATE):
        if fid in face_sets(ws.load_state(), race)[kind]:
            raise ValueError("It's in the set: take it out first.")
        if not (out_dir(race, kind) / fid).exists():
            raise ValueError(f"No {kind} candidate {fid}.")
        store.archive(out_dir(race, kind) / fid, ws.ARCHIVE / "workshop" / "face" / race / kind / fid)
    return state(race)


def _pick(race, kind):
    """The race's hair / face for previews: the first in its set, else the newest candidate, else None."""
    st = ws.load_state(); in_set = face_sets(st, race)[kind]
    d = out_dir(race, kind)
    if in_set and (d / in_set[0]).exists():
        return d / in_set[0]
    cands = sorted((p for p in d.iterdir() if p.is_dir() and not (p / "progress.json").exists()),
                   key=lambda p: p.stat().st_mtime) if d.exists() else []
    return cands[-1] if cands else None


def ramp_preview(build):
    """Ramps tab: a build's painted puppet wearing the race's hair and face, front and back, as transparent rig-size (576 px) PNGs,
    with three level maps (R = level, A = mask): skin (measured on both views together; hair and irises excluded), hair
    (254 = its outline) and irises, so each group's ramp colours its own layer."""
    import base64, io
    race = build.split(".", 1)[0]
    try:
        cid, sp, parts = base(build)
    except ValueError:
        return {"build": build, "id": None, "approved": False, "facings": {}}
    hair_d, face_d = _pick(race, "hair"), _pick(race, "face")
    url = lambda im: "data:image/png;base64," + base64.b64encode((lambda b: (im.save(b, "PNG"), b.getvalue())[1])(io.BytesIO())).decode()
    enc = lambda lv, m: np.dstack([np.clip(lv, 0, 255).astype(np.uint8), np.zeros(lv.shape + (2,), np.uint8), np.where(m, 255, 0).astype(np.uint8)])
    small = lambda a: np.asarray(Image.fromarray(a).resize((pp.PX, pp.PX), Image.NEAREST))
    comps, hairs, irises, levs = {}, {}, {}, {}
    cent = ppt.ensure_bands(build, cid)   # the skin key; makes missing bands first
    for v in ("front", "back"):
        face_L = np.asarray(Image.open(face_d / "layer_front.png").convert("RGBA")).copy() if face_d and v == "front" and (face_d / "layer_front.png").exists() else None
        iris = np.asarray(Image.open(face_d / "levels_front.png").convert("RGBA")) if face_d and v == "front" and (face_d / "levels_front.png").exists() else None
        if face_L is not None and iris is not None:
            ir = colour_levels(iris, group_ramp("eyes")); m_ = ir[..., 3] > 0; face_L[m_, :3] = ir[m_, :3]
        hl = np.asarray(Image.open(hair_d / f"levels_{v}.png").convert("RGBA")) if hair_d and (hair_d / f"levels_{v}.png").exists() else None
        hair_L = colour_levels(hl, hair_ramp()) if hl is not None else None
        comps[v] = compose(sp, v, parts[v], [face_L, hair_L], blank=face_L is not None)
        hairs[v] = hl; irises[v] = iris
        # the skin = the parts' bands (Parts editor), minus what the hair and face layers cover
        lev = ppt.bands_composite(ppt.out_dir(build) / cid, sp, v)
        for L in (hl, face_L):
            if L is not None:
                lev[L[..., 3] > 0] = -1
        levs[v] = lev
    out = {"build": build, "id": cid, "approved": bool(ppt.state(build)["approved"]), "hair": hair_d.name if hair_d else None,
           "face": face_d.name if face_d else None, "facings": {}}
    for v in ("front", "back"):
        lv = levs[v]
        f = {"art": url(comps[v].resize((pp.PX, pp.PX), Image.LANCZOS)), "levels": url(Image.fromarray(small(enc(np.clip(lv, 0, 255), lv >= 0)))), "key": cent}
        if hairs[v] is not None:
            f["hair"] = url(Image.fromarray(small(hairs[v])))
        if irises[v] is not None:
            f["eyes"] = url(Image.fromarray(small(irises[v])))
        out["facings"][v] = f
    return out


# ----------------------------------------------------------------------------- editing a layer (Workshop Parts tab)

def _views(kind):
    return ("front",) if kind == "face" else ("front", "back")


def layer_doc(race, kind, fid):
    """What the Parts editor needs to edit a hair / face layer in place: the puppet it was made on as (read-only) context
    layers (for a face, the head with its own face removed) and the layer itself, drawn right after the head."""
    d = out_dir(race, kind) / fid
    if not d.exists():
        raise ValueError(f"No {kind} candidate {fid}.")
    meta = store.read_meta(d)
    build = meta.get("on") or f"{race}.male"
    cid, sp, parts = base(build)
    pd = ppt.out_dir(build) / cid
    views = {}
    for v in _views(kind):
        order = list(sp["views"][v]["order"]); order.insert(order.index("head") + 1, "layer")
        paths = {n: ws.rel(pd / "parts" / v / f"{n}.png") for n in sp["views"][v]["order"]}
        if kind == "face":   # the faceless head under the face layer
            h = blank_face(parts[v]["head"], rig.face_ellipse(sp, 1.2) & rig.inner_head(parts[v]["head"]))
            Image.fromarray(h).save(d / f"ctx_head_{v}.png"); paths["head"] = ws.rel(d / f"ctx_head_{v}.png")
        paths["layer"] = ws.rel(d / f"layer_{v}.png")
        views[v] = {"order": order, "parts": paths, "template": {}}
        if kind == "hair" and (d / f"levels_{v}.png").exists():   # its cel levels, editable as bands
            views[v]["levels"] = {"layer": ws.rel(d / f"levels_{v}.png")}
        if kind == "face" and (d / f"levels_{v}.png").exists():   # the iris levels: only for the eye-ramp preview
            views[v]["ramp"] = {"layer": ws.rel(d / f"levels_{v}.png")}
    files = sorted(f.name for f in d.glob("*.*"))
    stamp = int(max((f.stat().st_mtime for f in d.glob("*.*")), default=0))
    prev = [f for f in ("prev_male_front.png", "prev_male_back.png", "prev_female_front.png", "prev_female_back.png") if f in files]
    return {"race": race, "kind": kind, "id": fid, "dir": ws.rel(d), "on": build, "views": views, "editable": ["layer"],
            "files": files, "previews": prev, "stamp": stamp, "edited": any(f.startswith("layer_orig_") for f in files),
            "bandsEdited": [f"{v}/layer" for v in meta.get("bands_edited", [])]}


def levels_from_layer(kind, L, sp=None):
    """Recompute a layer's colour levels after an edit. Hair: dark pixels = outline (254), the rest 5 brightness levels
    (so any colour painted in works; the ramp colours it). Face: the magenta-key irises."""
    if kind == "face":
        return iris_levels(L, eyes=sp["views"]["front"]["shapes"]["head"]["face"]["eyes"] if sp else None)
    a = L[..., 3] > 127; _, _, val = imageops.hsv(L[..., :3]); lev = np.zeros(L.shape, np.uint8)
    line = a & (val < 0.22); hair = a & ~line
    if hair.any():
        v_ = val[hair]
        lev[..., 0][hair] = nearest_level(v_, brightness_centres(v_, 5))
    lev[..., 0][line] = 254; lev[..., 3][a] = 255
    return lev


def save_layer(race, kind, fid, view, png):
    """An edited layer from the Parts editor (PNG data URL / bytes, the rest-pose canvas): the first edit keeps the original
    (layer_orig_<view>.png); levels and previews are recomputed."""
    import base64, io
    d = out_dir(race, kind) / fid
    if view not in _views(kind) or not d.exists():
        raise ValueError("Unknown candidate or view.")
    raw = base64.b64decode(png.split(",", 1)[-1]) if isinstance(png, str) else png
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    if im.size != (N, N):
        raise ValueError(f"expected {N} px, got {im.size}")
    if not (d / f"layer_orig_{view}.png").exists():
        shutil.copy(d / f"layer_{view}.png", d / f"layer_orig_{view}.png")
        if (d / f"levels_{view}.png").exists():
            shutil.copy(d / f"levels_{view}.png", d / f"levels_orig_{view}.png")
    L = np.asarray(im); im.save(d / f"layer_{view}.png")
    meta = store.read_meta(d)
    sp = base(meta.get("on") or f"{race}.male")[1]
    lev = levels_from_layer(kind, L, sp)
    if view in meta.get("bands_edited", []) and (d / f"levels_{view}.png").exists():   # hand-edited bands stay
        lev = keep_bands(np.asarray(Image.open(d / f"levels_{view}.png").convert("RGBA")), lev)
    Image.fromarray(lev).save(d / f"levels_{view}.png")
    store.update_meta(d, keyed={**meta.get("keyed", {}), view: int((lev[..., 3] > 0).sum())}, edited=True)
    previews(race, kind, fid)
    return layer_doc(race, kind, fid)


def _keep_orig(d, view):
    for stem in ("layer", "levels"):
        if (d / f"{stem}_{view}.png").exists() and not (d / f"{stem}_orig_{view}.png").exists():
            shutil.copy(d / f"{stem}_{view}.png", d / f"{stem}_orig_{view}.png")


def save_bands(race, kind, fid, view, png):
    """Hand-edited hair bands from the Workshop Editor (PNG data URL, R = level, A = painted): only the levels of the
    layer's band pixels change (not the outline); the view is listed in meta "bands_edited" so a repaint keeps them."""
    import base64, io
    d = out_dir(race, kind) / fid; f = d / f"levels_{view}.png"
    if kind != "hair" or view not in _views(kind) or not f.exists():
        raise ValueError("Bands are edited on hair layers (front / back).")
    new = np.asarray(Image.open(io.BytesIO(base64.b64decode(png.split(",", 1)[-1]))).convert("RGBA"))
    _keep_orig(d, view)
    lev = write_bands(np.asarray(Image.open(f).convert("RGBA")), new, 5); Image.fromarray(lev).save(f)
    meta = store.read_meta(d)
    store.update_meta(d, bands_edited=sorted(set(meta.get("bands_edited", [])) | {view}), edited=True)
    previews(race, kind, fid)
    return layer_doc(race, kind, fid)


def reset_bands(race, kind, fid, view=None):
    """Redo a hair layer's bands from its paint (one view, else both), dropping the hand edits."""
    d = out_dir(race, kind) / fid; meta = store.read_meta(d); sp = base(meta.get("on") or f"{race}.male")[1]
    views = [view] if view else list(_views(kind))
    for v in views:
        if (d / f"layer_{v}.png").exists():
            _keep_orig(d, v)
            L = np.asarray(Image.open(d / f"layer_{v}.png").convert("RGBA"))
            Image.fromarray(levels_from_layer(kind, L, sp)).save(d / f"levels_{v}.png")
    store.update_meta(d, bands_edited=sorted(set(meta.get("bands_edited", [])) - set(views)))
    previews(race, kind, fid)
    return layer_doc(race, kind, fid)


def revert_layer(race, kind, fid):
    d = out_dir(race, kind) / fid
    for v in _views(kind):
        for stem in ("layer", "levels"):
            o = d / f"{stem}_orig_{v}.png"
            if o.exists():
                shutil.move(str(o), str(d / f"{stem}_{v}.png"))
    store.update_meta(d, edited=None, bands_edited=None)
    previews(race, kind, fid)
    return layer_doc(race, kind, fid)


# ----------------------------------------------------------------------------- Studio job kinds (hdart.jobs)

@jobs.kind("paint", reload=("puppet", "puppet_template", "puppet_paint", "puppet_face"))   # puppet Paint step
def _job_paint(a):
    return paint(a["build"], int(a.get("n", 1)), int(a.get("seed", 1)))


@jobs.kind("face_gen", reload=("puppet", "puppet_template", "puppet_paint", "puppet_face"))
def _job_generate(a):   # Face tab: racial hair / faces; `what` = hair / face
    return generate(a["race"], a["what"], a.get("sex", "male"), int(a.get("n", 1)), int(a.get("seed", 1)))
