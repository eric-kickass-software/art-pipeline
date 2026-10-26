"""Item export into the game (A2; R-003, R-004): `hdart item-export [ids]`, the Item Workshop's Export button.

A game item (vault: Art - Unity Handoff, "Items") is one pack built from one or more Workshop pieces (`item` in
specs/items.json; default the piece's own id). Per game item, from each piece's approved candidate:

  Art/Items/<id>/item.json        the manifest (below); the folder is rebuilt whole
  Art/Items/<id>/<piece>/...      layer PNGs (coloured with the default ramps) + `.idx.png` index maps: R = cel level
                                  0..4 (254 = outline), G = material channel (4 cloth, 5 leather, 6 metal, 7 accent), A
  Art/index.json                  every pack on disk (items, puppets, dungeon sets), rewritten by every export
  Art/ramps.json, channels.json   every ramp group (ids `<group>.<name>`) and the channel numbers
  ArtContract/intake/items/<id>.json   for ids the game doesn't have yet (its Item Database imports them)

item.json `fits` is keyed by build (`<race>.<sex>`), then view, then a list of layers {piece, part, file, rect, depth,
place, clips}: `rect` [x, y, w, h] is in the puppet canvas (the same space as puppet.json's parts), so a layer rides
its part's bone at the offset it has from the part in the rest pose. place "over" = drawn just above its part (item
layers on one part by depth), "under" = just below it (held items: the hand's fingers close over the grip). `clips`:
layers this one cuts away where it covers them (hair). `held` keeps each held piece's upright art and grip settings.
"""
import json, shutil, time

import numpy as np
from PIL import Image

from . import config, contract, store, workshop as ws, puppet_face as pf, puppet_items as pi, unity_export as ue
from .palette import ramp_from_base

SCHEMA = 1
CHANNELS = {"1": "skin", "2": "hair", "3": "eyes", "4": "cloth", "5": "leather", "6": "metal", "7": "accent"}
OUTLINE = 254
# pack kind -> (folder under Art/, manifest, key prefix); the same table as the game's ArtCatalog.Kinds
KINDS = {"item": ("Items", "item.json", ""), "puppet": ("Puppets", "puppet.json", "race."),
         "dungeonSet": ("Dungeon", "set.json", "dungeonset.")}


def art_root():
    return config.project().export("art")


def items_root():
    return config.project().export("items")


def intake_dir():
    return config.project().contract / "intake" / "items"


# ----------------------------------------------------------------------------- game items

def game_ids(doc=None):
    """{game id: [piece ids]} in spec order."""
    doc = doc or pi.spec(); out = {}
    for p, it in doc["items"].items():
        out.setdefault(pi.game_id(p, it), []).append(p)
    return out


def _ramp_ids():
    doc = ws.palettes_doc()
    return {f"{g}.{n}" for g, grp in doc["groups"].items() for n in grp}


