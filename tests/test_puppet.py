"""Puppet template (hdart.puppet) on the real approved human.male pair (skipped if absent: the art-based skeleton detection
needs real mannequin art): runs end to end, writes the files, records state, and refuses without an approved front and
back. Run: uv run python tests/test_puppet.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, shutil, tempfile
from pathlib import Path
import hdart.ai_kit as ak
import hdart.workshop as ws

REAL = Path(__file__).resolve().parents[1] / "sprites" / "ai"
if not (REAL / "workshop.json").exists():   # worktree / fresh clone: the main checkout's sprites, if there
    REAL = Path("P:/Tools/art-pipeline/sprites/ai")
real = json.loads((REAL / "workshop.json").read_text()) if (REAL / "workshop.json").exists() else {}
src = {f: real.get("builds", {}).get("human.male", {}).get(f, {}).get("approved") for f in ("front", "back")}
if not (src["front"] and src["back"]):
    print("skip: needs an approved human.male front + back")
    raise SystemExit(0)

tmp = Path(tempfile.mkdtemp())
ak.AI_DIR = tmp / "ai"; ws.CAND = ak.AI_DIR / "candidates"; ws.STATE = ak.AI_DIR / "workshop.json"
import hdart.puppet as pp

(ws.CAND / "test" / "male").mkdir(parents=True)
for f in ("front", "back"):
    shutil.copy(REAL / src[f], ws.CAND / "test" / "male" / f"{f}.png")
ws.save_state({"builds": {"test.male": {"race": "test", "sex": "male",
               "front": {"approved": "candidates/test/male/front.png"}, "back": {}}}, "parts": {}})
try:
    pp.template("test.male"); raise SystemExit("no error")
except ValueError as e:
    assert "Approve both" in str(e)
s = ws.load_state(); s["builds"]["test.male"]["back"]["approved"] = "candidates/test/male/back.png"; ws.save_state(s)
pp.template("test.male", frames=4)
out = ak.AI_DIR / "puppet" / "test.male"
for name in ("template.json", "skeleton_front.png", "pieces_back.png", "walk_front.gif", "walk_4dir_64.gif"):
    assert (out / name).exists(), name
meta = json.loads((out / "template.json").read_text())
assert len(meta["views"]["front"]["order"]) == 14 and "torso" in meta["views"]["front"]["order"]
assert len(list((out / "front").glob("*.png"))) == 14
p = ws.load_state()["builds"]["test.male"]["puppet"]
assert p["approved"] is False and p["from"]["back"] == "candidates/test/male/back.png"
assert pp.approve("test.male")["approved"] is True
# hand-placed joints win and survive a rebuild
j = meta["views"]["front"]["joints"]["legB"]
moved = {"legB": {"pivot": j["pivot"], "mid": [j["mid"][0], j["mid"][1] - 12], "end": j["end"]}}
pp.set_joints("test.male", "front", moved)
pp.template("test.male", frames=2)
meta2 = json.loads((out / "template.json").read_text())
assert abs(meta2["views"]["front"]["joints"]["legB"]["mid"][1] - (j["mid"][1] - 12)) < 0.6, meta2["views"]["front"]["joints"]["legB"]
assert ws.load_state()["builds"]["test.male"]["puppet"]["joints"]["front"]["legB"]["mid"][1] == j["mid"][1] - 12
pp.set_joints("test.male", "front", None)
assert "front" not in ws.load_state()["builds"]["test.male"]["puppet"]["joints"]
shutil.rmtree(tmp)
print("ok")
