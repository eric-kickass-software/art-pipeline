"""Paint step: the clean puppet template painted per race build, figure first (vault: Hearthdelve - Art Pipeline,
paint tests 2-3, 2026-10-24). Nothing is detected or cut: the template says which part owns every pixel.

  1. figure pass      the template's parts assembled in the rest pose (tinted with the race's skin ramp, briefs zone
                      grey, face marks from the template's face guides) are painted as ONE character (masked img2img)
  2. completion pass  the parts laid out apart (exploded sheet), each filled with its painted visible pixels; only the
                      hidden areas (round ends under the parent, torso under the arms) are masked and FLUX fills them
  3. cleanup          near-white leftovers cleared, only each part's main piece kept
  4. outline          FLUX's silhouette line replaced by one even outline, left out where a part crosses the interior
                      of the next part at a joint (no ball-joint rings), always kept on the figure's outer silhouette
The back view uses the painted front figure as its reference image.

    paint(build, n, seed)     -> candidate ids, each in sprites/ai/paint/<build>/<id>/ (parts/<view>/<part>.png, figures,
                                 rest_<view>.png, walk_<view>.gif, meta.json)
    approve / archive / state / save_parts (Workshop Parts editor) / preview
"""
import json, math, shutil, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import ai_kit, comfy, jobs, workshop as ws, puppet as pp, puppet_template as pt

S, SHEET, GAP, MARGIN = 2, 1024, 14, 24
D_FIGURE, D_COMPLETE = 0.8, 0.9
OUTLINE_W, OUTLINE_BAND = 4, 7
LINE = (40, 32, 30)
BRIEFS = ((92, 94, 100), (176, 178, 184))
SKIN_DEFAULT = ((150, 92, 66), (232, 176, 138))
VIEWS = ("front", "back")
SPEC = ai_kit.ROOT / "specs" / "paint.json"   # the paint prompts (edited in the Workshop's Race tab)


def out_dir(build):
    return ai_kit.AI_DIR / "paint" / build


# ----------------------------------------------------------------------------- prompts and colours

def spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def save_spec(doc):
    """Validate + write specs/paint.json (Workshop: Paint block fields, Shared prompts card, AI fix row)."""
    old = spec()
    for k in ("figure", "complete"):
        for v in VIEWS:
            t = (doc.get(k) or {}).get(v)
            if not isinstance(t, str) or "{body}" not in t:
                raise ValueError(f"{k}.{v} must be text containing {{body}}")
    if "{body}" not in str(doc.get("fix", "")):
        raise ValueError("fix must contain {body}")
    for sex, e in (doc.get("sexes") or {}).items():
        if not isinstance(e, dict) or not all(isinstance(e.get(x, ""), str) for x in ("extra", "clothes")):
            raise ValueError(f"sexes.{sex} needs text fields extra and clothes")
    if doc.get("face_guide", "keyed") not in ("faint", "marks", "keyed"):
        raise ValueError("face_guide is keyed, faint or marks")
    out = {"_doc": old.get("_doc", ""), "face_guide": doc.get("face_guide", old.get("face_guide", "keyed")),
           "style_groups": doc.get("style_groups") or old["style_groups"],
           "figure": doc["figure"], "back_reference": str(doc.get("back_reference", "")), "complete": doc["complete"],
           "fix": doc["fix"], "sexes": doc.get("sexes") or old["sexes"]}
    SPEC.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    return out


def prompts(build, doc=None):
    """(body text, clothes) for a race build: race traits + description + the gender body (specs/races.json) +
    the paint extra and clothes of its sex (specs/paint.json)."""
    race, sex = build.split(".", 1)
    rd = ws.races_doc(); r = rd["races"][race]; e = (doc or spec())["sexes"].get(sex, {})
    body = ", ".join(x.strip() for x in (r.get("traits", ""), r.get("description", ""), rd["sexes"].get(sex, ""), e.get("extra", ""))
                     if x and x.strip())
    return body, e.get("clothes", "").strip() or "bald head, barefoot"


def style_text(doc=None):
    st = ws.style(); groups = (doc or spec()).get("style_groups", ["style", "proportions", "rendering"])
    return ", ".join(st["groups"][g]["text"] for g in groups if g in st["groups"] and st["groups"][g]["text"].strip())


def composed(build, doc=None):
    """The full prompts the Paint step will send for a build, per view: figure pass and completion pass."""
    doc = doc or spec(); body, clothes = prompts(build, doc); style = style_text(doc)
    fill = lambda t: t.replace("{body}", body).replace("{clothes}", clothes)
    out = {}
    for v in VIEWS:
        fig = f"{style}. {fill(doc['figure'][v])}"
        if v == "back" and doc.get("back_reference"):
            fig += " " + doc["back_reference"]
        out[v] = {"figure": fig, "complete": f"{style}. {fill(doc['complete'][v])}"}
    out["fix"] = f"{style}. {fill(doc['fix'])}"
    return out