def info(gid, doc=None, v=None, game=None):
    """What the game knows (or will be told) about a game item: name, kind, slot, weaponType, hands, heldAs, text,
    materials (material -> ramp id), exists (the game has it: art only, no intake), problems (block an export)."""
    doc = doc or pi.spec(); v = v or contract.vocab(); game = contract.game_items() if game is None else game
    pieces = game_ids(doc).get(gid, []); its = [doc["items"][p] for p in pieces]
    held = [it for it in its if it.get("kind") == "held"]; problems = []
    mats = list(dict.fromkeys(m for it in its for m in it.get("materials", [])))
    row = game.get(gid)
    if row:
        e = {"name": row.get("name"), "kind": row.get("kind"), "slot": row.get("slot"), "weaponType": row.get("weaponType"),
             "text": row.get("text"), "ramps": ((row.get("visual") or {}).get("ramps") or {})}
    else:
        e = dict(doc.get("game_items", {}).get(gid, {})); e["ramps"] = e.pop("materials", {})
        lead = (held or its or [{}])[0]
        e.setdefault("name", " ".join(w.capitalize() for w in gid[5:].split("_")))
        e.setdefault("kind", "weapon" if held else "armor")
        e.setdefault("slot", contract.slot_of(lead.get("slot"), v))
        if held and lead.get("held_as") in ("shield", "focus"):
            e.setdefault("weaponType", lead["held_as"])
        e.setdefault("text", lead.get("text", ""))
        bad = contract.id_problem(gid, v)
        if bad:
            problems.append(bad)
        if e["kind"] not in v["itemKinds"]:
            problems.append(f"kind {e['kind']!r} isn't one of the game's: {', '.join(v['itemKinds'])}")
        if e["slot"] not in [s["key"] for s in v["slots"]]:
            problems.append(f"slot {e['slot']!r} isn't a game slot")
        if held and not e.get("weaponType"):
            problems.append("a new held item needs a weaponType (specs/items.json → game_items)")
    wt = contract.weapon_type(e.get("weaponType"), v) if e.get("weaponType") else None
    if e.get("weaponType") and v["weaponTypes"] and not wt:
        problems.append(f"weaponType {e['weaponType']!r} isn't one of the game's")
    known = _ramp_ids(); ramps = {}
    for m in mats:
        r = e["ramps"].get(m) or f"{doc['materials'][m]['group']}.{next(iter(ws.palettes_doc()['groups'].get(doc['materials'][m]['group'], {'?': 0})))}"
        if r not in known:
            problems.append(f"ramp {r!r} ({m}) isn't in specs/palettes.json")
        ramps[m] = r
    if not pieces:
        problems.append("no Workshop piece makes this item")
    return {"pieces": pieces, "name": e.get("name"), "kind": e.get("kind"), "slot": e.get("slot"),
            "weaponType": e.get("weaponType"), "hands": (wt or {}).get("hands"), "heldAs": (wt or {}).get("heldAs") or
            (held[0].get("held_as", "weapon") if held else None), "text": e.get("text") or "", "materials": ramps,
            "exists": bool(row), "wanted": gid in v["wanted"], "problems": problems}


# ----------------------------------------------------------------------------- one pack

def _idx(lev, size):
    """A levels map (1024) as the pack's index map at `size`: R level / 254, G channel, A mask."""
    lv = ue._small(np.ascontiguousarray(lev), size, nearest=True).copy()
    m = lv[..., 3] > 127; lv[..., 2] = 0; lv[..., 3] = np.where(m, 255, 0); lv[~m] = 0
    return lv


def _layer(d, rel, lev, size, ramps, doc):
    """Save one layer (coloured art + index map) cropped; returns its rect or None."""
    art = ue._small(pi.colour(lev, ramps, doc), size)
    idx = _idx(lev, size); art[..., 3] = np.where(idx[..., 3] > 0, art[..., 3].clip(200), 0)   # the index map's mask is the shape
    return ue._crop_save(art, idx, d / rel, min_alpha=1)


def _held_upright(d, rel, cd, ramps, doc):
    img = np.asarray(Image.open(cd / "held_levels.png").convert("RGBA"))
    art = pi.colour(img, ramps, doc); idx = img.copy(); idx[..., 2] = 0
    (d / rel).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(art).save(d / f"{rel}.png"); Image.fromarray(idx).save(d / f"{rel}.idx.png")
    return [int(art.shape[1]), int(art.shape[0])]


