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
import fnmatch, json, re, shutil, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .levels import ISLAND, VAL_BLUR, _components_of, _mode, _spread, keep_bands, smooth_levels, smooth_val, write_bands
from . import ai_kit, comfy, jobs, memo, store, puppet as pp, puppet_template as pt, puppet_paint as ppt, puppet_face as pf

SPEC = ai_kit.ROOT / "specs" / "items.json"
S, N = ppt.S, pf.N
VIEWS = ("front", "back")
D_ITEM = 0.85
GUIDE = 1.0      # the zones tinted in the key colour (weaker tints let FLUX shrink the item, e.g. a helmet smaller than the skull)
LINE = "#221a18"  # the detail sketch's line colour (seams, edges, straps): a dark outline, never keyed
GUIDE_IN = 2     # px the tint stays inside the zone edge, feathered
LINE_W = 3       # the even outline drawn around every item silhouette (rims, sleeve ends and boot tops get no line from FLUX)
LEVELS = 5
D_COMPLETE = 0.55   # the hidden-area pass starts from the smear fill (already in key colours); higher repaints them as skin


spec_override = None   # held/preview: the spec with an item's unsaved grip settings, for one render


def spec():
    if spec_override is not None:
        return spec_override
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
    if k <= 0:   # MinFilter(1) crashes PIL
        return m.copy()
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


def levels_from_colour(L, lev, cents):
    """Cel levels of key-coloured item pixels from their (smoothed) brightness and the candidate's level brightnesses,
    keeping each pixel's material and the outline (254) of `lev`; then smoothed (islands, ragged edges)."""
    a = (lev[..., 3] > 127) & (lev[..., 0] < 254)
    if not a.any():
        return lev
    _, _, val = pf._hsv(L[..., :3]); vs = smooth_val(val, a); out = lev.copy()
    for ch in np.unique(lev[..., 1][a]):
        sel = a & (lev[..., 1] == ch); cent = cents.get(int(ch))
        if cent is None:
            continue
        out[..., 0][sel] = np.abs(vs[sel][:, None] - np.asarray(cent)[None]).argmin(1)
    return smooth_levels(out)


def relevel(race, item_id, cid):
    """Smooth the levels of an existing candidate in place (its per-part level maps), keeping everything else."""
    d = out_dir(race, item_id) / cid; meta = _meta(d)
    cents = {int(k): np.array(v) for k, v in meta.get("cents", {}).items()}
    if not cents and (d / "layer_front.png").exists():   # older candidates: the front layer's levels by k-means, shared by both views
        L = np.asarray(Image.open(d / "layer_front.png").convert("RGBA")); lev = np.asarray(Image.open(d / "levels_front.png").convert("RGBA"))
        a = (lev[..., 3] > 127) & (lev[..., 0] < 254); _, _, val = pf._hsv(L[..., :3]); vs = smooth_val(val, a)
        for ch in np.unique(lev[..., 1][a]):
            v_ = vs[a & (lev[..., 1] == ch)]; cent = np.percentile(v_, np.linspace(10, 90, LEVELS))
            for _ in range(20):
                lab = np.abs(v_[:, None] - cent[None]).argmin(1)
                cent = np.array([v_[lab == i].mean() if (lab == i).any() else cent[i] for i in range(LEVELS)])
            cents[int(ch)] = np.sort(cent)
        meta["cents"] = {str(k): [float(x) for x in v] for k, v in cents.items()}
        store.save_meta(d, meta)
    for f in sorted((d / "plevels").rglob("*.png")):
        lev = np.asarray(Image.open(f).convert("RGBA")).copy(); fp = d / "parts" / f.parent.name / f.name
        L = np.asarray(Image.open(fp).convert("RGBA")) if fp.exists() else None
        Image.fromarray(levels_from_colour(L, lev, cents) if (L is not None and cents) else smooth_levels(lev)).save(f)
    for f in sorted(d.glob("levels_*.png")):
        lev = np.asarray(Image.open(f).convert("RGBA")).copy(); fl = d / f.name.replace("levels_", "layer_")
        L = np.asarray(Image.open(fl).convert("RGBA")) if fl.exists() else None
        Image.fromarray(levels_from_colour(L, lev, cents) if (L is not None and cents) else smooth_levels(lev)).save(f)
    if (d / "held_levels.png").exists():
        f = d / "held_levels.png"; Image.fromarray(smooth_levels(np.asarray(Image.open(f).convert("RGBA")).copy(), 60)).save(f)
    previews(race, item_id, cid)
    return {"ok": True}


# ----------------------------------------------------------------------------- prompts

def prompts(race, build, item_id, doc=None):
    doc = doc or spec(); it = doc["items"][item_id]
    body, _ = ppt.prompts(build); style = ppt.style_text()
    mats = " ".join(doc["material_text"].replace("{name}", "metal" if m == "metal" else m).replace("{word}", doc["materials"][m]["word"])
                    for m in it["materials"])
    fill = lambda t: t.replace("{style}", style).replace("{body}", body).replace("{item}", it["text"]).replace("{materials}", mats)
    held = {"shield": doc.get("prompt_held_shield", PROMPT_HELD_SHIELD), "focus": doc.get("prompt_held_focus", PROMPT_HELD_FOCUS)
            }.get(it.get("held_as"), doc["prompt_held"])
    # an item drawn alone gets the style without the body proportions (chibi, stubby legs): with them FLUX puts a little
    # character behind a shield
    held_style = ppt.style_text({"style_groups": doc.get("held_style_groups", ["style", "rendering"])})
    held = fill(held.replace("{style}", held_style))
    return {"front": fill(doc["prompt"]), "back": fill(doc["prompt_back"]), "held": held,
            "complete": fill(doc.get("prompt_complete", PROMPT_COMPLETE)), "back_edit": fill(doc.get("back_edit", BACK_EDIT)),
            "design": fill(doc.get("prompt_design", PROMPT_DESIGN)),
            "held_design": doc.get("prompt_held_design", PROMPT_HELD_DESIGN).replace("{held}", re.sub(r" \([^)]*\)", "", held))}


BACK_EDIT = ("Show this exact character from behind: a three-quarter back view facing away to the right, seen slightly from "
             "above, the same A-pose, wearing the same item ({item}) in the same flat vivid colour keys, now its back side: the back "
             "of the garment over the shoulder blades and the spine, its back seams; nothing from the front of the body or "
             "the garment (no chest, no front opening or buttons), the same size and position in the frame, plain white "
             "background. {materials}")


PROMPT_HELD_SHIELD = ("{style}. A single {item} on its own, seen straight from the front, face-on and flat to the viewer, "
                      "upright, its whole front face visible, centred, game item art. Only the shield itself: no handle, no "
                      "hilt, no grip, no pole, nothing sticking out below or behind it, no hand, no arm. {materials} "
                      "Clean dark outlines, on a pure white background.")


PROMPT_HELD_FOCUS = ("{style}. A single {item} on its own, a small object held in one hand (a tome, an orb, a holy symbol, a "
                     "charm), seen straight from the front, face-on, upright, centred, game item art. Only the object itself: "
                     "no hand, no arm. {materials} Clean dark outlines, on a pure white background.")


PROMPT_COMPLETE = ("{style}. A parts sheet for 2D cutout animation: {item}, cut into its separate pieces (body, sleeves, "
                   "legs), laid out apart on a pure white background, each piece drawn whole with rounded ends, only the "
                   "garment, no body, no skin. {materials}")


# A design reference: the approved Limner gear art of the item (see design_ref) rides along as a reference image.
# Fitted: added to the front prompt (FLUX copies cut, trims, straps, plates, rivets; the keys still decide the colours).
# Held: the reference is redrawn upright in the colour keys (a FLUX edit) instead of painting the item from text.
PROMPT_DESIGN = (" The {item} copies the design of the item in the reference image: its cut, shape, belts, straps, trims, "
                 "plates and rivets, but painted only in the colour keys.")
# {held} is the held prompt without its parenthesised examples: the focus prompt's "(a tome, an orb, ...)" turned a
# bone effigy into a skull-covered book (2026-11-01); "the same object" keeps what the icon is
PROMPT_HELD_DESIGN = ("The same object as in image 1, redrawn: keep exactly what it is, its shape, proportions, parts and "
                      "details; do not turn it into another kind of object. {held}")
DESIGN_CAT = "gear"   # the Limner category the references come from


def design_choices():
    """{Limner gear entry id: approved candidate id}: the art a piece may copy its design from."""
    from . import limner
    return dict(limner._state().load().get("approved", {}).get(DESIGN_CAT, {}))


def design_auto(item_id, it, choices=None, game=None):
    """(gear entry id, why) a piece copies its design from when the spec doesn't say: the Limner icon of its game item,
    else the slot x material fallback (slot.body.plate, ...), else its weapon type; (None, None) if none is approved."""
    from . import contract
    choices = design_choices() if choices is None else choices
    game = contract.game_items() if game is None else game
    gid = game_id(item_id, it); row = game.get(gid) or {}
    if gid in choices:
        return gid, "item"
    words = f"{it.get('text', '')} {row.get('name', '')} {row.get('text', '')}".lower()
    if it.get("kind") == "held":
        wt = row.get("weaponType") or {"shield": "shield", "focus": "focus"}.get(it.get("held_as"))
        wt = wt or next((w for w in ("sword", "dagger", "axe", "mace", "staff", "wand", "rapier", "whip") if w in words), None)
        k = f"weapontype.{wt}"
        return (k, "weapon type") if wt and k in choices else (None, None)
    slot = row.get("slot") or contract.slot_of(it.get("slot"))
    mats = set(it.get("materials", [])) | set((row.get("visual") or {}).get("ramps") or {})
    mat = ("plate" if "metal" in mats or any(w in words for w in ("steel", "iron", "plate", "metal"))
           else "leather" if "leather" in mats or "leather" in words else "cloth")
    k = f"slot.{slot}.{mat}"
    return (k, "slot fallback") if k in choices else (None, None)


def design_ref(item_id, it, choices=None, game=None):
    """{entry, cid, why, path} of the piece's design reference, or None. The spec's `design` picks a gear entry by id,
    "none" turns it off; unset = design_auto."""
    from . import limner
    choices = design_choices() if choices is None else choices
    want = it.get("design")
    if want == "none":
        return None
    eid, why = (want, "set") if want else design_auto(item_id, it, choices, game)
    if not eid or eid not in choices:
        return None
    p = limner.root() / DESIGN_CAT / eid / choices[eid] / "master.png"
    return {"entry": eid, "cid": choices[eid], "why": why, "path": p} if p.exists() else None


