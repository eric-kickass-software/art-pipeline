"""Unity export of the painted puppets (2026-10-24): `hdart puppet-export [race]`.

Writes a race's character art into the game at <HD_PROJECT>/Assets/StreamingAssets/Art/Puppets/<race>/, where the
Unity puppet (Presentation/Characters/PuppetArt.cs) builds a rig from it and the character creator offers its options:

  puppet.json             the rig and the options: per sex and view the parts (file, rect, pivot joint, draw order) and
                          the joints in canvas px; the hair and face sets; the race's skin / hair / eye ramps
  <sex>/<view>/<part>     each body part of the sex's approved paint (else its newest), cropped
  hair/<id>/<view>        each hairstyle of the race's hair set (else every finished hair candidate)
  face/<id>/front         each face of the race's face set (else every finished face candidate); front only

Every image comes with an index map (`*.idx.png`): R = cel level (0 = darkest; 254 = a hair outline), G = colour
channel (0 = keep the painted colour, 1 = skin, 2 = hair, 3 = eyes), A = mask. The game colours channel pixels from
the chosen ramp, so one export serves every skin tone, hair colour and eye colour. Canvas = the paint's 1024 px rest
pose scaled to `size`, y down, origin top left; rects are [x, y, w, h] in canvas px.
"""
import json, shutil, time

import numpy as np
from PIL import Image

from . import config, store, workshop as ws, puppet_face as pf, puppet_paint as ppt
from .palette import ramp_from_base



def out_root():
    return config.project().export("puppets")
SEXES = ("male", "female")
VIEWS = ("front", "back")
SKIN, HAIR, EYES = 1, 2, 3
# Which joint a part turns around, and the joint at its far end (both in template.json's joints).
JOINT = {"upper": ("pivot", "mid"), "lower": ("mid", "end"), "hand": ("end", "tip")}


def _hex(c):
    return "#%02x%02x%02x" % tuple(int(v) for v in c[:3])


def ramps(race):
    """The race's ramps per group (skin, hair, eyes): those tagged with the race, else the whole group."""
    doc = ws.palettes_doc(); size = doc.get("rampSize", 5); out = {}
    for group in ("skin", "hair", "eyes"):
        grp = doc["groups"].get(group, {})
        tagged = {k: e for k, e in grp.items() if race in (e.get("races") or [])}
        out[group] = [{"id": f"{group}.{k}", "name": k,
                       "colors": [c if isinstance(c, str) else _hex(c) for c in (e.get("colors") or ramp_from_base(e["base"], size))]}
                      for k, e in (tagged or grp).items() if e.get("colors") or e.get("base")]
    return out


def _cands(race, kind):
    """The race's hair or face candidates to export: its set, else every finished candidate (oldest first)."""
    d = pf.out_dir(race, kind)
    if not d.exists():
        return []
    in_set = [d / c for c in pf._sets(ws.load_state(), race)[kind] if (d / c).is_dir()]
    if in_set:
        return in_set
    return sorted((p for p in d.iterdir() if p.is_dir() and not (p / "progress.json").exists() and (p / "layer_front.png").exists()),
                  key=lambda p: p.stat().st_mtime)


def _small(a, size, nearest=False):
    return np.array(Image.fromarray(a).resize((size, size), Image.NEAREST if nearest else Image.LANCZOS))


def _crop_save(art, idx, path, min_alpha=8):
    """Save art + index map cropped to the art's coverage; returns the rect [x, y, w, h], or None when empty."""
    ys, xs = np.nonzero(art[..., 3] >= min_alpha)
    if not len(ys):
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(art[y0:y1, x0:x1])).save(path.parent / (path.name + ".png"))   # names have dots (armA.hand)
    Image.fromarray(np.ascontiguousarray(idx[y0:y1, x0:x1])).save(path.parent / (path.name + ".idx.png"))
    return [x0, y0, x1 - x0, y1 - y0]


def skin_index(art, cent):
    """Index map of a part: its skin pixels (workshop.skin_mask on white) labelled with the nearest skin level."""
    rgb = np.full(art.shape[:2] + (3,), 255, np.uint8); a = art[..., 3] > 127; rgb[a] = art[a, :3]
    m = ws.skin_mask(rgb) & a
    idx = np.zeros(art.shape, np.uint8)
    if len(cent) and m.any():
        c = np.array(cent, float)
        lab = ((rgb[m].astype(float)[:, None] - c[None]) ** 2).sum(-1).argmin(1)
        idx[m, 0] = lab; idx[m, 1] = SKIN; idx[m, 3] = 255
    return idx


