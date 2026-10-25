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
D_COMPLETE = 0.55   # the hidden-area pass starts from the smear fill (already in key colours); higher repaints them as skin


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
    return {"front": fill(doc["prompt"]), "back": fill(doc["prompt_back"]), "held": fill(doc["prompt_held"]),
            "complete": fill(doc.get("prompt_complete", PROMPT_COMPLETE))}


PROMPT_COMPLETE = ("{style}. A parts sheet for 2D cutout animation: {item}, cut into its separate pieces (body, sleeves, "
                   "legs), laid out apart on a pure white background, each piece drawn whole with rounded ends, only the "
                   "garment, no body, no skin. {materials}")


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


ZONES = ai_kit.ROOT / "specs" / "item_zones"   # painted zones (Item Workshop zone editor), one 1024 px mask per item and view


def mask_path(item_id, view, sex=None):
    """The painted zone of an item and view; with `sex`, that sex's own zone (a gender variant), if painted."""
    return ZONES / (f"{item_id}.{sex}.{view}.png" if sex else f"{item_id}.{view}.png")


def _mask_file(item_id, view, sex=None):
    f = mask_path(item_id, view, sex) if sex else None
    return f if f is not None and f.exists() else mask_path(item_id, view)


def _flood(seed, allowed):
    """Pixels of `allowed` connected to `seed` (4 neighbours), worked on the bounding box of `allowed`."""
    if not seed.any() or not allowed.any():
        return np.zeros_like(seed)
    ys, xs = np.nonzero(allowed | seed); y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    a, r = allowed[y0:y1, x0:x1], seed[y0:y1, x0:x1].copy()
    for _ in range(4000):
        g = r.copy(); g[1:] |= r[:-1]; g[:-1] |= r[1:]; g[:, 1:] |= r[:, :-1]; g[:, :-1] |= r[:, 1:]
        g &= a | seed[y0:y1, x0:x1]
        if (g == r).all():
            break
        r = g
    out = np.zeros_like(seed); out[y0:y1, x0:x1] = r
    return out


def zones_for(sp, view, parts, item_id, it, sex=None):
    """(zone per part, the whole zone incl. any painted area past the body): the painted mask if there is one, else
    the item's preset zones. A painted zone only reaches what shows in the rest pose, so each part's zone also floods
    into that part's hidden pixels (under its parent / the near arm) from where it was painted: a vest painted on the
    chest continues under the arm, a sleeve into its shoulder cap."""
    f = _mask_file(item_id, view, sex)
    if not f.exists():
        zm = zone_masks(sp, view, parts, it.get("zones", []))
        return zm, (np.any(list(zm.values()), 0) if zm else np.zeros((N, N), bool))
    M = np.asarray(Image.open(f).convert("L").resize((N, N), Image.NEAREST)) > 127
    order = sp["views"][view]["order"]
    vis = ppt.owners({n: (parts[n],) for n in order}, order)
    zm = {}
    for n in order:
        a = parts[n][..., 3] > 127; m = M & a & vis[n]
        if m.sum() < 30:
            continue
        zm[n] = m | _flood(m, a & ~vis[n])
    return zm, M




def _url(im):
    import base64, io
    b = io.BytesIO(); im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


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

def _setup(doc, it, sp, v, parts, item_id, sex=None):
    """Zones, the paint mask, the guide-tinted composite and the bare composite (on white) of one view."""
    zm, Z = zones_for(sp, v, parts, item_id, it, sex)
    if not Z.any():
        raise ValueError(f"{item_id} has no zone in the {v} view: paint one (Item Workshop → zone editor).")
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


def _extract(doc, it, sp, v, parts, zm, Z, body, region, before, after, d, cents, flux=None):
    """The item layer of one view from FLUX's paint (`after`, full canvas): keyed, covered, outlined, split, the hidden
    joint areas filled (smear, then a FLUX completion pass when `flux` = {prompt, seed, ref, tag}); saved."""
    L, lev = key_item(doc, it["materials"], before, after, region, Z, cents=cents)
    L, lev = outline_item(*cover(L, lev, Z & body if it.get("cover", True) else np.zeros_like(Z)))
    Image.fromarray(L).save(d / f"layer_{v}.png"); Image.fromarray(lev).save(d / f"levels_{v}.png")
    layers = split(sp, v, parts, zm, L, lev)
    if flux:
        layers = complete_hidden(doc, it, sp, v, parts, zm, layers, cents, d, **flux)
    save_layers(d, v, layers)
    return int((lev[..., 3] > 0).sum())