# Limner intake: a Workshop piece for each approved Limner gear item that has none yet. Held items need no zones; a
# fitted one copies its zones (spec rules and painted zone files) from the Workshop piece of the same game slot.
HELD_LEN = {"dagger": 0.3, "sword": 0.48, "rapier": 0.5, "twohander": 0.7, "axe": 0.4, "mace": 0.42, "staff": 0.85,
            "wand": 0.28, "polearm": 0.95, "musket": 0.7, "bow": 0.6, "whip": 0.45, "flask": 0.22, "banner": 0.9,
            "shield": 0.48, "focus": 0.25}
ZONE_FROM = {"head": "item.iron_helm", "body": "item.cloth_tunic", "legs": "item.cloth_trousers", "feet": "item.leather_boots"}
FITTED_SLOT = {"head": "head", "body": "chest", "legs": "legs", "feet": "feet"}   # game slot -> art slot


def limner_intake(dry_run=False):
    """Create Workshop pieces for approved Limner gear items without one.
    Returns {"added": [ids], "skipped": {id: why}, "defs": {id: piece}} (dry_run: what it would add)."""
    from . import contract
    doc = spec(); game = contract.game_items(); have = {game_id(k, v) for k, v in doc["items"].items()}
    added, skipped, defs = [], {}, {}
    for gid in sorted(e for e in design_choices() if e.startswith("item.") and e not in have):
        row = game.get(gid)
        if not row:
            skipped[gid] = "not in the game's content"; continue
        mats = [m for m in (row.get("visual") or {}).get("ramps") or {} if m in doc["materials"]] or ["leather"]
        text = row.get("text") or row.get("name") or gid
        slot = row.get("slot")
        if slot in ("main", "off"):
            wt = row.get("weaponType") or ""
            held_as = "shield" if wt == "shield" else "focus" if wt == "focus" else "weapon"
            d = {"slot": "mainhand" if slot == "main" else "offhand", "kind": "held", "materials": mats, "text": text,
                 "hand": "armA" if slot == "main" else "armB", "length": HELD_LEN.get(wt, 0.48), "held_as": held_as,
                 "grip_angle": 0 if held_as != "weapon" else -45, "flip": False, "fit": "shared", "depth": 3 if slot == "main" else 8}
        elif slot in ZONE_FROM and ZONE_FROM[slot] in doc["items"]:
            src = ZONE_FROM[slot]; t = doc["items"][src]
            d = {k: t[k] for k in ("depth", "bulk", "fit", "clips", "zones") if k in t}
            d.update(slot=FITTED_SLOT[slot], materials=mats, text=text, depth=4 if slot == "body" else t.get("depth", 3))
        else:
            skipped[gid] = f"no art slot for '{slot}'" if slot in (None, "trinket") else f"no zones to copy for the {slot} slot: add it by hand and paint its zones"
            continue
        added.append(gid); defs[gid] = d
        if dry_run:
            continue
        save_item(gid, d)
        if d.get("kind") != "held":
            for f in ZONES.glob(f"{src}.*.png"):
                rest = f.name[len(src):]
                if not rest.endswith(".sketch.png") and not (ZONES / (gid + rest)).exists():
                    shutil.copyfile(f, ZONES / (gid + rest))
    return {"added": added, "skipped": skipped, "defs": defs}


def plan_design_batch(race="human", n=2, items=None, extra=None):
    """One retryable job per candidate for every piece with a design reference (per-sex pieces on both builds), random
    seeds. `extra`: pieces not in the spec yet (a dry-run intake). Returns (batch id, [(label, job args)])."""
    import random
    doc = spec(); choices = design_choices(); bid = time.strftime("d%m%d_%H%M%S"); plan = []
    for i, it in {**doc["items"], **(extra or {})}.items():
        if (items and i not in items) or not design_ref(i, it, choices):
            continue
        builds = [f"{race}.male", f"{race}.female"] if it.get("fit") == "per_sex" else [f"{race}.male"]
        for b in builds:
            for _ in range(n):
                s = random.randint(1, 2 ** 31 - 1)
                plan.append((f"Item design batch {bid} · {i} · {b} s{s}",
                             {"kind": "item", "action": "generate", "race": race, "item": i, "build": b, "n": 1, "seed": s,
                              "retries": 2, "batch": bid}))
    return bid, plan


def _design_image(ref, d):
    """The reference on white (the masters are cut out), saved with the candidate as design_ref.png."""
    im = Image.open(ref["path"]).convert("RGBA"); bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); bg.alpha_composite(im)
    p = d / "design_ref.png"; bg.convert("RGB").save(p)
    return p


# ----------------------------------------------------------------------------- zones

def zone_masks(sp, view, parts, zones):
    """{part: bool mask (rest-pose canvas)} of where the item may sit on each part."""
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


ZONES = ai_kit.ROOT / "specs" / "item_zones"   # painted zones (Item Workshop zone editor), one rest-pose canvas mask (1152 px) per item and view


def mask_path(item_id, view, sex=None):
    """The painted zone of an item and view; with `sex`, that sex's own zone (a gender variant), if painted."""
    return ZONES / (f"{item_id}.{sex}.{view}.png" if sex else f"{item_id}.{view}.png")


def _mask_file(item_id, view, sex=None):
    f = mask_path(item_id, view, sex) if sex else None
    return f if f is not None and f.exists() else mask_path(item_id, view)


def sketch_path(item_id, view, sex=None):
    """The detail sketch painted over an item's zone (RGBA, rest-pose canvas): material key colours and dark lines that
    are stamped onto the guide FLUX starts from (a belt, a trim, seams, a neckline)."""
    return ZONES / (f"{item_id}.{sex}.{view}.sketch.png" if sex else f"{item_id}.{view}.sketch.png")


def _sketch_file(item_id, view, sex=None):
    f = sketch_path(item_id, view, sex) if sex else None
    return f if f is not None and f.exists() else sketch_path(item_id, view)


def sketch_guide(guide, comp, item_id, view, sex=None):
    """`guide` (RGBA) with the item's detail sketch stamped on: the sketch's colours shaded by the puppet's brightness
    (like the zone tint, so the form still reads), at full strength where drawn. Returns (guide, drawn?)."""
    f = _sketch_file(item_id, view, sex)
    if not f.exists():
        return guide, False
    sk = np.asarray(Image.open(f).convert("RGBA").resize((N, N), Image.NEAREST)).astype(float)
    a = sk[..., 3:4] / 255.0
    if not (a > 0).any():
        return guide, False
    ca = np.asarray(guide).copy(); lum = np.asarray(comp)[..., :3].astype(float).mean(-1, keepdims=True) / 200.0
    shade = np.where(lum > 0, 0.72 + 0.35 * lum, 1.0)   # off the body (bulk), unshaded
    ink = np.clip(sk[..., :3] * shade, 0, 255)
    ca[..., :3] = np.clip(ca[..., :3] * (1 - a) + ink * a, 0, 255).astype(np.uint8)
    ca[..., 3] = np.maximum(ca[..., 3], sk[..., 3].astype(np.uint8))
    return Image.fromarray(ca), True


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
    vs = smooth_val(val, found)   # blurred inside the keyed pixels: band edges follow smooth contours, not pixel noise
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
    return L, smooth_levels(lev, ISLAND if min_px >= 60 else 60)


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

def hand_frame(sp, view, parts, arm):
    """A hand in the rest pose: (centre of its pixels, unit direction wrist -> fingertip, size = sqrt(area))."""
    V = sp["views"][view]; L = V["limbs"][f"{arm}.hand"]
    a_, b_ = pt._joint(V, L["from"]) * S, pt._joint(V, L["to"]) * S; d = (b_ - a_) / (np.linalg.norm(b_ - a_) + 1e-6)
    m = parts[f"{arm}.hand"][..., 3] > 127; ys, xs = np.nonzero(m)
    return np.array([xs.mean(), ys.mean()]), d, float(np.sqrt(m.sum()))


def grip_axis(d, wrist, view="front"):
    """The grip line through a fist (unit vector toward a weapon's tip), fixed to the hand: chosen so that with the arm
    hanging straight down (the walk) the tip points up and `wrist` degrees forward (screen left in the front view, screen
    right in the back view). The hand is rigid, so the same angle to the hand's direction holds in every pose."""
    for th in (180 - wrist, -(180 - wrist)):
        hang = _rot(np.array([0.0, 1.0]), th)
        if (hang[0] < 0) == (view == "front"):
            return _rot(np.asarray(d, float), th)
    return _rot(np.asarray(d, float), 180 - wrist)


def grip_point(sp, view, arm, at=0.4):
    """Where a fist grips: `at` of the way from the wrist to the fingertip (the open hand's centre is too far out)."""
    V = sp["views"][view]; L = V["limbs"][f"{arm}.hand"]
    a_, b_ = pt._joint(V, L["from"]) * S, pt._joint(V, L["to"]) * S
    return a_ + at * (b_ - a_)


def _rot(u, deg):
    """Turn a screen vector (y down) by `deg` degrees counter-clockwise as seen on screen."""
    t = np.radians(deg); c, s_ = np.cos(t), np.sin(t)
    return np.array([c * u[0] + s_ * u[1], -s_ * u[0] + c * u[1]])


def held_frame(doc, it, sp, view, parts, img, lev_img, grip=None):
    """Where a held item goes on its hand, before `grip_angle` and `flip`: g = its grip point (px in the image), scale,
    c = the hand's grip point and u0 = the grip axis (rest-pose canvas). held_layer and the Studio's live stage share it."""
    arm = it.get("hand", "armA")
    a = img[..., 3] > 127; ys, xs = np.nonzero(a); H_, W_ = a.shape
    if it.get("grip_at"):
        gx, gy = it["grip_at"][0] * W_, it["grip_at"][1] * H_
        if view == "back":   # the image is mirrored for the back view
            gx = W_ - gx
    elif it.get("held_as") in ("shield", "focus"):   # held face-on: the strap / the palm behind its middle
        gy, gx = (ys.min() + ys.max()) / 2, (xs.min() + xs.max()) / 2
    else:
        g = a & (lev_img[..., 1] == doc["materials"]["leather"]["channel"]) & (lev_img[..., 0] < 254)   # not the outline
        gy, gx = (np.nonzero(g)[0].mean(), np.nonzero(g)[1].mean()) if g.sum() > 50 else (ys.min() + 0.85 * (ys.max() - ys.min()), xs.mean())
    scale = it.get("length", 0.5) * figure_height(sp, view, parts) / max(1, ys.max() - ys.min())
    c, u = hand_grip(sp, view, parts, arm, grip)
    return {"g": [float(gx), float(gy)], "scale": float(scale), "c": [float(x) for x in c], "u0": [float(x) for x in u]}


