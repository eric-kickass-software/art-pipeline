"""Dungeon Forge export: the approved pieces of a set, cut into cells, written into the game project's dungeon art
folder (config.project().export("dungeon")) as a whole pack.
"""
import json


from hdart.tools.dungeon.geometry import layout, mirrored, output_tile
from hdart.tools.dungeon.sets import export_root, family_settings, load_set, piece_of, resolved, state
from hdart.tools.dungeon.cells import cells


def export(name, tiles=(32, 64)):
    """Approved variants -> Assets/StreamingAssets/Art/Dungeon/<set>/r<tile>/<key>.<v>[.<suffix>].png (+ .n.png
    normal maps) and set.json (format 2). Several pieces with the same `board` key become variants of it
    (door styles, wall runs of different lengths, floor atlases).
    The images are at the set's output resolution (`output_tile`: pixelated or full) in every r<tile> folder the
    game asks for; set.json's `ppu` (pixels per tile = Unity pixels per unit) and `filter` say how to show them."""
    spec = load_set(name)
    s = state(name)
    root = export_root() / spec["id"]
    out_t = output_tile(spec)
    meta = {"id": spec["id"], "name": spec.get("name", spec["id"]), "format": 2, "tiles": list(tiles),
            "ppu": out_t, "filter": "point" if spec.get("pixel") else "bilinear", "pieces": {}}
    for t in tiles:  # variant counts can shrink: start each grid folder clean
        for old in (root / f"r{t}").glob("*.png"):
            old.unlink()
    for p0 in spec["pieces"]:
        if p0.get("retired") or p0.get("kind") == "elevation":  # retired: kept for reference; elevations: sources
            continue
        piece = resolved(spec, p0["id"])
        approved = s["pieces"].get(piece["id"], {}).get("approved", [])
        if not approved:
            continue
        key = piece.get("board", piece["id"])  # the id the game board asks for
        of = piece.get("of")
        of = piece_of(spec, of).get("board", of) if of and any(p["id"] == of for p in spec["pieces"]) else of
        entry = meta["pieces"].setdefault(key, {"kind": piece["kind"], "of": of, "variants": []})
        if piece.get("method") == "master":  # floor family: block size weights for the board's planner
            entry["blockWeights"] = family_settings(piece)["weights"]
        for rel in approved:
            vi = len(entry["variants"])
            var = {"source": piece["id"], "layout": layout(piece), "mirror": mirrored(piece),
                   "length": int(piece.get("length", 1)) if layout(piece) == "run" else 1,
                   "atlas": int(piece.get("atlas", 4)) if layout(piece) == "atlas" else 0}
            cs = cells(name, piece["id"], rel, out_t)
            h = next(iter(cs.values()))[0].height
            var["sprites"] = sorted(cs)
            for t in tiles:
                d = root / f"r{t}"
                d.mkdir(parents=True, exist_ok=True)
                for suf, (rgba, nm) in cs.items():
                    stem = f"{key}.{vi}" + (f".{suf}" if suf else "")
                    rgba.save(d / f"{stem}.png")
                    nm.save(d / f"{stem}.n.png")
                var[f"pivot{t}"] = [0.5, 0.1] if piece["kind"] == "prop" else [0.5, (out_t / 4) / h]
            entry["variants"].append(var)
    root.mkdir(parents=True, exist_ok=True)
    (root / "set.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    from hdart.export import art_pack
    art_pack.write_index()
    return root
