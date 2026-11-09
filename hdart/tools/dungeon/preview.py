"""Dungeon Forge previews: family stages and layouts, wall build status, cell images, the family preview and the mock
room preview with the game's floor blocks (same rules and hash as FloorBlocks.cs, so the preview matches the board).
"""
from PIL import Image

from hdart.core.locks import CELLS as _CELL_CACHE, HARMONY as _REF_CACHE
from hdart.tools.dungeon.geometry import layout, output_tile
from hdart.tools.dungeon.sets import (export_root, family_settings, load_set, master_rel, piece_of, png_bytes, resolved,
    set_dir, sets_dir, state, wall_family_settings)
from hdart.tools.dungeon.colour import harmony_key
from hdart.tools.dungeon.generation import family_pieces, family_template, material_prompt, wall_family_pieces
from hdart.tools.dungeon.cells import cells


# The mock room for the preview, in piece space (rows = +y, columns = +x). Walls are thin and join up by
# neighbours exactly like Prototype V does it: a post where walls meet or end, half-cell arms towards each
# neighbouring wall, long straight stretches cut into the longest wall runs available.
# W wall, T wall decor (on the room side), D door, A archway, P pillar, R rubble, X trap, a..l props.
ROOM = [
    "WWTWWWDWWTWW",
    "W.....W....W",
    "T.a...W..b.W",
    "W.....A....W",
    "W.c..WWW...W",
    "D....X...d.W",
    "W.e.....P..T",
    "W..f..R..g.W",
    "W.h......i.W",
    "WWWWWWWWWWWW",
]
WALLISH = "WTDA"


def _roles(spec, approved):
    """Room letter -> board key -> list of (source piece, rel, layout info) to draw with."""
    by_key = {}
    for p0 in spec["pieces"]:
        if p0.get("retired"):
            continue
        p = resolved(spec, p0["id"])
        for rel in approved.get(p["id"], []):
            by_key.setdefault(p.get("board", p["id"]), []).append((p, rel))

    def first_key(pred):
        for key, vs in by_key.items():
            if any(pred(p) for p, _ in vs):
                return key
        return None

    def word(p, *ws):
        return any(w in p["id"] for w in ws)

    floor = lambda p: p.get("shape") == "floor" and p["kind"] == "tile"
    wall = lambda p: p.get("shape") == "run" and p["kind"] == "tile" and p.get("opening") != "door"
    roles = {
        ".": first_key(lambda p: floor(p) and p.get("method") == "atlas")
             or first_key(lambda p: floor(p) and not word(p, "rubble", "trap")),
        "R": first_key(lambda p: floor(p) and word(p, "rubble")),
        "X": first_key(lambda p: floor(p) and word(p, "trap")),
        "W": first_key(wall),
        "post": first_key(lambda p: p.get("shape") == "post"),
        "D": first_key(lambda p: p.get("opening") == "door"),
        "A": first_key(lambda p: p.get("shape") == "mesh"),
        "P": first_key(lambda p: p.get("shape") == "pillar"),
    }
    decor = [k for k, vs in by_key.items() if vs[0][0]["kind"] == "wall_decor"]
    props = [k for k, vs in by_key.items() if vs[0][0]["kind"] == "prop"]
    for k, ch in enumerate("abcdefghijkl"):
        roles[ch] = props[k % len(props)] if props else None
    roles["decor"] = decor
    for k, fallback in (("R", "."), ("X", ".")):
        roles[k] = roles[k] or roles[fallback]
    return roles, by_key