def save_layers(d, v, layers):
    for sub in ("parts", "plevels"):
        for f in (d / sub / v).glob("*.png"):
            f.unlink()
    for n, (Ln, Vn) in layers.items():
        for sub, im in (("parts", Ln), ("plevels", Vn)):
            (d / sub / v).mkdir(parents=True, exist_ok=True); Image.fromarray(im).save(d / sub / v / f"{n}.png")


def classify(doc, mats, rgb, cents):
    """Per pixel of `rgb`: (keyed mask, material channel, cel level) by the material keys and the shared levels."""
    h, sat, val = pf._hsv(rgb); keyed = np.zeros(rgb.shape[:2], bool)
    ch = np.zeros(rgb.shape[:2], np.uint8); lv = np.zeros(rgb.shape[:2], np.uint8)
    for m, kh in _key_hues(doc, mats):
        c = doc["materials"][m]["channel"]; dh = np.minimum(np.abs(h - kh), 360 - np.abs(h - kh))
        k = (dh < 30) & (sat > 0.25) & (val > 0.1) & ~keyed
        cent = cents.get(c, np.linspace(0.3, 0.9, LEVELS))
        lv[k] = np.abs(val[k][:, None] - np.asarray(cent)[None]).argmin(1); ch[k] = c; keyed |= k
    return keyed, ch, lv