def figure_height(sp, view, parts):
    fig = np.any([parts[n][..., 3] > 127 for n in sp["views"][view]["order"]], 0); fy = np.nonzero(fig)[0]
    return float(fy.max() - fy.min())


def hand_grip(sp, view, parts, arm, grip=None):
    """(grip point, grip axis) of a hand: from the build's approved grip hand, else from the Template."""
    if grip:
        return np.array(grip["c"], float), np.array(grip["u"], float)
    _, d, _ = hand_frame(sp, view, parts, arm)
    return grip_point(sp, view, arm), grip_axis(d, spec().get("grip", {}).get("wrist", GRIP_WRIST), view)


def held_layer(doc, it, sp, view, parts, img, lev_img, grip=None):
    """A held item (RGBA + levels, upright, tip at the top) placed on its hand: its grip point (`grip_at` = [x, y]
    fractions of the image, else the middle of its leather, else 85% down) on the hand's grip point, its length along
    the hand's grip axis (from the build's approved grip hand, else from the Template), turned by `grip_angle` degrees,
    tip the other way with `flip`. A shield (`held_as`) grips at its middle by default.
    Returns (hand part name, RGBA, levels) on the rest-pose canvas."""
    hand = f"{it.get('hand', 'armA')}.hand"
    f = held_frame(doc, it, sp, view, parts, img, lev_img, grip)
    gx, gy, scale, c = *f["g"], f["scale"], np.array(f["c"])
    u = _rot(np.array(f["u0"]), it.get("grip_angle", 0) * (1 if view == "front" else -1))
    if it.get("flip"):
        u = -u
    ang = np.degrees(np.arctan2(-u[1], u[0])) - 90   # the image's up (0, -1) onto u
    out = []
    for src in (img, lev_img):
        nearest = src is lev_img
        im = Image.fromarray(src).resize((max(1, int(src.shape[1] * scale)), max(1, int(src.shape[0] * scale))), Image.NEAREST if nearest else Image.LANCZOS)
        big = Image.new("RGBA", (N * 2, N * 2)); big.paste(im, (N - int(gx * scale), N - int(gy * scale)))
        big = big.rotate(ang, resample=Image.NEAREST if nearest else Image.BICUBIC, center=(N, N))
        out.append(np.asarray(big.crop((int(N - c[0]), int(N - c[1]), int(N - c[0]) + N, int(N - c[1]) + N))).copy())
    L, lev = out; m = L[..., 3] > 127; L[..., 3] = np.where(m, 255, 0); lev[..., 3] = np.where(m, 255, 0)
    return hand, L, lev


def _gen_held(doc, it, P, d, sd, design=None):
    if design:   # the approved icon redrawn upright in the colour keys (`design` = its image on white)
        im = comfy.image(comfy.build_edit(comfy.upload(design), P["held_design"], seed=sd, lora_strength=0.0,
                                          megapixels=0.59, remove_bg=True, prefix="hdart/item_held"))
    else:
        im = comfy.image(comfy.build_txt2img(P["held"], 768, 768, seed=sd, lora_strength=0.0, remove_bg=True, prefix="hdart/item_held"))
    rgba = np.asarray(im.convert("RGBA")).copy(); Image.fromarray(rgba).save(d / "paint_held.png")
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


def _back_guide(doc, it, P, d, seed, comp, guide, before, region, Z):
    """The back view's guide, the way the back hair does it (AB2): a FLUX edit turns the painted front around ("the same
    garment from behind": FLUX thinks about the 3D garment instead of copying the front onto the back: no chest on the
    back of a tunic), its key-coloured item pixels are mapped onto the back puppet over the zone tint, and the edit (not
    the front) is the back pass's reference image. Returns (guide image, reference path)."""
    name = comfy.upload(d / "paint_front.png")
    ed = comfy.image(comfy.build_edit(name, P["back_edit"], seed=seed, lora_strength=0.6, prefix="hdart/itemback"))
    ed = ed.convert("RGB").resize((ppt.SHEET, ppt.SHEET), Image.LANCZOS); ed.save(d / "edit_back.png")
    box = pf._figure_box(comp); side = box[2] - box[0]
    mapped = before.copy(); mapped[box[1]:box[3], box[0]:box[2]] = np.asarray(ed.resize((side, side), Image.LANCZOS))
    Le, _ = key_item(doc, it["materials"], before, mapped, region, Z, cents={})
    ga = np.asarray(guide).copy(); m = (Le[..., 3] > 0) & region
    ga[m, :3] = Le[m, :3]; ga[m, 3] = 255
    Image.fromarray(ga).save(d / "guide_back.png")
    return Image.fromarray(ga), d / "edit_back.png"


def generate(race, item_id, build=None, seed=1):
    """One candidate of an item, fitted on `build` (default <race>.male)."""
    doc = spec(); it = doc["items"][item_id]; build = build or f"{race}.male"
    cid_paint, sp, parts = pf.base(build); P = prompts(race, build, item_id, doc)
    cid, d = store.new(out_dir(race, item_id), seed)
    meta = {"race": race, "item": item_id, "on": build, "paint": cid_paint, "made": int(time.time())}
    dr = design_ref(item_id, it); design = _design_image(dr, d) if dr else None
    if dr:
        meta["design"] = {k: dr[k] for k in ("entry", "cid", "why")}
    if it.get("kind") == "held":
        img, lev = _gen_held(doc, it, P, d, seed, design)
        Image.fromarray(img).save(d / "held.png"); Image.fromarray(lev).save(d / "held_levels.png")
    else:
        ref, cents = None, {}
        for v in VIEWS:
            zm, Z, body, region, comp, guide, before = _setup(doc, it, sp, v, parts[v], item_id, build.split(".")[1])
            if v == "back":   # like the back hair (AB2): the painted front turned around by a FLUX edit guides the back
                guide, ref = _back_guide(doc, it, P, d, seed, comp, guide, before, region, Z)
            guide, drawn = sketch_guide(guide, comp, item_id, v, build.split(".")[1])   # Eric's detail sketch, on top
            if drawn:
                meta.setdefault("sketch", []).append(v)
            guide.save(d / f"guide_{v}.png")
            use = design if (v == "front" and design) else None   # the back follows the front (an icon is a front view)
            _, after, _, out = pf._crop_pass(guide, region, pf._figure_box(comp), P[v] + (P["design"] if use else ""), seed,
                                             it.get("denoise", D_ITEM), f"item_{item_id}_{cid}_{v}",
                                             [ref] if ref else [use] if use else [])
            out.save(d / f"paint_{v}.png")
            if v == "front":
                ref = d / "paint_front.png"
            flux = {"prompt": P["complete"], "seed": seed, "ref": ref, "tag": f"item_{item_id}_{cid}_c"} if it.get("complete", True) else None
            meta.setdefault("keyed", {})[v] = _extract(doc, it, sp, v, parts[v], zm, Z, body, region, before, after, d, cents, flux)
        meta["completed"] = bool(it.get("complete", True))
        meta["cents"] = {str(k): [float(x) for x in v] for k, v in cents.items()}
    store.write_meta(d, "item", seed=seed, prompts=P, inputs={"paint": cid_paint}, params={"build": build}, **meta)
    previews(race, item_id, cid)
    return cid


def rekey(race, item_id, cid, complete=False):
    """Re-run the extraction of a fitted candidate from its saved paints: after a change to keying, cover, outline or
    split, or to the item's zones. complete=True also runs the FLUX completion pass (GPU); else the smear fill."""
    doc = spec(); it = doc["items"][item_id]; d = out_dir(race, item_id) / cid
    meta = _meta(d)
    _, sp, parts = pf.base(meta["on"]); cents = {}
    P = prompts(race, meta["on"], item_id, doc)
    for v in VIEWS:
        zm, Z, body, region, comp, guide, before = _setup(doc, it, sp, v, parts[v], item_id, meta["on"].split(".")[1])
        box = pf._figure_box(comp); side = box[2] - box[0]
        after = before.copy()
        after[box[1]:box[3], box[0]:box[2]] = np.asarray(Image.open(d / f"paint_{v}.png").convert("RGB").resize((side, side), Image.LANCZOS))
        bref = d / "edit_back.png" if (d / "edit_back.png").exists() else d / "paint_front.png"
        flux = {"prompt": P["complete"], "seed": meta["seed"], "ref": bref if v == "back" else None,
                "tag": f"item_{item_id}_{cid}_c"} if complete else None
        meta.setdefault("keyed", {})[v] = _extract(doc, it, sp, v, parts[v], zm, Z, body, region, before, after, d, cents, flux)
    meta["completed"] = bool(complete)
    meta["cents"] = {str(k): [float(x) for x in v] for k, v in cents.items()}
    store.save_meta(d, meta)
    previews(race, item_id, cid)
    return meta["keyed"]


# ----------------------------------------------------------------------------- wearing items

def colour(lev, ramps, doc=None):
    """A levels map coloured with one ramp per material ({material: ramp name or id like metal.iron}; default the
    group's first)."""
    doc = doc or spec(); out = np.zeros(lev.shape, np.uint8); a = lev[..., 3] > 127
    for m, e in doc["materials"].items():
        sel = a & (lev[..., 1] == e["channel"]) & (lev[..., 0] < 254)
        if sel.any():
            r = (ramps or {}).get(m)
            pal = np.array(pf.group_ramp(e["group"], r.split(".", 1)[1] if r and r.startswith(e["group"] + ".") else r), np.uint8)
            out[sel, :3] = pal[np.clip(lev[..., 0][sel], 0, len(pal) - 1)]
    out[a & (lev[..., 0] == 254), :3] = ppt.LINE; out[..., 3] = np.where(a, 255, 0)
    return out


def item_layers(race, item_id, cid, sp, view, parts, ramps=None, painted=False, grips=None):
    """{part: RGBA} of one item candidate on a build's parts (coloured by `ramps`, or as painted). Held items sit on
    their hand's grip (`grips` = grip_assets of the build, if it has an approved grip hand)."""
    doc = spec()
    return {n: (L if painted else colour(lev, ramps, doc)) for n, (L, lev) in item_raw(race, item_id, cid, sp, view, parts, grips, doc).items()}