def _runs(cells, along_x, lengths):
    """Straight wall stretches along one axis -> {(x, y): (length of the chosen run, index within it)}.
    Greedy longest-first, so long walls get long, non-repeating runs."""
    out = {}
    lengths = sorted(set(lengths), reverse=True) or [1]
    lines = {}
    for (x, y) in cells:
        lines.setdefault(y if along_x else x, []).append(x if along_x else y)
    for line, vals in lines.items():
        vals.sort()
        seg = [[vals[0]]]
        for v in vals[1:]:
            (seg[-1].append(v) if v == seg[-1][-1] + 1 else seg.append([v]))
        for s in seg:
            i = 0
            while i < len(s):
                L = next((L for L in lengths if L <= len(s) - i), 1)
                for k in range(L):
                    v = s[i + k]
                    out[(v, line) if along_x else (line, v)] = (L, k, s[i])
                i += L
    return out


def export_status(name, spec):
    """"none" (never exported), "stale" (approvals or the spec changed since), or "ok"."""
    f = export_root() / spec["id"] / "set.json"
    if not f.exists():
        return "none"
    newest = max((p.stat().st_mtime for p in (set_dir(name) / "state.json", sets_dir() / f"{name}.json") if p.exists()), default=0)
    return "stale" if newest > f.stat().st_mtime + 1 else "ok"


def family_stages(name, spec, master, exported=None):
    """Where a family stands, step by step: [{key, label, state (done / part / todo / stale), detail}]. The steps
    build on each other: layout template -> master (draft, then full) -> variations -> (walls: doors, built) -> export."""
    s = state(name)["pieces"]
    wall = master.get("method") == "wall_master"
    out = []
    tpl = family_template(master)
    out.append({"key": "template", "label": "Template", "state": "done" if tpl else "part",
                "detail": ("linked to the sheet" if tpl.get("link") else tpl["pattern"]) if tpl else "prompt only (no layout template)"})
    mrel = master_rel(name, master["id"])
    meta = s.get(master["id"], {}).get("meta", {})
    drafts = sum(1 for r in s.get(master["id"], {}).get("candidates", []) if meta.get(r, {}).get("draft"))
    if not mrel:
        out.append({"key": "master", "label": "Master", "state": "todo", "detail": f"{drafts} drafts, none active" if drafts else "none yet"})
    elif meta.get(mrel, {}).get("draft"):
        out.append({"key": "master", "label": "Master", "state": "part", "detail": "the active master is a draft: finalize it"})
    else:
        out.append({"key": "master", "label": "Master", "state": "done", "detail": "active"})
    if wall:
        fam = wall_family_settings(master, spec)
        pcs = wall_family_pieces(spec, master["id"])
        sizes = [int(n) for n in fam["sizes"]]
        have = {L: [r for r in s.get(pcs[f"var{L}"]["id"], {}).get("approved", [])] if f"var{L}" in pcs else [] for L in sizes}
        stale = sum(1 for L in sizes for r in have[L] if s[pcs[f"var{L}"]["id"]].get("meta", {}).get(r, {}).get("master") != mrel)
        n_ok = sum(1 for L in sizes if have[L])
        doors = len(s.get(pcs["door"]["id"], {}).get("approved", [])) if "door" in pcs else 0
        b = wall_build_status(name, spec, master)
        out.append({"key": "variations", "label": "Variations", "state": "stale" if stale else "done" if n_ok == len(sizes) else "part" if n_ok else "todo",
                    "detail": f"{sum(len(v) for v in have.values())} approved, {n_ok}/{len(sizes)} run lengths" + (f", {stale} from another master" if stale else "")})
        out.append({"key": "doors", "label": "Doors", "state": "done" if doors else "todo", "detail": f"{doors} approved"})
        out.append({"key": "built", "label": "Built", "state": "stale" if b["stale"] else "done" if b["built"] else "todo", "detail": b["why"] or "current"})
    else:
        fam = family_settings(master)
        pcs = family_pieces(spec, master["id"])
        want = {int(n): fam["targets"].get(str(n), 1) for n in fam["sizes"]}
        have = {n: [r for r in s.get(pcs[n]["id"], {}).get("approved", [])] if n in pcs else [] for n in want}
        stale = sum(1 for n in want for r in have[n] if s[pcs[n]["id"]].get("meta", {}).get(r, {}).get("master") != mrel)
        met = sum(1 for n in want if len(have[n]) >= want[n])
        out.append({"key": "variations", "label": "Variations", "state": "stale" if stale else "done" if met == len(want) else "part" if any(have.values()) else "todo",
                    "detail": f"{sum(len(v) for v in have.values())} approved, targets met for {met}/{len(want)} sizes" + (f", {stale} from another master" if stale else "")})
    ex = exported or export_status(name, spec)
    out.append({"key": "export", "label": "Exported", "state": {"ok": "done", "stale": "stale", "none": "todo"}[ex],
                "detail": {"ok": "up to date", "stale": "changed since the last export", "none": "not yet"}[ex]})
    return out