def complete_hidden(doc, it, sp, v, parts, zm, layers, cents, d, prompt, seed, ref=None, tag="item"):
    """FLUX completion of each part's hidden zone pixels (round ends under the parent, the torso under the near arm):
    the parts wearing their item layers (hidden areas smear-filled, so already a key-coloured guide) laid out apart on
    one sheet, only the hidden areas masked, repainted in one pass; the key-coloured result replaces the smear there."""
    V = sp["views"][v]; order = V["order"]
    vis = ppt.owners({n: (parts[n],) for n in order}, order)
    hidden = {n: zm[n] & ~vis[n] & (parts[n][..., 3] > 127) for n in layers if n in zm}
    if sum(int(h.sum()) for h in hidden.values()) < 200:
        return layers
    # the item's own pieces only, on white: with the bare body around them FLUX paints the hidden ends as skin
    order = [n for n in order if n in layers]
    dressed = {n: layers[n][0] for n in order}
    lay = ppt.layout({n: (dressed[n],) for n in order}, order, V)
    sheet, _ = ppt.build_sheet(order, lay, dressed)
    hm = np.zeros((ppt.SHEET, ppt.SHEET), bool)
    for n, h in hidden.items():
        (x0, y0, x1, y1), px, py, w, hh = ppt._placed(lay, n)
        cut = Image.fromarray(np.where(h, 255, 0).astype(np.uint8)[y0:y1, x0:x1]).resize((w, hh), Image.NEAREST)
        hm[py:py + hh, px:px + w] |= np.asarray(cut) > 0
    hm = _grow(hm, 3)
    sheet.save(d / f"complete_{v}_in.png")
    painted = ppt._run(sheet, hm, prompt, seed, it.get("complete_denoise", D_COMPLETE), f"{tag}_{v}", [ref] if ref else [])
    painted.save(d / f"complete_{v}.png")
    out = {}
    for n, (Ln, Vn) in layers.items():
        if n not in hidden or not hidden[n].any():
            out[n] = (Ln, Vn); continue
        un = ppt.unsheet_one(painted, dressed[n], lay, n)[..., :3]
        keyed, ch, lv = classify(doc, it["materials"], un, cents)
        take = hidden[n] & keyed
        Ln, Vn = Ln.copy(), Vn.copy()
        Ln[take, :3] = un[take]; Ln[take, 3] = 255
        Vn[take, 0] = lv[take]; Vn[take, 1] = ch[take]; Vn[take, 3] = 255
        out[n] = (Ln, Vn)
    return out


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
            zm, Z, body, region, comp, guide, before = _setup(doc, it, sp, v, parts[v], item_id, build.split(".")[1])
            _, after, _, out = pf._crop_pass(guide, region, pf._figure_box(comp), P[v], seed, it.get("denoise", D_ITEM),
                                             f"item_{item_id}_{cid}_{v}", [ref] if ref else [])
            out.save(d / f"paint_{v}.png")
            if v == "front":
                ref = d / "paint_front.png"
            flux = {"prompt": P["complete"], "seed": seed, "ref": ref, "tag": f"item_{item_id}_{cid}_c"} if it.get("complete", True) else None
            meta.setdefault("keyed", {})[v] = _extract(doc, it, sp, v, parts[v], zm, Z, body, region, before, after, d, cents, flux)
        meta["completed"] = bool(it.get("complete", True))
    (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    previews(race, item_id, cid)
    return cid


def rekey(race, item_id, cid, complete=False):
    """Re-run the extraction of a fitted candidate from its saved paints: after a change to keying, cover, outline or
    split, or to the item's zones. complete=True also runs the FLUX completion pass (GPU); else the smear fill."""
    doc = spec(); it = doc["items"][item_id]; d = out_dir(race, item_id) / cid
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    _, sp, parts = pf.base(meta["on"]); cents = {}
    P = prompts(race, meta["on"], item_id, doc)
    for v in VIEWS:
        zm, Z, body, region, comp, guide, before = _setup(doc, it, sp, v, parts[v], item_id, meta["on"].split(".")[1])
        box = pf._figure_box(comp); side = box[2] - box[0]
        after = before.copy()
        after[box[1]:box[3], box[0]:box[2]] = np.asarray(Image.open(d / f"paint_{v}.png").convert("RGB").resize((side, side), Image.LANCZOS))
        flux = {"prompt": P["complete"], "seed": meta["seed"], "ref": d / "paint_front.png" if v == "back" else None,
                "tag": f"item_{item_id}_{cid}_c"} if complete else None
        meta.setdefault("keyed", {})[v] = _extract(doc, it, sp, v, parts[v], zm, Z, body, region, before, after, d, cents, flux)
    meta["completed"] = bool(complete)
    (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    previews(race, item_id, cid)
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


# ----------------------------------------------------------------------------- Item Workshop (state, API, jobs)

SEXES = ("male", "female")


def _builds(race):
    """The race's builds that have a painted puppet (male / female)."""
    out = []
    for sx in SEXES:
        try:
            pf.base(f"{race}.{sx}"); out.append(f"{race}.{sx}")
        except Exception:
            pass
    return out


def _state_path(race):
    return ai_kit.AI_DIR / "items" / race / "state.json"


def load_state(race):
    f = _state_path(race)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"approved": {}}


def save_state(race, st):
    _state_path(race).parent.mkdir(parents=True, exist_ok=True)
    _state_path(race).write_text(json.dumps(st, indent=1), encoding="utf-8")


def fit_key(it, build):
    """Which approval a build wears: its sex for a per-sex item (gender variants), else the shared one ("all")."""
    return build.split(".")[1] if it.get("fit") == "per_sex" else "all"


def resolve(race, item_id, build, st=None, doc=None):
    """The approved candidate a build wears for an item (per-sex fit falls back to the shared one), or None."""
    doc = doc or spec(); st = st or load_state(race); ap = st["approved"].get(item_id, {})
    return ap.get(fit_key(doc["items"][item_id], build)) or ap.get("all")


def previews(race, item_id, cid, ramps=None):
    """prev_<build>_<view>.png (512 px): the candidate alone on each of the race's builds."""
    d = out_dir(race, item_id) / cid
    for b in _builds(race):
        try:
            r = render(b, [(item_id, cid, ramps or {})], frames=0)
        except Exception:
            continue
        for v in VIEWS:
            pt._save(r[v][0], d / f"prev_{b.split('.')[1]}_{v}.png")


def candidates(race, item_id):
    root = out_dir(race, item_id); out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir(), key=lambda p: p.name):
        if not d.is_dir() or d.name.startswith("_") or not (d / "meta.json").exists():
            continue
        m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        rel = lambda f: str((d / f).relative_to(ai_kit.AI_DIR)).replace("\\", "/") if (d / f).exists() else None
        out.append({"id": d.name, "on": m.get("on"), "seed": m.get("seed"), "made": m.get("made"), "completed": m.get("completed"),
                    "paint": rel("paint_front.png") or rel("paint_held.png"), "paint_back": rel("paint_back.png"),
                    "prev": {f"{sx}_{v}": rel(f"prev_{sx}_{v}.png") for sx in SEXES for v in VIEWS},
                    "stamp": int((d / "meta.json").stat().st_mtime)})
    return sorted(out, key=lambda c: -(c["made"] or 0))


