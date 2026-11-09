"""Dungeon Forge sets: the CRUD layer. A set is a spec (specs/dungeon_sets/<set>.json: pieces, families, the material
sheet) plus its data folder (sprites/dungeon/<set>/: state.json, candidates/). Reading and saving specs and state,
the style groups, family and wall-family settings, approving, archiving (nothing is deleted: it goes to archive/) and
the candidate files.

Every read-modify-write of a set spec or a set's state.json holds LOCK (hdart.core.locks.DUNGEON).
"""
import json
import re
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image

from hdart.core import config, store
from hdart.tools.dungeon import iso, patterns
from hdart.core.locks import CELLS as _CELL_CACHE, DUNGEON as LOCK
from hdart.tools.dungeon.geometry import pixel_steps


ORDER = ("medium", "style", "view", "rendering", "framing", "biome")


def dun_dir() -> Path:
    """<data>/sprites/dungeon: one folder per set (state.json, candidates/), plus _meshes/ and tmp/."""
    return config.data_dir() / "sprites" / "dungeon"


def style_path() -> Path:
    """specs/dungeon_style.json: the shared dungeon style groups."""
    return config.specs_dir() / "dungeon_style.json"


def style():
    return json.loads(style_path().read_text(encoding="utf-8"))


def style_text(st=None, groups=ORDER):
    st = st or style()
    return ", ".join(st["groups"][g]["text"] for g in groups if st["groups"][g]["text"].strip())


EVEN_TONE = 0.8  # family default: a master tile repeats every tile, so a stone that stands out repeats too


# ----------------------------------------------------------------------------- sets: candidates, approval, export

def sets_dir() -> Path:
    """specs/dungeon_sets: one spec per set."""
    return config.specs_dir() / "dungeon_sets"


def export_root():
    return config.project().export("dungeon")


def load_set(name):
    return json.loads((sets_dir() / f"{name}.json").read_text(encoding="utf-8"))


def set_dir(name):
    return dun_dir() / name


def state(name):
    return store.read_json(set_dir(name) / "state.json", {"pieces": {}})


def save_state(name, s):
    """Atomic (hdart.core.store); callers hold LOCK from their read to this write."""
    store.write_json(set_dir(name) / "state.json", s)


def piece_of(spec, pid):
    p = next((p for p in spec["pieces"] if p["id"] == pid), None)
    if p is None:
        raise KeyError(f"piece {pid} is not in set {spec.get('id')}")
    return p


GEOMETRY_KEYS = ("shape", "length", "height", "thickness", "opening", "mesh")


def resolved(spec, pid):
    """A piece with its geometry inherited from `of` (wall decor is painted on that wall's exact shape)."""
    piece = dict(piece_of(spec, pid))
    if piece["kind"] == "wall_decor" and piece.get("of") and any(p["id"] == piece["of"] for p in spec["pieces"]):
        base = piece_of(spec, piece["of"])
        for k in GEOMETRY_KEYS:
            if k in base:
                piece[k] = base[k]
    return piece


def set_style(spec):
    """Shared dungeon descriptors with the set's own biome text swapped in."""
    st = style()
    if spec.get("biome"):
        st["groups"]["biome"]["text"] = spec["biome"]
    words = ((spec.get("sheet") or {}).get("words") or "").strip()
    if words:  # the material sheet's words: every floor and wall of the set is made of the same stuff
        st["groups"]["biome"]["text"] = ", ".join(t for t in (st["groups"]["biome"]["text"].strip(), words) if t)
    return st


def load_candidate(name, rel):
    f = set_dir(name) / rel
    mp = f.with_suffix(".npy")
    return Image.open(f).convert("RGB"), (np.load(mp) if mp.exists() else None)


def approve(name, pid, rel, on=True):
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        if on and rel not in ps["approved"]:
            ps["approved"].append(rel)
        if not on and rel in ps["approved"]:
            ps["approved"].remove(rel)
        save_state(name, s)
        return ps