def export_item(gid, race="human", size=512, doc=None, v=None, game=None, st=None):
    """Write one game item's pack (and its intake file when the game doesn't have it). Returns a report dict; raises
    ValueError when nothing can be exported."""
    doc = doc or pi.spec(); v = v or contract.vocab(); st = st or pi.load_state(race)
    inf = info(gid, doc, v, game)
    if inf["problems"]:
        raise ValueError(f"{gid}: " + "; ".join(inf["problems"]))
    pieces = sorted(inf["pieces"], key=lambda p: doc["items"][p].get("depth", 0)); warn = []
    builds = {}
    for b in pi._builds(race):
        cids = {p: pi.resolve(race, p, b, st, doc) for p in pieces}
        if all(cids.values()):
            builds[b] = cids
        else:
            warn.append(f"{b}: no approved " + ", ".join(p for p, c in cids.items() if not c))
    if not builds:
        raise ValueError(f"{gid}: no build has every piece approved ({', '.join(pieces)})" + (f": {'; '.join(warn)}" if warn else ""))
    d = items_root() / gid
    if d.exists():
        shutil.rmtree(d)   # this item's pack only: stale layers go
    d.mkdir(parents=True)
    ramps = inf["materials"]; fits = {}; held = []; saved = {}; uprights = set(); used = {}
    for b, cids in builds.items():
        _, sp, parts = pf.base(b); fit = fits.setdefault(b, {})
        for view in pi.VIEWS:
            lays = fit.setdefault(view, [])
            for p in pieces:
                it = doc["items"][p]; cid = cids[p]; cd = pi.out_dir(race, p) / cid; short = p[5:]
                used.setdefault(p, {})[b] = cid
                extra = {"depth": it.get("depth", 0), "clips": it.get("clips", [])}
                if it.get("kind") == "held":   # placed on this build's hand (its grip hand, else the Template's)
                    img = np.asarray(Image.open(cd / "held.png").convert("RGBA")); lev = np.asarray(Image.open(cd / "held_levels.png").convert("RGBA"))
                    if view == "back":
                        img, lev = img[:, ::-1].copy(), lev[:, ::-1].copy()
                    grip = pi.grip_assets(race, b, view).get(it.get("hand", "armA"))
                    hand, _, lv = pi.held_layer(doc, it, sp, view, parts[view], img, lev, grip)
                    rel = f"{short}/{cid}/{b.split('.')[1]}/{view}/held"
                    rect = _layer(d, rel, lv, size, ramps, doc)
                    if rect:
                        lays.append({"piece": p, "part": hand, "file": rel + ".png", "rect": rect, "place": "under", **extra})
                    if (p, cid) not in uprights:
                        uprights.add((p, cid))
                        held.append({"piece": p, "file": f"{short}/{cid}/held.png", "size": _held_upright(d, f"{short}/{cid}/held", cd, ramps, doc),
                                     "heldAs": it.get("held_as", "weapon"), "hand": it.get("hand", "armA"), "length": it.get("length", 0.5),
                                     "gripAngle": it.get("grip_angle", 0), "flip": bool(it.get("flip")),
                                     "gripAt": it.get("grip_at")})
                    continue
                for f in sorted((cd / "plevels" / view).glob("*.png")):
                    part = f.stem; rel = f"{short}/{cid}/{view}/{part}"
                    if rel not in saved:   # a shared fit is written once for every build
                        saved[rel] = _layer(d, rel, np.asarray(Image.open(f).convert("RGBA")), size, ramps, doc)
                    if saved[rel]:
                        lays.append({"piece": p, "part": part, "file": rel + ".png", "rect": saved[rel], "place": "over", **extra})
    mats = {m: {"channel": doc["materials"][m]["channel"], "group": doc["materials"][m]["group"], "ramp": r} for m, r in ramps.items()}
    made = int(time.time()); sha = store.git_sha()
    manifest = {"_doc": __doc__.strip().split("\n\n")[2], "schemaVersion": SCHEMA, "kind": "item", "id": gid, "name": inf["name"],
                "made": made, "canvas": size, "race": race, "slot": inf["slot"],
                "artSlots": list(dict.fromkeys(doc["items"][p].get("slot") for p in pieces)),
                "weaponType": inf["weaponType"], "heldAs": inf["heldAs"], "hands": inf["hands"], "materials": mats,
                "pieces": [{"id": p, "artSlot": doc["items"][p].get("slot"), "layerKind": "held" if doc["items"][p].get("kind") == "held" else "fitted",
                            "depth": doc["items"][p].get("depth", 0), "fit": doc["items"][p].get("fit", "shared"),
                            "clips": doc["items"][p].get("clips", [])} for p in pieces],
                "fits": fits, "held": held,
                "source": {"tool": "item-workshop", "sha": sha, "candidates": used, "requirementsRevision": v["revision"]}}
    (d / "item.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    intake = None
    if not inf["exists"]:
        intake = intake_dir() / f"{gid}.json"
        row = {"schemaVersion": 1, "id": gid, "name": inf["name"], "kind": inf["kind"], "slot": inf["slot"]}
        for key in ("weaponType", "hands"):
            if inf[key] is not None:
                row[key] = inf[key]
        row.update({"text": inf["text"], "materials": ramps,
                    "source": {"tool": "item-workshop", "sha": sha, "candidate": ",".join(sorted({c for u in used.values() for c in u.values()})),
                               "requirementsRevision": v["revision"]}, "made": made})
        store.write_json(intake, row)
    return {"pack": str(d), "builds": list(builds), "intake": str(intake) if intake else None, "warnings": warn}


# ----------------------------------------------------------------------------- shared files

def write_shared():
    """Art/ramps.json and Art/channels.json: every ramp group (ids <group>.<name>) and the channel numbers."""
    pal = ws.palettes_doc(); size = pal.get("rampSize", 5); root = art_root()
    groups = {g: [{"id": f"{g}.{n}", "name": n, "colors": [c if isinstance(c, str) else ue._hex(c) for c in (e.get("colors") or ramp_from_base(e["base"], size))]}
                  for n, e in grp.items() if e.get("colors") or e.get("base")] for g, grp in pal["groups"].items()}
    store.write_json(root / "ramps.json", {"schemaVersion": 1, "rampSize": size, "outline": pal.get("outline"), "groups": groups})
    mats = {m: {"channel": e["channel"], "group": e["group"]} for m, e in pi.spec()["materials"].items()}
    store.write_json(root / "channels.json", {"schemaVersion": 1, "channels": CHANNELS, "levels": size, "outline": OUTLINE,
                                              "materials": mats,
                                              "_doc": "Index maps: R = cel level 0..levels-1 (254 = outline), G = channel, A = mask. "
                                                      "Channel 0 keeps the painted colour."})


def write_index():
    """Art/index.json: every pack on disk, sorted by key (the game's ArtCatalog reads it)."""
    root = art_root(); packs = {}
    for kind, (folder, manifest, prefix) in KINDS.items():
        for d in sorted((root / folder).glob("*/")) if (root / folder).exists() else []:
            f = d / manifest
            if not f.exists():
                continue
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            packs[prefix + d.name] = {"kind": kind, "path": f"{folder}/{d.name}", "schemaVersion": m.get("schemaVersion") or m.get("format") or 1}
    store.write_json(root / "index.json", {"schemaVersion": 1, "packs": dict(sorted(packs.items()))})
    return len(packs)


def export(ids=None, race="human", size=512):
    """Export game items (default: every game item with approved art). Returns {exported: {id: report}, skipped:
    {id: reason}, index: pack count}."""
    doc = pi.spec(); v = contract.vocab(); game = contract.game_items(); st = pi.load_state(race)
    out = {"exported": {}, "skipped": {}}
    for gid in ids or list(game_ids(doc)):
        try:
            out["exported"][gid] = export_item(gid, race, size, doc, v, game, st)
        except (ValueError, KeyError, FileNotFoundError) as e:
            out["skipped"][gid] = str(e)
    write_shared(); out["index"] = write_index()
    return out


# ----------------------------------------------------------------------------- hdart export --check

def _ids_in(idx_path):
    with Image.open(idx_path) as im:
        a = np.asarray(im.convert("RGBA"))
    on = a[..., 3] > 0
    return a.shape[1::-1], set(np.unique(a[..., 1][on]).tolist()), set(np.unique(a[..., 0][on]).tolist())


def _check_file(root, d, rel, rect, channels, problems, where):
    f = d / rel; fi = d / rel.replace(".png", ".idx.png")
    if not f.exists() or not fi.exists():
        problems.append(f"{where}: {rel} (or its .idx.png) is missing"); return
    with Image.open(f) as im:
        size = im.size
    isize, chans, levels = _ids_in(fi)
    if rect and (size != isize or list(size) != list(rect[2:])):
        problems.append(f"{where}: {rel} is {size}, its index map {isize}, its rect says {rect[2:]}")
    if not chans <= channels:
        problems.append(f"{where}: {rel} uses channels {sorted(chans - channels)} it doesn't register")
    if not levels <= set(range(8)) | {OUTLINE}:
        problems.append(f"{where}: {rel} has cel levels {sorted(levels - set(range(8)) - {OUTLINE})}")


def check():
    """Every manifest parses, every file it names exists, index maps only use registered channels, index.json lists
    exactly the packs on disk, intake files follow requirements.json. Returns a list of problems (empty = fine)."""
    root = art_root(); problems = []; v = contract.vocab(); ramp_ids = _ramp_ids()
    on_disk = {}
    for kind, (folder, manifest, prefix) in KINDS.items():
        for d in sorted((root / folder).glob("*/")) if (root / folder).exists() else []:
            if (d / manifest).exists():
                on_disk[prefix + d.name] = (kind, d / manifest)
    try:
        idx = json.loads((root / "index.json").read_text(encoding="utf-8"))
        listed = idx.get("packs", {})
        for key in sorted(set(listed) ^ set(on_disk)):
            problems.append(f"index.json: {key} is {'listed but has no pack' if key in listed else 'on disk but not listed'}")
    except FileNotFoundError:
        if on_disk:
            problems.append("index.json is missing (run an export)")
    except ValueError as e:
        problems.append(f"index.json: {e}")
    for key, (kind, f) in on_disk.items():
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            problems.append(f"{key}: {e}"); continue
        d = f.parent
        if kind == "item":
            chans = {0} | {e["channel"] for e in m.get("materials", {}).values()}
            if m.get("id") != d.name:
                problems.append(f"{key}: id {m.get('id')!r} isn't its folder's name")
            if m.get("slot") not in [s["key"] for s in v["slots"]]:
                problems.append(f"{key}: slot {m.get('slot')!r} isn't a game slot")
            for mat, e in m.get("materials", {}).items():
                if e.get("ramp") not in ramp_ids:
                    problems.append(f"{key}: ramp {e.get('ramp')!r} ({mat}) isn't in ramps.json")
            for build, views in m.get("fits", {}).items():
                for view, lays in views.items():
                    for L in lays:
                        _check_file(root, d, L["file"], L.get("rect"), chans, problems, f"{key} {build} {view}")
            for h in m.get("held", []):
                _check_file(root, d, h["file"], None, chans, problems, f"{key} held")
        elif kind == "puppet":
            for sex, e in m.get("sexes", {}).items():
                for view, V in e.get("views", {}).items():
                    for p in V.get("parts", []):
                        _check_file(root, d, p["file"], p.get("rect"), {0, 1}, problems, f"{key} {sex} {view}")
            for kind_ in ("hair", "faces"):
                for e in m.get(kind_, []):
                    for view, V in e.get("views", {}).items():
                        _check_file(root, d, V["file"], V.get("rect"), {0, 2 if kind_ == "hair" else 3}, problems, f"{key} {e['id']}")
    req = (contract.load() or {}).get("intake", {})
    for f in sorted(intake_dir().glob("*.json")) if config.project().contract else []:
        try:
            row = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            problems.append(f"intake {f.name}: {e}"); continue
        miss = [k for k in req.get("required", ["schemaVersion", "id", "name", "kind", "slot"]) if k not in row]
        if miss:
            problems.append(f"intake {f.name}: missing {', '.join(miss)}")
        if f.stem != row.get("id") or contract.id_problem(row.get("id"), v):
            problems.append(f"intake {f.name}: bad id {row.get('id')!r}")
        if row.get("kind") not in v["itemKinds"] or row.get("slot") not in [s["key"] for s in v["slots"]]:
            problems.append(f"intake {f.name}: kind {row.get('kind')!r} / slot {row.get('slot')!r} not in the game's vocabulary")
        if row.get("weaponType") and v["weaponTypes"] and not contract.weapon_type(row["weaponType"], v):
            problems.append(f"intake {f.name}: weaponType {row['weaponType']!r} isn't one of the game's")
        for mat, r in (row.get("materials") or {}).items():
            if r not in ramp_ids:
                problems.append(f"intake {f.name}: ramp {r!r} ({mat}) isn't in specs/palettes.json")
    return problems