def state(race):
    from . import workshop as ws
    doc = spec(); st = load_state(race); pal = ws.palettes_doc()["groups"]
    return {"spec": doc, "approved": st["approved"], "builds": _builds(race),
            "candidates": {i: candidates(race, i) for i in doc["items"]},
            "masks": {i: sorted(f.name[len(i) + 1:-4] for f in ZONES.glob(f"{i}.*.png")) for i in doc["items"]} if ZONES.exists() else {},
            "ramps": {m: list(pal.get(e["group"], {})) for m, e in doc["materials"].items()},
            "hair": ws.load_state()["builds"].get(f"{race}.unisex", {}).get("face_sets", {}).get("hair", []),
            "hair_ramps": list(pal.get("hair", {}))}


ITEM_KEYS = {"slot": str, "kind": str, "text": str, "materials": list, "depth": (int, float), "bulk": (int, float),
             "clips": list, "fit": str, "hand": str, "length": (int, float), "tilt": (int, float), "zones": list,
             "denoise": (int, float), "complete": bool, "cover": bool, "guide": (int, float)}


def save_item(item_id, d, old_id=None):
    """Create / update an item definition in specs/items.json (Item Workshop: item settings)."""
    doc = spec()
    if not item_id.startswith("item.") or not all(c.isalnum() or c in "._-" for c in item_id):
        raise ValueError("an item id looks like item.name (letters, digits, . _ -)")
    if old_id and old_id != item_id:
        if item_id in doc["items"]:
            raise ValueError(f"{item_id} already exists")
        doc["items"][item_id] = doc["items"].pop(old_id)
    it = dict(doc["items"].get(item_id, {"slot": "torso", "depth": 3, "materials": ["cloth"], "bulk": 6, "zones": []}))
    for k, v in d.items():
        if k not in ITEM_KEYS:
            continue
        if v in (None, "") and k not in ("text",):
            it.pop(k, None); continue
        if not isinstance(v, ITEM_KEYS[k]):
            raise ValueError(f"{k} has the wrong type")
        it[k] = v
    bad = [m for m in it.get("materials", []) if m not in doc["materials"]]
    if bad or not it.get("materials"):
        raise ValueError(f"materials must be some of {list(doc['materials'])}")
    if it.get("fit", "shared") not in ("shared", "per_sex"):
        raise ValueError("fit is shared or per_sex")
    doc["items"][item_id] = it
    _write_spec(doc)
    return doc


def save_prompts(d):
    doc = spec()
    for k in ("prompt", "prompt_back", "prompt_held", "prompt_complete", "material_text"):
        if k in d:
            if not isinstance(d[k], str) or not d[k].strip():
                raise ValueError(f"{k} can't be empty")
            doc[k] = d[k]
    for k in ("prompt", "prompt_back", "prompt_held", "prompt_complete"):
        if k in doc and "{item}" not in doc[k]:
            raise ValueError(f"{k} must contain {{item}}")
    _write_spec(doc)
    return doc


def _write_spec(doc):
    SPEC.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


def zone_doc(race, item_id, build, view):
    """For the zone editor: the bare puppet of one view (rest pose, 1024 px) and the zone mask it edits (the build's
    sex's own zone when painted, else the shared one, else the preset zones)."""
    doc = spec(); it = doc["items"][item_id]; _, sp, parts = pf.base(build); sex = build.split(".")[1]
    _, Z = zones_for(sp, view, parts[view], item_id, it, sex)
    own = mask_path(item_id, view, sex).exists()
    return {"puppet": _url(pf.compose(sp, view, parts[view])), "mask": _url(Image.fromarray(np.where(Z, 255, 0).astype(np.uint8))),
            "source": "this sex" if own else ("painted" if mask_path(item_id, view).exists() else "preset")}


def save_zone(item_id, view, png, sex=None):
    import base64, io
    ZONES.mkdir(parents=True, exist_ok=True)
    im = Image.open(io.BytesIO(base64.b64decode(png.split(",", 1)[1]))).convert("L").resize((N, N), Image.NEAREST)
    m = np.asarray(im) > 127
    Image.fromarray(np.where(m, 255, 0).astype(np.uint8)).convert("1").save(mask_path(item_id, view, sex))
    return {"ok": True, "px": int(m.sum())}


