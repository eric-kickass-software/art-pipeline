"""hdart backup: what's copied (state, layers, approved dungeon candidates + sidecars, specs) and what isn't (scratch,
unapproved candidates), and that it refuses a folder inside the tool or its data. Run: uv run python tests/test_backup.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, shutil, tempfile
from pathlib import Path
from hdart import backup, config

tmp = Path(tempfile.mkdtemp())
data, dest = tmp / "data", tmp / "backups"


def put(rel, text="x"):
    f = data / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text); return f


put("sprites/ai/workshop.json", "{}")
put("sprites/ai/paint/human.male/s1_1/parts/front/head.png")
put("sprites/ai/paint/_tmp/s1_front_init.png")
put("sprites/ai/candidates/tmp/out.png")
put("sprites/dungeon/_meshes/wall.glb")
put("sprites/dungeon/tmp/x.png")
put("sprites/dungeon/crypt/state.json", json.dumps({"pieces": {"tile.floor": {
    "candidates": ["candidates/tile.floor/s1_1.png", "candidates/tile.floor/s2_2.png"],
    "approved": ["candidates/tile.floor/s1_1.png"]}, "wall.x": {"master": "candidates/wall.x/m_3.png"}}}))
for f in ("tile.floor/s1_1.png", "tile.floor/s1_1.npy", "tile.floor/s2_2.png", "tile.floor/s2_2.npy", "wall.x/m_3.png"):
    put(f"sprites/dungeon/crypt/candidates/{f}")
put("sprites/dungeon/broken/state.json", "not json")
put("sprites/dungeon/broken/candidates/p/s9_9.png")
put("archive/old.png")

got = {str(r).replace("\\", "/") for _, r in backup.plan(data)}
sprites = {g for g in got if g.startswith("sprites/")}
assert sprites == {"sprites/ai/workshop.json", "sprites/ai/paint/human.male/s1_1/parts/front/head.png",
                   "sprites/dungeon/crypt/state.json", "sprites/dungeon/crypt/candidates/tile.floor/s1_1.png",
                   "sprites/dungeon/crypt/candidates/tile.floor/s1_1.npy", "sprites/dungeon/crypt/candidates/wall.x/m_3.png",
                   "sprites/dungeon/broken/state.json", "sprites/dungeon/broken/candidates/p/s9_9.png"}, sprites
assert "specs/items.json" in got and not any(g.startswith("archive/") for g in got)

r = backup.backup(dest, data)
d = Path(r["dest"])
assert d.parent == dest.resolve() and (d / "sprites/dungeon/crypt/candidates/tile.floor/s1_1.npy").exists()
m = json.loads((d / "manifest.json").read_text())
assert m["files"] == r["files"] == len(got) and m["bytes"] == r["bytes"] and m["data"] == str(data.resolve())
assert backup.backup(dest, data, dry_run=True)["files"] == r["files"] and len(list(dest.iterdir())) == 1

for bad in (data / "bk", config.ROOT / "work" / "bk"):
    try:
        backup.backup(bad, data, dry_run=True); raise SystemExit("no error")
    except ValueError as e:
        assert "inside" in str(e)
shutil.rmtree(tmp)
print("ok")
