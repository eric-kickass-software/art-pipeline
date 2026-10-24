"""Face tab: racial hair and faces on the painted puppets (2026-10-24, v1).

Male and female are painted on the same generic Template, so their heads have the same shape in the same place: a
hairstyle or face made on one build fits every build of the race. Layers are 1024 px RGBA in the rest pose (the paint
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
import json, math, shutil, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import ai_kit, comfy, workshop as ws, puppet as pp, puppet_template as pt, puppet_paint as ppt

SPEC = ai_kit.ROOT / "specs" / "face.json"
KINDS = ("hair", "face")
SEXES = ("male", "female")
S = ppt.S
N = pp.PX * S
D_HAIR, D_FACE = 0.85, 0.75


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
    out = {"_doc": old.get("_doc", ""), "hair": doc["hair"], "face": doc["face"], "races": races}
    SPEC.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    return out


def out_dir(race, kind):
    return ai_kit.AI_DIR / "face" / race / kind


def composed(race, sex, doc=None):
    """The prompts a hair / face generation on <race>.<sex> sends."""
    doc = doc or spec(); r = doc["races"].get(race, {})
    body, clothes = ppt.prompts(f"{race}.{sex}"); style = ppt.style_text()
    fill = lambda t: (t.replace("{style}", style).replace("{body}", body).replace("{hair}", r.get("hair", "").strip() or "hair")
                      .replace("{face}", r.get("face", "").strip() or "a face"))
    return {"hair": {v: fill(doc["hair"][v]) for v in ("front", "back")}, "face": fill(doc["face"])}


# ----------------------------------------------------------------------------- the painted puppet to work on

def base(build):
    """(candidate id, its template spec, {view: (composite RGBA, parts dict)}) of a build's approved paint (else newest)."""
    st = ppt.state(build)
    cands = sorted(st["candidates"], key=lambda c: c["meta"].get("made", 0))
    cid = st["approved"] or (cands[-1]["id"] if cands else None)
    if not cid:
        raise ValueError(f"{build} has no paint yet: paint it first (Race → Paint).")
    d = ppt.out_dir(build) / cid; sp = ppt._spec_for(d); out = {}
    for v in ("front", "back"):
        parts = {n: np.asarray(Image.open(d / "parts" / v / f"{n}.png").convert("RGBA")) for n in sp["views"][v]["order"]}
        out[v] = parts
    return cid, sp, out


def compose(sp, view, parts, layers=(), blank=False):
    """The rest pose with extra layers drawn right over the head (before the near arm); `blank` draws the head with its
    own face removed (for a face layer)."""
    cv = Image.new("RGBA", (N, N))
    for n in sp["views"][view]["order"]:
        im = parts[n]
        if n == "head" and blank and view == "front":
            im = blank_face(im, face_ellipse(sp, 1.2))   # a little wider than the new face: old lashes go too
        cv.alpha_composite(Image.fromarray(im))
        if n == "head":
            for L in layers:
                if L is not None:
                    cv.alpha_composite(Image.fromarray(L))
    return cv