def skin_ramp(race):
    """(dark, light) init tint from the race's first skin ramp (specs/palettes.json), else a neutral skin."""
    try:
        grp = ws.palettes_doc()["groups"].get("skin", {})
        for e in grp.values():
            if race in (e.get("races") or []) and e.get("colors"):
                c = [tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for h in e["colors"]]
                return c[1], c[-2]
    except Exception:
        pass
    return SKIN_DEFAULT


# ----------------------------------------------------------------------------- init images

def tinted(rgba, ramp, mask_brief=None):
    """Clay part -> init colours: its shading onto the skin ramp (the briefs zone onto grey), the outline kept dark."""
    rgb = rgba[..., :3].astype(float); lum = rgb.mean(-1)
    t = np.clip((lum - 0.62 * 204) / (0.42 * 204), 0, 1)[..., None]
    col = np.array(ramp[0], float) + (np.array(ramp[1], float) - np.array(ramp[0], float)) * t
    if mask_brief is not None:
        g = np.array(BRIEFS[0], float) + (np.array(BRIEFS[1], float) - np.array(BRIEFS[0], float)) * t
        col[mask_brief] = g[mask_brief]
    col[lum < 100] = LINE
    return np.dstack([col.astype(np.uint8), rgba[..., 3]])


def briefs_zone(V, cov):
    """Torso pixels below the waistband guide (its lower edge is the leg holes)."""
    w = pt._waist(V, S, cov)
    if not w:
        return np.zeros(cov.shape, bool)
    xs = np.array([p[0] for p in w]) * S; ys = np.array([p[1] for p in w]) * S
    yy, xx = np.mgrid[:cov.shape[0], :cov.shape[1]]
    return (cov > 0.5) & (yy > np.interp(xx, xs, ys, left=ys[0], right=ys[-1]))


