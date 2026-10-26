"""Item export (hdart.item_export, A2) on the fake ComfyUI into a temp game project (HD_PROJECT) with a small contract:
the game's vocabulary (R-001), pieces -> game items (R-002), the pack with its layers and index maps, Art/index.json,
ramps.json, intake files for new ids only (R-003), and `export --check`.
Run: uv run python tests/test_item_export.py"""
import json, os, shutil, tempfile
from pathlib import Path
import numpy as np
from PIL import Image
import hdart.ai_kit as ak
import hdart.workshop as ws
import hdart.puppet_paint as ppt
import hdart.puppet_items as pi
import hdart.item_export as ie
from hdart import contract
from hdart.fake_comfy import FakeComfy, info

tmp = Path(tempfile.mkdtemp())
ak.AI_DIR = tmp / "ai"; ws.CAND = ak.AI_DIR / "candidates"; ws.STATE = ak.AI_DIR / "workshop.json"; ws.ARCHIVE = tmp / "archive"
shutil.copy(pi.SPEC, tmp / "items.json"); pi.SPEC = tmp / "items.json"
game = tmp / "game"; os.environ["HD_PROJECT"] = str(game)

# the game: a contract (vocabulary, id rule, revision) and one existing item (the sword); the trousers are new
SLOTS = [{"key": "head", "artSlots": ["head"]}, {"key": "body", "artSlots": ["shirt", "chest", "shoulders", "skirt"]},
         {"key": "legs", "artSlots": ["legs"]}, {"key": "feet", "artSlots": ["feet"]}, {"key": "hands", "artSlots": ["gloves"]},
         {"key": "back", "artSlots": ["back"]}, {"key": "main", "artSlots": ["mainhand"]}, {"key": "off", "artSlots": ["offhand"]}]
REQ = {"schemaVersion": 1, "revision": "abc123", "ids": {"item": "^item\\.[a-z][a-z0-9_]*$"}, "slots": SLOTS,
       "weaponTypes": [{"key": "sword", "heldAs": "weapon", "slot": "main", "hands": 1},
                       {"key": "shield", "heldAs": "shield", "slot": "off", "hands": 1}],
       "itemKinds": ["weapon", "armor"], "intake": {"required": ["schemaVersion", "id", "name", "kind", "slot"]},
       "wanted": {"itemArt": [{"id": "item.iron_sword"}]}}
(game / "ArtContract" / "intake" / "items").mkdir(parents=True)
(game / "ArtContract" / "requirements.json").write_text(json.dumps(REQ))
(game / "Assets/StreamingAssets/Content/core/items").mkdir(parents=True)
(game / "Assets/StreamingAssets/Content/core/items/weapons.json").write_text(json.dumps(
    [{"id": "item.iron_sword", "name": "Iron Sword", "kind": "weapon", "slot": "main", "weaponType": "sword",
      "visual": {"ramps": {"metal": "metal.steel", "leather": "leather.dark"}}}]))

# vocabulary: art slots in game order, slot_of, id rule
v = contract.vocab()
assert v["found"] and v["revision"] == "abc123" and contract.art_slots(v)[:5] == ["head", "shirt", "chest", "shoulders", "skirt"]
assert contract.slot_of("chest", v) == "body" and contract.slot_of("torso", v) is None
assert contract.id_problem("item.iron_breastplate", v) is None and contract.id_problem("item.Bad-Name", v)
assert set(contract.game_items()) == {"item.iron_sword"}

# the Workshop takes the game's art slots and id rule; focus is a held mode
for bad in ({"slot": "torso"}, {"held_as": "lantern"}, {"item": "item.Iron"}):
    try:
        pi.save_item("item.iron_sword", bad); raise AssertionError(bad)
    except ValueError:
        pass
try:
    pi.save_item("item.New-Thing", {"slot": "shirt", "materials": ["cloth"]}); raise AssertionError("bad id accepted")
except ValueError:
    pass
