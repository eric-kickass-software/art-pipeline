"""Item Workshop, v1 test: equipment as overlay layers on a race's painted puppet (vault: Hearthdelve - Art Pipeline,
"Item Workshop: revamp", 2026-10-24). Specs: specs/items.json.

Every build of a race is painted on the one shared Template, so a layer fitted on one build sits on every build's parts.
An item is fitted once on a "fitting build" and its per-part layers are reused on the others.

  fitted item   1. zones (part + range along its bone / rows / waist seam, from the Template) and the item's bulk give
                   the mask; the zones are tinted in the item's first material key as a guide
                2. one masked FLUX pass on the whole puppet ("now wearing ..."), the item painted in material key colours
                3. the item = key-coloured pixels (+ the dark line around them) touching the zones; brightness -> cel
                   levels per material (levels map: R = level 0..4 or 254 = outline, G = material channel, A)
                4. split per part: the topmost zone part at each pixel, pixels past the body go to the nearest zone part;
                   each part's hidden zone pixels (under its parent / the torso) are filled from its own layer
                5. back view: the same with the painted front as the reference image
  held item     painted alone on white (BiRefNet cut-out), keyed the same way, rotated onto the hand bone at its grip

    generate(race, item_id, build, seed)  -> candidate id in sprites/ai/items/<race>/<item>/<cid>/
                                             (paint_<view>.png, layer_/levels_<view>.png, parts/<view>/<part>.png,
                                              plevels/<view>/<part>.png, meta.json)
    dressed(build, worn, view)            -> the build's parts with the worn items' layers stacked on, for previews
"""
import fnmatch, json, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from . import ai_kit, comfy, puppet as pp, puppet_template as pt, puppet_paint as ppt, puppet_face as pf

SPEC = ai_kit.ROOT / "specs" / "items.json"
S, N = ppt.S, pf.N
VIEWS = ("front", "back")
D_ITEM = 0.85
GUIDE = 1.0      # the zones tinted in the key colour (weaker tints let FLUX shrink the item, e.g. a helmet smaller than the skull)
GUIDE_IN = 2     # px the tint stays inside the zone edge, feathered
LINE_W = 3       # the even outline drawn around every item silhouette (rims, sleeve ends and boot tops get no line from FLUX)
LEVELS = 5


def spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def out_dir(race, item):
    return ai_kit.AI_DIR / "items" / race / item


def _rgb(hexs):
    h = hexs.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _grow(m, k):
    if k <= 0:
        return m.copy()
    return np.asarray(Image.fromarray(np.where(m, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * k + 1))) > 0