def item_raw(race, item_id, cid, sp, view, parts, grips=None, doc=None):
    """{part: (painted RGBA, levels RGBA)} of one item candidate on a build's parts, before colouring."""
    doc = doc or spec(); it = doc["items"][item_id]; d = out_dir(race, item_id) / cid
    if it.get("kind") == "held":
        img = np.asarray(Image.open(d / "held.png").convert("RGBA")); lev = np.asarray(Image.open(d / "held_levels.png").convert("RGBA"))
        if view == "back":
            img, lev = img[:, ::-1].copy(), lev[:, ::-1].copy()
        g = (grips or {}).get(it.get("hand", "armA"))
        hand, L, lv = held_layer(doc, it, sp, view, parts, img, lev, g)
        lay = {hand: (L, lv)}
    else:
        lay = {}
        for f in sorted((d / "plevels" / view).glob("*.png")):
            lay[f.stem] = (np.asarray(Image.open(d / "parts" / view / f.name).convert("RGBA")),
                           np.asarray(Image.open(f).convert("RGBA")))
    return lay


_LAYERS = memo.LRU(96)   # item_raw() of worn items, cropped to their pixels (a few hundred KB each)


def _crop(L, lev):
    """(y, x, painted crop, levels crop) of a layer cut to the box of its pixels."""
    ys, xs = np.nonzero((L[..., 3] > 0) | (lev[..., 3] > 0))
    if not len(xs):
        return (0, 0, memo.frozen(L[:0, :0].copy()), memo.frozen(lev[:0, :0].copy()))
    box = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    return (int(ys.min()), int(xs.min()), memo.frozen(L[box].copy()), memo.frozen(lev[box].copy()))


def _uncrop(y, x, a):
    L = np.zeros((N, N, 4), np.uint8); L[y:y + a.shape[0], x:x + a.shape[1]] = a
    return L


def _layers_key(race, build, bkey, item_id, cid, view, doc):
    """What item_raw() of one worn item depends on: the build's paint, the item's settings and files, its grip hand."""
    it = doc["items"][item_id]; d = out_dir(race, item_id) / cid
    files = (d / "held.png", d / "held_levels.png") if it.get("kind") == "held" else (d / "parts" / view, d / "plevels" / view)
    grip = None
    if it.get("kind") == "held":
        gid = load_state(race).get("grip", {}).get(build)
        grip = (gid, memo.stamp(grip_dir(race, build) / gid)) if gid else None
    return (bkey, item_id, cid, view, json.dumps(it, sort_keys=True), json.dumps(doc["materials"], sort_keys=True),
            grip, memo.stamp(*files))


def _skin(build, pcid, view, parts, colours):
    """The parts with their skin recoloured from the paint's skin bands (bands/<view>/<part>.png, R = level) by a ramp."""
    d = ppt.out_dir(build) / pcid / "bands" / view; pal = np.array(colours, np.uint8); out = dict(parts)
    for n, im in parts.items():
        f = d / f"{n}.png"
        if not f.exists():
            continue
        b = np.asarray(Image.open(f).convert("RGBA")); sel = (b[..., 3] > 127) & (b[..., 0] < 254) & (im[..., 3] > 127)
        if sel.any():
            a = im.copy(); a[sel, :3] = pal[np.clip(b[..., 0][sel], 0, len(pal) - 1)]; out[n] = a
    return out


def dressed(build, worn, view, hair=None, painted=False, pcid=None, skin=None, face=None):
    """The build's parts with items stacked on (`worn` = [(item_id, cid, ramps)], by depth; ramps None = as painted), plus
    an optional hair layer (RGBA, rest pose) on the head that items with clips: [hair] cut away where they cover it.
    pcid = another paint than the build's approved one; skin = ramp colours for its skin bands; face = a face layer (RGBA)
    drawn on the head with its painted face removed."""
    race = build.split(".")[0]; doc = spec()
    _, sp, parts = pf.base(build, pcid); parts = dict(parts[view])   # read-only arrays: layers below replace entries, never draw in place
    if skin is not None:
        parts = _skin(build, pcid or pf.base_cid(build), view, parts, skin)
    if face is not None and "head" in parts:
        h = np.array(parts["head"])
        if view == "front":
            h = pf.blank_face(h, pf.face_ellipse(sp, 1.2) & pf.inner_head(h))
        hi = Image.fromarray(np.ascontiguousarray(h)); hi.alpha_composite(Image.fromarray(face)); parts["head"] = np.asarray(hi).copy()
    worn = sorted(worn, key=lambda w: doc["items"][w[0]].get("depth", 0))
    grips = grip_assets(race, build, view); bkey = pf.base_key(build, pcid); lays = []
    for iid, cid, ramps in worn:   # cached before colouring: a new colour recolours only the item's crop
        k = _layers_key(race, build, bkey, iid, cid, view, doc); hit = _LAYERS.get(k)
        if hit is None:
            hit = _LAYERS.put(k, {n: _crop(L, lev) for n, (L, lev) in item_raw(race, iid, cid, sp, view, parts, grips, doc).items()})
        lays.append((iid, {n: _uncrop(y, x, Lc if (painted or ramps is None) else colour(lv, ramps, doc)) for n, (y, x, Lc, lv) in hit.items()}))
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
            if doc["items"].get(iid, {}).get("kind") == "held":   # held: under-hand < weapon < fingers (grip hand), else under the hand
                g = grips.get(n.split(".")[0])
                if g:
                    base = Image.fromarray(g["under"]); base.alpha_composite(Image.fromarray(L)); base.alpha_composite(Image.fromarray(g["over"]))
                else:
                    base = Image.fromarray(L); base.alpha_composite(Image.fromarray(parts[n]))
                parts[n] = np.asarray(base).copy()
            else:
                base = Image.fromarray(parts[n]); base.alpha_composite(Image.fromarray(L)); parts[n] = np.asarray(base).copy()
    return sp, parts


def render(build, worn, hair_fn=None, frames=8, painted=False):
    """{view: (rest RGB, [walk frames RGB]) at rig size (576)} of a build wearing `worn`; hair_fn(view) -> hair RGBA or None."""
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


def editor_walk(race, build, pcid, view, items, hair=None, face=None, skin=None, frames=8, size=256):
    """The Workshop Editor's walk: one view of a paint candidate wearing `items` ([[item, cid, ramps or None]]), a hair
    and a face ({"id", "ramp" or None}; None ramp = as painted) and a skin ramp (None = as painted), as an 8-frame strip
    (data URL, `size` px per frame), drawn from the saved files."""
    if view not in VIEWS:
        raise ValueError("Unknown view.")

    def layer(kind, e):
        if not e or not e.get("id"):
            return None
        d = pf.out_dir(race, kind) / e["id"]; f, fl = d / f"layer_{view}.png", d / f"levels_{view}.png"
        if not f.exists():
            return None
        if kind == "hair":
            if e.get("ramp") and fl.exists():
                return pf.colour_levels(np.asarray(Image.open(fl).convert("RGBA")), pf.hair_ramp(e["ramp"]))
            return np.asarray(Image.open(f).convert("RGBA"))
        L = np.asarray(Image.open(f).convert("RGBA")).copy()
        if e.get("ramp") and fl.exists():   # the irises in the eye ramp
            lv = np.asarray(Image.open(fl).convert("RGBA")); ir = pf.colour_levels(lv, pf.group_ramp("eyes", e["ramp"]))
            m = (lv[..., 3] > 127) & (lv[..., 0] < 254); L[m, :3] = ir[m, :3]
        return L

    worn = [(i, c, r) for i, c, r in items]
    sp, parts = dressed(build, worn, view, layer("hair", hair), pcid=pcid, skin=pf.group_ramp("skin", skin) if skin else None,
                        face=layer("face", face))
    order = sp["views"][view]["order"]; tup = ppt._parts_tuple(sp, view, parts)
    B2, pieces = pt.rig_from(pt.rig_from_spec(sp, view), tup); B2["rigid"] = (pieces, order)
    strip = Image.new("RGB", (size * frames, size))
    for i in range(frames):
        fr = pp.on_bg(pp.render(B2, view, i / frames)).resize((size, size), Image.LANCZOS)
        strip.paste(fr, (i * size, 0))
    return {"strip": _url(strip), "view": view}


def held_stage(race, item_id, build, cid=None, unsaved=None, ramps=None):
    """The Studio's live grip stage (rest pose, both views): the build drawn below and above the held item (`unsaved`
    = the form's hand / held_as over the saved ones), the hand's grip point and axis, the figure height, and with a
    candidate its coloured image (mirrored for the back) with the automatic grip point and scale. The browser turns
    the item by `grip_angle` / `flip` as held_layer does, so the angle can be dragged live."""
    doc = spec(); it = dict(doc["items"][item_id]); it.pop("grip_at", None)
    it.update({k: v for k, v in (unsaved or {}).items() if k in ("hand", "held_as") and v})
    arm = it.get("hand", "armA")
    _, sp, parts = pf.base(build); out = {}
    for v in VIEWS:
        P = parts[v]; order = [n for n in sp["views"][v]["order"] if n in P]
        g = grip_assets(race, build, v).get(arm); hn = f"{arm}.hand"
        below, above = Image.new("RGBA", (N, N)), Image.new("RGBA", (N, N)); cv = below
        for n in order:
            if n == hn:   # under-hand < weapon < fingers (grip hand), else weapon < the painted hand
                if g:
                    below.alpha_composite(Image.fromarray(g["under"])); above.alpha_composite(Image.fromarray(g["over"]))
                else:
                    above.alpha_composite(Image.fromarray(P[n]))
                cv = above
                continue
            cv.alpha_composite(Image.fromarray(P[n]))
        c, u = hand_grip(sp, v, P, arm, g)
        o = {"below": _url(below), "above": _url(above), "c": [float(x) for x in c], "u0": [float(x) for x in u],
             "fig": figure_height(sp, v, P), "grip_hand": bool(g)}
        if cid:
            d = out_dir(race, item_id) / cid
            img = np.asarray(Image.open(d / "held.png").convert("RGBA")); lev = np.asarray(Image.open(d / "held_levels.png").convert("RGBA"))
            if v == "back":
                img, lev = img[:, ::-1].copy(), lev[:, ::-1].copy()
            f = held_frame(doc, it, sp, v, P, img, lev, g); ys = np.nonzero(img[..., 3] > 127)[0]
            o.update(img=_url(Image.fromarray(colour(lev, ramps, doc))), g_auto=f["g"], w=img.shape[1], h=img.shape[0],
                     span=float(ys.max() - ys.min()))
        out[v] = o
    return {"N": N, "views": out}