# ----------------------------------------------------------------------------- tooling (Studio > Dungeon)

def list_sets():
    return sorted(p.stem for p in sets_dir().glob("*.json"))


def save_style(groups=None, prop_view=None, prop_framing=None, prop_medium=None):
    st = style()
    if prop_medium is not None:
        st["prop_medium"] = prop_medium
    for g, text in (groups or {}).items():
        if g in st["groups"]:
            st["groups"][g]["text"] = text
    if prop_view is not None:
        st["prop_view"] = prop_view
    if prop_framing is not None:
        st["prop_framing"] = prop_framing
    style_path().write_text(json.dumps(st, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return st


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def _backup_spec(path):
    """Keep the last 40 versions of each set spec in archive/history/<set>/, so a lost piece can be recovered."""
    if not path.exists():
        return
    hist = config.archive_dir() / "history" / path.stem
    hist.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, hist / f"{_stamp()}_{time.time_ns() % 1000000:06d}.json")
    for old in sorted(hist.glob("*.json"))[:-40]:
        old.unlink()


def save_set(name, spec):
    """Replace a set spec (ids must be unique). Keeps `_doc`; the previous version goes to archive/history/."""
    ids = [p["id"] for p in spec["pieces"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate piece ids")
    with LOCK:
        path = sets_dir() / f"{name}.json"
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        out = {"_doc": old.get("_doc", spec.get("_doc", "")), **{k: v for k, v in spec.items() if k != "_doc"}, "id": name}
        _backup_spec(path)
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return out


def new_set(name, title=None, biome="", copy_from="crypt"):
    """A new biome set. Pieces are copied from `copy_from` with the biome word swapped in their ids; each keeps
    a `board` id so Prototype V (which asks for the crypt content ids) can show the new art."""
    if not name.replace("_", "").isalnum():
        raise ValueError("set id: letters, digits and _ only")
    with LOCK:
        if (sets_dir() / f"{name}.json").exists():
            raise ValueError(f"set {name} exists")
        src = load_set(copy_from)
        pieces = []
        for p in src["pieces"]:
            q = dict(p)
            q["board"] = p.get("board", p["id"])
            q["id"] = p["id"].replace(f"{copy_from}_", f"{name}_")
            if q.get("of"):
                q["of"] = q["of"].replace(f"{copy_from}_", f"{name}_")
            pieces.append(q)
        return save_set(name, {"_doc": src.get("_doc", ""), "id": name, "name": title or name.title(),
                               "tile": src.get("tile", 64),
                               "biome": biome or set_style(src)["groups"]["biome"]["text"], "pieces": pieces})


def update_set(name, fields):
    """Set-level fields: name, biome, tile, pixel (px per tile; 0/empty = full resolution), harmonize (0..1)."""
    with LOCK:
        spec = load_set(name)
        for k in ("name", "biome", "tile"):
            if k in fields:
                spec[k] = int(fields[k]) if k == "tile" else fields[k]
        if "pixel" in fields:
            px = int(fields["pixel"] or 0)
            if px and px not in pixel_steps(spec):
                raise ValueError(f"pixel: one of {pixel_steps(spec)} px per tile, or 0 for full resolution")
            if px:
                spec["pixel"] = px
            else:
                spec.pop("pixel", None)
        if "harmonize" in fields:
            k = round(min(max(float(fields["harmonize"] or 0), 0.0), 1.0), 2)
            if k:
                spec["harmonize"] = k
            else:
                spec.pop("harmonize", None)
        return save_set(name, spec)


def upsert_piece(name, piece, old_id=None):
    with LOCK:
        spec = load_set(name)
        old_id = old_id or piece["id"]
        clean = {k: v for k, v in piece.items() if v not in (None, "")}
        tpl = (clean.get("family") or {}).get("template") or {}
        if tpl.get("link") and clean.get("method") in ("master", "wall_master"):  # linked: the sheet's values win
            kind = "wall" if clean["method"] == "wall_master" else "floor"
            clean["family"] = {**clean["family"], "template": sheet_template(tpl, kind, sheet(spec))}
        i = next((k for k, p in enumerate(spec["pieces"]) if p["id"] == old_id), None)
        if i is None:
            spec["pieces"].append(clean)
        else:
            spec["pieces"][i] = clean
        out = save_set(name, spec)  # first: it refuses duplicate ids, and then the state must not move either
        if old_id != piece["id"]:  # renamed: carry its candidates/approvals along
            s = state(name)
            if old_id in s["pieces"]:
                s["pieces"][piece["id"]] = s["pieces"].pop(old_id)
                save_state(name, s)
        return out


# ----------------------------------------------------------------------------- archive (nothing is deleted here)
# archive/dungeon/<set>/<rel>       archived candidates (png + npy mask), same relative path as under the set folder
# archive/sets/<set>_<stamp>/       archived sets: spec.json + the set's sprites folder
# archive/history/<set>/            previous versions of each set spec
# Delete archive/ (or parts of it) by hand once you're sure.

def archive_candidate(name, rel):
    src = candidate_path(name, rel)
    dst = config.archive_dir() / "dungeon" / name / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    for f in (src, src.with_suffix(".npy")):
        if f.exists():
            shutil.move(str(f), str(dst.with_suffix(f.suffix)))


def drop_candidate(ps, rels):
    for k in ("candidates", "approved"):
        ps[k] = [r for r in ps.get(k, []) if r not in rels]
    for r in rels:
        ps.get("meta", {}).pop(r, None)


def discard(name, pid, rel):
    """Archive one candidate: its files move to archive/dungeon/<set>/ and it leaves the piece."""
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        archive_candidate(name, rel)
        drop_candidate(ps, {rel})
        save_state(name, s)
        return ps


def archive_unapproved(name, pid):
    """Archive every candidate of a piece that isn't approved. Returns the piece state and how many moved."""
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        rels = {r for r in ps.get("candidates", []) if r not in ps.get("approved", [])}
        for r in rels:
            archive_candidate(name, r)
        drop_candidate(ps, rels)
        save_state(name, s)
        return ps, len(rels)


def _archive_piece_state(name, s, pid):
    ps = s["pieces"].pop(pid, None) or {}
    for r in set(ps.get("candidates", [])) | set(ps.get("approved", [])):
        archive_candidate(name, r)


def delete_piece(name, pid):
    """Remove a piece from the set and archive all of its generated images."""
    with LOCK:
        spec = load_set(name)
        spec["pieces"] = [p for p in spec["pieces"] if p["id"] != pid]
        out = save_set(name, spec)
        s = state(name)
        if pid in s["pieces"]:
            _archive_piece_state(name, s, pid)
            save_state(name, s)
        return out


def orphans(name):
    """Pieces that have generated images but are no longer in the set spec: {id: {"count", "prompt"}}."""
    ids = {p["id"] for p in load_set(name)["pieces"]}
    out = {}
    for pid, ps in state(name)["pieces"].items():
        n = len(set(ps.get("candidates", [])) | set(ps.get("approved", [])))
        if pid not in ids and n:
            out[pid] = {"count": n, "prompt": ps.get("prompt", "")}
    return out


def restore_piece(name, pid):
    """Put an orphan back into the set: from the style catalog when it came from there, else a plain floor tile or
    prop, with the prompt its images were generated with."""
    with LOCK:
        spec = load_set(name)
        if any(p["id"] == pid for p in spec["pieces"]):
            return spec
        ps = state(name)["pieces"].get(pid, {})
        cat = next((dict(c) for c in style().get("catalog", []) if c["id"].replace("{set}", name) == pid), None)
        if cat:
            cat.pop("group", None)
            cat["id"] = pid
            if cat.get("of"):
                cat["of"] = cat["of"].replace("{set}", name)
            piece = cat
        elif pid.startswith("prop."):
            piece = {"id": pid, "kind": "prop", "footprint": 0.5}
        else:
            piece = {"id": pid, "kind": "tile", "shape": "floor", "variants": 2}
        if ps.get("prompt"):
            piece["prompt"] = ps["prompt"]
        spec["pieces"].append(piece)
        return save_set(name, spec)


def archive_orphan(name, pid):
    with LOCK:
        s = state(name)
        _archive_piece_state(name, s, pid)
        save_state(name, s)


def archive_set(name):
    """Move a whole set (spec + generated images + state) to archive/sets/<set>_<stamp>/. Its Unity export, if any,
    stays in the Hearthdelve project. Returns the archive folder."""
    with LOCK:
        others = [n for n in list_sets() if n != name]
        if not others:
            raise ValueError("can't archive the only set")
        path = sets_dir() / f"{name}.json"
        if not path.exists():
            raise ValueError(f"no set {name}")
        dst = config.archive_dir() / "sets" / f"{name}_{_stamp()}"
        dst.mkdir(parents=True)
        shutil.move(str(path), str(dst / "spec.json"))
        if set_dir(name).exists():
            shutil.move(str(set_dir(name)), str(dst / "sprites"))
        return dst


def family_members(spec, master_id):
    """Every piece of a family, retired ones included: the master, its repaints, wall variations, doors and the wall
    pieces built from it."""
    return [p["id"] for p in spec["pieces"]
            if p["id"] == master_id or p.get("from") == master_id or p.get("built_from") == master_id]


def archive_family(name, master_id):
    """Remove a family from the set: its pieces leave the spec and their whole image folders (candidates, trials,
    blockouts) and state move to archive/families/<set>_<master>_<stamp>/, with family.json holding the piece specs
    and state so it can be put back by hand. Wall pieces its builds retired come back when no other wall family is
    built on those boards. Its Unity export, if any, stays until the next export. Returns the archive folder."""
    with LOCK:
        spec = load_set(name)
        master = piece_of(spec, master_id)
        if master.get("method") not in ("master", "wall_master"):
            raise ValueError(f"{master_id} is not a family master")
        ids = family_members(spec, master_id)
        boards = set(wall_family_settings(master, spec)["boards"].values()) if master["method"] == "wall_master" else set()
        dst = config.archive_dir() / "families" / f"{name}_{_slug_id(master_id)}_{_stamp()}"
        dst.mkdir(parents=True)
        s = state(name)
        (dst / "family.json").write_text(json.dumps({
            "set": name, "master": master_id, "pieces": [p for p in spec["pieces"] if p["id"] in ids],
            "state": {pid: s["pieces"][pid] for pid in ids if pid in s["pieces"]}}, indent=2), encoding="utf-8")
        for pid in ids:
            src = set_dir(name) / "candidates" / pid
            if src.exists():
                shutil.move(str(src), str(dst / "candidates" / pid))
            s["pieces"].pop(pid, None)
        save_state(name, s)
        spec["pieces"] = [p for p in spec["pieces"] if p["id"] not in ids]
        if boards:  # older walls on its boards were retired by its builds: bring them back unless another family builds there
            built = {p.get("board", p["id"]) for p in spec["pieces"] if p.get("built_from") and not p.get("retired")}
            for p in spec["pieces"]:
                if p.get("retired") and not p.get("built_from") and p.get("board", p["id"]) in boards - built:
                    p.pop("retired")
        save_set(name, spec)
        _CELL_CACHE.clear()
        return dst


def _slug_id(pid):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", pid)


def candidate_path(name, rel):
    p = (set_dir(name) / rel).resolve()
    if set_dir(name).resolve() not in p.parents:
        raise ValueError("outside the set folder")
    return p


def png_bytes(im, scale=1):
    import io
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# ----------------------------------------------------------------------------- editing candidates (image panel)

def new_candidate(name, pid, img, meta, tag):
    """Save an image as a new candidate of a piece (full mask), with its meta, at the top of the list."""
    out = set_dir(name) / "candidates" / pid
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{tag}_{time.time_ns() // 1000000}.png"
    img.save(f)
    np.save(f.with_suffix(".npy"), np.ones(img.size[::-1], bool))
    rel = str(f.relative_to(set_dir(name))).replace("\\", "/")
    with LOCK:
        s = state(name)
        ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
        ps["candidates"].insert(0, rel)
        ps.setdefault("meta", {})[rel] = meta
        save_state(name, s)
    return rel


# ----------------------------------------------------------------------------- wall families (Studio > wall board)
# A wall family is the floor family idea for walls: one flat front-view ELEVATION of the wall (the master: 3 units
# tall, one tile wide, repeating sideways every tile, because the game drops runs anywhere and each run starts its
# texture at its own edge), wall variations N tiles wide for runs of N cells (repainted from the master tiled N
# times, keeping its edges, like floor blocks), and doors repainted into a one-tile slice of it (only the 2-unit opening changes, so a door keeps the wall's own stone and colours). "Build"
# projects the approved elevations onto the exact thin-wall geometry (runs of 1-4 cells, the post, the doorway): every
# wall piece shares one texture, so joins line up and the palette carries over between walls and doors. The
# projections are ordinary candidates of ordinary wall pieces, so cutting, export and Unity are unchanged.
# Elevation pieces have kind "elevation": they are sources, never exported or drawn themselves.

WALL_UNITS, DOOR_UNITS, DOOR_WIDTH = 3.0, 2.0, 1.0   # GDD 5.1 scale: walls 3 units, doors 2 (hero ~1.5); doors


def set_wall_boards(spec):
    """The board ids the game asks for (wall, post, door), read from the set's existing wall pieces."""
    def first(pred, default):
        p = next((p for p in spec["pieces"] if pred(p) and not p.get("built_from")), None)
        return p.get("board", p["id"]) if p else default
    return {"wall": first(lambda p: p.get("shape") == "run" and not p.get("opening") and int(p.get("length", 1)) == 1, "tile.crypt_wall"),
            "post": first(lambda p: p.get("shape") == "post", "tile.crypt_wall.post"),
            "door": first(lambda p: p.get("opening") == "door", "tile.crypt_door")}


def wall_family_settings(master, spec=None):
    f = family_settings(master)
    f["doors"] = [v if isinstance(v, dict) else {"id": f"d{i + 1}", "text": v}
                  for i, v in enumerate((master.get("family") or {}).get("doors",
                      ["a closed arched wooden door with iron bands", "a heavy riveted black iron door",
                       "a rusty iron portcullis grate", "closed double wooden doors with carved panels"]))]
    f["height"] = float(f.get("height", WALL_UNITS))
    f["door_height"] = float(f.get("door_height", DOOR_UNITS))
    f["door_width"] = float(f.get("door_width", DOOR_WIDTH))
    f["span"] = f.get("span", "once")  # multi-cell variations: the feature "once" across the run, or "spread"
    f["boards"] = {**(set_wall_boards(spec) if spec else {"wall": "tile.crypt_wall", "post": "tile.crypt_wall.post", "door": "tile.crypt_door"}),
                   **(master.get("family") or {}).get("boards", {})}
    return f


# ----------------------------------------------------------------------------- floor families (Studio > family board)
# A family = one master piece (method "master") + one repaint piece per block size, made from its approved candidate.
# Settings live on the master piece as `family`: prompt versions for the master shootout, variation phrases, target
# counts and planner weights per size. Starred master candidates and variation trials live in the master's state.

FAMILY_SIZES = (1, 2, 3, 4)
DEFAULT_WEIGHT = 70  # % of anchors where a block size that fits is used (FloorBlocks; 70 = the old 30% skip)
DEFAULT_VARIATIONS = ["one large crack running across", "moss growing in the joints", "a few sunken and chipped stones",
                      "a shallow puddle", "scattered dust and small rubble", "a worn, smoother patch"]


def family_settings(master):
    f = dict(master.get("family") or {})
    f.setdefault("prompts", [master.get("prompt", "")])
    f.setdefault("variations", list(DEFAULT_VARIATIONS))
    # entries {id, text}: the id tags the candidates made from it (meta.variation), so renaming keeps them together
    f["variations"] = [v if isinstance(v, dict) else {"id": f"v{i + 1}", "text": v} for i, v in enumerate(f["variations"])]
    f["sizes"] = [int(n) for n in f.get("sizes", FAMILY_SIZES)]
    f["palette_lock"] = bool(f.get("palette_lock", True))
    f["span"] = f.get("span", "once")  # blocks / runs: the feature "once" across the piece, or "spread"
    f["even_tone"] = float(f.get("even_tone", EVEN_TONE))
    f["targets"] = {str(n): int(v) for n, v in {"1": 3, "2": 2, "3": 1, "4": 1, **f.get("targets", {})}.items()}
    f["weights"] = {str(n): int(v) for n, v in {"2": DEFAULT_WEIGHT, "3": DEFAULT_WEIGHT, "4": DEFAULT_WEIGHT,
                                                **f.get("weights", {})}.items()}
    return f


def master_rel(name, master_id):
    appr = state(name)["pieces"].get(master_id, {}).get("approved", [])
    return appr[0] if appr else None


# ----------------------------------------------------------------------------- set material sheet
# One per set (spec["sheet"]): what every family of the set is made of, decided once before generating. Families
# whose template is linked (template.link) get the sheet's palette, stone module and joints written into their
# template whenever the sheet changes, so floors and walls share colour and stone size. The sheet's words join the
# biome in every texture prompt. Wall masters are colour-matched to the floor master (sheet "match", "wall_light").

SHEET_DEFAULT = {"palette": {"stones": ["#7d7c80", "#6f6e73", "#87858a", "#76736f"], "mortar": "#3b3a3f", "variation": 0.08},
                 "floor_stone": 0.33,   # a floor stone's size, tiles (~3 per tile)
                 "wall_course": 0.15,   # a wall course's height, units (wall = 3 units)
                 "wall_brick": 0.33,    # a wall brick's length, tiles
                 "joint": 0.012, "bevel": 0.012,  # mortar width and edge rounding, tiles
                 "words": "",           # material words added to every texture prompt
                 "match": 0.6,          # wall masters: pull toward the floor master's colours (0 = off)
                 "wall_light": 4.0,     # ... with this much lightness added (CIELAB L; walls usually read lighter)
                 "floor": ""}           # the floor family walls match (default: the first with an active master)


def sheet(spec):
    s = {**SHEET_DEFAULT, **(spec.get("sheet") or {})}
    s["palette"] = {**SHEET_DEFAULT["palette"], **(s.get("palette") or {})}
    return s


def sheet_template(tpl, kind, sh):
    """A procedural family template with the sheet's palette and module written in (image templates: unchanged)."""
    tpl = dict(tpl or {})
    pattern = tpl.get("pattern")
    if pattern in (None, "none", "image"):
        return tpl
    defs = patterns.PATTERNS.get(pattern, {})
    p = dict(tpl.get("params") or {})
    if kind == "wall":
        p["across"] = max(1, int(round(1 / max(sh["wall_brick"], 0.04))))
        if defs.get("course", 0):
            p["course"] = round(sh["wall_course"] * iso.KZ, 4)
    else:
        p["across"] = max(1, int(round(1 / max(sh["floor_stone"], 0.04))))
        if defs.get("course", 0):
            p["course"] = round(sh["floor_stone"] / 2, 4)  # bricks / planks on a floor: twice as long as wide
    p["mortar"], p["bevel"] = sh["joint"], sh["bevel"]
    tpl.update(params=p, palette=dict(sh["palette"]))
    return tpl
