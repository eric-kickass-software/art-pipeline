"""Archive, orphan restore and locking in hdart.dungeon, on a temp copy of the specs. Run: uv run python tests/test_dungeon_archive.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, shutil, tempfile, threading
from pathlib import Path
import numpy as np
from PIL import Image
import hdart.dungeon as d

tmp = Path(tempfile.mkdtemp())
shutil.copytree(d.SETS, tmp / "sets"); d.SETS = tmp / "sets"; d.DUN = tmp / "dun"; d.ARCHIVE = tmp / "archive"

def fake(name, pid, n):
    out = d.set_dir(name) / "candidates" / pid; out.mkdir(parents=True, exist_ok=True)
    s = d.state(name); ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
    for k in range(n):
        f = out / f"s{k}_1.png"; Image.new("RGB", (4, 4)).save(f); np.save(f.with_suffix(".npy"), np.ones((4, 4), bool))
        rel = f"candidates/{pid}/s{k}_1.png"; ps["candidates"].append(rel); ps.setdefault("meta", {})[rel] = {"seed": k}
    ps["prompt"] = "mossy test"; d.save_state(name, s)

d.new_set("zz", biome="test")
pid = "tile.zz_floor"; fake("zz", pid, 4)
d.approve("zz", pid, f"candidates/{pid}/s0_1.png")
d.discard("zz", pid, f"candidates/{pid}/s1_1.png")
assert (d.ARCHIVE / "dungeon/zz/candidates" / pid / "s1_1.png").exists() and (d.ARCHIVE / "dungeon/zz/candidates" / pid / "s1_1.npy").exists()
ps, n = d.archive_unapproved("zz", pid); assert n == 2 and ps["candidates"] == [f"candidates/{pid}/s0_1.png"], ps
d.delete_piece("zz", pid); assert pid not in d.state("zz")["pieces"]; assert not any(p["id"] == pid for p in d.load_set("zz")["pieces"])
assert len(list((d.ARCHIVE / "dungeon/zz/candidates" / pid).glob("*.png"))) == 4
# orphan: images whose piece vanished from the spec (the floor_moss case)
fake("zz", "tile.zz_floor_moss", 2)
assert d.orphans("zz") == {"tile.zz_floor_moss": {"count": 2, "prompt": "mossy test"}}, d.orphans("zz")
spec = d.restore_piece("zz", "tile.zz_floor_moss"); p = d.piece_of(spec, "tile.zz_floor_moss")
assert p["prompt"] == "mossy test" and p["kind"] == "tile" and d.orphans("zz") == {}, p
# rename into an existing id must fail without moving state
try: d.upsert_piece("zz", {**p, "id": "tile.zz_rubble"}, old_id="tile.zz_floor_moss"); raise SystemExit("no error")
except ValueError: pass
assert "tile.zz_floor_moss" in d.state("zz")["pieces"]
# lost-update: a generate-style merge while approvals happen concurrently
def bump(i): d.approve("zz", "tile.zz_floor_moss", f"candidates/tile.zz_floor_moss/s{i % 2}_1.png", on=bool(i % 3))
ts = [threading.Thread(target=bump, args=(i,)) for i in range(40)]; [t.start() for t in ts]; [t.join() for t in ts]
json.loads((d.set_dir("zz") / "state.json").read_text())
assert len(list((d.ARCHIVE / "history/zz").glob("*.json"))) == 2
# delete a family: master + its repaints + built walls leave the spec, folders go to archive/families/, retired walls return
d.upsert_piece("zz", {"id": "tile.zz_fm", "kind": "tile", "shape": "floor", "method": "master", "variants": 1})
d.upsert_piece("zz", {"id": "tile.zz_fm_2x2", "kind": "tile", "shape": "floor", "method": "repaint", "from": "tile.zz_fm", "atlas": 2})
d.upsert_piece("zz", {"id": "tile.zz_other", "kind": "tile", "shape": "floor", "method": "repaint", "from": "tile.zz_x"})
fake("zz", "tile.zz_fm", 2); fake("zz", "tile.zz_fm_2x2", 1); fake("zz", "tile.zz_other", 1)
assert d.family_members(d.load_set("zz"), "tile.zz_fm") == ["tile.zz_fm", "tile.zz_fm_2x2"]
dst = d.archive_family("zz", "tile.zz_fm"); ids = {p["id"] for p in d.load_set("zz")["pieces"]}; st = d.state("zz")["pieces"]
assert "tile.zz_fm" not in ids and "tile.zz_fm_2x2" not in ids and "tile.zz_other" in ids and "tile.zz_fm" not in st and "tile.zz_other" in st
assert (dst / "candidates/tile.zz_fm/s1_1.png").exists() and (dst / "candidates/tile.zz_fm_2x2/s0_1.npy").exists()
assert not (d.set_dir("zz") / "candidates/tile.zz_fm").exists() and (d.set_dir("zz") / "candidates/tile.zz_other").exists()
fam = json.loads((dst / "family.json").read_text()); assert len(fam["pieces"]) == 2 and set(fam["state"]) == {"tile.zz_fm", "tile.zz_fm_2x2"}
d.upsert_piece("zz", {"id": "tile.zz_wm", "kind": "elevation", "shape": "elevation", "method": "wall_master", "variants": 1, "family": {"boards": {"wall": "tile.zz_w", "post": "tile.zz_p", "door": "tile.zz_d"}}})
d.upsert_piece("zz", {"id": "tile.zz_w", "kind": "tile", "shape": "run", "length": 1, "retired": True})
d.upsert_piece("zz", {"id": "tile.zz_wm_run1", "kind": "tile", "shape": "run", "length": 1, "board": "tile.zz_w", "from": "tile.zz_wm", "built_from": "tile.zz_wm"})
d.archive_family("zz", "tile.zz_wm"); sp = d.load_set("zz")
assert not d.piece_of(sp, "tile.zz_w").get("retired") and not any(p["id"].startswith("tile.zz_wm") for p in sp["pieces"])
try: d.archive_family("zz", "tile.zz_other"); raise SystemExit("no error")
except ValueError: pass
dst = d.archive_set("zz"); assert (dst / "spec.json").exists() and (dst / "sprites/state.json").exists() and "zz" not in d.list_sets()
try: piece = d.piece_of({"id": "x", "pieces": []}, "nope"); raise SystemExit("no error")
except KeyError as e: assert "not in set x" in str(e)
shutil.rmtree(tmp); print("all archive tests passed")