def reset_zone(item_id, view, sex=None):
    mask_path(item_id, view, sex).unlink(missing_ok=True)
    return {"ok": True}


def approve(race, item_id, cid, key="all", on=True):
    st = load_state(race); ap = st["approved"].setdefault(item_id, {})
    if on:
        ap[key] = cid
    elif ap.get(key) == cid:
        ap.pop(key)
    save_state(race, st)
    return st["approved"]


def archive(race, item_id, cid):
    import shutil
    src = out_dir(race, item_id) / cid; dst = ai_kit.AI_DIR / "items" / race / "_archive" / item_id / cid
    dst.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(dst))
    st = load_state(race)
    for k, v in list(st["approved"].get(item_id, {}).items()):
        if v == cid:
            st["approved"][item_id].pop(k)
    save_state(race, st)
    return {"ok": True}


def outfit(race, build, items, ramps=None, hair=None, hair_ramp=None, walk=True, cids=None):
    """Previews of a build wearing `items` (each item's approved fit for that build, or `cids[item]`): rest PNGs and
    walk GIFs per view as data URLs. hair = a hair candidate id of the race's hair set (clipped by items that clip it)."""
    import base64, io
    doc = spec(); st = load_state(race); worn = []
    for i in items:
        c = (cids or {}).get(i) or resolve(race, i, build, st, doc)
        if c:
            worn.append((i, c, (ramps or {}).get(i, {})))
    hfn = None
    if hair:
        hd = pf.out_dir(race, "hair") / hair
        def hfn(v):
            f = hd / f"levels_{v}.png"
            return pf.colour_levels(np.asarray(Image.open(f).convert("RGBA")), pf.hair_ramp(hair_ramp)) if f.exists() else None
    r = render(build, worn, hair_fn=hfn, frames=8 if walk else 0)
    out = {"worn": [[i, c] for i, c, _ in worn]}
    for v in VIEWS:
        out[v] = _url(r[v][0])
        if walk:
            b = io.BytesIO(); fr = [f.convert("RGB").resize((320, 320), Image.LANCZOS) for f in r[v][1]]
            fr[0].save(b, "GIF", save_all=True, append_images=fr[1:], duration=120, loop=0)
            out[f"walk_{v}"] = "data:image/gif;base64," + base64.b64encode(b.getvalue()).decode()
    return out


def api(action, b):
    """The Item Workshop's HTTP actions (/api/ws/items/<action>); the Studio reloads this module per request."""
    race = b.get("race", "human")
    if action == "state":
        return state(race)
    if action == "prompts":   # the composed prompts of an item on a build (shown next to Generate)
        return prompts(race, b["build"], b["item"])
    if action == "item/save":
        save_item(b["item"], b.get("def", {}), b.get("old_id")); return state(race)
    if action == "spec/save":
        save_prompts(b.get("prompts", {})); return state(race)
    if action == "zone/get":
        return zone_doc(race, b["item"], b["build"], b["view"])
    if action == "zone/save":
        return save_zone(b["item"], b["view"], b["png"], b.get("sex"))
    if action == "zone/reset":
        return reset_zone(b["item"], b["view"], b.get("sex"))
    if action == "approve":
        return {"approved": approve(race, b["item"], b["id"], b.get("key", "all"), b.get("on", True))}
    if action == "archive":
        return archive(race, b["item"], b["id"])
    if action == "outfit":
        return outfit(race, b["build"], b.get("items", []), b.get("ramps"), b.get("hair"), b.get("hair_ramp"),
                      b.get("walk", True), b.get("cids"))
    raise ValueError(f"unknown items action {action}")


def job(a):
    """The Item Workshop's queue jobs (job kind "item", `action` in the args); reloaded per job."""
    race = a.get("race", "human")
    if a["action"] == "generate":
        n, seed = int(a.get("n", 1)), int(a.get("seed", 1))
        return [generate(race, a["item"], a.get("build"), seed + i) for i in range(n)]
    if a["action"] == "rekey":
        return rekey(race, a["item"], a["id"], bool(a.get("complete")))
    raise ValueError(f"unknown items job {a['action']}")
