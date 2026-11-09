"""Game art packs, shared by every export (puppets, items, dungeon sets): the pack table, the shared files and the
image helpers the exporters write layers with.

    Art/index.json                  every pack on disk (write_index; every export rewrites it)
    Art/ramps.json, channels.json   every ramp group (ids `<group>.<name>`) and the channel numbers (write_shared)

Index maps (`*.idx.png`): R = cel level (OUTLINE = 254), G = colour channel (CHANNELS), A = mask.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import config, palette, store, item_workshop as iw
from .palette import ramp_from_base

CHANNELS = {"1": "skin", "2": "hair", "3": "eyes", "4": "cloth", "5": "leather", "6": "metal", "7": "accent"}
OUTLINE = 254
# pack kind -> (folder under Art/, manifest, key prefix); the same table as the game's ArtCatalog.Kinds
KINDS = {"item": ("Items", "item.json", ""), "puppet": ("Puppets", "puppet.json", "race."),
         "dungeonSet": ("Dungeon", "set.json", "dungeonset.")}


def art_root() -> Path:
    """The active project's Art/ folder (every pack, index.json, ramps.json, channels.json)."""
    return config.project().export("art")


def hex_colour(c) -> str:
    """An RGB(A) colour as "#rrggbb"."""
    return "#%02x%02x%02x" % tuple(int(v) for v in c[:3])


def small(a: np.ndarray, size: int, nearest: bool = False) -> np.ndarray:
    """An image array resized to size x size (LANCZOS, or NEAREST for index maps)."""
    return np.array(Image.fromarray(a).resize((size, size), Image.NEAREST if nearest else Image.LANCZOS))


def crop_save(art: np.ndarray, idx: np.ndarray, path: Path, min_alpha: int = 8) -> list | None:
    """Save art + index map cropped to the art's coverage; returns the rect [x, y, w, h], or None when empty."""
    ys, xs = np.nonzero(art[..., 3] >= min_alpha)
    if not len(ys):
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(art[y0:y1, x0:x1])).save(path.parent / (path.name + ".png"))   # names have dots (armA.hand)
    Image.fromarray(np.ascontiguousarray(idx[y0:y1, x0:x1])).save(path.parent / (path.name + ".idx.png"))
    return [x0, y0, x1 - x0, y1 - y0]


def write_shared() -> None:
    """Art/ramps.json and Art/channels.json: every ramp group (ids <group>.<name>) and the channel numbers."""
    pal = palette.palettes_doc(); size = pal.get("rampSize", 5); root = art_root()
    groups = {g: [{"id": f"{g}.{n}", "name": n, "colors": [c if isinstance(c, str) else hex_colour(c) for c in (e.get("colors") or ramp_from_base(e["base"], size))]}
                  for n, e in grp.items() if e.get("colors") or e.get("base")] for g, grp in pal["groups"].items()}
    store.write_json(root / "ramps.json", {"schemaVersion": 1, "rampSize": size, "outline": pal.get("outline"), "groups": groups})
    mats = {m: {"channel": e["channel"], "group": e["group"]} for m, e in iw.spec()["materials"].items()}
    store.write_json(root / "channels.json", {"schemaVersion": 1, "channels": CHANNELS, "levels": size, "outline": OUTLINE,
                                              "materials": mats,
                                              "_doc": "Index maps: R = cel level 0..levels-1 (254 = outline), G = channel, A = mask. "
                                                      "Channel 0 keeps the painted colour."})


def write_index() -> int:
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
