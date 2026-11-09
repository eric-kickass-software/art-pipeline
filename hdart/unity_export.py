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
the chosen ramp, so one export serves every skin tone, hair colour and eye colour. Canvas = the paint's 1152 px rest
pose scaled to `size`, y down, origin top left; rects are [x, y, w, h] in canvas px.
"""
import json, shutil, time

import numpy as np
from PIL import Image

from . import art_pack, config, palette, rig, store, workshop as ws, puppet_face as pf, puppet_paint as ppt, puppet_template as pt
from .levels import blank_face
from .palette import ramp_from_base



def out_root():
    return config.project().export("puppets")
SEXES = ("male", "female")
VIEWS = ("front", "back")
SKIN, HAIR, EYES = 1, 2, 3
CANVAS = rig.PX         # the default export canvas: the rig canvas (576), the same px density as before the headroom
# Which joint a part turns around, and the joint at its far end (both in template.json's joints).
JOINT = {"upper": ("pivot", "mid"), "lower": ("mid", "end"), "hand": ("end", "tip")}


def ramps(race):
    """The race's ramps per group (skin, hair, eyes): those tagged with the race, else the whole group."""
    doc = palette.palettes_doc(); size = doc.get("rampSize", 5); out = {}
    for group in ("skin", "hair", "eyes"):
        grp = doc["groups"].get(group, {})
        tagged = {k: e for k, e in grp.items() if race in (e.get("races") or [])}
        out[group] = [{"id": f"{group}.{k}", "name": k,
                       "colors": [c if isinstance(c, str) else art_pack.hex_colour(c) for c in (e.get("colors") or ramp_from_base(e["base"], size))]}
                      for k, e in (tagged or grp).items() if e.get("colors") or e.get("base")]
    return out


def _cands(race, kind):
    """The race's hair or face candidates to export: its set, else every finished candidate (oldest first)."""
    d = pf.out_dir(race, kind)
    if not d.exists():
        return []
    in_set = [d / c for c in pf.face_sets(ws.load_state(), race)[kind] if (d / c).is_dir()]
    if in_set:
        return in_set
    return sorted((p for p in d.iterdir() if p.is_dir() and not (p / "progress.json").exists() and (p / "layer_front.png").exists()),
                  key=lambda p: p.stat().st_mtime)


def skin_index(art, band, size):
    """Index map of a part from its skin bands (Parts editor, puppet_paint.bake_bands), at the export size."""
    b = art_pack.small(band, size, nearest=True)
    idx = np.zeros(art.shape, np.uint8); m = (b[..., 3] > 127) & (b[..., 0] != ppt.NOT_SKIN) & (art[..., 3] > 127)
    idx[m, 0] = b[m, 0]; idx[m, 1] = SKIN; idx[m, 3] = 255
    return idx


def keyed_index(levels, channel, size):
    """Index map from a Face tab levels map (R = level, 254 = outline, A = mask), at the export size."""
    lv = art_pack.small(levels, size, nearest=True)
    idx = np.zeros(lv.shape, np.uint8); m = lv[..., 3] > 127
    idx[m, 0] = lv[m, 0]; idx[m, 1] = channel; idx[m, 3] = 255
    return idx


def live_joints(sp):
    """{view: joints} from the current template when the paint was painted on the same base body (same `from` and
    parts), else {}. A joint is only a turning point: the parts keep their pixels and rects, so moving a pivot in the
    Template editor after painting (e.g. the hips for the walk, 2026-11-01) reaches the game without repainting."""
    cur = pt.load()
    if cur.get("from") != sp.get("from"):
        return {}
    out = {}
    for v in VIEWS:
        C, V = cur["views"].get(v, {}), sp["views"].get(v, {})
        if set(C.get("joints", {})) == set(V.get("joints", {})) and set(C.get("order", [])) == set(V.get("order", [])):
            out[v] = C["joints"]
    return out


def _body(race, sex, size, has_faces, d):
    """Export one sex's painted body; returns its puppet.json entry (None when the build has no paint)."""
    try:
        cid, sp, parts = pf.base(f"{race}.{sex}")
    except ValueError:
        return None
    k = size / (pf.N / ppt.S)   # rig px (576) -> canvas px
    for v in VIEWS:             # the face comes from the face set, so the painted head goes blank
        if has_faces and v == "front":
            parts[v]["head"] = blank_face(parts[v]["head"], rig.face_ellipse(sp, 1.2) & rig.inner_head(parts[v]["head"]))
    # Skin levels = the parts' bands (made from the paint, maybe edited by hand), the key measured on both views.
    cent = ppt.ensure_bands(f"{race}.{sex}", cid); bands = ppt.out_dir(f"{race}.{sex}") / cid / "bands"
    entry = {"paint": cid, "skinKey": cent, "views": {}}
    cur = live_joints(sp)
    for v in VIEWS:
        V = sp["views"][v]; joints = {}
        for limb, j in (cur.get(v) or V["joints"]).items():
            if isinstance(j, dict):
                joints[limb] = {n: [round(p[0] * k, 2), round(p[1] * k, 2)] for n, p in j.items() if isinstance(p, list)}
                if "R" in j:
                    joints[limb]["R"] = round(j["R"] * k, 2)
            else:
                joints[limb] = [round(j[0] * k, 2), round(j[1] * k, 2)]
        out_parts = []
        for n in V["order"]:
            art = art_pack.small(parts[v][n], size)
            band = np.asarray(Image.open(bands / v / f"{n}.png").convert("RGBA"))
            rect = art_pack.crop_save(art, skin_index(art, band, size), d / sex / v / n)
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
            art = art_pack.small(np.asarray(Image.open(lf).convert("RGBA")), size)
            idx = keyed_index(np.asarray(Image.open(lv).convert("RGBA")), HAIR if kind == "hair" else EYES, size) \
                if lv.exists() else np.zeros(art.shape, np.uint8)
            if kind == "hair":   # hair pixels the key missed at the LANCZOS edge still read as hair (no green fringe)
                art[..., 3] = np.where(idx[..., 3] > 0, 255, np.where(art[..., 3] > 127, art[..., 3], 0))
            rect = art_pack.crop_save(art, idx, d / kind / c.name / v)
            if rect:
                e["views"][v] = {"file": f"{kind}/{c.name}/{v}.png", "rect": rect}
        if e["views"]:
            out.append(e)
    return out


def export(race="human", size=None):
    """Export a race's puppet art into the game; returns the folder."""
    size = size or CANVAS
    d = out_root() / race
    if d.exists():
        shutil.rmtree(d)   # only this race's export folder: stale parts / layers go
    d.mkdir(parents=True)
    faces = _layers(race, "face", size, d)
    doc = {"_doc": __doc__.strip().split("\n\n")[1], "schemaVersion": 1, "race": race, "made": int(time.time()),
           "canvas": size, "rampSize": palette.palettes_doc().get("rampSize", 5),
           "hairOutline": "#28201e", "sexes": {}, "hair": _layers(race, "hair", size, d), "faces": faces,
           "ramps": ramps(race)}
    for sex in SEXES:
        e = _body(race, sex, size, bool(faces), d)
        if e:
            doc["sexes"][sex] = e
    if not doc["sexes"]:
        raise ValueError(f"{race} has no painted puppet yet (Workshop → Race → Paint).")
    (d / "puppet.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    art_pack.write_shared(); art_pack.write_index()
    return d
