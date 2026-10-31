"""`hdart canvas-migrate`: move stored puppet art into the 576 px rig canvas (2026-10-31).

The rig canvas grew from 512 to 576 px (paint canvas 1024 -> 1152) so hair and helmets have room above the head; the
figure keeps its scale and sits at puppet.PAD (32, 64) rig px. This pads every canvas-space image and shifts every
stored canvas coordinate by that much, losslessly (nothing is resampled). Idempotent: specs with px = 576 and images
that aren't 1024 px are left alone, so a second run changes nothing.

  specs           specs/puppet_template.json and each paint candidate's template.json: px, joints, shapes, face guides,
                  the waist seam (limb outlines are in their bone's frame and don't move)
  canvas images   paint parts / parts_orig / bands / template; hair and face layers + levels (+ _orig, ctx_head); item
                  layers, levels, guide_back, parts, plevels, items/_blank.png; grip after / over / under; item zones
  meta            grip hands' grip points (grip.<arm>.<view>.c)
  left alone      crop and sheet space (figure_, sheet_, complete_, paint_, edit_back, held*, sep_, tmp/)
  remade          previews (rest/walk strips, prev_*, head_*, grip sheets, the template sheets)

A real run makes an `hdart backup` first (skip_backup only for tests).
"""
import json, re
from collections import Counter
from pathlib import Path

from PIL import Image

from . import ai_kit, puppet as pp

OLD_PX, S = 512, 2
OLD_N, NEW_N = OLD_PX * S, pp.PX * S
DX, DY = pp.PAD                      # rig px
PDX, PDY = DX * S, DY * S            # paint px

PAD_RULES = [   # (group, regex on the path relative to sprites/ai) of canvas-space images
    ("paint parts", r"^paint/[^/]+/[^/]+/(parts|parts_orig|bands|template)/(front|back)/[^/]+\.png$"),
    ("face layers", r"^face/[^/]+/(hair|face)/[^/]+/(layer|levels|layer_orig|levels_orig|ctx_head)_(front|back)\.png$"),
    ("item layers", r"^items/[^/]+/item\.[^/]+/[^/]+/(layer_(front|back)|levels_(front|back)|guide_back)\.png$"),
    ("item parts", r"^items/[^/]+/item\.[^/]+/[^/]+/(parts|plevels)/(front|back)/[^/]+\.png$"),
    ("item blank", r"^items/_blank\.png$"),
    ("grip hands", r"^items/[^/]+/_grip/[^/]+/[^/]+/(after|over_arm[AB]|under_arm[AB])_(front|back)\.png$"),
]
KEEP = r"(^|/)tmp/|(^|/)(figure|sheet|complete|paint|edit|held|sep|prev|head|rest|walkstrip)_?[^/]*\.png$|^puppet_template/|^candidates/|^layers/"


def shift_spec(spec):
    """A template spec in the new canvas (a copy); unchanged if it's there already."""
    if spec.get("px", OLD_PX) == pp.PX:
        return spec
    sp = json.loads(json.dumps(spec))
    mv = lambda p: [round(p[0] + DX, 1), round(p[1] + DY, 1)]
    for V in sp["views"].values():
        for k, j in V["joints"].items():
            V["joints"][k] = {n: (mv(p) if isinstance(p, list) else p) for n, p in j.items()} if isinstance(j, dict) else mv(j)
        for sh in V["shapes"].values():
            sh["points"] = [mv(p) for p in sh["points"]]
            fc = sh.get("face")
            if fc:
                fc["eyes"] = [mv(p) for p in fc["eyes"]]; fc["mouth"] = mv(fc["mouth"]); fc["center"] = [mv(p) for p in fc["center"]]
        for seam in (V.get("seams") or {}).values():
            if "y" in seam:
                seam["y"] = round(seam["y"] + DY, 1)
    sp["px"] = pp.PX
    return sp


def pad_image(f):
    """Pad a 1024 px canvas image to 1152 at (PDX, PDY), filled with its own corner; False if it isn't 1024 px."""
    im = Image.open(f); im.load()
    if im.size != (OLD_N, OLD_N):
        return False
    out = Image.new(im.mode, (NEW_N, NEW_N), im.getpixel((0, 0)))
    if im.mode == "P":
        out.putpalette(im.getpalette())
    out.paste(im, (PDX, PDY)); out.save(f)
    return True


def shift_grip_meta(f):
    m = json.loads(f.read_text(encoding="utf-8"))
    if m.get("canvas") == NEW_N or not m.get("grip"):
        return False
    for arm in m["grip"].values():
        for g in arm.values():
            if isinstance(g, dict) and "c" in g:
                g["c"] = [g["c"][0] + PDX, g["c"][1] + PDY]
    m["canvas"] = NEW_N
    f.write_text(json.dumps(m, indent=1), encoding="utf-8")
    return True


