"""The game's side of the art contract (R-001, R-002): what the active project accepts, read from its
`<contract>/requirements.json` (written by the game, never edited here) and its content JSON.

    vocab()          slots, art slots, weapon types, item kinds, the id rule and the file's revision; built-in defaults
                     when the project has no requirements.json (the tools still work without a game)
    art_slots()      the layer slots in game order (head, shirt, chest, …) and slot_of(art_slot) -> the game slot
    game_items()     {id: content row} of every item the game already has (existing ids get art, not intake)
    id_problem(id)   why a new item id breaks the game's rules, or None
"""
import json, re

from . import config

# Used when the project has no requirements.json (same shape as its "slots" and "weaponTypes").
DEFAULT_SLOTS = [{"key": "head", "artSlots": ["head"]}, {"key": "body", "artSlots": ["shirt", "chest", "shoulders", "skirt"]},
                 {"key": "legs", "artSlots": ["legs"]}, {"key": "feet", "artSlots": ["feet"]},
                 {"key": "hands", "artSlots": ["gloves"]}, {"key": "back", "artSlots": ["back"]},
                 {"key": "main", "artSlots": ["mainhand"]}, {"key": "off", "artSlots": ["offhand"]}]
ID_RULE = r"^item\.[a-z][a-z0-9_]*$"
HELD_AS = ("weapon", "shield", "focus")


def path():
    c = config.project().contract
    return c / "requirements.json" if c else None


def load():
    """requirements.json of the active project, or None."""
    f = path()
    return json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else None


def vocab():
    r = load() or {}
    return {"revision": r.get("revision"), "slots": r.get("slots") or DEFAULT_SLOTS, "weaponTypes": r.get("weaponTypes") or [],
            "itemKinds": r.get("itemKinds") or ["weapon", "armor"], "idRule": (r.get("ids") or {}).get("item") or ID_RULE,
            "idRules": (r.get("ids") or {}).get("rules") or [], "rarities": r.get("rarities") or [],
            "wanted": [w["id"] for w in (r.get("wanted") or {}).get("itemArt", [])], "found": bool(r)}


def art_slots(v=None):
    v = v or vocab()
    return [a for s in v["slots"] for a in s.get("artSlots", [])]


def slot_of(art_slot, v=None):
    """The game slot (head, body, main, …) an art slot belongs to, or None."""
    v = v or vocab()
    return next((s["key"] for s in v["slots"] if art_slot in s.get("artSlots", [])), None)


def weapon_type(key, v=None):
    v = v or vocab()
    return next((w for w in v["weaponTypes"] if w["key"] == key), None)


def game_items():
    """{id: row} of the items in the game's content (every items/*.json of every content pack)."""
    c = getattr(config.project(), "content", None); out = {}   # None in a Studio started before A2 (config not reloaded)
    if not c or not c.exists():
        return out
    for f in sorted(c.glob("*/items/*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for row in d if isinstance(d, list) else d.get("items", []) if isinstance(d, dict) else []:
            if isinstance(row, dict) and str(row.get("id", "")).startswith("item."):
                out[row["id"]] = row
    return out


def id_problem(item_id, v=None):
    v = v or vocab()
    if not re.match(v["idRule"], item_id or ""):
        return f"{item_id!r} doesn't match the game's id rule {v['idRule']} (item.<lowercase_words>)"
    if "#" in item_id:
        return "rolled ids (item.x#…) are never art keys"
    return None