def _erode(m, k):
    return np.asarray(Image.fromarray(np.where(m, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(2 * k + 1))) > 0


def _holes(m, max_frac):
    """Enclosed holes of a mask (not reachable from the border), each smaller than max_frac of the mask's area."""
    if not m.any():
        return m.copy()
    reach = np.zeros_like(m); reach[0, :] = reach[-1, :] = reach[:, 0] = reach[:, -1] = True; reach &= ~m
    for _ in range(4000):
        g = (reach | np.roll(reach, 1, 0) | np.roll(reach, -1, 0) | np.roll(reach, 1, 1) | np.roll(reach, -1, 1)) & ~m
        if (g == reach).all():
            break
        reach = g
    holes = ~m & ~reach
    return pf._components(holes, 1) & ~(pf._components(holes, int(max_frac * m.sum()) + 1))


def _spread(mask_from, values, into, steps=120):
    """Copy each pixel of `values` (H, W, C) outward from `mask_from` into `into`, nearest first (8 neighbours)."""
    known = mask_from.copy(); out = values.copy()
    for _ in range(steps):
        todo = into & ~known
        if not todo.any():
            break
        took = np.zeros_like(known)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            k = np.roll(known, (dy, dx), (0, 1)) & todo & ~took
            out[k] = np.roll(values if False else out, (dy, dx), (0, 1))[k]
            took |= k
        if not took.any():
            break
        known |= took
    return out, known


# ----------------------------------------------------------------------------- prompts

def prompts(race, build, item_id, doc=None):
    doc = doc or spec(); it = doc["items"][item_id]
    body, _ = ppt.prompts(build); style = ppt.style_text()
    mats = " ".join(doc["material_text"].replace("{name}", "metal" if m == "metal" else m).replace("{word}", doc["materials"][m]["word"])
                    for m in it["materials"])
    fill = lambda t: t.replace("{style}", style).replace("{body}", body).replace("{item}", it["text"]).replace("{materials}", mats)
    return {"front": fill(doc["prompt"]), "back": fill(doc["prompt_back"]), "held": fill(doc["prompt_held"])}


# ----------------------------------------------------------------------------- zones

def zone_masks(sp, view, parts, zones):
    """{part: bool mask (1024 rest pose)} of where the item may sit on each part."""
    V = sp["views"][view]; out = {}
    yy, xx = np.mgrid[:N, :N]
    for z in zones:
        if z.get("view", view) != view:   # a zone for one view only
            continue
        for n in fnmatch.filter(V["order"], z["part"]):
            m = parts[n][..., 3] > 127
            if "t" in z and n in V["limbs"]:
                L = V["limbs"][n]; a, b = pt._joint(V, L["from"]) * S, pt._joint(V, L["to"]) * S; d = b - a
                t = ((xx - a[0]) * d[0] + (yy - a[1]) * d[1]) / float(d @ d)
                t0, t1 = z["t"]
                if t0 > 0:
                    m &= t >= t0
                if t1 < 1:
                    m &= t < t1
            if "rows" in z and m.any():
                ys = np.nonzero(parts[n][..., 3] > 127)[0]; y0, h = ys.min(), ys.max() - ys.min()
                m &= (yy >= y0 + z["rows"][0] * h) & (yy < y0 + z["rows"][1] * h)
            if "ellipse" in z and m.any():   # [cx, cy, rx, ry] as fractions of the part's box (curved edges, e.g. a helmet rim)
                ys, xs = np.nonzero(parts[n][..., 3] > 127); y0, x0 = ys.min(), xs.min(); h, w = ys.max() - y0, xs.max() - x0
                cx, cy, rx, ry = z["ellipse"]
                m &= ((xx - x0 - cx * w) / (rx * w)) ** 2 + ((yy - y0 - cy * h) / (ry * h)) ** 2 <= 1
            if "seam" in z:
                wy = V["seams"]["waist"]["y"] * S + z.get("seam_px", 0)
                m &= (yy >= wy) if z["seam"] == "below_waist" else (yy < wy)
            out[n] = out.get(n, np.zeros((N, N), bool)) | m
    return out


# ----------------------------------------------------------------------------- keying

def _key_hues(doc, mats):
    return [(m, float(pf._hsv(np.array([[_rgb(doc["materials"][m]["key"])]], np.uint8))[0][0, 0])) for m in mats]


def key_item(doc, mats, before, after, region, touch, min_px=60, cents=None):
    """Key-coloured item pixels of `after` (+ the dark line next to them) inside `region`, pieces touching `touch`.
    Returns (layer RGBA as painted, levels RGBA: R = level / 254 outline, G = material channel, A). `cents`
    ({channel: level brightnesses}) is filled from the first view and reused for the next, so front and back share
    their cel levels (separate k-means made the back much darker)."""
    cents = {} if cents is None else cents
    h, sat, val = pf._hsv(after)
    found = np.zeros((N, N), bool); mat = np.zeros((N, N), np.uint8)
    for m, kh in _key_hues(doc, mats):
        dh = np.minimum(np.abs(h - kh), 360 - np.abs(h - kh))
        k = region & (dh < 28) & (sat > 0.3) & (val > 0.12) & ~found
        mat[k] = doc["materials"][m]["channel"]; found |= k
    found = np.asarray(Image.fromarray(np.where(found, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))) > 0
    found &= region & (mat > 0)
    changed = np.abs(after.astype(int) - before.astype(int)).max(-1) > 38
    line = region & _grow(found, 4) & ~found & changed & (val < 0.35)
    m_ = pf._components(found | line, min_px)
    keep = m_ & _grow(touch, 3)
    for _ in range(3000):   # keep only pieces connected to the zones
        g = (keep | np.roll(keep, 1, 0) | np.roll(keep, -1, 0) | np.roll(keep, 1, 1) | np.roll(keep, -1, 1)) & m_
        if (g == keep).all():
            break
        keep = g
    found &= keep; line &= keep
    keep |= _holes(keep, 0.03)   # small enclosed holes (white glints, unkeyed specks) belong to the item
    hole = keep & ~found & ~line
    L = np.zeros((N, N, 4), np.uint8); L[keep, :3] = after[keep]; L[keep, 3] = 255
    lev = np.zeros((N, N, 4), np.uint8)
    vs = np.asarray(Image.fromarray((val * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(5)), float) / 255
    for ch in np.unique(mat[found]):
        sel = found & (mat == ch); v_ = vs[sel]
        cent = cents.get(int(ch))
        if cent is None:
            cent = np.percentile(v_, np.linspace(10, 90, LEVELS))
            for _ in range(20):   # 1D k-means on brightness, dark -> light
                lab = np.abs(v_[:, None] - cent[None]).argmin(1)
                cent = np.array([v_[lab == i].mean() if (lab == i).any() else cent[i] for i in range(LEVELS)])
            cent.sort(); cents[int(ch)] = cent
        lev[..., 0][sel] = np.abs(v_[:, None] - cent[None]).argmin(1); lev[..., 1][sel] = ch
    if line.any():   # the outline takes the channel of the nearest keyed pixel
        near, _ = _spread(found, lev[..., 1:2].copy(), line, 8)
        lev[..., 1][line] = near[..., 0][line]
    if hole.any():   # a hole takes its neighbours' channel; bright = the top level
        near, _ = _spread(found, lev[..., :2].copy(), hole, 40)
        lev[..., 1][hole] = near[..., 1][hole]
        lev[..., 0][hole] = np.where(val[hole] > 0.75, LEVELS - 1, near[..., 0][hole])
    lev[..., 0][line] = 254; lev[..., 3][keep] = 255
    return L, lev


# ----------------------------------------------------------------------------- split per part

def cover(L, lev, need):
    """The item covers its whole zone: `need` pixels it left bare (FLUX drew it smaller than the body there) are
    filled from its nearest pixels (levels, not blended colours; outline pixels don't spread)."""
    have = (L[..., 3] > 0) & (lev[..., 0] < 254)
    miss = need & ~(L[..., 3] > 0)
    if not miss.any() or not have.any():
        return L, lev
    both = np.concatenate([L, lev], -1)
    filled, got = _spread(have, both, have | miss, 300)
    add = miss & got; L, lev = L.copy(), lev.copy()
    L[add] = filled[add][:, :4]; lev[add] = filled[add][:, 4:]
    return L, lev


def outline_item(L, lev):
    """One even outline around the whole item (rims, sleeve ends and boot tops get no line from FLUX)."""
    keep = L[..., 3] > 0; rim = keep & ~_erode(keep, LINE_W)
    L, lev = L.copy(), lev.copy(); L[rim, :3] = ppt.LINE; lev[..., 0][rim] = 254
    return L, lev


def split(sp, view, parts, zm, L, lev):
    """Per part: (layer RGBA, levels RGBA). Item pixels go to the topmost zone part there, the rest (past the body) to
    the nearest zone part; then each part's hidden zone pixels (not visible in the rest pose) are filled from its own
    layer, so a joint can turn without showing skin."""
    V = sp["views"][view]; order = [n for n in V["order"] if n in zm]
    item = L[..., 3] > 0
    own = np.full((N, N), -1)
    for i, n in enumerate(order):
        own[zm[n] & item] = i
    lab = own[..., None].astype(np.int32)
    lab2, known = _spread(own >= 0, lab, item, 200)
    own = np.where(item & known, lab2[..., 0], -1)
    vis = ppt.owners({n: (parts[n],) for n in V["order"]}, V["order"])
    out = {}
    for i, n in enumerate(order):
        mine = own == i
        if not mine.any():
            continue
        Ln = np.where(mine[..., None], L, 0).astype(np.uint8); Vn = np.where(mine[..., None], lev, 0).astype(np.uint8)
        hidden = zm[n] & ~vis[n] & ~mine
        if hidden.any():
            both = np.concatenate([Ln, Vn], -1)
            filled, got = _spread(mine, both, hidden | mine, 160)
            add = hidden & got
            Ln[add] = filled[add][:, :4]; Vn[add] = filled[add][:, 4:]
            Vn[add & (Vn[..., 0] == 254), 0] = LEVELS - 2   # a filled area has no outline inside the part
        out[n] = (Ln, Vn)
    return out


# ----------------------------------------------------------------------------- held items

def held_layer(doc, it, sp, view, parts, img, lev_img):
    """A held item (RGBA + levels, upright) rotated onto the hand: grip = middle of its leather, else 85% down."""
    V = sp["views"][view]; hand = f"{it.get('hand', 'armA')}.hand"
    a = img[..., 3] > 127; ys, xs = np.nonzero(a)
    grip_ch = doc["materials"]["leather"]["channel"]
    g = a & (lev_img[..., 1] == grip_ch)
    gy, gx = (np.nonzero(g)[0].mean(), np.nonzero(g)[1].mean()) if g.sum() > 50 else (ys.min() + 0.85 * (ys.max() - ys.min()), xs.mean())
    fig = np.any([parts[n][..., 3] > 127 for n in V["order"]], 0); fy = np.nonzero(fig)[0]
    scale = it.get("length", 0.5) * (fy.max() - fy.min()) / max(1, ys.max() - ys.min())
    hm = parts[hand][..., 3] > 127; hy, hx = np.nonzero(hm); cx, cy = hx.mean(), hy.mean()
    pel = pt._joint(V, "pelvis") * S
    ang = it.get("tilt", 20) * (1 if cx < pel[0] else -1)   # the top leans away from the body
    out = []
    for src in (img, lev_img):
        im = Image.fromarray(src).resize((int(src.shape[1] * scale), int(src.shape[0] * scale)), Image.NEAREST if src is lev_img else Image.LANCZOS)
        big = Image.new("RGBA", (N * 2, N * 2)); ox, oy = N - int(gx * scale), N - int(gy * scale)
        big.paste(im, (ox, oy)); big = big.rotate(ang, resample=Image.NEAREST if src is lev_img else Image.BICUBIC, center=(N, N))
        out.append(np.asarray(big.crop((int(N - cx), int(N - cy), int(N - cx) + N, int(N - cy) + N))).copy())
    L, lev = out; m = L[..., 3] > 127; L[..., 3] = np.where(m, 255, 0); lev[..., 3] = np.where(m, 255, 0)
    return {hand: (L, lev)}


def _gen_held(doc, it, P, d, sd):
    p = comfy.run(comfy.build_txt2img(P["held"], 768, 768, seed=sd, lora_strength=0.0, remove_bg=True, prefix="hdart/item_held"), d / "tmp")[0]
    rgba = np.asarray(Image.open(p).convert("RGBA")).copy(); Image.fromarray(rgba).save(d / "paint_held.png")
    a = ai_kit.clean(rgba[..., 3] > 127, min_area=300); ys, xs = np.nonzero(a)
    rgba = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1]; a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    H, W = a.shape; pad = 8
    big = np.full((H + 2 * pad, W + 2 * pad, 3), 255, np.uint8); big[pad:-pad, pad:-pad][a] = rgba[..., :3][a]
    am = np.zeros(big.shape[:2], bool); am[pad:-pad, pad:-pad] = a
    # key_item works on the N x N canvas: put the item in its middle
    cv = np.full((N, N, 3), 255, np.uint8); cm = np.zeros((N, N), bool)
    sc = min(1.0, (N - 40) / max(big.shape[:2]))
    im = Image.fromarray(big).resize((int(big.shape[1] * sc), int(big.shape[0] * sc)), Image.LANCZOS)
    mm = Image.fromarray(np.where(am, 255, 0).astype(np.uint8)).resize(im.size, Image.NEAREST)
    x0, y0 = (N - im.width) // 2, (N - im.height) // 2
    cv[y0:y0 + im.height, x0:x0 + im.width] = np.asarray(im); cm[y0:y0 + im.height, x0:x0 + im.width] = np.asarray(mm) > 0
    L, lev = key_item(doc, it["materials"], np.full((N, N, 3), 255, np.uint8), cv, _grow(cm, 2), cm, min_px=30)
    keep = (L[..., 3] > 0) | cm   # the whole cut-out stays (unkeyed bits too), keyed pixels get levels
    L2 = np.zeros((N, N, 4), np.uint8); L2[keep, :3] = cv[keep]; L2[keep, 3] = 255
    lev[keep & (lev[..., 3] == 0)] = (LEVELS // 2, doc["materials"][it["materials"][0]]["channel"], 0, 255)
    ys, xs = np.nonzero(keep)
    crop = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    return L2[crop], lev[crop]


# ----------------------------------------------------------------------------- generate

def _setup(doc, it, sp, v, parts):
    """Zones, the paint mask, the guide-tinted composite and the bare composite (on white) of one view."""
    zm = zone_masks(sp, v, parts, it["zones"])
    Z = np.any(list(zm.values()), 0)
    body = np.any([p[..., 3] > 127 for p in parts.values()], 0)
    own = np.any([parts[n][..., 3] > 127 for n in zm], 0)   # the zone parts: FLUX may end the item anywhere on them
    region = _grow(Z, it.get("bulk", 6)) & ~(body & ~own)
    comp = pf.compose(sp, v, parts); ca = np.asarray(comp).copy()
    key = np.array(_rgb(doc["materials"][it["materials"][0]]["key"]), float)
    lum = ca[..., :3].astype(float).mean(-1, keepdims=True) / 200.0
    g = Image.fromarray(np.where(_erode(Z, GUIDE_IN), 255, 0).astype(np.uint8)).filter(ImageFilter.GaussianBlur(GUIDE_IN))
    w = (np.asarray(g, float) / 255 * it.get("guide", GUIDE))[..., None]
    tint = np.clip(key * (0.72 + 0.35 * lum), 0, 255)
    ca[..., :3] = np.clip(ca[..., :3] * (1 - w) + tint * w, 0, 255).astype(np.uint8)
    al = np.asarray(comp)[..., 3:4] / 255.0
    before = (np.asarray(comp)[..., :3] * al + 255 * (1 - al)).astype(np.uint8)
    return zm, Z, body, region, comp, Image.fromarray(ca), before


def _extract(doc, it, sp, v, parts, zm, Z, body, region, before, after, d, cents):
    """The item layer of one view from FLUX's paint (`after`, full canvas): keyed, covered, outlined, split; saved."""
    L, lev = key_item(doc, it["materials"], before, after, region, Z, cents=cents)
    L, lev = outline_item(*cover(L, lev, Z & body if it.get("cover", True) else np.zeros_like(Z)))
    Image.fromarray(L).save(d / f"layer_{v}.png"); Image.fromarray(lev).save(d / f"levels_{v}.png")
    for sub in ("parts", "plevels"):
        for f in (d / sub / v).glob("*.png"):
            f.unlink()
    for n, (Ln, Vn) in split(sp, v, parts, zm, L, lev).items():
        for sub, im in (("parts", Ln), ("plevels", Vn)):
            (d / sub / v).mkdir(parents=True, exist_ok=True); Image.fromarray(im).save(d / sub / v / f"{n}.png")
    return int((lev[..., 3] > 0).sum())


def generate(race, item_id, build=None, seed=1):
    """One candidate of an item, fitted on `build` (default <race>.male)."""
    doc = spec(); it = doc["items"][item_id]; build = build or f"{race}.male"
    cid_paint, sp, parts = pf.base(build); P = prompts(race, build, item_id, doc)
    cid = f"s{seed}_{int(time.time())}"; d = out_dir(race, item_id) / cid; (d / "tmp").mkdir(parents=True, exist_ok=True)
    meta = {"race": race, "item": item_id, "on": build, "paint": cid_paint, "seed": seed, "made": int(time.time()), "prompts": P}
    if it.get("kind") == "held":
        img, lev = _gen_held(doc, it, P, d, seed)
        Image.fromarray(img).save(d / "held.png"); Image.fromarray(lev).save(d / "held_levels.png")
    else:
        ref, cents = None, {}
        for v in VIEWS:
            zm, Z, body, region, comp, guide, before = _setup(doc, it, sp, v, parts[v])
            _, after, _, out = pf._crop_pass(guide, region, pf._figure_box(comp), P[v], seed, it.get("denoise", D_ITEM),
                                             f"item_{item_id}_{cid}_{v}", [ref] if ref else [])
            out.save(d / f"paint_{v}.png")
            if v == "front":
                ref = d / "paint_front.png"
            meta.setdefault("keyed", {})[v] = _extract(doc, it, sp, v, parts[v], zm, Z, body, region, before, after, d, cents)
    (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return cid


def rekey(race, item_id, cid):
    """Re-run the extraction of a fitted candidate from its saved paints (no GPU): after a change to keying,
    cover, outline or split, or to the item's zones."""
    doc = spec(); it = doc["items"][item_id]; d = out_dir(race, item_id) / cid
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    _, sp, parts = pf.base(meta["on"]); cents = {}
    for v in VIEWS:
        zm, Z, body, region, comp, guide, before = _setup(doc, it, sp, v, parts[v])
        box = pf._figure_box(comp); side = box[2] - box[0]
        after = before.copy()
        after[box[1]:box[3], box[0]:box[2]] = np.asarray(Image.open(d / f"paint_{v}.png").convert("RGB").resize((side, side), Image.LANCZOS))
        meta.setdefault("keyed", {})[v] = _extract(doc, it, sp, v, parts[v], zm, Z, body, region, before, after, d, cents)
    (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta["keyed"]


# ----------------------------------------------------------------------------- wearing items

def colour(lev, ramps, doc=None):
    """A levels map coloured with one ramp per material ({material: ramp name}; default the group's first)."""
    doc = doc or spec(); out = np.zeros(lev.shape, np.uint8); a = lev[..., 3] > 127
    for m, e in doc["materials"].items():
        sel = a & (lev[..., 1] == e["channel"]) & (lev[..., 0] < 254)
        if sel.any():
            pal = np.array(pf.group_ramp(e["group"], (ramps or {}).get(m)), np.uint8)
            out[sel, :3] = pal[np.clip(lev[..., 0][sel], 0, len(pal) - 1)]
    out[a & (lev[..., 0] == 254), :3] = ppt.LINE; out[..., 3] = np.where(a, 255, 0)
    return out


def item_layers(race, item_id, cid, sp, view, parts, ramps=None, painted=False):
    """{part: RGBA} of one item candidate on a build's parts (coloured by `ramps`, or as painted)."""
    doc = spec(); it = doc["items"][item_id]; d = out_dir(race, item_id) / cid
    if it.get("kind") == "held":
        img = np.asarray(Image.open(d / "held.png").convert("RGBA")); lev = np.asarray(Image.open(d / "held_levels.png").convert("RGBA"))
        if view == "back":
            img, lev = img[:, ::-1].copy(), lev[:, ::-1].copy()
        lay = held_layer(doc, it, sp, view, parts, img, lev)
    else:
        lay = {}
        for f in sorted((d / "plevels" / view).glob("*.png")):
            lay[f.stem] = (np.asarray(Image.open(d / "parts" / view / f.name).convert("RGBA")),
                           np.asarray(Image.open(f).convert("RGBA")))
    return {n: (L if painted else colour(lev, ramps, doc)) for n, (L, lev) in lay.items()}


def dressed(build, worn, view, hair=None, painted=False):
    """The build's parts with items stacked on (`worn` = [(item_id, cid, ramps)], by depth), plus an optional hair
    layer (RGBA, rest pose) on the head that items with clips: [hair] cut away where they cover it."""
    race = build.split(".")[0]; doc = spec()
    _, sp, parts = pf.base(build); parts = {n: p.copy() for n, p in parts[view].items()}
    worn = sorted(worn, key=lambda w: doc["items"][w[0]].get("depth", 0))
    lays = [(w[0], item_layers(race, w[0], w[1], sp, view, parts, w[2], painted)) for w in worn]
    if hair is not None:
        h = hair.copy()
        for iid, lay in lays:
            if "hair" in doc["items"][iid].get("clips", []):
                for L in lay.values():
                    h[_grow(L[..., 3] > 0, 2), 3] = 0
        lays.insert(0, ("hair", {"head": h}))
    for iid, lay in lays:
        for n, L in lay.items():
            if n not in parts:
                continue
            if doc["items"].get(iid, {}).get("kind") == "held":   # held: under the hand, so the fingers wrap the grip
                base = Image.fromarray(L); base.alpha_composite(Image.fromarray(parts[n])); parts[n] = np.asarray(base).copy()
            else:
                base = Image.fromarray(parts[n]); base.alpha_composite(Image.fromarray(L)); parts[n] = np.asarray(base).copy()
    return sp, parts


def render(build, worn, hair_fn=None, frames=8, painted=False):
    """{view: (rest RGB 512, [walk frames RGB 512])} of a build wearing `worn`; hair_fn(view) -> hair RGBA or None."""
    out = {}
    for v in VIEWS:
        sp, parts = dressed(build, worn, v, hair_fn(v) if hair_fn else None, painted)
        order = sp["views"][v]["order"]
        tup = ppt._parts_tuple(sp, v, parts)
        rest = pp.on_bg(np.asarray(pt._compose(tup, order).resize((pp.PX, pp.PX), Image.LANCZOS)))
        walk = []
        if frames:
            B2, pieces = pt.rig_from(pt.rig_from_spec(sp, v), tup); B2["rigid"] = (pieces, order)
            walk = [pp.on_bg(pp.render(B2, v, i / frames)) for i in range(frames)]
        out[v] = (rest, walk)
    return out