# ----------------------------------------------------------------------------- Item Workshop (state, API, jobs)

SEXES = ("male", "female")


def _builds(race):
    """The race's builds that have a painted puppet (male / female)."""
    return [f"{race}.{sx}" for sx in SEXES if pf.base_cid(f"{race}.{sx}")]   # no image loading: just "has a paint"


def _state(race):
    return store.State(ai_kit.AI_DIR / "items" / race / "state.json", {"approved": {}})


def load_state(race):
    return _state(race).load()


def save_state(race, st):
    _state(race).save(st)


def fit_key(it, build):
    """Which approval a build wears: its sex for a per-sex item (gender variants), else the shared one ("all")."""
    return build.split(".")[1] if it.get("fit") == "per_sex" else "all"


def resolve(race, item_id, build, st=None, doc=None):
    """The approved candidate a build wears for an item (per-sex fit falls back to the shared one), or None."""
    doc = doc or spec(); st = st or load_state(race); ap = st["approved"].get(item_id, {})
    return ap.get(fit_key(doc["items"][item_id], build)) or ap.get("all")


def previews(race, item_id, cid, ramps=None):
    """prev_<build>_<view>.png (rig size, 576 px): the candidate alone on each of the race's builds."""
    d = out_dir(race, item_id) / cid
    for b in _builds(race):
        try:
            r = render(b, [(item_id, cid, ramps or {})], frames=0)
        except Exception:
            continue
        for v in VIEWS:
            pt._save(r[v][0], d / f"prev_{b.split('.')[1]}_{v}.png")


def candidates(race, item_id):
    out = []
    for d in store.folders(out_dir(race, item_id), finished=True):
        m = store.read_meta(d)
        rel = lambda f: str((d / f).relative_to(ai_kit.AI_DIR)).replace("\\", "/") if (d / f).exists() else None
        out.append({"id": d.name, "on": m.get("on"), "seed": m.get("seed"), "made": m.get("made"), "completed": m.get("completed"),
                    "edited": bool(m.get("edited")),
                    "paint": rel("paint_front.png") or rel("paint_held.png"), "paint_back": rel("paint_back.png"),
                    "held": rel("held.png"),
                    "prev": {f"{sx}_{v}": rel(f"prev_{sx}_{v}.png") for sx in SEXES for v in VIEWS},
                    "stamp": int((d / "meta.json").stat().st_mtime)})
    return sorted(out, key=lambda c: -(c["made"] or 0))


def state(race):
    from . import workshop as ws
    from . import contract, item_export as ie
    doc = spec(); st = load_state(race); pal = ws.palettes_doc()["groups"]
    doc.setdefault("prompt_held_shield", PROMPT_HELD_SHIELD); doc.setdefault("prompt_held_focus", PROMPT_HELD_FOCUS)
    doc.setdefault("prompt_design", PROMPT_DESIGN); doc.setdefault("prompt_held_design", PROMPT_HELD_DESIGN)
    v = contract.vocab(); game = contract.game_items(); gi = {g: ie.info(g, doc, v, game) for g in ie.game_ids(doc)}
    choices = design_choices()
    design = {i: (lambda r: r and {k: r[k] for k in ("entry", "cid", "why")})(design_ref(i, it, choices, game))
              for i, it in doc["items"].items()}
    return {"spec": doc, "approved": st["approved"], "builds": _builds(race),
            "candidates": {i: candidates(race, i) for i in doc["items"]},
            "masks": {i: sorted(f.name[len(i) + 1:-4] for f in ZONES.glob(f"{i}.*.png")) for i in doc["items"]} if ZONES.exists() else {},
            "ramps": {m: list(pal.get(e["group"], {})) for m, e in doc["materials"].items()},
            "hair": ws.load_state()["builds"].get(f"{race}.unisex", {}).get("face_sets", {}).get("hair", []),
            "hair_ramps": list(pal.get("hair", {})), "grips": grip_state(race), "grip_spec": grip_spec(),
            "vocab": {"artSlots": contract.art_slots(v), "heldAs": list(contract.HELD_AS), "found": v["found"],
                      "revision": v["revision"], "known": sorted(set(game) | set(v["wanted"])), "slots": v["slots"]},
            "game": gi,
            # design references: the approved Limner gear art each piece copies (Items tab › Design)
            "design": design, "designChoices": {e: choices[e] for e in sorted(choices)},
            # the game's items no Workshop piece makes yet (Outfit tab: shown per slot as "no art")
            "noart": {g: {"slot": r.get("slot"), "name": r.get("name") or g, "wanted": g in v["wanted"]}
                      for g, r in game.items() if g not in gi and r.get("slot")},
            "swatches": {grp: {n: _swatch(grp, n) for n in ramps} for grp, ramps in pal.items()}}