def face_marks(rgba, fc, mode="marks"):
    """Where the template's face guides say: "faint" = thin lines a little darker than the skin (eye ovals, mouth), so
    the painted head stays faceless and every face comes from the Face tab; "marks" = solid eye ovals (with a highlight)
    and a mouth line (the painted head gets its own face)."""
    if mode == "keyed":   # the head gets its own face, with the irises in the magenta colour key (recolourable,
        im = Image.fromarray(rgba); d = ImageDraw.Draw(im)   # harvested into the Face tab's face set)
        h_ = json.loads((ai_kit.ROOT / "specs" / "face.json").read_text(encoding="utf-8")).get("eye_key", "#d020d0").lstrip("#")
        key = tuple(int(h_[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
        e0, e1 = np.array(fc["eyes"][0]) * S, np.array(fc["eyes"][1]) * S; r = 0.16 * np.linalg.norm(e1 - e0)
        for e in (e0, e1):
            d.ellipse([e[0] - r, e[1] - 1.3 * r, e[0] + r, e[1] + 1.3 * r], fill=(40, 30, 28, 255))
            d.ellipse([e[0] - 0.82 * r, e[1] - 1.08 * r, e[0] + 0.82 * r, e[1] + 1.08 * r], fill=(246, 244, 240, 255))
            d.ellipse([e[0] - 0.55 * r, e[1] - 0.75 * r, e[0] + 0.55 * r, e[1] + 0.85 * r], fill=key)
            d.ellipse([e[0] - 0.25 * r, e[1] - 0.6 * r, e[0] + 0.02 * r, e[1] - 0.25 * r], fill=(250, 250, 250, 255))
        m = np.array(fc["mouth"]) * S
        d.line([(m[0] - 0.45 * r, m[1]), (m[0] + 0.45 * r, m[1])], fill=(110, 60, 55, 255), width=max(2, int(r * 0.18)))
        out = np.asarray(im).copy(); out[..., 3] = rgba[..., 3]
        return out
    if mode == "faint":
        im = Image.fromarray(rgba); d = ImageDraw.Draw(im)
        e0, e1 = np.array(fc["eyes"][0]) * S, np.array(fc["eyes"][1]) * S; r = 0.16 * np.linalg.norm(e1 - e0)
        a = rgba[..., 3] > 127; col = tuple(int(c * 0.86) for c in np.median(rgba[a][:, :3], 0)) + (255,)
        for e in (e0, e1):
            d.ellipse([e[0] - r, e[1] - 1.3 * r, e[0] + r, e[1] + 1.3 * r], outline=col, width=2)
        m = np.array(fc["mouth"]) * S
        d.line([(m[0] - 0.45 * r, m[1]), (m[0] + 0.45 * r, m[1])], fill=col, width=2)
        out = np.asarray(im).copy(); out[..., 3] = rgba[..., 3]
        return out
    im = Image.fromarray(rgba); d = ImageDraw.Draw(im)
    e0, e1 = np.array(fc["eyes"][0]) * S, np.array(fc["eyes"][1]) * S; r = 0.1 * np.linalg.norm(e1 - e0)
    for e in (e0, e1):
        d.ellipse([e[0] - r, e[1] - 1.35 * r, e[0] + r, e[1] + 1.35 * r], fill=(40, 30, 28, 255))
        d.ellipse([e[0] - 0.35 * r, e[1] - 0.9 * r, e[0] + 0.1 * r, e[1] - 0.35 * r], fill=(245, 245, 245, 255))
    m = np.array(fc["mouth"]) * S
    d.line([(m[0] - 0.8 * r, m[1]), (m[0] + 0.8 * r, m[1])], fill=(110, 60, 55, 255), width=max(2, int(r * 0.3)))
    out = np.asarray(im).copy(); out[..., 3] = rgba[..., 3]
    return out


def inits_for(spec, view, parts, ramp, guide="keyed"):
    V = spec["views"][view]
    out = {n: tinted(parts[n][0], ramp, briefs_zone(V, parts[n][0][..., 3] / 255) if n == "torso" else None)
           for n in V["order"]}
    fc = V["shapes"]["head"].get("face")
    if fc:
        out["head"] = face_marks(out["head"], fc, guide)
    return out


# ----------------------------------------------------------------------------- layout (exploded sheet)

def layout(parts, order, V):
    """Push every part out from the body centre until no two (grown) parts touch, then fit the sheet."""
    PXS = pp.PX * S
    crops = {}
    for n in order:
        a = parts[n][0][..., 3] > 8; ys, xs = np.nonzero(a)
        crops[n] = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    C = (np.array(V["joints"]["neck"]) + np.array(V["joints"]["pelvis"])) / 2 * S
    cent = {n: np.array([(c[0] + c[2]) / 2, (c[1] + c[3]) / 2]) for n, c in crops.items()}
    for f in np.arange(1.1, 3.0, 0.05):
        occ = np.zeros((4 * PXS, 4 * PXS), bool); ok = True; place = {}
        for n in order:
            x0, y0, x1, y1 = crops[n]; off = (cent[n] - C) * (f - 1)
            px, py = int(x0 + off[0]) + PXS, int(y0 + off[1]) + PXS
            m = Image.fromarray(np.where(parts[n][0][y0:y1, x0:x1, 3] > 8, 255, 0).astype(np.uint8))
            m = np.asarray(m.filter(ImageFilter.MaxFilter(2 * (GAP // 2) + 1))) > 0
            reg = occ[py:py + m.shape[0], px:px + m.shape[1]]
            if (reg & m).any():
                ok = False; break
            reg |= m; place[n] = (px, py)
        if ok:
            break
    X0 = min(place[n][0] for n in order); Y0 = min(place[n][1] for n in order)
    X1 = max(place[n][0] + crops[n][2] - crops[n][0] for n in order); Y1 = max(place[n][1] + crops[n][3] - crops[n][1] for n in order)
    k = min((SHEET - 2 * MARGIN) / (X1 - X0), (SHEET - 2 * MARGIN) / (Y1 - Y0))
    ox = (SHEET - (X1 - X0) * k) / 2; oy = (SHEET - (Y1 - Y0) * k) / 2
    return {n: (crops[n], (ox + (place[n][0] - X0) * k, oy + (place[n][1] - Y0) * k), k) for n in order}


def _placed(lay, n):
    (x0, y0, x1, y1), (px, py), k = lay[n]
    return (x0, y0, x1, y1), round(px), round(py), max(1, round((x1 - x0) * k)), max(1, round((y1 - y0) * k))


def build_sheet(order, lay, imgs):
    sheet = Image.new("RGB", (SHEET, SHEET), (255, 255, 255)); mask = np.zeros((SHEET, SHEET), bool)
    for n in order:
        (x0, y0, x1, y1), px, py, w, h = _placed(lay, n)
        im = Image.fromarray(imgs[n][y0:y1, x0:x1]).resize((w, h), Image.LANCZOS)
        sheet.paste(im.convert("RGB"), (px, py), im)
        mask[py:py + h, px:px + w] |= np.asarray(im)[..., 3] > 100
    return sheet, mask


def unsheet_one(painted, base, lay, n):
    (x0, y0, x1, y1), px, py, w, h = _placed(lay, n)
    crop = painted.crop((px, py, px + w, py + h)).resize((x1 - x0, y1 - y0), Image.LANCZOS)
    new = base.copy(); new[y0:y1, x0:x1, :3] = np.asarray(crop)
    return new


# ----------------------------------------------------------------------------- ComfyUI passes

def _run(init, mask, prompt, seed, denoise, tag, refs=()):
    """One masked img2img pass (`tag` names it in logs only); `refs` are image paths."""
    out = comfy.img2img(init, prompt, seed=seed, denoise=denoise, refs=[Image.open(r).convert("RGB") for r in refs],
                        mask=np.asarray(mask, dtype=bool), prefix="hdart/paint")
    return out.resize(init.size, Image.LANCZOS)


def owners(parts, order):
    """Which part shows at each pixel of the rest pose (the last one drawn wins): each part's visible mask."""
    N = next(iter(parts.values()))[0].shape[0]; own = np.full((N, N), -1)
    for i, n in enumerate(order):
        own[parts[n][0][..., 3] > 127] = i
    return {n: own == i for i, n in enumerate(order)}


def figure_pass(order, inits, prompt, seed, tag, refs=()):
    N = next(iter(inits.values())).shape[0]
    comp = Image.new("RGBA", (N, N))
    for n in order:
        comp.alpha_composite(Image.fromarray(inits[n]))
    a = np.asarray(comp)[..., 3] > 127; ys, xs = np.nonzero(a)
    pad = 30; x0, y0, x1, y1 = xs.min() - pad, ys.min() - pad, xs.max() + pad, ys.max() + pad
    side = max(x1 - x0, y1 - y0); cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    box = (cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side)
    bg = Image.new("RGBA", (N, N), (255, 255, 255, 255)); bg.alpha_composite(comp)
    init = bg.convert("RGB").crop(box).resize((SHEET, SHEET), Image.LANCZOS)
    m = Image.fromarray(np.where(a, 255, 0).astype(np.uint8)).crop(box).resize((SHEET, SHEET), Image.NEAREST)
    painted = _run(init, np.asarray(m.filter(ImageFilter.MaxFilter(5))) > 0, prompt, seed, D_FIGURE, tag, refs)
    back = Image.new("RGB", (N, N), (255, 255, 255)); back.paste(painted.resize((side, side), Image.LANCZOS), box[:2])
    return painted, np.asarray(back)


def complete_pass(order, lay, inits, fig_px, vis, prompt, seed, tag, refs=()):
    filled = {}
    for n in order:
        rgba = inits[n].copy(); v_ = vis[n] & (rgba[..., 3] > 127)
        rgba[v_, :3] = fig_px[v_]; filled[n] = rgba
    sheet, _ = build_sheet(order, lay, filled)
    hm = np.zeros((SHEET, SHEET), bool)
    for n in order:
        (x0, y0, x1, y1), px, py, w, h = _placed(lay, n)
        hid = np.where((inits[n][..., 3] > 127) & ~vis[n], 255, 0).astype(np.uint8)
        hm[py:py + h, px:px + w] |= np.asarray(Image.fromarray(hid[y0:y1, x0:x1]).resize((w, h), Image.NEAREST)) > 0
    hm = np.asarray(Image.fromarray(np.where(hm, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 0
    painted = _run(sheet, hm, prompt, seed, D_COMPLETE, tag, refs)
    out = {}
    for n in order:   # visible pixels stay exactly the figure's; the hidden ones come from the completion
        done = unsheet_one(painted, filled[n], lay, n)
        keep = vis[n] & (inits[n][..., 3] > 127); done[keep, :3] = fig_px[keep]
        out[n] = done
    return painted, out


# ----------------------------------------------------------------------------- cleanup + outline

def _erode(a, n):
    a = a.copy()
    for _ in range(n):
        a &= np.roll(a, 1, 0) & np.roll(a, -1, 0) & np.roll(a, 1, 1) & np.roll(a, -1, 1)
    return a


def _grow(a, n):
    return np.asarray(Image.fromarray(np.where(a, 255, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * n + 1))) > 0


def clear_white(rgba):
    """Leftover background inside a part (FLUX drew it a little smaller than its template shape): near-white pixels
    connected to the part's edge, plus a 1 px rim, become transparent."""
    c = rgba[..., :3].astype(int); white = (c.min(-1) > 222) & (c.max(-1) - c.min(-1) < 26)
    reach = rgba[..., 3] <= 127
    for _ in range(80):
        g = reach | np.roll(reach, 1, 0) | np.roll(reach, -1, 0) | np.roll(reach, 1, 1) | np.roll(reach, -1, 1)
        new = reach | (g & white)
        if (new == reach).all():
            break
        reach = new
    out = rgba.copy(); gone = reach & (rgba[..., 3] > 127)
    out[gone | (_grow(gone, 1) & (rgba[..., 3] > 127)), 3] = 0
    return out


def main_piece(rgba):
    """Only the part's main connected piece (drops detached strokes such as motion arcs)."""
    a = rgba[..., 3] > 127
    if not a.any():
        return rgba
    ys, xs = np.nonzero(a); y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    sub = a[y0:y1, x0:x1]
    blur = np.asarray(Image.fromarray(np.where(sub, 255, 0).astype(np.uint8)).filter(ImageFilter.GaussianBlur(4)), float) * sub
    reach = np.zeros_like(sub); reach[np.unravel_index(np.argmax(blur), sub.shape)] = True
    for _ in range(3000):
        new = (reach | np.roll(reach, 1, 0) | np.roll(reach, -1, 0) | np.roll(reach, 1, 1) | np.roll(reach, -1, 1)) & sub
        if (new == reach).all():
            break
        reach = new
    out = rgba.copy(); drop = np.zeros_like(a); drop[y0:y1, x0:x1] = sub & ~reach; out[drop, 3] = 0
    return out


def outline(rgba, skip=None, w=OUTLINE_W, band=OUTLINE_BAND):
    """Replace the painted silhouette line by one even outline (anti-aliased); none where `skip` is set."""
    a = rgba[..., 3] > 127
    if not a.any():
        return rgba
    ys, xs = np.nonzero(a); y0, y1, x0, x1 = max(0, ys.min() - 4), ys.max() + 5, max(0, xs.min() - 4), xs.max() + 5
    sub = rgba[y0:y1, x0:x1].copy(); A = sub[..., 3] > 127
    core = _erode(A, band)
    if core.sum() < 20:
        core = _erode(A, max(1, band // 2))
    col = sub[..., :3].astype(float); known = core.copy()
    for _ in range(band + 3):   # colours flow outward from the interior over the old line band
        todo = A & ~known
        if not todo.any():
            break
        acc = np.zeros_like(col); cnt = np.zeros(A.shape)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            k = np.roll(known, (dy, dx), (0, 1)); c = np.roll(col, (dy, dx), (0, 1))
            acc[k] += c[k]; cnt[k] += 1
        take = todo & (cnt > 0)
        col[take] = acc[take] / cnt[take][:, None]; known |= take
    inner = np.asarray(Image.fromarray(np.where(A, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(2 * int(round(w)) + 1)), float) / 255
    soft = np.asarray(Image.fromarray(((A.astype(float) - inner) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6)), float) / 255
    k = np.clip(soft, 0, 1)[..., None] * A[..., None]
    if skip is not None:
        sk = np.asarray(Image.fromarray(np.where(skip[y0:y1, x0:x1], 255, 0).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), float) / 255
        k = k * (1 - sk[..., None])
    col = col * (1 - k) + np.array(LINE, float) * k
    sub[..., :3] = np.clip(col, 0, 255).astype(np.uint8)
    out = rgba.copy(); out[y0:y1, x0:x1] = sub
    return out


def joint_zones(V, parts):
    """Where each part gets no outline: its edge crossing the INTERIOR (shrunk 6 px) of the next part at a joint (the
    parent's end over its child, the shoulder/hip cap over the torso, the chin over the neck), never within 6 px of the
    whole figure's outer silhouette."""
    tm = {n: parts[n][..., 3] > 127 for n in V["order"]}
    N = tm["torso"].shape[0]; yy, xx = np.mgrid[:N, :N] / S
    figure = np.any(list(tm.values()), 0); sil = figure & ~_erode(figure, 6)
    near = lambda j, r: ((xx - j[0]) ** 2 + (yy - j[1]) ** 2) <= r * r
    out = {}
    for n in V["order"]:
        if n == "torso":
            z = np.zeros((N, N), bool)
        elif n == "head":
            z = near(pt._joint(V, "neck"), 30) & _erode(tm["torso"], 6)
        else:
            k, seg = n.split("."); L = V["limbs"][n]
            hw = lambda t0: max([abs(p[1]) for p in L["points"] if abs(p[0] - t0) < 0.3] or [10.0])
            z = np.zeros((N, N), bool)
            if seg != "hand":
                child = f"{k}.lower" if seg == "upper" else f"{k}.hand"
                z |= near(pt._joint(V, L["to"]), 1.4 * hw(1.0)) & _erode(tm[child], 6)
            if seg == "upper":
                z |= near(pt._joint(V, L["from"]), 1.4 * hw(0.0)) & _erode(tm["torso"], 6)
        out[n] = z & ~sil
    return out


def finish(V, raw):
    """Cleanup + even outline for every painted part of one view."""
    clean = {n: main_piece(clear_white(raw[n])) for n in V["order"]}
    zones = joint_zones(V, clean)
    return {n: outline(clean[n], zones[n]) for n in V["order"]}


# ----------------------------------------------------------------------------- candidates

def _parts_tuple(spec, view, imgs):
    """{name: (rgba 1024, guides (unused), pivot 512)} as the template renderer returns it, for the rig previews."""
    V = spec["views"][view]
    piv = {n: pt._joint(V, L["from"]) for n, L in V["limbs"].items()}
    piv["torso"], piv["head"] = pt._joint(V, "pelvis"), pt._joint(V, "neck")
    return {n: (imgs[n], None, piv[n]) for n in V["order"]}


def render_previews(d, spec, frames=8):
    """rest_<view>.png and walk_<view>.gif of a candidate folder, from its saved parts."""
    for v in VIEWS:
        order = spec["views"][v]["order"]
        imgs = {n: np.asarray(Image.open(d / "parts" / v / f"{n}.png").convert("RGBA")) for n in order}
        parts = _parts_tuple(spec, v, imgs)
        B2, pieces = pt.rig_from(pt.rig_from_spec(spec, v), parts); B2["rigid"] = (pieces, order)
        walk = [pp.on_bg(pp.render(B2, v, i / frames)) for i in range(frames)]
        pt._save(walk[0], d / f"walk_{v}.gif", save_all=True, append_images=walk[1:], duration=120, loop=0)
        strip = Image.new("RGB", (pp.PX * frames, pp.PX))   # the Parts editor's pose scrubber
        for i, w in enumerate(walk):
            strip.paste(w, (i * pp.PX, 0))
        pt._save(strip, d / f"walkstrip_{v}.png")
        rest = pp.on_bg(np.asarray(pt._compose(parts, order).resize((pp.PX, pp.PX), Image.LANCZOS)))
        pt._save(rest, d / f"rest_{v}.png")


def _progress(d, step, of, stage, seed, error=None):
    """progress.json of a candidate being made (the Workshop shows it); removed when the candidate is done."""
    (d / "progress.json").write_text(json.dumps({"step": step, "of": of, "stage": stage, "seed": seed, "error": error,
                                                 "at": int(time.time())}), encoding="utf-8")


def paint(build, n=1, seed=1):
    """Queue job: n paint candidates for a race build (each = front + back)."""
    race, sex = build.split(".", 1)
    spec = pt.load()
    if not spec["views"]["front"]["shapes"]["head"].get("face"):
        raise ValueError("The template has no face guides yet: add them (Template tab / puppet_template.add_face).")
    ramp = skin_ramp(race); P = composed(build); guide = globals()["spec"]().get("face_guide", "keyed")
    rendered = {v: pt.render(spec, v, S) for v in VIEWS}
    lays = {v: layout(rendered[v], spec["views"][v]["order"], spec["views"][v]) for v in VIEWS}
    made = []
    for i in range(n):
        sd = seed + i; cid = f"s{sd}_{int(time.time())}"
        d = out_dir(build) / cid; (d / "parts").mkdir(parents=True, exist_ok=True)
        try:
            _paint_one(build, d, cid, sd, spec, rendered, lays, ramp, P, guide)
        except Exception as e:
            _progress(d, 0, 5, "failed", sd, str(e)); raise
        (d / "progress.json").unlink(missing_ok=True)
        if guide in ("keyed", "marks"):   # the painted face becomes a face candidate of the race (Face tab)
            try:
                from . import puppet_face
                puppet_face.face_from_paint(build.split(".", 1)[0], build, cid)
            except Exception as e:
                print(f"face harvest of {build} {cid} failed: {e}")
        made.append(cid)
    return made


def _paint_one(build, d, cid, sd, spec, rendered, lays, ramp, P, guide):
    """One paint candidate (figure + completion pass per view, cleanup, outline, previews), reporting its progress."""
    if True:
        ref = None; meta = {"build": build, "seed": sd, "made": int(time.time()), "template_edited": spec.get("edited"),
                            "template_made": spec.get("made"), "prompts": {}}
        step = 0
        for v in VIEWS:
            V = spec["views"][v]; order = V["order"]; parts = rendered[v]
            inits = inits_for(spec, v, parts, ramp, guide)
            fprompt = P[v]["figure"]
            step += 1; _progress(d, step, 5, f"figure pass ({v})", sd)
            fpaint, fig_px = figure_pass(order, inits, fprompt, sd, f"{build}_{cid}_{v}_fig", [ref] if ref else [])
            fpaint.save(d / f"figure_{v}.png")
            if v == "front":
                ref = d / "figure_front.png"
            cprompt = P[v]["complete"]
            step += 1; _progress(d, step, 5, f"hidden ends + cleanup + outline ({v})", sd)
            csheet, raw = complete_pass(order, lays[v], inits, fig_px, owners(parts, order), cprompt, sd,
                                        f"{build}_{cid}_{v}_cmp", [ref])
            csheet.save(d / f"sheet_{v}.png")
            done = finish(V, raw)
            (d / "parts" / v).mkdir(exist_ok=True)
            for name, img in done.items():
                Image.fromarray(img).save(d / "parts" / v / f"{name}.png")
            meta["prompts"][v] = {"figure": fprompt, "complete": cprompt}
        (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        (d / "template.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")   # joints/pivots of these parts
        for v in VIEWS:   # the template shapes (the editor's clamp)
            (d / "template" / v).mkdir(parents=True, exist_ok=True)
            for name, (rgba, _, _) in rendered[v].items():
                Image.fromarray(rgba).save(d / "template" / v / f"{name}.png")
        _progress(d, 5, 5, "walk previews", sd)
        render_previews(d, spec)


def _spec_for(d):
    """The template a candidate was painted on (pivots/joints match its parts); the current one if not stored."""
    f = d / "template.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else pt.load()


def candidates(build):
    d = out_dir(build)
    if not d.exists():
        return []
    out = []
    for c in sorted((p for p in d.iterdir() if p.is_dir() and not p.name.startswith("_")), key=lambda p: p.stat().st_ctime):   # oldest first
        meta = json.loads((c / "meta.json").read_text(encoding="utf-8")) if (c / "meta.json").exists() else {}
        files = sorted(f.name for f in c.glob("*.*"))
        prog = json.loads((c / "progress.json").read_text(encoding="utf-8")) if (c / "progress.json").exists() else None
        out.append({"id": c.name, "dir": ws.rel(c), "meta": meta, "files": files, "edited": (c / "parts_orig").exists(),
                    "progress": prog, "stamp": int(max((f.stat().st_mtime for f in c.glob("*.*")), default=0))})
    return out


def state(build):
    s = ws.load_state(); p = s["builds"].get(build, {}).get("paint", {})
    spec = pt.load()
    cands = candidates(build)
    for c in cands:   # painted on an older template than the current one (not known while it's still being painted)
        c["stale"] = not c["progress"] and (c["meta"].get("template_edited"), c["meta"].get("template_made")) != (spec.get("edited"), spec.get("made"))
    return {"build": build, "candidates": cands, "approved": p.get("approved")}


def ramp_preview(build):
    """Ramps tab: the build's approved paint (else its newest) assembled in the rest pose, front and back, as transparent
    512 px PNGs, plus skin level maps (R = level, A = skin) measured on both views together, so front and back share
    one set of cel levels."""
    import base64, io
    st = state(build)
    cands = sorted((c for c in st["candidates"] if not c["progress"]), key=lambda c: c["meta"].get("made", 0))
    cid = st["approved"] or (cands[-1]["id"] if cands else None)
    out = {"build": build, "id": cid, "approved": bool(st["approved"]), "facings": {}}
    if not cid:
        return out
    d = out_dir(build) / cid; spec = _spec_for(d); size = ws.palettes_doc().get("rampSize", 5)
    comps = {}
    for v in VIEWS:
        cv = Image.new("RGBA", (pp.PX * S, pp.PX * S))
        for n in spec["views"][v]["order"]:
            cv.alpha_composite(Image.open(d / "parts" / v / f"{n}.png").convert("RGBA"))
        comps[v] = cv.resize((pp.PX, pp.PX), Image.LANCZOS)
    both = Image.new("RGB", (pp.PX * 2, pp.PX), (255, 255, 255))
    for i, v in enumerate(VIEWS):
        both.paste(comps[v], (i * pp.PX, 0), comps[v])
    lev, cent = ws.skin_levels(both, size)
    url = lambda im: "data:image/png;base64," + base64.b64encode((lambda b: (im.save(b, "PNG"), b.getvalue())[1])(io.BytesIO())).decode()
    for i, v in enumerate(VIEWS):
        lv = lev[:, i * pp.PX:(i + 1) * pp.PX]
        enc = np.zeros(lv.shape + (4,), np.uint8); enc[..., 0] = np.clip(lv, 0, 255); enc[..., 3] = np.where(lv >= 0, 255, 0)
        out["facings"][v] = {"art": url(comps[v]), "levels": url(Image.fromarray(enc)), "key": cent}
    return out


def approve(build, cid, on=True):
    if on and not (out_dir(build) / cid).exists():
        raise ValueError(f"No paint candidate {cid} for {build}.")
    s = ws.load_state(); b = s["builds"].setdefault(build, {})
    p = b.setdefault("paint", {})
    if on:
        p["approved"] = cid
    elif p.get("approved") == cid:
        p.pop("approved")
    ws.save_state(s)
    return state(build)


def archive(build, cid):
    """Move a candidate to archive/workshop/paint/<build>/<id> (nothing is deleted)."""
    if state(build)["approved"] == cid:
        raise ValueError("That's the approved paint. Approve another one first.")
    src = out_dir(build) / cid
    if not src.exists():
        raise ValueError(f"No paint candidate {cid}.")
    dst = ws.ARCHIVE / "workshop" / "paint" / build / cid; dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return state(build)


# ----------------------------------------------------------------------------- Parts editor

def parts_doc(build, cid):
    """What the Parts editor needs: per view the draw order, part image paths, template shapes (for the clamp)."""
    d = out_dir(build) / cid
    if not d.exists():
        raise ValueError(f"No paint candidate {cid} for {build}.")
    spec = _spec_for(d); tdir = d / "template" if (d / "template").exists() else pt.out_dir()
    views = {}
    for v in VIEWS:
        order = spec["views"][v]["order"]
        views[v] = {"order": order, "parts": {n: ws.rel(d / "parts" / v / f"{n}.png") for n in order},
                    "template": {n: ws.rel(tdir / v / f"{n}.png") for n in order if (tdir / v / f"{n}.png").exists()}}
    stamp = int(max((f.stat().st_mtime for f in (d / "parts").rglob("*.png")), default=0))
    return {"build": build, "id": cid, "dir": ws.rel(d), "views": views, "stamp": stamp,
            "edited": (d / "parts_orig").exists(), "files": sorted(f.name for f in d.glob("*.*"))}


def save_parts(build, cid, view, pngs):
    """Write edited part images (name -> PNG bytes, 1024 px RGBA); the first edit keeps the originals in parts_orig/."""
    import base64, io
    d = out_dir(build) / cid
    if view not in VIEWS or not d.exists():
        raise ValueError("Unknown candidate or view.")
    order = set(_spec_for(d)["views"][view]["order"])
    if not (d / "parts_orig").exists():
        shutil.copytree(d / "parts", d / "parts_orig")
    for name, data in pngs.items():
        if name not in order:
            raise ValueError(f"Unknown part {name}.")
        raw = base64.b64decode(data.split(",", 1)[-1]) if isinstance(data, str) else data
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        if im.size != (pp.PX * S, pp.PX * S):
            raise ValueError(f"{name}: expected {pp.PX * S} px, got {im.size}.")
        im.save(d / "parts" / view / f"{name}.png")
    return parts_doc(build, cid)


def revert_parts(build, cid):
    """Back to the painted originals (undo every editor save)."""
    d = out_dir(build) / cid
    if (d / "parts_orig").exists():
        shutil.rmtree(d / "parts"); shutil.move(str(d / "parts_orig"), str(d / "parts"))
    render_previews(d, _spec_for(d))
    return parts_doc(build, cid)


def preview(build, cid):
    d = out_dir(build) / cid
    render_previews(d, _spec_for(d))
    return parts_doc(build, cid)


def fix_region(build, cid, view, part, mask, prompt="", seed=3, denoise=0.75):
    """AI fix (Parts editor): FLUX repaints the marked area of one part in context. The part is drawn on top of the
    assembled figure (so its hidden ends show), the mask (PNG data URL or bytes, part canvas = rest pose, white = fix)
    is clamped to the part's template shape, and only those pixels change (feathered)."""
    import base64, io
    d = out_dir(build) / cid; spec = _spec_for(d); V = spec["views"][view]
    if part not in V["order"]:
        raise ValueError(f"Unknown part {part}.")
    imgs = {n: np.asarray(Image.open(d / "parts" / view / f"{n}.png").convert("RGBA")) for n in V["order"]}
    raw = base64.b64decode(mask.split(",", 1)[-1]) if isinstance(mask, str) else mask
    mi = np.asarray(Image.open(io.BytesIO(raw)).convert("RGBA").resize(imgs[part].shape[1::-1]))
    # the editor sends a coloured mark on transparency (alpha = marked); an opaque mask is white = marked
    m = mi[..., 3] > 127 if mi[..., 3].min() < 255 else mi[..., :3].mean(-1) > 127
    shape = imgs[part][..., 3] > 127
    tpl = d / "template" / view / f"{part}.png"
    if tpl.exists():   # the part may grow back into its template shape (gaps), never past it
        shape |= np.asarray(Image.open(tpl))[..., 3] > 127
    m &= shape
    if not m.any():
        raise ValueError("The marked area doesn't touch the part.")
    N = imgs[part].shape[0]
    comp = Image.new("RGBA", (N, N), (255, 255, 255, 255))
    for n in V["order"]:
        comp.alpha_composite(Image.fromarray(imgs[n]))
    comp.alpha_composite(Image.fromarray(imgs[part]))
    ys, xs = np.nonzero(m); cx, cy = (xs.min() + xs.max()) // 2, (ys.min() + ys.max()) // 2
    side = int(min(N, max(256, 2.5 * max(xs.max() - xs.min(), ys.max() - ys.min()))))
    x0 = int(np.clip(cx - side // 2, 0, N - side)); y0 = int(np.clip(cy - side // 2, 0, N - side))
    box = (x0, y0, x0 + side, y0 + side)
    init = comp.convert("RGB").crop(box).resize((SHEET, SHEET), Image.LANCZOS)
    mk = Image.fromarray(np.where(m, 255, 0).astype(np.uint8)).crop(box).resize((SHEET, SHEET), Image.NEAREST)
    text = f"{composed(build)['fix']} {prompt}".strip()
    out = _run(init, np.asarray(mk.filter(ImageFilter.MaxFilter(3))) > 0, text, seed, denoise, f"{build}_{cid}_{view}_{part}_fix")
    patch = np.asarray(out.resize((side, side), Image.LANCZOS)).astype(float)
    if not (d / "parts_orig").exists():
        shutil.copytree(d / "parts", d / "parts_orig")
    new = imgs[part].copy()
    feather = np.asarray(Image.fromarray(np.where(m, 255, 0).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)), float) / 255
    f_ = (feather * m)[y0:y0 + side, x0:x0 + side][..., None]   # soft edge inside the marked area only
    region = new[y0:y0 + side, x0:x0 + side, :3].astype(float)
    new[y0:y0 + side, x0:x0 + side, :3] = np.clip(region * (1 - f_) + patch * f_, 0, 255).astype(np.uint8)
    new[..., 3] = np.where(m, 255, new[..., 3])          # filled gaps become part of the part
    Image.fromarray(new).save(d / "parts" / view / f"{part}.png")
    render_previews(d, spec)
    return parts_doc(build, cid)["stamp"]


# ----------------------------------------------------------------------------- Studio job kinds (hdart.jobs)

@jobs.kind("paint", reload=("puppet", "puppet_template", "puppet_paint"))   # puppet Paint step
def _job_paint(a):
    return paint(a["build"], int(a.get("n", 1)), int(a.get("seed", 1)))


@jobs.kind("paint_preview", gpu=False, reload=("puppet", "puppet_template", "puppet_paint"))   # walk re-render
def _job_preview(a):
    return preview(a["build"], a["id"])["stamp"]


@jobs.kind("paint_fix", reload=("puppet", "puppet_template", "puppet_paint"))   # Parts editor: repaint a region
def _job_fix(a):
    return fix_region(a["build"], a["id"], a["view"], a["part"], a["mask"], a.get("prompt", ""), int(a.get("seed", 3)))