def _head_box(parts, grow_up=0.45, grow_side=0.3, grow_down=0.55):
    a = parts["head"][..., 3] > 127; ys, xs = np.nonzero(a)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max(); h, w = y1 - y0, x1 - x0
    bx0, bx1 = int(x0 - grow_side * w), int(x1 + grow_side * w); by0, by1 = int(y0 - grow_up * h), int(y1 + grow_down * h)
    side = max(bx1 - bx0, by1 - by0); cx, cy = (bx0 + bx1) // 2, (by0 + by1) // 2
    bx0 = int(np.clip(cx - side // 2, 0, N - side)); by0 = int(np.clip(cy - side // 2, 0, N - side))
    return (bx0, by0, bx0 + side, by0 + side), a


def face_ellipse(sp, scale=1.0):
    """The face area (an ellipse around the eyes and mouth, from the Template's face guides) as a 1024 px mask."""
    fc = sp["views"]["front"]["shapes"]["head"].get("face")
    if not fc:
        raise ValueError("The Template has no face guides (Template tab).")
    e0, e1, m = (np.array(p) * S for p in (fc["eyes"][0], fc["eyes"][1], fc["mouth"]))
    c = (e0 + e1 + m) / 3; w = np.linalg.norm(e1 - e0)
    rx, ry = 0.95 * w * scale, 0.85 * (np.linalg.norm(m - (e0 + e1) / 2) + 0.35 * w) * scale
    yy, xx = np.mgrid[:N, :N]
    return ((xx - c[0]) / rx) ** 2 + ((yy - c[1]) / ry) ** 2 <= 1


def _components(mask, min_px):
    """Drop connected pieces smaller than min_px (4-neighbour labelling)."""
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


def _paint(init, mask, prompt, seed, denoise, tag, refs=()):
    return ppt._run(init, mask, prompt, seed, denoise, tag, refs)


HAIR_COLOURS = [("dark brown", (78, 52, 38)), ("light brown", (150, 104, 66)), ("auburn", (130, 60, 40)),
                ("ginger", (190, 100, 50)), ("blonde", (222, 182, 112)), ("blond", (222, 182, 112)), ("platinum", (232, 226, 200)),
                ("black", (40, 34, 32)), ("brown", (110, 74, 48)), ("red", (160, 62, 40)), ("white", (232, 230, 225)),
                ("silver", (190, 192, 198)), ("grey", (150, 150, 155)), ("gray", (150, 150, 155)), ("green", (70, 130, 70)),
                ("blue", (60, 90, 160)), ("purple", (110, 70, 140)), ("pink", (220, 130, 160))]


def hair_colour(text):
    """The init tint for a hairstyle: the first colour word in its description (default brown)."""
    t = text.lower()
    for w, c in HAIR_COLOURS:
        if w in t:
            return c
    return (110, 74, 48)


def key_rgb():
    h = spec().get("hair_key", "#22c822").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hsv(rgb):
    a = rgb.astype(float) / 255; mx, mn = a.max(-1), a.min(-1); d = mx - mn + 1e-6
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    return h, np.where(mx > 0, d / (mx + 1e-6), 0), mx


def key_layer(before, after, region, head, levels=5):
    """Colour-key cut-out of the hair: pixels near the key hue (saturated enough) + the dark outline pixels touching
    them, only pieces that touch the head. Returns (preview RGBA as painted, levels RGBA: R = cel level 0..levels-1 by
    brightness, 254 = outline, A = hair)."""
    kh, _, _ = _hsv(np.array([[key_rgb()]], np.uint8)); kh = float(kh[0, 0])
    h, sat, val = _hsv(after)
    dh = np.minimum(np.abs(h - kh), 360 - np.abs(h - kh))
    hair = region & (dh < 35) & (sat > 0.28) & (val > 0.12)
    hair = np.asarray(Image.fromarray(np.where(hair, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))) > 0
    hair &= region
    near = np.asarray(Image.fromarray(np.where(hair, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))) > 0
    changed = np.abs(after.astype(int) - before.astype(int)).max(-1) > 38
    line = region & near & ~hair & changed & (val < 0.35)
    m = _components(hair | line, 60)
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
        v_ = val[hair]; cent = np.percentile(v_, np.linspace(10, 90, levels))
        for _ in range(20):   # 1D k-means on brightness, dark -> light
            lab = np.abs(v_[:, None] - cent[None]).argmin(1)
            cent = np.array([v_[lab == i].mean() if (lab == i).any() else cent[i] for i in range(levels)])
        cent.sort(); lab = np.abs(v_[:, None] - cent[None]).argmin(1)
        lev[..., 0][hair] = lab
    lev[..., 0][line] = 254; lev[..., 3][m] = 255
    return L, lev


def iris_levels(layer, levels=5, eyes=None):
    """The irises of a face layer by colour key (`eye_key`, magenta), only near the eye guides (blush is pinkish too):
    levels RGBA (R = cel level by brightness, A = iris)."""
    h_ = spec().get("eye_key", "#d020d0").lstrip("#"); kr = np.array([[[int(h_[i:i + 2], 16) for i in (0, 2, 4)]]], np.uint8)
    kh = float(_hsv(kr)[0][0, 0]); h, sat, val = _hsv(layer[..., :3])
    dh = np.minimum(np.abs(h - kh), 360 - np.abs(h - kh))
    iris = (layer[..., 3] > 127) & (dh < 32) & (sat > 0.42) & (val > 0.1) & (val < 0.92)   # blush is paler and lighter
    if eyes:
        e0, e1 = np.array(eyes[0]) * S, np.array(eyes[1]) * S; r = 0.85 * np.linalg.norm(e1 - e0)
        yy, xx = np.mgrid[:layer.shape[0], :layer.shape[1]]
        iris &= (np.hypot(xx - e0[0], yy - e0[1]) < r) | (np.hypot(xx - e1[0], yy - e1[1]) < r)
    lev = np.zeros(layer.shape, np.uint8)
    if iris.any():
        v_ = val[iris]; cent = np.percentile(v_, np.linspace(10, 90, levels))
        for _ in range(20):
            lab = np.abs(v_[:, None] - cent[None]).argmin(1)
            cent = np.array([v_[lab == i].mean() if (lab == i).any() else cent[i] for i in range(levels)])
        cent.sort(); lev[..., 0][iris] = np.abs(v_[:, None] - cent[None]).argmin(1); lev[..., 3][iris] = 255
    return lev


def group_ramp(group, name=None):
    """The colours (dark -> light, RGB) of a ramp of a palettes group; its first ramp by default."""
    from .palette import ramp_from_base
    doc = ws.palettes_doc(); grp = doc["groups"].get(group, {})
    e = grp.get(name) if name else None
    e = e or (next(iter(grp.values())) if grp else {"base": "#7a4a2b"})
    if e.get("colors"):
        return [tuple(int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for c in e["colors"]]
    return ramp_from_base(e["base"], doc.get("rampSize", 5))


def hair_ramp(name=None):
    """The colours (dark -> light, RGB) of a hair ramp (palettes group "hair"); the first one by default."""
    from .palette import ramp_from_base
    doc = ws.palettes_doc(); grp = doc["groups"].get("hair", {})
    e = grp.get(name) if name else None
    e = e or (next(iter(grp.values())) if grp else {"base": "#7a4a2b"})
    if e.get("colors"):
        return [tuple(int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for c in e["colors"]]
    return ramp_from_base(e["base"], doc.get("rampSize", 5))   # a base-only ramp, as the Ramps tab shows it


def colour_levels(lev, colours, line=(40, 32, 30)):
    """A hair levels map coloured with a ramp (outline pixels in the outline colour)."""
    out = np.zeros(lev.shape, np.uint8); a = lev[..., 3] > 127; r = lev[..., 0]
    pal = np.array(colours, np.uint8)
    hair = a & (r < 254); out[hair, :3] = pal[np.clip(r[hair], 0, len(pal) - 1)]
    out[a & (r == 254), :3] = line; out[..., 3] = np.where(a, 255, 0)
    return out


def skin_key(head):
    """The skin cel levels of a head part (RGB centres)."""
    a = head[..., 3] > 127
    bg = np.full(head.shape[:2] + (3,), 255, np.uint8); bg[a] = head[a, :3]
    _, cent = ws.skin_levels(Image.fromarray(bg), 5)
    return np.array(cent, float) if cent else np.array([[220, 170, 140]], float)


def skin_dist(rgb, key):
    """Per pixel: the RGB distance to the nearest skin level."""
    return np.sqrt(((rgb[..., None, :].astype(float) - key[None, None]) ** 2).sum(-1)).min(-1)


def blank_face(head, ellipse, key=None):
    """The head with its painted face removed: inside the face area, pixels that aren't skin (eyes, brows, mouth,
    their outlines) are filled from the skin around them, so a face layer can go on top of any build."""
    key = skin_key(head)[2:] if key is None else key   # lit skin levels: dark lines and pupils count as features
    a = head[..., 3] > 127; feat = ellipse & a & (skin_dist(head[..., :3], key) > 32)
    feat = np.asarray(Image.fromarray(np.where(feat, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 0
    feat &= a & ellipse
    ys, xs = np.nonzero(feat)
    if not len(ys):
        return head
    y0, y1, x0, x1 = max(0, ys.min() - 4), ys.max() + 5, max(0, xs.min() - 4), xs.max() + 5
    col = head[y0:y1, x0:x1, :3].astype(float); todo = feat[y0:y1, x0:x1].copy(); known = a[y0:y1, x0:x1] & ~todo
    for _ in range(400):
        if not (todo & ~known).any():
            break
        acc = np.zeros_like(col); cnt = np.zeros(todo.shape)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            k = np.roll(known, (dy, dx), (0, 1)); c = np.roll(col, (dy, dx), (0, 1))
            acc[k] += c[k]; cnt[k] += 1
        take = todo & ~known & (cnt > 0)
        col[take] = acc[take] / cnt[take][:, None]; known |= take
    out = head.copy(); out[y0:y1, x0:x1, :3] = np.clip(col, 0, 255).astype(np.uint8)
    return out


def _layer(before, after, region, thresh, min_px, key=None, skin_far=40, touch=None):
    """The pixels FLUX changed inside `region` as an RGBA layer (1024 px); with a skin key, only pixels that aren't
    skin (so re-shaded skin stays out of the layer); with `touch`, only pieces that touch that mask (hair grows from
    the head)."""
    d = np.abs(after.astype(int) - before.astype(int)).max(-1)
    ch = (d > thresh) & region
    if key is not None:
        ch &= skin_dist(after, key) > skin_far
    ch = np.asarray(Image.fromarray(np.where(ch, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))) > 0
    ch = _components(ch & region, min_px)
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

def _crop_pass(comp, region, box, prompt, seed, denoise, tag, refs=()):
    """Paint `region` of the composite inside `box` (resized to 1024), return the painted full-size RGB."""
    bg = Image.new("RGBA", (N, N), (255, 255, 255, 255)); bg.alpha_composite(comp)
    full = bg.convert("RGB"); side = box[2] - box[0]
    init = full.crop(box).resize((ppt.SHEET, ppt.SHEET), Image.LANCZOS)
    mk = Image.fromarray(np.where(region, 255, 0).astype(np.uint8)).crop(box).resize((ppt.SHEET, ppt.SHEET), Image.NEAREST)
    out = _paint(init, np.asarray(mk) > 0, prompt, seed, denoise, tag, refs)
    painted = np.asarray(full).copy()
    painted[box[1]:box[3], box[0]:box[2]] = np.asarray(out.resize((side, side), Image.LANCZOS))
    return np.asarray(full), painted, init, out


def generate(race, kind, sex="male", n=1, seed=1):
    """Queue job: n hair or face candidates for a race, painted on <race>.<sex>'s puppet; previews on male and female."""
    if kind not in KINDS:
        raise ValueError(f"kind is one of {KINDS}")
    build = f"{race}.{sex}"; cid, sp, parts = base(build); P = composed(race, sex)
    made = []
    for i in range(n):
        sd = seed + i; fid = f"s{sd}_{int(time.time())}"
        d = out_dir(race, kind) / fid; d.mkdir(parents=True, exist_ok=True)
        layers, levels = {}, {}
        if kind == "hair":
            ref = None
            for v in ("front", "back"):
                box, head = _head_box(parts[v])
                region = np.zeros((N, N), bool); region[box[1]:box[3], box[0]:box[2]] = True
                g = int(0.16 * (box[2] - box[0]))   # hair volume: a band around the head (no free-floating shapes above it)
                around = np.asarray(Image.fromarray(np.where(head, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * g + 1))) > 0
                ys = np.nonzero(head)[0]
                region &= around & ~(face_ellipse(sp, 1.05) if v == "front" else np.zeros_like(head))
                body = np.zeros((N, N), bool)   # never repaint the rest of the body (only the head and around it)
                for nm, p_ in parts[v].items():
                    if nm != "head":
                        body |= p_[..., 3] > 127
                region &= ~(body & ~around)
                key = skin_key(parts[v]["head"])
                # the hair guide: a hair-coloured cap (above the brows in front, the whole back of the head behind), shaded
                # like the head, so FLUX paints a full head of hair instead of keeping the bald skull
                hy0, hy1 = ys.min(), ys.max(); hh = hy1 - hy0
                gs = int(0.04 * (box[2] - box[0]))   # the guide hugs the head (FLUX may grow the hair into `around`)
                around_guide = np.asarray(Image.fromarray(np.where(head, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * gs + 1))) > 0
                if v == "front":
                    fc = sp["views"]["front"]["shapes"]["head"]["face"]
                    e0, e1 = np.array(fc["eyes"][0]) * S, np.array(fc["eyes"][1]) * S
                    brow = (e0[1] + e1[1]) / 2 - 0.42 * np.linalg.norm(e1 - e0)
                    cap = (around_guide & (np.arange(N)[:, None] < brow)) & ~face_ellipse(sp, 1.05)
                else:
                    cap = around_guide & (np.arange(N)[:, None] < hy0 + 0.82 * hh)
                hc = np.array(key_rgb(), float)   # colour key: the hair is painted in one key colour, cut out by hue
                comp = compose(sp, v, parts[v]); ca = np.asarray(comp).copy()
                lum = ca[..., :3].astype(float).mean(-1, keepdims=True) / 200.0
                ca[cap, :3] = np.clip(hc * (0.75 + 0.35 * lum[cap]), 0, 255).astype(np.uint8); ca[cap, 3] = 255
                comp = Image.fromarray(ca)
                before, after, init, out = _crop_pass(comp, region, box, P["hair"][v], sd, D_HAIR, f"{race}_{fid}_hair_{v}",
                                                      [ref] if ref else [])
                before = np.asarray(compose(sp, v, parts[v]).convert("RGBA"))   # the layer = what differs from the bald head
                bgw = np.full((N, N, 3), 255, np.uint8); al = before[..., 3:4] / 255.0
                before = (before[..., :3] * al + bgw * (1 - al)).astype(np.uint8)
                out.save(d / f"paint_{v}.png")
                if v == "front":
                    ref = d / "paint_front.png"
                layers[v], levels[v] = key_layer(before, after, region, head)
        else:
            v = "front"; region = face_ellipse(sp)
            region &= parts[v]["head"][..., 3] > 127
            box, _ = _head_box(parts[v], 0.05, 0.05, 0.05)
            key = skin_key(parts[v]["head"])
            comp = compose(sp, v, parts[v], blank=True)
            before, after, init, out = _crop_pass(comp, region, box, P["face"], sd, D_FACE, f"{race}_{fid}_face")
            out.save(d / "paint_front.png")
            layers[v] = _layer(before, after, region, 30, 12, key[2:], 35)   # lit skin levels only (dark lines stay in)
            levels[v] = iris_levels(layers[v], eyes=sp["views"]["front"]["shapes"]["head"]["face"]["eyes"])   # the irises (colour key) as cel levels for the eye ramps
        for v, L in layers.items():
            Image.fromarray(L).save(d / f"layer_{v}.png")
        for v, lv in levels.items():   # hair: its cel levels (R = level 0..4, 254 = outline; A = hair), coloured by a hair ramp
            Image.fromarray(lv).save(d / f"levels_{v}.png")
        keyed = {v: int((lv[..., 3] > 0).sum()) for v, lv in levels.items()}   # pixels the colour key found (0 = not recolourable)
        (d / "meta.json").write_text(json.dumps({"race": race, "kind": kind, "seed": sd, "made": int(time.time()), "on": build,
                                                 "paint": cid, "keyed": keyed,
                                                 "prompt": P[kind] if kind == "face" else P["hair"]}, indent=1),
                                     encoding="utf-8")
        previews(race, kind, fid)
        made.append(fid)
    return made


def previews(race, kind, fid, ramp=None, save=True):
    """prev_<sex>_<view>.png (512 px, the layer on that sex's painted puppet) and head close-ups; hair is coloured with
    `ramp` (a hair ramp name; default the first of the hair group). save=False returns data URLs instead (Face tab's
    hair colour picker)."""
    import base64, io
    d = out_dir(race, kind) / fid; urls = {}
    colours = hair_ramp(ramp) if kind == "hair" else group_ramp("eyes", ramp)
    def put(im, name):
        if save:
            pt._save(im, d / name)
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
                box, _ = _head_box(parts[v], 0.25, 0.15, 0.1)
                put(pp.on_bg(np.asarray(comp.crop(box).resize((pp.PX, pp.PX), Image.LANCZOS))), f"head_{sex}.png")
    return urls


# ----------------------------------------------------------------------------- candidates and sets

def _sets(s, race):
    return s["builds"].setdefault(f"{race}.unisex", {}).setdefault("face_sets", {"hair": [], "face": []})


def state(race):
    s = ws.load_state(); sets = _sets(s, race); doc = spec()
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
            for c in sorted((p for p in dd.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime):
                meta = json.loads((c / "meta.json").read_text(encoding="utf-8")) if (c / "meta.json").exists() else {}
                lst.append({"id": c.name, "dir": ws.rel(c), "meta": meta, "files": sorted(f.name for f in c.glob("*.*")),
                            "stamp": int(max((f.stat().st_mtime for f in c.glob("*.*")), default=0)), "in_set": c.name in sets[kind]})
        out["kinds"][kind] = lst
    return out


def set_member(race, kind, fid, on=True):
    if not (out_dir(race, kind) / fid).exists():
        raise ValueError(f"No {kind} candidate {fid}.")
    s = ws.load_state(); sets = _sets(s, race)
    if on and fid not in sets[kind]:
        sets[kind].append(fid)
    if not on and fid in sets[kind]:
        sets[kind].remove(fid)
    ws.save_state(s)
    return state(race)


def archive(race, kind, fid):
    s = ws.load_state()
    if fid in _sets(s, race)[kind]:
        raise ValueError("It's in the set: take it out first.")
    src = out_dir(race, kind) / fid
    if not src.exists():
        raise ValueError(f"No {kind} candidate {fid}.")
    dst = ws.ARCHIVE / "workshop" / "face" / race / kind / fid; dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return state(race)


def _pick(race, kind):
    """The race's hair / face for previews: the first in its set, else the newest candidate, else None."""
    st = ws.load_state(); in_set = _sets(st, race)[kind]
    d = out_dir(race, kind)
    if in_set and (d / in_set[0]).exists():
        return d / in_set[0]
    cands = sorted((p for p in d.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime) if d.exists() else []
    return cands[-1] if cands else None


def ramp_preview(build):
    """Ramps tab: a build's painted puppet wearing the race's hair and face, front and back, as transparent 512 px PNGs,
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
    comps, hairs, irises = {}, {}, {}
    for v in ("front", "back"):
        face_L = np.asarray(Image.open(face_d / "layer_front.png").convert("RGBA")).copy() if face_d and v == "front" and (face_d / "layer_front.png").exists() else None
        iris = np.asarray(Image.open(face_d / "levels_front.png").convert("RGBA")) if face_d and v == "front" and (face_d / "levels_front.png").exists() else None
        if face_L is not None and iris is not None:
            ir = colour_levels(iris, group_ramp("eyes")); m_ = ir[..., 3] > 0; face_L[m_, :3] = ir[m_, :3]
        hl = np.asarray(Image.open(hair_d / f"levels_{v}.png").convert("RGBA")) if hair_d and (hair_d / f"levels_{v}.png").exists() else None
        hair_L = colour_levels(hl, hair_ramp()) if hl is not None else None
        comps[v] = compose(sp, v, parts[v], [face_L, hair_L], blank=face_L is not None)
        hairs[v] = hl; irises[v] = iris
    both = Image.new("RGB", (N * 2, N), (255, 255, 255))
    for i, v in enumerate(("front", "back")):
        both.paste(comps[v], (i * N, 0), comps[v])
    excl = np.zeros((N, N * 2), bool)   # hair and irises are not skin
    for i, v in enumerate(("front", "back")):
        for lv in (hairs[v], irises[v]):
            if lv is not None:
                excl[:, i * N:(i + 1) * N] |= lv[..., 3] > 0
    bw = np.asarray(both).copy(); bw[excl] = 255
    lev, cent = ws.skin_levels(Image.fromarray(bw), ws.palettes_doc().get("rampSize", 5))
    out = {"build": build, "id": cid, "approved": bool(ppt.state(build)["approved"]), "hair": hair_d.name if hair_d else None,
           "face": face_d.name if face_d else None, "facings": {}}
    for i, v in enumerate(("front", "back")):
        lv = small(lev[:, i * N:(i + 1) * N].astype(np.int16).clip(-1, 255).astype(np.int16))
        f = {"art": url(comps[v].resize((pp.PX, pp.PX), Image.LANCZOS)), "levels": url(Image.fromarray(enc(np.clip(lv, 0, 255), lv >= 0))), "key": cent}
        if hairs[v] is not None:
            f["hair"] = url(Image.fromarray(small(hairs[v])))
        if irises[v] is not None:
            f["eyes"] = url(Image.fromarray(small(irises[v])))
        out["facings"][v] = f
    return out


def refresh_previews(race):
    """Re-render every candidate's previews (after a new paint is approved)."""
    for kind in KINDS:
        dd = out_dir(race, kind)
        for c in (dd.iterdir() if dd.exists() else []):
            if c.is_dir():
                previews(race, kind, c.name)
    return state(race)