def _swatch(group, name):
    """A ramp's middle colour as #rrggbb (the Outfit tab's colour buttons)."""
    try:
        r = pf.group_ramp(group, name)
    except Exception:
        return "#808080"
    return "#%02x%02x%02x" % tuple(r[len(r) // 2])

ITEM_KEYS = {"slot": str, "kind": str, "text": str, "materials": list, "depth": (int, float), "bulk": (int, float),
             "clips": list, "fit": str, "hand": str, "length": (int, float), "tilt": (int, float), "zones": list,
             "denoise": (int, float), "complete": bool, "cover": bool, "guide": (int, float),
             "grip_angle": (int, float), "flip": bool, "grip_at": list, "held_as": str, "item": str, "design": str}


def game_id(piece_id, it):
    """The game item a Workshop piece belongs to (several pieces can make one item: breastplate + pauldrons)."""
    return it.get("item") or piece_id


def save_item(item_id, d, old_id=None):
    """Create / update an item definition in specs/items.json (Item Workshop: item settings)."""
    from . import contract
    doc = spec(); voc = contract.vocab()
    if item_id not in doc["items"] or (old_id and old_id != item_id):   # new names follow the game's id rule (R-002)
        bad = contract.id_problem(item_id, voc)
        if bad:
            raise ValueError(bad)
    if old_id and old_id != item_id:
        if item_id in doc["items"]:
            raise ValueError(f"{item_id} already exists")
        doc["items"][item_id] = doc["items"].pop(old_id)
    it = dict(doc["items"].get(item_id, {"slot": "shirt", "depth": 3, "materials": ["cloth"], "bulk": 6, "zones": []}))
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
    if it.get("held_as", "weapon") not in contract.HELD_AS:
        raise ValueError(f"held_as is one of {', '.join(contract.HELD_AS)}")
    if it.get("slot") not in contract.art_slots(voc):   # the game's layer slots (R-001)
        raise ValueError(f"slot must be one of the game's art slots: {', '.join(contract.art_slots(voc))}")
    if it.get("item"):
        bad = contract.id_problem(it["item"], voc)
        if bad:
            raise ValueError(bad)
    if it.get("fit", "shared") not in ("shared", "per_sex"):
        raise ValueError("fit is shared or per_sex")
    if it.get("design") not in (None, "none") and it["design"] not in design_choices():
        raise ValueError(f"design: {it['design']} has no approved art in Limner › Gear")
    doc["items"][item_id] = it
    _write_spec(doc)
    return doc


def save_prompts(d):
    doc = spec()
    for k in ("prompt", "prompt_back", "prompt_held", "prompt_held_shield", "prompt_held_focus", "prompt_complete", "material_text",
              "prompt_design", "prompt_held_design"):
        if k in d:
            if not isinstance(d[k], str) or not d[k].strip():
                raise ValueError(f"{k} can't be empty")
            doc[k] = d[k]
    for k in ("prompt", "prompt_back", "prompt_held", "prompt_held_shield", "prompt_held_focus", "prompt_complete"):
        if k in doc and "{item}" not in doc[k]:
            raise ValueError(f"{k} must contain {{item}}")
    if "{held}" not in doc.get("prompt_held_design", "{held}"):
        raise ValueError("prompt_held_design must contain {held}")
    _write_spec(doc)
    return doc


def _write_spec(doc):
    SPEC.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


def zone_doc(race, item_id, build, view):
    """For the zone editor: the bare puppet of one view (rest-pose canvas) and the zone mask it edits (the build's
    sex's own zone when painted, else the shared one, else the preset zones)."""
    doc = spec(); it = doc["items"][item_id]; _, sp, parts = pf.base(build); sex = build.split(".")[1]
    _, Z = zones_for(sp, view, parts[view], item_id, it, sex)
    own = mask_path(item_id, view, sex).exists(); sk = _sketch_file(item_id, view, sex)
    mats = [{"name": m, "key": doc["materials"][m]["key"]} for m in it.get("materials", [])]
    return {"puppet": _url(pf.compose(sp, view, parts[view])), "mask": _url(Image.fromarray(np.where(Z, 255, 0).astype(np.uint8))),
            "sketch": _url(Image.open(sk).convert("RGBA")) if sk.exists() else None, "materials": mats, "line": LINE,
            "source": "this sex" if own else ("painted" if mask_path(item_id, view).exists() else "preset")}


def save_zone(item_id, view, png, sex=None, sketch=None):
    """The painted zone and (when given) the detail sketch; an empty sketch removes its file."""
    import base64, io
    ZONES.mkdir(parents=True, exist_ok=True)
    dec = lambda u: Image.open(io.BytesIO(base64.b64decode(u.split(",", 1)[1])))
    m = np.asarray(dec(png).convert("L").resize((N, N), Image.NEAREST)) > 127
    Image.fromarray(np.where(m, 255, 0).astype(np.uint8)).convert("1").save(mask_path(item_id, view, sex))
    out = {"ok": True, "px": int(m.sum())}
    if sketch is not None:
        sk = np.asarray(dec(sketch).convert("RGBA").resize((N, N), Image.NEAREST)).copy()
        sk[sk[..., 3] < 128] = 0; on = sk[..., 3] > 0; sk[on, 3] = 255   # hard edges, and every pixel snapped to the
        pal = np.array([_rgb(c["key"]) for c in spec()["materials"].values()] + [_rgb(LINE)], float)   # palette: pure keys
        if on.any():
            px = sk[on, :3].astype(float); sk[on, :3] = pal[((px[:, None] - pal[None]) ** 2).sum(-1).argmin(1)].astype(np.uint8)
        f = sketch_path(item_id, view, sex); out["sketch_px"] = int((sk[..., 3] > 0).sum())
        if out["sketch_px"]:
            Image.fromarray(sk).save(f)
        else:
            f.unlink(missing_ok=True)
    return out


def reset_zone(item_id, view, sex=None):
    mask_path(item_id, view, sex).unlink(missing_ok=True); sketch_path(item_id, view, sex).unlink(missing_ok=True)
    return {"ok": True}


def approve(race, item_id, cid, key="all", on=True):
    with _state(race).edit() as st:
        store.pick(st, ("approved", item_id, key), cid, on)
    return st["approved"]


def archive(race, item_id, cid):
    """Move a candidate to archive/workshop/items/<race>/<item>/<id> (nothing is deleted); it stops being approved."""
    from . import workshop as ws
    with _state(race).edit() as st:
        store.archive(out_dir(race, item_id) / cid, ws.ARCHIVE / "workshop" / "items" / race / item_id / cid)
        for k, v in list(st["approved"].get(item_id, {}).items()):
            if v == cid:
                st["approved"][item_id].pop(k)
    return {"ok": True}


OUTFIT_V = 1                                         # bump when render() changes how a preview looks
OUTFIT_KEEP = 400                                    # rendered previews kept on disk (newest)


def _outfit_cache():
    from . import config
    return config.data_dir() / "work" / "cache" / "outfit"


def outfit(race, build, items, ramps=None, hair=None, hair_ramp=None, walk=True, cids=None, part=None):
    """Previews of a build wearing `items` (each item's approved fit for that build, or `cids[item]`) as data URLs:
    part "rest" = rest PNGs per view, "walk" = walk GIFs per view (walk_front, walk_back), None = rest + (if walk) walk.
    hair = a hair candidate id of the race's hair set (clipped by items that clip it). Results are cached on disk
    (work/cache/outfit), keyed by everything they're drawn from: the paint, each item's files and settings, ramps, hair."""
    import base64, hashlib, io
    from . import workshop as ws
    part = part or ("all" if walk else "rest")
    doc = spec(); st = load_state(race); worn = []
    for i in items:
        c = (cids or {}).get(i) or resolve(race, i, build, st, doc)
        if c:
            worn.append((i, c, (ramps or {}).get(i, {})))
    hd = pf.out_dir(race, "hair") / hair if hair else None
    bkey = pf.base_key(build)
    key = repr((OUTFIT_V, part, bkey, [(_layers_key(race, build, bkey, i, c, v, doc), json.dumps(r, sort_keys=True)) for i, c, r in worn for v in VIEWS],
                hair, memo.stamp(hd) if hd else None, hair_ramp, json.dumps(ws.palettes_doc(), sort_keys=True)))
    cf = _outfit_cache() / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    if cf.exists():
        try:
            out = json.loads(cf.read_text(encoding="utf-8")); out["cached"] = True; cf.touch()
            return out
        except ValueError:
            pass
    hfn = None
    if hd:
        def hfn(v):
            f = hd / f"levels_{v}.png"
            return pf.colour_levels(np.asarray(Image.open(f).convert("RGBA")), pf.hair_ramp(hair_ramp)) if f.exists() else None
    r = render(build, worn, hair_fn=hfn, frames=8 if part != "rest" else 0)
    out = {"worn": [[i, c] for i, c, _ in worn]}
    for v in VIEWS:
        if part != "walk":
            out[v] = _url(r[v][0])
        if part != "rest":
            b = io.BytesIO(); fr = [f.convert("RGB").resize((320, 320), Image.LANCZOS) for f in r[v][1]]
            fr[0].save(b, "GIF", save_all=True, append_images=fr[1:], duration=120, loop=0)
            out[f"walk_{v}"] = "data:image/gif;base64," + base64.b64encode(b.getvalue()).decode()
    store.write_json(cf, out, indent=None)
    old = sorted(cf.parent.glob("*.json"), key=lambda f: f.stat().st_mtime)[:-OUTFIT_KEEP]
    for f in old:
        f.unlink(missing_ok=True)
    return out


# ----------------------------------------------------------------------------- grip hands (holding a weapon)

GRIP_WRIST = 40     # with the arm hanging, the weapon's tip points up and this many degrees forward
GRIP_ROD = 0.36     # the guide rod's width as a fraction of the hand's size
D_GRIP = 0.75
GRIP_PROMPT = ("{style}. A close-up of the hand and forearm of one {body}: the hand is closed in a fist, tightly gripping a "
               "plain straight rod; the fingers wrap around the rod in front of it, the thumb over the fingers, a big chunky "
               "cartoon fist. The rod is one flat vivid pure magenta colour (a colour key). Clean dark outlines, the same skin "
               "and shading, on a pure white background.")
GRIP_PROMPT_BACK = ("{style}. A close-up of the same hand from the other side, seen from behind (three-quarter back view): the "
                    "fist tightly gripping the plain straight rod as in the reference image, a big chunky cartoon fist. The rod "
                    "is one flat vivid pure magenta colour (a colour key). Clean dark outlines, on a pure white background.")
GRIP_ZOOM = 4.2     # the close-up box around a hand, in hand sizes (a hand is ~40 px in the full figure: too small for a fist)


def grip_dir(race, build):
    return ai_kit.AI_DIR / "items" / race / "_grip" / build


def grip_spec():
    g = spec().get("grip", {})
    return {"prompt": g.get("prompt", GRIP_PROMPT), "prompt_back": g.get("prompt_back", GRIP_PROMPT_BACK),
            "wrist": g.get("wrist", GRIP_WRIST), "rod": g.get("rod", GRIP_ROD), "hands": g.get("hands", ["armA", "armB"]),
            "key": g.get("key", "accent")}


def grip_prompts(build, g=None):
    g = g or grip_spec(); body, _ = ppt.prompts(build); style = ppt.style_text()
    fill = lambda t: t.replace("{style}", style).replace("{body}", body)
    return {"front": fill(g["prompt"]), "back": fill(g["prompt_back"])}


def _rod(c, u, length, width):
    """The guide rod's corners: a bar along u through c."""
    n = np.array([-u[1], u[0]]); h = length / 2; w = width / 2
    return [tuple(c + u * h + n * w), tuple(c + u * h - n * w), tuple(c - u * h - n * w), tuple(c - u * h + n * w)]


def _grip_setup(sp, v, pv, g, key):
    """The guide of one view: the puppet with a key-coloured rod through each fist, the paint mask and per hand
    (centre, axis, rod width, rod mask, hand mask, fist disc, region)."""
    from PIL import ImageDraw
    comp = pf.compose(sp, v, pv); ca = Image.fromarray(np.asarray(comp).copy()); dr = ImageDraw.Draw(ca)
    body = np.any([q[..., 3] > 127 for q in pv.values()], 0); region = np.zeros((N, N), bool); rods = {}
    yy, xx = np.mgrid[:N, :N]
    for arm in g["hands"]:
        c, dd, sz = hand_frame(sp, v, pv, arm); u = grip_axis(dd, g["wrist"], v); w = g["rod"] * sz
        c = grip_point(sp, v, arm)
        poly = _rod(c, u, 2.6 * sz, w); dr.polygon(poly, fill=key + (255,), outline=(34, 26, 24, 255), width=3)
        rm = Image.new("L", (N, N)); ImageDraw.Draw(rm).polygon(poly, fill=255); rm = np.asarray(rm) > 0
        hm = pv[f"{arm}.hand"][..., 3] > 127
        lower = pv[f"{arm}.lower"][..., 3] > 127
        wrist = lower & _grow(hm, int(0.35 * sz))   # the forearm's end: repainted so the fist joins the wrist
        reg = (_grow(hm, int(0.3 * sz)) | _grow(rm, 6)) & ~(body & ~hm & ~wrist)
        fist = np.hypot(xx - c[0], yy - c[1]) < 0.8 * sz   # the fist: hand pixels farther out along the rod are its outline
        side = int(max(256, GRIP_ZOOM * sz)); bx = int(np.clip(c[0] - side / 2, 0, N - side)); by = int(np.clip(c[1] - side / 2, 0, N - side))
        region |= reg; rods[arm] = (c, u, w, rm, hm, fist, reg, lower, (bx, by, bx + side, by + side))
    return comp, ca, region, rods


def _rod_fit(K, near, guide_u):
    """The rod FLUX drew (its key pixels near the hand): centre, unit axis (same sense as the guide's) and width."""
    m = pf._components(K & near, 80)
    ys, xs = np.nonzero(m)
    if len(xs) < 80:
        return None
    P_ = np.stack([xs, ys], 1).astype(float); c = P_.mean(0); _, _, vt = np.linalg.svd(P_ - c, full_matrices=False)
    u = vt[0] / np.linalg.norm(vt[0])
    if u @ guide_u < 0:
        u = -u
    t = (P_ - c) @ u; length = t.max() - t.min()
    return c, u, len(xs) / max(length, 1.0), m


def _grip_extract(after, key, rods, d, v, meta=None):
    """Per hand: under (the hand, its palm behind the rod filled in) and over (the fingers in front of the rod), from
    the rod FLUX actually drew (it rarely keeps the guide's angle); the measured grip point and axis go into meta."""
    h, sat, val = pf._hsv(after); kh = float(pf._hsv(np.array([[key]], np.uint8))[0][0, 0])
    dh = np.minimum(np.abs(h - kh), 360 - np.abs(h - kh)); K = (dh < 30) & (sat > 0.3) & (val > 0.15)
    white = (after.min(-1) > 225) & ((after.max(-1).astype(int) - after.min(-1)) < 24)
    yy, xx = np.mgrid[:N, :N]
    for arm, (c, u, w, rm, hm, fist, reg, lower, box) in rods.items():
        sz = np.sqrt(hm.sum())
        Hm = reg & ~_grow(K, 1) & ~white & (_grow(hm, int(0.3 * sz)) | (lower & reg))
        core = _grow(_erode(Hm, 3), 3)   # thin lines (the rod's outline) drop out; the fist's own outline stays next to it
        Hm = pf._components(core | (Hm & _grow(core, 4)), 60)
        fit = _rod_fit(K, reg, u)
        if fit:   # the fingers in front = hand pixels inside the drawn rod's strip, between its two visible ends
            rc, ru, rw, rmask = fit
            t = (xx - rc[0]) * ru[0] + (yy - rc[1]) * ru[1]; n_ = np.abs(-(xx - rc[0]) * ru[1] + (yy - rc[1]) * ru[0])
            strip = n_ <= rw / 2 + 1
            hid = Hm & strip & ~(lower & ~hm)   # the fist over the rod (not the forearm crossing it): its middle is the grip point
            gc = (np.array([xx[hid].mean(), yy[hid].mean()]) if hid.sum() > 20 else rc)
            gt = (gc - rc) @ ru; gc = rc + gt * ru
            over = hid
            if meta is not None:
                meta["grip"][arm][v] = {"c": [float(gc[0]), float(gc[1])], "u": [float(ru[0]), float(ru[1])], "w": float(rw), "measured": True}
            rod_px = rmask
        else:
            over = Hm & _grow(rm, 1); rod_px = K & reg
        under = np.zeros((N, N, 4), np.uint8); under[Hm, :3] = after[Hm]; under[Hm, 3] = 255
        behind = rod_px & (_grow(hm, 3) | _grow(Hm, 2))   # the palm behind the rod: filled from the hand around it
        if behind.any():
            filled, got = _spread(Hm, under, Hm | behind, 60); add = behind & got; under[add] = filled[add]
        ov = np.zeros((N, N, 4), np.uint8); ov[over, :3] = after[over]; ov[over, 3] = 255
        Image.fromarray(under).save(d / f"under_{arm}_{v}.png"); Image.fromarray(ov).save(d / f"over_{arm}_{v}.png")


def gen_grip(race, build, seed=1):
    """Grip hands for a build: FLUX paints each hand of its painted puppet closed around a magenta guide rod (front, then
    the back with the front as reference). Each hand splits into an under-hand (drawn behind a weapon; the palm behind the
    rod filled in) and an over layer (the fingers in front of the grip); the rod gives the grip point and axis."""
    g = grip_spec(); doc = spec(); P = grip_prompts(build, g)
    pcid, sp, parts = pf.base(build)
    gid, d = store.new(grip_dir(race, build), seed)
    key = _rgb(doc["materials"][g["key"]]["key"])
    meta = {"build": build, "paint": pcid, "made": int(time.time()), "wrist": g["wrist"], "grip": {}, "zoom": GRIP_ZOOM}
    ref = None
    for v in VIEWS:
        comp, ca, region, rods = _grip_setup(sp, v, parts[v], g, key)
        al = np.asarray(ca)[..., 3:4] / 255.0; after = (np.asarray(ca)[..., :3] * al + 255 * (1 - al)).astype(np.uint8)
        for arm, (c, u, w, rm, hm, fist, reg, lower, box) in rods.items():
            meta["grip"].setdefault(arm, {})[v] = {"c": [float(c[0]), float(c[1])], "u": [float(u[0]), float(u[1])], "w": float(w)}
            r_ = ref.get(arm) if ref else None
            _, painted, _, out = pf._crop_pass(ca, reg, box, P[v], seed, D_GRIP, f"grip_{build}_{gid}_{arm}_{v}", [r_] if r_ else [])
            out.save(d / f"paint_{arm}_{v}.png"); after[reg] = painted[reg]
        if v == "front":
            ref = {arm: d / f"paint_{arm}_front.png" for arm in rods}
        Image.fromarray(after).save(d / f"after_{v}.png")
        _grip_extract(after, key, rods, d, v, meta)
    store.write_meta(d, "grip", seed=seed, prompts=P, inputs={"paint": pcid}, params={"wrist": g["wrist"], "zoom": GRIP_ZOOM}, **meta)
    grip_previews(race, build, gid)
    return gid


def regrip(race, build, gid):
    """Re-split a grip candidate's hands from its saved paints (no GPU), after a change to the extraction."""
    d = grip_dir(race, build) / gid; meta = _meta(d); g = grip_spec(); g["wrist"] = meta.get("wrist", g["wrist"])
    key = _rgb(spec()["materials"][g["key"]]["key"]); _, sp, parts = pf.base(build)
    for v in VIEWS:
        comp, ca, region, rods = _grip_setup(sp, v, parts[v], g, key)
        after = np.asarray(Image.open(d / f"after_{v}.png").convert("RGB")).copy()
        _grip_extract(after, key, rods, d, v, meta)
    store.save_meta(d, meta)
    grip_previews(race, build, gid)
    return gid


def grip_previews(race, build, gid):
    """sep_<view>.png: close-ups of both grip hands, the over layer (fingers in front) tinted red, the rest as painted."""
    d = grip_dir(race, build) / gid; _, sp, parts = pf.base(build); meta = _meta(d)
    for v in VIEWS:
        crops = []
        for arm in meta["grip"]:
            u_ = np.asarray(Image.open(d / f"under_{arm}_{v}.png")); o_ = np.asarray(Image.open(d / f"over_{arm}_{v}.png"))
            im = Image.new("RGBA", (N, N), (196, 198, 204, 255))
            for n in sp["views"][v]["order"]:
                im.alpha_composite(Image.fromarray(u_ if n == f"{arm}.hand" else parts[v][n]))
            tint = o_.copy(); tint[..., :3] = (np.asarray(o_[..., :3], float) * 0.5 + np.array([220, 40, 40]) * 0.5).astype(np.uint8)
            im.alpha_composite(Image.fromarray(tint))
            c = meta["grip"][arm][v]["c"]; r = 90
            crops.append(im.crop((int(c[0] - r), int(c[1] - r), int(c[0] + r), int(c[1] + r))).resize((256, 256), Image.LANCZOS))
        sheet = Image.new("RGB", (256 * len(crops), 256))
        for i, cr in enumerate(crops):
            sheet.paste(cr.convert("RGB"), (i * 256, 0))
        pt._save(sheet, d / f"sep_{v}.png")


def grip_assets(race, build, view):
    """{arm: {under, over (RGBA), c, u, w}} of a build's approved grip hand, or {}."""
    gid = load_state(race).get("grip", {}).get(build)
    if not gid:
        return {}
    d = grip_dir(race, build) / gid
    if not (d / "meta.json").exists():
        return {}
    meta = _meta(d); out = {}
    for arm, views in meta["grip"].items():
        if view in views and (d / f"under_{arm}_{view}.png").exists():
            out[arm] = {"under": np.asarray(Image.open(d / f"under_{arm}_{view}.png").convert("RGBA")),
                        "over": np.asarray(Image.open(d / f"over_{arm}_{view}.png").convert("RGBA")), **views[view]}
    return out


def grip_state(race):
    st = load_state(race); out = {}
    for b in _builds(race):
        cands = []
        for d in reversed(store.folders(grip_dir(race, b), finished=True)):
            rel = lambda f: str((d / f).relative_to(ai_kit.AI_DIR)).replace("\\", "/") if (d / f).exists() else None
            cands.append({"id": d.name, "sep": {v: rel(f"sep_{v}.png") for v in VIEWS},
                          "paint": {v: rel(f"paint_{v}.png") for v in VIEWS}, "stamp": int((d / "meta.json").stat().st_mtime)})
        out[b] = {"candidates": cands, "approved": st.get("grip", {}).get(b)}
    return out


def approve_grip(race, build, gid, on=True):
    with _state(race).edit() as st:
        store.pick(st, ("grip", build), gid, on)
    return grip_state(race)


# ----------------------------------------------------------------------------- layers editor (Parts editor, item mode)

LAYER_SEP = "@item"   # an item layer's name in the editor: "<part>@item", drawn right after its body part


def _meta(d):
    if not (d / "meta.json").exists():
        raise ValueError(f"No candidate {d.name}.")
    return store.read_meta(d)


def _blank():
    f = ai_kit.AI_DIR / "items" / "_blank.png"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True); Image.new("RGBA", (N, N)).save(f)
    return f


def layer_doc(race, item_id, cid):
    """What the Parts editor needs to touch up a fitted item: the puppet it was fitted on as read-only context and one
    editable layer per Template part (drawn right after that part; parts the item doesn't cover yet start empty, so it
    can be painted onto them)."""
    from . import workshop as ws
    doc = spec(); it = doc["items"][item_id]
    if it.get("kind") == "held":
        raise ValueError("Held items are edited in their Grip card (the weapon image), not per part.")
    d = out_dir(race, item_id) / cid
    if not (d / "meta.json").exists():
        raise ValueError(f"No candidate {cid} of {item_id}.")
    meta = _meta(d); build = meta["on"]; pcid = pf.base(build)[0]; pd = ppt.out_dir(build) / pcid; sp = ppt._spec_for(pd)
    views = {}; editable = []
    for v in VIEWS:
        order = []; paths = {}; levels = {}
        for n in sp["views"][v]["order"]:
            order += [n, n + LAYER_SEP]
            paths[n] = ws.rel(pd / "parts" / v / f"{n}.png")
            f = d / "parts" / v / f"{n}.png"
            paths[n + LAYER_SEP] = ws.rel(f if f.exists() else _blank())
            if (d / "plevels" / v / f"{n}.png").exists():   # its cel levels, editable as bands
                levels[n + LAYER_SEP] = ws.rel(d / "plevels" / v / f"{n}.png")
            editable.append(n + LAYER_SEP)
        views[v] = {"order": order, "parts": paths, "template": {}, "levels": levels}
    files = sorted(f.name for f in d.glob("*.*"))
    stamp = int(max((f.stat().st_mtime for f in d.rglob("*.png")), default=0))
    prev = [f for f in ("prev_male_front.png", "prev_male_back.png", "prev_female_front.png", "prev_female_back.png") if f in files]
    return {"race": race, "kind": "item", "item": item_id, "id": cid, "dir": ws.rel(d), "on": build, "views": views,
            "editable": sorted(set(editable)), "files": files, "previews": prev, "stamp": stamp, "edited": (d / "parts_orig").exists(),
            "slot": it.get("slot"), "depth": it.get("depth", 0), "materials": it.get("materials", []),
            "bandsEdited": [f"{b.split('/')[0]}/{b.split('/')[1]}{LAYER_SEP}" for b in meta.get("bands_edited", [])]}


def levels_from_edit(doc, it, L, cents):
    """Levels of an edited item layer: key-coloured pixels by their material and the candidate's cel levels; dark pixels =
    outline (254); any other colour painted in goes to the item's first material, levelled by brightness."""
    a = L[..., 3] > 127; lev = np.zeros(L.shape, np.uint8)
    if not a.any():
        return lev
    keyed, ch, lv = classify(doc, it["materials"], L[..., :3], cents)
    _, _, val = pf._hsv(L[..., :3]); line = a & (val < 0.22) & ~keyed
    rest = a & ~keyed & ~line; c0 = doc["materials"][it["materials"][0]]["channel"]
    cent = np.asarray(cents.get(c0, np.linspace(0.3, 0.9, LEVELS)))
    lev[..., 0][keyed & a] = lv[keyed & a]; lev[..., 1][keyed & a] = ch[keyed & a]
    if rest.any():
        lev[..., 0][rest] = np.abs(val[rest][:, None] - cent[None]).argmin(1); lev[..., 1][rest] = c0
    if line.any():
        has = keyed & a
        near, _ = _spread(has, lev[..., 1:2].copy(), line, 12) if has.any() else (np.full(L.shape[:2] + (1,), c0, np.uint8), None)
        lev[..., 1][line] = np.where(near[..., 0][line] > 0, near[..., 0][line], c0)
    lev[..., 0][line] = 254; lev[..., 3][a] = 255
    return lev


def layer_save(race, item_id, cid, view, layers):
    """Edited item layers from the Parts editor ({"<part>@item": PNG data URL}): the first edit keeps the originals
    (parts_orig/, plevels_orig/); levels are recomputed from the key colours; empty layers are removed; previews redone."""
    import base64, io, shutil
    doc = spec(); it = doc["items"][item_id]; d = out_dir(race, item_id) / cid; meta = _meta(d)
    cents = {int(k): np.array(v) for k, v in meta.get("cents", {}).items()}
    _keep_orig(d); edited = set(meta.get("bands_edited", []))
    for name, png in layers.items():
        n = name[:-len(LAYER_SEP)] if name.endswith(LAYER_SEP) else name
        if not all(c.isalnum() or c in "._" for c in n):
            raise ValueError(f"bad layer {name}")
        L = np.asarray(Image.open(io.BytesIO(base64.b64decode(png.split(",", 1)[-1]))).convert("RGBA")).copy()
        if L.shape[:2] != (N, N):
            raise ValueError(f"expected {N} px")
        L[..., 3] = np.where(L[..., 3] > 127, 255, 0)
        fp, fl = d / "parts" / view / f"{n}.png", d / "plevels" / view / f"{n}.png"
        if not (L[..., 3] > 0).any():
            fp.unlink(missing_ok=True); fl.unlink(missing_ok=True); continue
        fp.parent.mkdir(parents=True, exist_ok=True); fl.parent.mkdir(parents=True, exist_ok=True)
        lev = levels_from_edit(doc, it, L, cents)
        if f"{view}/{n}" in edited and fl.exists():   # hand-edited bands stay where the material is unchanged
            lev = keep_bands(np.asarray(Image.open(fl).convert("RGBA")), lev)
        Image.fromarray(L).save(fp); Image.fromarray(lev).save(fl)
    store.update_meta(d, edited=True)
    previews(race, item_id, cid)
    return layer_doc(race, item_id, cid)


def _keep_orig(d):
    """The first edit of a candidate keeps its originals (parts_orig/, plevels_orig/) for Revert."""
    import shutil
    if not (d / "parts_orig").exists():
        for sub in ("parts", "plevels"):
            if (d / sub).exists():
                shutil.copytree(d / sub, d / f"{sub}_orig")
            else:
                (d / f"{sub}_orig").mkdir(parents=True)


def _part_name(name):
    n = name[:-len(LAYER_SEP)] if name.endswith(LAYER_SEP) else name
    if not n or not all(c.isalnum() or c in "._" for c in n):
        raise ValueError(f"bad layer {name}")
    return n


def bands_save(race, item_id, cid, view, bands):
    """Hand-edited bands from the Workshop Editor ({"<part>@item": PNG data URL, R = level, A = painted}): only the
    levels of the layer's band pixels change (material, outline and coverage stay); listed in meta "bands_edited"."""
    import base64, io
    d = out_dir(race, item_id) / cid; meta = _meta(d)
    if view not in VIEWS:
        raise ValueError("Unknown view.")
    _keep_orig(d); done = set()
    for name, png in bands.items():
        n = _part_name(name); fl = d / "plevels" / view / f"{n}.png"
        if not fl.exists():
            continue
        new = np.asarray(Image.open(io.BytesIO(base64.b64decode(png.split(",", 1)[-1]))).convert("RGBA"))
        Image.fromarray(write_bands(np.asarray(Image.open(fl).convert("RGBA")), new, LEVELS)).save(fl); done.add(f"{view}/{n}")
    store.update_meta(d, edited=True, bands_edited=sorted(set(meta.get("bands_edited", [])) | done))
    previews(race, item_id, cid)
    return layer_doc(race, item_id, cid)


def bands_reset(race, item_id, cid, view=None, part=None):
    """Redo an item's bands from its paint (one part of a view, else every part), keeping each pixel's material and the
    outline; drops the hand edits."""
    d = out_dir(race, item_id) / cid; meta = _meta(d)
    cents = {int(k): np.array(v) for k, v in meta.get("cents", {}).items()}
    _keep_orig(d); done = set()
    for v in ([view] if view else VIEWS):
        for fl in sorted((d / "plevels" / v).glob("*.png")):
            if part and fl.stem != _part_name(part):
                continue
            fp = d / "parts" / v / fl.name; lev = np.asarray(Image.open(fl).convert("RGBA")).copy()
            L = np.asarray(Image.open(fp).convert("RGBA")) if fp.exists() else None
            Image.fromarray(levels_from_colour(L, lev, cents) if (L is not None and cents) else smooth_levels(lev)).save(fl)
            done.add(f"{v}/{fl.stem}")
    store.update_meta(d, bands_edited=sorted(set(meta.get("bands_edited", [])) - done) or None)
    previews(race, item_id, cid)
    return layer_doc(race, item_id, cid)


def layer_revert(race, item_id, cid):
    import shutil
    d = out_dir(race, item_id) / cid
    if (d / "parts_orig").exists():
        for sub in ("parts", "plevels"):
            shutil.rmtree(d / sub, ignore_errors=True); shutil.move(str(d / f"{sub}_orig"), str(d / sub))
    store.update_meta(d, edited=None, bands_edited=None)
    previews(race, item_id, cid)
    return layer_doc(race, item_id, cid)


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
        return save_zone(b["item"], b["view"], b["png"], b.get("sex"), b.get("sketch"))
    if action == "zone/reset":
        return reset_zone(b["item"], b["view"], b.get("sex"))
    if action == "approve":
        return {"approved": approve(race, b["item"], b["id"], b.get("key", "all"), b.get("on", True))}
    if action == "archive":
        return archive(race, b["item"], b["id"])
    if action == "grip/approve":
        return {"grips": approve_grip(race, b["build"], b["id"], b.get("on", True))}
    if action == "grip/spec":   # the grip hand prompts and wrist angle (shared by every build)
        doc = spec(); g = dict(doc.get("grip", {}))
        for k in ("prompt", "prompt_back"):
            if k in b and b[k].strip():
                g[k] = b[k]
        if "wrist" in b:
            g["wrist"] = float(b["wrist"])
        doc["grip"] = g; _write_spec(doc)
        return state(race)
    if action == "grip/prompts":
        return grip_prompts(b["build"])
    if action == "held/stage":   # the live grip stage (rest pose) the browser turns the held item on
        return held_stage(race, b["item"], b["build"], b.get("id"), b.get("def"), b.get("ramps"))
    if action == "held/preview":   # a held item in the hand (rest + one walk frame, both views) with unsaved settings
        doc = spec(); doc["items"][b["item"]].update(b.get("def", {}))
        global spec_override
        spec_override = doc
        try:
            r = render(b["build"], [(b["item"], b["id"], b.get("ramps", {}))], frames=8)
        finally:
            spec_override = None
        return {f"{v}_{k}": _url(im) for v in VIEWS for k, im in (("rest", r[v][0]), ("walk", r[v][1][2]), ("walk2", r[v][1][6]))}
    if action == "layer_doc":
        return layer_doc(race, b["item"], b["id"])
    if action == "layer_save":
        return layer_save(race, b["item"], b["id"], b["view"], b["layers"])
    if action == "layer_revert":
        return layer_revert(race, b["item"], b["id"])
    if action == "bands_save":   # Workshop Editor: hand-edited item bands
        return bands_save(race, b["item"], b["id"], b["view"], b["bands"])
    if action == "bands_reset":
        return bands_reset(race, b["item"], b["id"], b.get("view"), b.get("part"))
    if action == "editor_walk":   # Workshop Editor: the walk of a paint with the shown items, hair, face and ramps
        return editor_walk(race, b["build"], b["pcid"], b.get("view", "front"), b.get("items", []), b.get("hair"), b.get("face"), b.get("skin"))
    if action == "ramp_colours":   # every palettes group's ramps as colours (the editor's ramp previews)
        from . import workshop as ws
        return {g: {n: [list(c) for c in pf.group_ramp(g, n)] for n in ramps} for g, ramps in ws.palettes_doc()["groups"].items()}
    if action == "export":   # pack + layers (+ intake for new ids) of game items into the game (R-003, R-004)
        from . import item_export as ie
        return ie.export(b.get("items") or None, race)
    if action == "outfit":
        return outfit(race, b["build"], b.get("items", []), b.get("ramps"), b.get("hair"), b.get("hair_ramp"),
                      b.get("walk", True), b.get("cids"), b.get("part"))
    raise ValueError(f"unknown items action {action}")


@jobs.kind("item", reload=("puppet", "puppet_template", "puppet_paint", "puppet_face", "puppet_items"))
def job(a):
    """The Item Workshop's queue jobs (job kind "item", `action` in the args); reloaded per job."""
    race = a.get("race", "human")
    if a["action"] == "generate":
        n, seed = int(a.get("n", 1)), int(a.get("seed", 1))
        return [generate(race, a["item"], a.get("build"), seed + i) for i in range(n)]
    if a["action"] == "grip":
        return gen_grip(race, a["build"], int(a.get("seed", 1)))
    if a["action"] == "regrip":
        return regrip(race, a["build"], a["id"])
    if a["action"] == "relevel":
        return relevel(race, a["item"], a["id"])
    if a["action"] == "rekey":
        return rekey(race, a["item"], a["id"], bool(a.get("complete")))
    raise ValueError(f"unknown items job {a['action']}")