pi.save_item("item.bone_focus", {"slot": "offhand", "kind": "held", "held_as": "focus", "materials": ["cloth"], "text": "a skull"})
assert "small object held in one hand" in pi.prompts("human", "human.male", "item.bone_focus")["held"]

# game items from pieces: breastplate + pauldrons make item.iron_breastplate; existing vs new
gids = ie.game_ids()
assert gids["item.iron_breastplate"] == ["item.pauldrons", "item.breastplate"] and gids["item.iron_sword"] == ["item.iron_sword"]
sw, tr = ie.info("item.iron_sword"), ie.info("item.cloth_trousers")
assert sw["exists"] and sw["slot"] == "main" and sw["weaponType"] == "sword" and sw["hands"] == 1 and sw["materials"] == {"leather": "leather.dark", "metal": "metal.steel"}
assert not tr["exists"] and tr["slot"] == "legs" and tr["kind"] == "armor" and tr["name"] == "Cloth Trousers" and not tr["problems"]
assert not ie.info("item.kite_shield")["problems"] and ie.info("item.kite_shield")["heldAs"] == "shield"

# art: a fake-painted puppet, the trousers (fitted) and the sword (held), approved
KEY = {m: np.array([int(e["key"][i:i + 2], 16) for i in (1, 3, 5)], float) for m, e in pi.spec()["materials"].items()}
paint_key = {"m": None}


def shaded(key, h, w):
    return np.clip(np.broadcast_to(key * np.linspace(1.0, 0.45, h)[:, None, None], (h, w, 3)), 0, 255).astype(np.uint8)


def respond(nodes, uploads):
    i = info(nodes, uploads)
    if i["init"] is None:   # the held sword on white, background removed
        im = np.zeros((768, 768, 4), np.uint8)
        im[120:560, 360:408, :3] = shaded(KEY["metal"], 440, 48); im[560:660, 368:400, :3] = shaded(KEY["leather"], 100, 32)
        im[120:660, 360:408, 3] = np.where(im[120:660, 360:408, :3].sum(-1) > 0, 255, 0)
        return [Image.fromarray(im, "RGBA")]
    a = np.asarray(i["init"].convert("RGB")).copy()
    if i["mask"] is not None and paint_key["m"]:
        a[i["mask"]] = shaded(KEY[paint_key["m"]], *a.shape[:2])[i["mask"]]
    return [Image.fromarray(a)]


with FakeComfy(respond):
    c = ppt.paint("human.male", n=1, seed=1)[0]; ppt.approve("human.male", c)
    paint_key["m"] = "cloth"; t = pi.generate("human", "item.cloth_trousers", seed=2)
    paint_key["m"] = None; s = pi.generate("human", "item.iron_sword", seed=3)
pi.approve("human", "item.cloth_trousers", t); pi.approve("human", "item.iron_sword", s)

r = ie.export(size=256)
assert set(r["exported"]) == {"item.cloth_trousers", "item.iron_sword"}, r
assert "item.iron_breastplate" in r["skipped"] and "item.pauldrons" in r["skipped"]["item.iron_breastplate"]   # nothing approved
art = game / "Assets/StreamingAssets/Art"

# the trousers' pack: fitted layers per build and view on leg/torso parts, coloured art + cloth-channel index maps
d = art / "Items" / "item.cloth_trousers"; m = json.loads((d / "item.json").read_text())
assert m["schemaVersion"] == 1 and m["kind"] == "item" and m["id"] == "item.cloth_trousers" and m["slot"] == "legs" and m["canvas"] == 256
assert m["materials"] == {"cloth": {"channel": 4, "group": "cloth", "ramp": "cloth.brown"}} and m["held"] == []
assert m["source"]["candidates"] == {"item.cloth_trousers": {"human.male": t}} and m["source"]["requirementsRevision"] == "abc123"
assert list(m["fits"]) == ["human.male"] and set(m["fits"]["human.male"]) == {"front", "back"}
for view, lays in m["fits"]["human.male"].items():
    parts = {L["part"] for L in lays}
    assert "torso" in parts and any(p.startswith("leg") for p in parts) and all(L["place"] == "over" for L in lays), (view, parts)
    for L in lays:
        with Image.open(d / L["file"]) as im, Image.open(d / L["file"].replace(".png", ".idx.png")) as ix:
            a, x = np.asarray(im), np.asarray(ix)
        assert im.size == tuple(L["rect"][2:]) and x.shape[:2] == a.shape[:2]
        on = x[..., 3] > 0
        assert set(np.unique(x[..., 1][on])) == {4} and set(np.unique(x[..., 0][on])) <= set(range(5)) | {254}
        assert (a[..., 3][on] > 0).all() and not (a[..., 3][~on] > 0).any()   # the art's shape is the index map's