def family_info(name, spec, master):
    """What the family board needs about a family: kind, settings, its pieces by size, material, built state."""
    if master.get("method") == "wall_master":
        pcs = wall_family_pieces(spec, master["id"])
        built = state(name)["pieces"].get(pcs["run1"]["id"], {}).get("approved") if "run1" in pcs else None
        return {"kind": "wall", "settings": wall_family_settings(master, spec), "material": material_prompt(name, master),
                "pieces": {int(k[3:]): p["id"] for k, p in pcs.items() if k.startswith("var")},
                "door": pcs["door"]["id"] if "door" in pcs else None,
                "built": [pcs[k]["id"] for k in pcs if k.startswith("run") or k in ("post", "doorway")] if built else [],
                "build": wall_build_status(name, spec, master), "stages": family_stages(name, spec, master)}
    return {"kind": "floor", "settings": family_settings(master), "material": material_prompt(name, master),
            "pieces": {n: q["id"] for n, q in family_pieces(spec, master["id"]).items()},
            "stages": family_stages(name, spec, master)}


def family_layout(name, master_id, seed=0, W=16, H=12):
    """A planner-laid floor for the live preview: each cell's sprite (piece, candidate, cut suffix) at the set's
    output resolution, from the approved family (the same pieces, weights and rules as the game)."""
    spec = load_set(name)
    master = piece_of(spec, master_id)
    s = state(name)["pieces"]
    tiles = {}
    rel = master_rel(name, master_id)
    if rel:
        tiles[1] = [(master_id, rel)]
    for N, p in family_pieces(spec, master_id).items():
        tiles.setdefault(N, []).extend((p["id"], r) for r in s.get(p["id"], {}).get("approved", []))
    if not tiles:
        raise ValueError("no active master yet")
    holes = {(W // 3, H // 3), (2 * W // 3, H // 3), (W // 3, 2 * H // 3), (2 * W // 3, 2 * H // 3)}
    cells_ = [(x, y) for y in range(H) for x in range(W) if (x, y) not in holes]
    weights = {int(k): v for k, v in family_settings(master)["weights"].items()}
    plan = plan_floor_blocks(cells_, sorted(tiles), (_fnv_str(master.get("board", master_id)) + seed) & 0xFFFFFFFF, weights)
    out = []
    for (x, y), (bx, by, n) in sorted(plan.items(), key=lambda kv: (kv[0][0] + kv[0][1], kv[0][0])):
        pid, r = tiles[n][_fnv((bx, by, seed)) % len(tiles[n])]
        out.append({"x": x, "y": y, "bx": bx, "by": by, "n": n, "piece": pid, "rel": r,
                    "suf": f"i{x - bx}j{y - by}" if n > 1 else ""})
    return {"tile": output_tile(spec), "pixel": bool(spec.get("pixel")), "W": W, "H": H,
            "holes": sorted(holes), "cells": out}


def preview_rev(name, spec):
    """For the browser's image URLs: with Harmonize on, an approval or master switch can change a sprite's colours
    without changing its file, so the state file's time is part of the URL (the server's own cache is keyed per
    sprite by harmony_key, so unchanged sprites come straight from it)."""
    if not spec.get("harmonize"):
        return "0"
    f = set_dir(name) / "state.json"
    return str(f.stat().st_mtime_ns) if f.exists() else "0"


def clear_preview_caches():
    """Forget every cached cut sprite and colour reference (the preview's force refresh)."""
    _CELL_CACHE.clear()
    _REF_CACHE.clear()


def wall_build_status(name, spec, master):
    """Is the wall family's build current? {"built", "stale", "why"}: stale when the runs, post and doorway were
    projected from another active master, or the approved variations / doors changed since."""
    pcs = wall_family_pieces(spec, master["id"])
    s = state(name)["pieces"]
    mrel = master_rel(name, master["id"])
    keys = [k for k in pcs if k.startswith("run") or k in ("post", "doorway")]
    have = {(k, s.get(pcs[k]["id"], {}).get("meta", {}).get(r, {}).get("built_from"))
            for k in keys for r in s.get(pcs[k]["id"], {}).get("approved", [])}
    if not have:
        return {"built": False, "stale": False, "why": "not built yet"}
    if not mrel:
        return {"built": True, "stale": False, "why": ""}
    want = {("post", mrel)}
    for L in (1, 2, 3, 4):
        want.add((f"run{L}", mrel))
        v = pcs.get(f"var{L}")
        if v:
            ps = s.get(v["id"], {})
            want |= {(f"run{L}", r) for r in ps.get("approved", []) if ps.get("meta", {}).get(r, {}).get("master") == mrel}
    if "door" in pcs:
        want |= {("doorway", r) for r in s.get(pcs["door"]["id"], {}).get("approved", [])}
    built_master = {r for k, r in have if k == "post"}
    if built_master and mrel not in built_master:
        return {"built": True, "stale": True, "why": "built from another master"}
    if have != want:
        return {"built": True, "stale": True, "why": "approvals changed since the last build"}
    return {"built": True, "stale": False, "why": ""}


def cell_sprites(name, pid, rel, tile=None, spec=None):
    """A candidate's cut sprites {suffix: RGBA} (what the export ships), cached across requests: the live previews
    and the room preview ask for the same ones over and over."""
    spec = spec or load_set(name)
    tile = tile or output_tile(spec)
    key = (name, pid, rel, tile, spec.get("pixel"), spec.get("harmonize"), harmony_key(name, spec, resolved(spec, pid)))
    if key not in _CELL_CACHE:
        if len(_CELL_CACHE) > 160:
            _CELL_CACHE.clear()
        _CELL_CACHE[key] = {k: v[0] for k, v in cells(name, pid, rel, tile).items()}
    return _CELL_CACHE[key]


def cell_png(name, pid, rel, suf, tile=None):
    return png_bytes(cell_sprites(name, pid, rel, tile)[suf])


def family_tiles(name, spec, master_id):
    """{N: [top-down squares]}: the active master and the approved variants of each size."""
    s = state(name)["pieces"]
    rel = master_rel(name, master_id)
    out = {1: [Image.open(set_dir(name) / rel).convert("RGB")]} if rel else {}
    for N, p in family_pieces(spec, master_id).items():
        for r in s.get(p["id"], {}).get("approved", []):
            out.setdefault(N, []).append(Image.open(set_dir(name) / r).convert("RGB"))
    return out


def family_preview(name, master_id, seed=0, outline=False, view="iso", W=14, H=10, per=64):
    """A W x H floor laid out by the planner from the family (same rules and weights as the game), top-down or iso.
    `outline` draws the blocks (white 1x1, blue 2x2, yellow 3x3, red 4x4)."""
    from PIL import ImageDraw
    spec = load_set(name)
    master = piece_of(spec, master_id)
    tiles = family_tiles(name, spec, master_id)
    if not tiles:
        raise ValueError("no active master yet")
    weights = {int(k): v for k, v in family_settings(master)["weights"].items()}
    holes = {(W // 3, H // 3), (2 * W // 3, H // 3), (W // 3, 2 * H // 3), (2 * W // 3, 2 * H // 3)}
    cells = [(x, y) for y in range(H) for x in range(W) if (x, y) not in holes]
    plan = plan_floor_blocks(cells, sorted(tiles), _fnv_str(master.get("board", master_id)) + seed, weights)
    top = Image.new("RGB", (W * per, H * per), (24, 20, 30))
    blocks = sorted(set(plan.values()))
    for bx, by, n in blocks:
        imgs = tiles[n]
        top.paste(imgs[_fnv((bx, by, seed)) % len(imgs)].resize((n * per, n * per), Image.LANCZOS), (bx * per, by * per))
    if outline:
        dr = ImageDraw.Draw(top)
        colour = {1: (255, 255, 255), 2: (90, 200, 255), 3: (255, 200, 60), 4: (255, 90, 120)}
        for bx, by, n in blocks:
            dr.rectangle([bx * per + 1, by * per + 1, (bx + n) * per - 2, (by + n) * per - 2],
                         outline=colour.get(n, (200, 200, 200)), width=3 if n > 1 else 1)
    if view != "iso":
        return top
    a = per // 2  # iso: the top-down squares warped onto the 2:1 diamond grid (what cutting does per cell)
    X0, k = H * a, per / a
    return top.transform(((W + H) * a, (W + H) * a // 2), Image.AFFINE,
                         (k / 2, k, -X0 * k / 2, -k / 2, k, X0 * k / 2), Image.BICUBIC, fillcolor=(24, 20, 30))


# ----------------------------------------------------------------------------- floor blocks (mixed atlas sizes)
# Same rules and hash as the game's FloorBlocks.cs (Presentation/Board), so the room preview matches the board.

FLOOR_SKIP_PERCENT = 30


def _fnv(vals):
    """FNV-1a over ints as little-endian uint32 (FloorBlocks.Hash(int x, int y, uint seed))."""
    h = 2166136261
    for v in vals:
        for k in range(4):
            h = ((h ^ ((v & 0xFFFFFFFF) >> (8 * k) & 0xFF)) * 16777619) & 0xFFFFFFFF
    return h


def _fnv_str(s):
    """FNV-1a over UTF-16 code units (FloorBlocks.Hash(string))."""
    h = 2166136261
    for c in s:
        h = ((h ^ ord(c)) * 16777619) & 0xFFFFFFFF
    return h


def plan_floor_blocks(cells, sizes, seed, weights=None):
    """{(x, y): (block x, block y, size)}: square blocks of the given sizes (plus 1x1) over the cells. Greedy in
    scan order, largest size first; at each anchor a size that fits is used with `weights[size]` % (default 70)."""
    floor = set(cells)
    big = sorted({s for s in sizes if s > 1}, reverse=True)
    plan = {}
    for x, y in sorted(floor, key=lambda c: (c[1], c[0])):
        if (x, y) in plan:
            continue
        size = 1
        for s in big:
            if (_fnv((x, y, (seed + s) & 0xFFFFFFFF)) % 100 >= 100 - (weights or {}).get(s, 100 - FLOOR_SKIP_PERCENT)
                    and all((x + dx, y + dy) in floor and (x + dx, y + dy) not in plan
                            for dy in range(s) for dx in range(s))):
                size = s
                break
        for dy in range(size):
            for dx in range(size):
                plan[(x + dx, y + dy)] = (x, y, size)
    return plan


def room_grid(W=14, H=10):
    """A preview room: back walls along the top row and left column, each with a door, a free-standing wall
    across the far side (both faces and post ends show), floor everywhere else."""
    g = [["." for _ in range(W)] for _ in range(H)]
    for x in range(W):
        g[0][x] = "W"
    for y in range(H):
        g[y][0] = "W"
    g[0][W // 2], g[H // 2][0] = "D", "D"
    for x in range(W // 2, W - 1):
        g[2 * H // 3][x] = "W"
    return ["".join(r) for r in g]


def room_preview(name, tile=None, scale=3, include=None, grid=None, floor_board=None, as_layout=False):
    """Compose approved pieces (native pixels) into a small iso room with thin, joined-up walls. `include` =
    {pid: rel} swaps in one candidate for a piece, to judge it in context before approving. `grid` replaces the
    mock room (see room_grid), `floor_board` picks the main floor. `as_layout` returns the draw list instead of an
    image (the Studio's live preview draws it): {w, h, ox, oy, tile, items: [{piece, rel, suf, x, y}], blocks}."""
    spec = load_set(name)
    tile = tile or output_tile(spec)
    s = state(name)
    approved = {pid: list(ps.get("approved", [])) for pid, ps in s["pieces"].items()}
    for pid, rel in (include or {}).items():
        approved[pid] = [rel] if rel else []
    roles, by_key = _roles(spec, approved)
    if floor_board:
        roles["."] = roles["R"] = roles["X"] = floor_board
    cache = {}

    def variants(key):
        return by_key.get(key, []) if key else []

    def sprite(p, rel, suf):
        if (p["id"], rel) not in cache:
            cache[(p["id"], rel)] = cell_sprites(name, p["id"], rel, tile, spec)
        im = cache[(p["id"], rel)].get(suf)
        piv = [0.5, 0.1] if p["kind"] == "prop" else [0.5, (tile / 4) / im.height] if im else None
        return im, piv

    def pick(key, x, y, pred=lambda p: True):
        vs = [v for v in variants(key) if pred(v[0])]
        return vs[(x * 7 + y * 13) % len(vs)] if vs else None

    grid = grid or ROOM
    rows, cols = len(grid), len(grid[0])
    at = lambda x, y: grid[y][x] if 0 <= x < cols and 0 <= y < rows else " "
    wallish = {(x, y) for y in range(rows) for x in range(cols) if at(x, y) in WALLISH}
    nb = {c: {"+x": (c[0] + 1, c[1]) in wallish, "-x": (c[0] - 1, c[1]) in wallish,
              "+y": (c[0], c[1] + 1) in wallish, "-y": (c[0], c[1] - 1) in wallish} for c in wallish}
    run_lengths = [v[0].get("length", 1) for v in variants(roles["W"])]
    xs = _runs([c for c in wallish if nb[c]["+x"] or nb[c]["-x"]], True, run_lengths)
    ys = _runs([c for c in wallish if nb[c]["+y"] or nb[c]["-y"]], False, run_lengths)

    W = int((rows + cols) * tile / 2 + tile)
    H = int((rows + cols) * tile / 4 + tile * 2.5)
    ox, oy = rows * tile / 2 + tile / 2, tile * 2.0
    canvas = Image.new("RGBA", (W, H), (22, 18, 28, 255))
    items = []  # (layer, depth, sub, x, y, piece, rel, suffix)

    def add(layer, x, y, sub, pv, suf):
        if pv:
            items.append((layer, x + y, sub, x, y, pv[0], pv[1], suf))

    def floor_key(x, y):
        """The floor piece key under a cell, or None (thin walls only need floor on the side the camera sees)."""
        ch = at(x, y)
        if ch in WALLISH and ch not in "DA" and not any(at(x + dx, y + dy) in ".RXP" + "abcdefghijkl"
                                                        for dx, dy in ((1, 0), (0, 1), (1, 1))):
            return None
        return roles[ch] if ch in "RX" else roles["."]

    main = roles["."]
    atlas_vs = [v for v in variants(main) if layout(v[0]) == "atlas"]
    singles = [v for v in variants(main) if layout(v[0]) != "atlas"]
    mp = next((p for p in spec["pieces"] if p.get("method") == "master" and p.get("board", p["id"]) == main), None)
    plan = plan_floor_blocks([(x, y) for y in range(rows) for x in range(cols) if main and floor_key(x, y) == main],
                             [1] + [int(v[0].get("atlas", 4)) for v in atlas_vs], _fnv_str(main or ""),
                             {int(k): v for k, v in family_settings(mp)["weights"].items()} if mp else None) if atlas_vs else {}

    for y in range(rows):
        for x in range(cols):
            ch = at(x, y)
            fkey = floor_key(x, y)
            fv = pick(fkey, x, y) if fkey else None
            if fv and (x, y) in plan:  # mixed atlas sizes: this cell's tile of its block (preview axes: i = x, j = y)
                bx, by, n = plan[(x, y)]
                same = [v for v in atlas_vs if int(v[0].get("atlas", 4)) == n]
                if n > 1:
                    add(0, x, y, 0, same[(bx * 7 + by * 13) % len(same)], f"i{x - bx}j{y - by}")
                    fv = None
                elif singles:
                    add(0, x, y, 0, singles[(x * 7 + y * 13) % len(singles)], "")
                    fv = None
            if fv:
                p = fv[0]
                if layout(p) == "atlas":
                    N = int(p.get("atlas", 4))
                    fv = [v for v in variants(fkey) if layout(v[0]) == "atlas"][0]
                    add(0, x, y, 0, fv, f"i{x % N}j{y % N}")
                else:
                    add(0, x, y, 0, fv, "")
            c = (x, y)
            if ch in WALLISH:
                n = nb[c]
                straight_x = (n["+x"] or n["-x"]) and not (n["+y"] or n["-y"])
                straight_y = (n["+y"] or n["-y"]) and not (n["+x"] or n["-x"])
                m = "" if straight_x or not straight_y else "m"
                if ch in "DA" and roles[ch]:
                    add(1, x, y, 2, pick(roles[ch], x, y), m)
                    continue
                decor = None
                if ch == "T" and roles["decor"] and (straight_x or straight_y):
                    decor = pick(roles["decor"][(x + y) % len(roles["decor"])], x, y)
                for axis, run, mark in (("x", xs, ""), ("y", ys, "m")):
                    if c not in run:
                        continue
                    L, k, start = run[c]
                    pv = decor or pick(roles["W"], (c[1] if axis == "x" else c[0]), start,
                                       lambda p, L=L: int(p.get("length", 1)) == L)
                    if decor:
                        k = 0
                    if not pv:
                        continue
                    if n[f"-{axis}"]:
                        add(1, x, y, 0, pv, f"k{k}h0{mark}")
                    if n[f"+{axis}"]:
                        add(1, x, y, 3, pv, f"k{k}h1{mark}")
                if not ((n["+x"] and n["-x"] and not (n["+y"] or n["-y"])) or
                        (n["+y"] and n["-y"] and not (n["+x"] or n["-x"]))):
                    add(1, x, y, 1, pick(roles["post"], x, y), "")
            elif ch == "P" or ch.islower():
                add(1, x, y, 1, pick(roles[ch], x, y), "")
    draws = []
    for layer, depth, sub, x, y, p, rel, suf in sorted(items, key=lambda t: (t[0], t[1], t[2])):
        im, piv = sprite(p, rel, suf)
        if im is None:
            continue
        sx, sy = ox + (x - y) * tile / 2, oy + (x + y) * tile / 4
        draws.append((p["id"], rel, suf, int(round(sx - piv[0] * im.width)), int(round(sy - (1 - piv[1]) * im.height)), im))
    if as_layout:
        return {"w": W, "h": H, "ox": ox, "oy": oy, "tile": tile, "pixel": bool(spec.get("pixel")), "W": cols, "H": rows,
                "items": [{"piece": d[0], "rel": d[1], "suf": d[2], "x": d[3], "y": d[4]} for d in draws],
                "blocks": sorted(set(plan.values())), "walls": [[x, y] for (x, y) in sorted(wallish)]}
    for d in draws:
        canvas.alpha_composite(d[5], (d[3], d[4]))
    out = canvas.convert("RGB")
    return out.resize((out.width * scale, out.height * scale), Image.NEAREST)
