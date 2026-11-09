"""Archive, orphan restore and locking in the Dungeon Forge (hdart/tools/dungeon), on a temp copy of the specs. Run: uv run python tests/test_dungeon_archive.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, os, shutil, tempfile, threading
from pathlib import Path
import numpy as np
from PIL import Image
from hdart.tools.dungeon import sets as dsets
from hdart.core import config

tmp = Path(tempfile.mkdtemp())
os.environ["HD_DATA"] = str(tmp)   # sprites/dungeon and archive in a temp folder; the sets are the fixture copy

def fake(name, pid, n):
    out = dsets.set_dir(name) / "candidates" / pid; out.mkdir(parents=True, exist_ok=True)
    s = dsets.state(name); ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
    for k in range(n):
        f = out / f"s{k}_1.png"; Image.new("RGB", (4, 4)).save(f); np.save(f.with_suffix(".npy"), np.ones((4, 4), bool))
        rel = f"candidates/{pid}/s{k}_1.png"; ps["candidates"].append(rel); ps.setdefault("meta", {})[rel] = {"seed": k}
    ps["prompt"] = "mossy test"; dsets.save_state(name, s)

dsets.new_set("zz", biome="test")
pid = "tile.zz_floor"; fake("zz", pid, 4)
dsets.approve("zz", pid, f"candidates/{pid}/s0_1.png")
dsets.discard("zz", pid, f"candidates/{pid}/s1_1.png")
assert (config.archive_dir() / "dungeon/zz/candidates" / pid / "s1_1.png").exists() and (config.archive_dir() / "dungeon/zz/candidates" / pid / "s1_1.npy").exists()
ps, n = dsets.archive_unapproved("zz", pid); assert n == 2 and ps["candidates"] == [f"candidates/{pid}/s0_1.png"], ps
dsets.delete_piece("zz", pid); assert pid not in dsets.state("zz")["pieces"]; assert not any(p["id"] == pid for p in dsets.load_set("zz")["pieces"])
assert len(list((config.archive_dir() / "dungeon/zz/candidates" / pid).glob("*.png"))) == 4
# orphan: images whose piece vanished from the spec (the floor_moss case)
fake("zz", "tile.zz_floor_moss", 2)
assert dsets.orphans("zz") == {"tile.zz_floor_moss": {"count": 2, "prompt": "mossy test"}}, dsets.orphans("zz")
spec = dsets.restore_piece("zz", "tile.zz_floor_moss"); p = dsets.piece_of(spec, "tile.zz_floor_moss")
assert p["prompt"] == "mossy test" and p["kind"] == "tile" and dsets.orphans("zz") == {}, p
# rename into an existing id must fail without moving state
try: dsets.upsert_piece("zz", {**p, "id": "tile.zz_rubble"}, old_id="tile.zz_floor_moss"); raise SystemExit("no error")
except ValueError: pass
assert "tile.zz_floor_moss" in dsets.state("zz")["pieces"]
# lost-update: a generate-style merge while approvals happen concurrently
def bump(i): dsets.approve("zz", "tile.zz_floor_moss", f"candidates/tile.zz_floor_moss/s{i % 2}_1.png", on=bool(i % 3))
ts = [threading.Thread(target=bump, args=(i,)) for i in range(40)]; [t.start() for t in ts]; [t.join() for t in ts]
json.loads((dsets.set_dir("zz") / "state.json").read_text())
assert len(list((config.archive_dir() / "history/zz").glob("*.json"))) == 2
# delete a family: master + its repaints + built walls leave the spec, folders go to archive/families/, retired walls return
dsets.upsert_piece("zz", {"id": "tile.zz_fm", "kind": "tile", "shape": "floor", "method": "master", "variants": 1})
dsets.upsert_piece("zz", {"id": "tile.zz_fm_2x2", "kind": "tile", "shape": "floor", "method": "repaint", "from": "tile.zz_fm", "atlas": 2})
dsets.upsert_piece("zz", {"id": "tile.zz_other", "kind": "tile", "shape": "floor", "method": "repaint", "from": "tile.zz_x"})
fake("zz", "tile.zz_fm", 2); fake("zz", "tile.zz_fm_2x2", 1); fake("zz", "tile.zz_other", 1)
assert dsets.family_members(dsets.load_set("zz"), "tile.zz_fm") == ["tile.zz_fm", "tile.zz_fm_2x2"]
dst = dsets.archive_family("zz", "tile.zz_fm"); ids = {p["id"] for p in dsets.load_set("zz")["pieces"]}; st = dsets.state("zz")["pieces"]
assert "tile.zz_fm" not in ids and "tile.zz_fm_2x2" not in ids and "tile.zz_other" in ids and "tile.zz_fm" not in st and "tile.zz_other" in st
assert (dst / "candidates/tile.zz_fm/s1_1.png").exists() and (dst / "candidates/tile.zz_fm_2x2/s0_1.npy").exists()
assert not (dsets.set_dir("zz") / "candidates/tile.zz_fm").exists() and (dsets.set_dir("zz") / "candidates/tile.zz_other").exists()
fam = json.loads((dst / "family.json").read_text()); assert len(fam["pieces"]) == 2 and set(fam["state"]) == {"tile.zz_fm", "tile.zz_fm_2x2"}
dsets.upsert_piece("zz", {"id": "tile.zz_wm", "kind": "elevation", "shape": "elevation", "method": "wall_master", "variants": 1, "family": {"boards": {"wall": "tile.zz_w", "post": "tile.zz_p", "door": "tile.zz_d"}}})
dsets.upsert_piece("zz", {"id": "tile.zz_w", "kind": "tile", "shape": "run", "length": 1, "retired": True})
dsets.upsert_piece("zz", {"id": "tile.zz_wm_run1", "kind": "tile", "shape": "run", "length": 1, "board": "tile.zz_w", "from": "tile.zz_wm", "built_from": "tile.zz_wm"})
dsets.archive_family("zz", "tile.zz_wm"); sp = dsets.load_set("zz")
assert not dsets.piece_of(sp, "tile.zz_w").get("retired") and not any(p["id"].startswith("tile.zz_wm") for p in sp["pieces"])
try: dsets.archive_family("zz", "tile.zz_other"); raise SystemExit("no error")
except ValueError: pass
dst = dsets.archive_set("zz"); assert (dst / "spec.json").exists() and (dst / "sprites/state.json").exists() and "zz" not in dsets.list_sets()
try: piece = dsets.piece_of({"id": "x", "pieces": []}, "nope"); raise SystemExit("no error")
except KeyError as e: assert "not in set x" in str(e)
shutil.rmtree(tmp); print("all archive tests passed")
