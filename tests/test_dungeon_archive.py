"""Archive, orphan restore and locking in hdart.dungeon, on a temp copy of the specs. Run: uv run python tests/test_dungeon_archive.py"""
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
dst = d.archive_set("zz"); assert (dst / "spec.json").exists() and (dst / "sprites/state.json").exists() and "zz" not in d.list_sets()
try: piece = d.piece_of({"id": "x", "pieces": []}, "nope"); raise SystemExit("no error")
except KeyError as e: assert "not in set x" in str(e)
shutil.rmtree(tmp); print("all archive tests passed")