def plan(ai_dir=None, specs=None):
    """What a run would touch: {group: [paths]}, plus "unknown" = 1024 px images no rule covers."""
    ai_dir = Path(ai_dir or ai_kit.AI_DIR); specs = Path(specs or ai_kit.ROOT / "specs")
    out = {g: [] for g, _ in PAD_RULES}; out.update({"specs": [], "item zones": [], "grip meta": [], "unknown": []})
    rules = [(g, re.compile(r)) for g, r in PAD_RULES]; keep = re.compile(KEEP)
    if (specs / "puppet_template.json").exists():
        out["specs"].append(specs / "puppet_template.json")
    out["item zones"] = sorted((specs / "item_zones").glob("*.png"))
    if ai_dir.exists():
        for f in sorted(ai_dir.rglob("*")):
            rel = f.relative_to(ai_dir).as_posix()
            if f.name == "template.json" and rel.startswith("paint/"):
                out["specs"].append(f)
            elif f.name == "meta.json" and "/_grip/" in rel:
                out["grip meta"].append(f)
            elif f.suffix == ".png":
                g = next((g for g, r in rules if r.search(rel)), None)
                if g:
                    out[g].append(f)
                elif not keep.search(rel):
                    with Image.open(f) as im:
                        if im.size == (OLD_N, OLD_N):
                            out["unknown"].append(f)
    return out


def migrate(ai_dir=None, specs=None, dry=False, skip_backup=False, previews=True, log=print):
    """Run (or with dry, only count) the migration; returns {group: changed count}."""
    todo = plan(ai_dir, specs)
    if todo["unknown"]:
        raise ValueError("1024 px images no rule covers (add a rule first):\n  " + "\n  ".join(map(str, todo["unknown"][:20])))
    if dry:
        return {g: len(v) for g, v in todo.items()}
    if not skip_backup:
        from .backup import backup
        r = backup(); log(f"backup: {r['files']} files -> {r['dest']}")
    done = Counter()
    for f in todo["specs"]:
        sp = json.loads(f.read_text(encoding="utf-8")); new = shift_spec(sp)
        if new is not sp:
            f.write_text(json.dumps(new, indent=1), encoding="utf-8"); done["specs"] += 1
    for f in todo["grip meta"]:
        done["grip meta"] += shift_grip_meta(f)
    for g in [g for g, _ in PAD_RULES] + ["item zones"]:
        for f in todo[g]:
            done[g] += pad_image(f)
    done = {g: n for g, n in done.items() if n}
    log("migrated: " + ", ".join(f"{g} {n}" for g, n in done.items()) if done else "nothing to migrate")
    if previews and done:
        remake_previews(log)
    return done


def remake_previews(log=print):
    """Re-render every preview that shows the canvas (each step on its own: one failure doesn't stop the rest)."""
    from . import puppet_template as pt, puppet_paint as ppt, puppet_face as pf, puppet_items as pit
    steps = [("template sheets", lambda: pt.preview(pt.load()))]
    for b in sorted(p.name for p in (ai_kit.AI_DIR / "paint").iterdir() if p.is_dir()) if (ai_kit.AI_DIR / "paint").exists() else []:
        for d in sorted(p for p in ppt.out_dir(b).iterdir() if (p / "parts").is_dir()):
            steps.append((f"paint {b}/{d.name}", lambda d=d: ppt.render_previews(d, ppt._spec_for(d))))
    races = sorted(p.name for p in (ai_kit.AI_DIR / "face").iterdir() if p.is_dir()) if (ai_kit.AI_DIR / "face").exists() else []
    steps += [(f"face {r}", lambda r=r: pf.refresh_previews(r)) for r in races]
    items = ai_kit.AI_DIR / "items"
    for rd in sorted(p for p in items.iterdir() if p.is_dir()) if items.exists() else []:
        for idir in sorted(p for p in rd.iterdir() if p.is_dir() and p.name.startswith("item.")):
            for c in sorted(p for p in idir.iterdir() if (p / "meta.json").exists()):
                steps.append((f"item {idir.name}/{c.name}", lambda r=rd.name, i=idir.name, c=c.name: pit.previews(r, i, c)))
        for bd in sorted(p for p in (rd / "_grip").iterdir() if p.is_dir()) if (rd / "_grip").exists() else []:
            for g in sorted(p for p in bd.iterdir() if (p / "meta.json").exists()):
                steps.append((f"grip {bd.name}/{g.name}", lambda r=rd.name, b=bd.name, g=g.name: pit.grip_previews(r, b, g)))
    bad = []
    for name, fn in steps:
        try:
            fn()
        except Exception as e:   # noqa: BLE001 (report and go on)
            bad.append(f"{name}: {e}")
    log(f"previews: {len(steps) - len(bad)}/{len(steps)} remade" + ("".join(f"\n  failed {b}" for b in bad)))
    return bad