# the sword's pack: one layer on the hand, drawn under it; the upright art kept; existing id -> no intake
d2 = art / "Items" / "item.iron_sword"; m2 = json.loads((d2 / "item.json").read_text())
assert m2["weaponType"] == "sword" and m2["heldAs"] == "weapon" and m2["hands"] == 1
for view in ("front", "back"):
    (L,) = m2["fits"]["human.male"][view]
    assert L["part"].endswith(".hand") and L["place"] == "under"
(h,) = m2["held"]
assert (d2 / h["file"]).exists() and h["heldAs"] == "weapon" and h["size"] == list(Image.open(d2 / h["file"]).size)

# intake only for the new item, in the contract's shape
assert sorted(f.name for f in (game / "ArtContract/intake/items").iterdir()) == ["item.cloth_trousers.json"]
row = json.loads((game / "ArtContract/intake/items/item.cloth_trousers.json").read_text())
assert {k: row[k] for k in ("schemaVersion", "id", "name", "kind", "slot")} == {"schemaVersion": 1, "id": "item.cloth_trousers",
                                                                             "name": "Cloth Trousers", "kind": "armor", "slot": "legs"}
assert row["materials"] == {"cloth": "cloth.brown"} and row["source"]["candidate"] == t and row["source"]["requirementsRevision"] == "abc123"
assert "weaponType" not in row

# index.json lists every pack (items, plus the puppet once exported); ramps.json has ids
idx = json.loads((art / "index.json").read_text())
assert idx["packs"] == {"item.cloth_trousers": {"kind": "item", "path": "Items/item.cloth_trousers", "schemaVersion": 1},
                        "item.iron_sword": {"kind": "item", "path": "Items/item.iron_sword", "schemaVersion": 1}}
ramps = json.loads((art / "ramps.json").read_text())
assert "metal.iron" in [x["id"] for x in ramps["groups"]["metal"]] and all(len(x["colors"]) == 5 for x in ramps["groups"]["cloth"])
assert json.loads((art / "channels.json").read_text())["materials"]["accent"] == {"channel": 7, "group": "accent"}

# --check: clean now; finds a missing index map, a stray pack, a bad intake row
assert ie.check() == [], ie.check()
import hdart.unity_export as ue
ue.export("human", size=256)
assert "race.human" in json.loads((art / "index.json").read_text())["packs"] and ie.check() == [], ie.check()
(d / m["fits"]["human.male"]["front"][0]["file"].replace(".png", ".idx.png")).unlink()
(art / "Items" / "item.stray").mkdir(); (art / "Items" / "item.stray" / "item.json").write_text("{}")
(game / "ArtContract/intake/items/item.bad.json").write_text(json.dumps({"schemaVersion": 1, "id": "item.bad", "kind": "weapon", "slot": "pocket"}))
p = ie.check()
assert any("is missing" in x for x in p) and any("item.stray is on disk but not listed" in x for x in p)
assert any("item.bad.json: missing name" in x for x in p) and any("slot 'pocket'" in x for x in p), p

# a re-export rebuilds the pack folder whole
(d / "stale.png").write_bytes(b"x")
ie.export(["item.cloth_trousers"], size=256)
assert not (d / "stale.png").exists() and ie.check() != []   # the stray pack and bad intake are still there

os.environ.pop("HD_PROJECT"); shutil.rmtree(tmp, ignore_errors=True)
print("ok")