def keyed_index(levels, channel, size):
    """Index map from a Face tab levels map (R = level, 254 = outline, A = mask), at the export size."""
    lv = _small(levels, size, nearest=True)
    idx = np.zeros(lv.shape, np.uint8); m = lv[..., 3] > 127
    idx[m, 0] = lv[m, 0]; idx[m, 1] = channel; idx[m, 3] = 255
    return idx


def _body(race, sex, size, has_faces, d):
    """Export one sex's painted body; returns its puppet.json entry (None when the build has no paint)."""
    try:
        cid, sp, parts = pf.base(f"{race}.{sex}")
    except ValueError:
        return None
    k = size / (pf.N / ppt.S)   # rig px (512) -> canvas px
    for v in VIEWS:             # the face comes from the face set, so the painted head goes blank
        if has_faces and v == "front":
            parts[v]["head"] = pf.blank_face(parts[v]["head"], pf.face_ellipse(sp, 1.2) & pf.inner_head(parts[v]["head"]))
    # Skin levels measured on both views together (like the Ramps tab), so front and back match.
    both = Image.new("RGB", (pf.N * 2, pf.N), (255, 255, 255))
    for i, v in enumerate(VIEWS):
        rest = pf.compose(sp, v, parts[v])
        both.paste(rest, (i * pf.N, 0), rest)
    _, cent = ws.skin_levels(both.resize((size * 2, size), Image.LANCZOS), ws.palettes_doc().get("rampSize", 5))
    entry = {"paint": cid, "skinKey": cent, "views": {}}
    for v in VIEWS:
        V = sp["views"][v]; joints = {}
        for limb, j in V["joints"].items():
            if isinstance(j, dict):
                joints[limb] = {n: [round(p[0] * k, 2), round(p[1] * k, 2)] for n, p in j.items() if isinstance(p, list)}
                if "R" in j:
                    joints[limb]["R"] = round(j["R"] * k, 2)
            else:
                joints[limb] = [round(j[0] * k, 2), round(j[1] * k, 2)]
        out_parts = []
        for n in V["order"]:
            art = _small(parts[v][n], size)
            rect = _crop_save(art, skin_index(art, cent), d / sex / v / n)
            if rect is None:
                continue
            if n == "torso":
                pivot, end = joints["pelvis"], joints["neck"]
            elif n == "head":
                pivot, end = joints["neck"], None
            else:
                limb, seg = n.split(".")
                a, b = JOINT[seg]
                pivot, end = joints[limb][a], joints[limb][b]
            out_parts.append({"name": n, "file": f"{sex}/{v}/{n}.png", "rect": rect, "pivot": pivot, "end": end})
        entry["views"][v] = {"parts": out_parts, "joints": joints}
    return entry


def _layers(race, kind, size, d):
    out = []
    for c in _cands(race, kind):
        meta = store.read_meta(c)
        e = {"id": f"{kind}.{c.name}", "seed": meta.get("seed"), "views": {}}
        for v in (VIEWS if kind == "hair" else ("front",)):
            lf, lv = c / f"layer_{v}.png", c / f"levels_{v}.png"
            if not lf.exists():
                continue
            art = _small(np.asarray(Image.open(lf).convert("RGBA")), size)
            idx = keyed_index(np.asarray(Image.open(lv).convert("RGBA")), HAIR if kind == "hair" else EYES, size) \
                if lv.exists() else np.zeros(art.shape, np.uint8)
            if kind == "hair":   # hair pixels the key missed at the LANCZOS edge still read as hair (no green fringe)
                art[..., 3] = np.where(idx[..., 3] > 0, 255, np.where(art[..., 3] > 127, art[..., 3], 0))
            rect = _crop_save(art, idx, d / kind / c.name / v)
            if rect:
                e["views"][v] = {"file": f"{kind}/{c.name}/{v}.png", "rect": rect}
        if e["views"]:
            out.append(e)
    return out


def export(race="human", size=512):
    """Export a race's puppet art into the game; returns the folder."""
    d = out_root() / race
    if d.exists():
        shutil.rmtree(d)   # only this race's export folder: stale parts / layers go
    d.mkdir(parents=True)
    faces = _layers(race, "face", size, d)
    doc = {"_doc": __doc__.strip().split("\n\n")[1], "schemaVersion": 1, "race": race, "made": int(time.time()),
           "canvas": size, "rampSize": ws.palettes_doc().get("rampSize", 5),
           "hairOutline": "#28201e", "sexes": {}, "hair": _layers(race, "hair", size, d), "faces": faces,
           "ramps": ramps(race)}
    for sex in SEXES:
        e = _body(race, sex, size, bool(faces), d)
        if e:
            doc["sexes"][sex] = e
    if not doc["sexes"]:
        raise ValueError(f"{race} has no painted puppet yet (Workshop → Race → Paint).")
    (d / "puppet.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return d
