"""Race mannequin chain in hdart.workshop (ComfyUI stubbed). Run: uv run python tests/test_races.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import shutil, tempfile
from pathlib import Path
from PIL import Image
import hdart.ai_kit as ak
import hdart.workshop as ws

tmp = Path(tempfile.mkdtemp())
ak.AI_DIR = tmp / "ai"; ws.CAND = ak.AI_DIR / "candidates"; ws.STATE = ak.AI_DIR / "workshop.json"
shutil.copy(ws.RACES, tmp / "races.json"); ws.RACES = tmp / "races.json"
calls = []
class FakeComfy:
    @staticmethod
    def upload(p): calls.append(("upload", Path(p).name)); return Path(p).name
    @staticmethod
    def build_edit(name, prompt, **kw): calls.append(("edit", name, prompt)); return {"edit": name}
    @staticmethod
    def build_txt2img(prompt, w, h, **kw): calls.append(("txt2img", prompt)); return {"t2i": 1}
    @staticmethod
    def run(nodes, out_dir):
        Path(out_dir).mkdir(parents=True, exist_ok=True); p = Path(out_dir) / f"o{len(calls)}.png"
        Image.new("RGB", (1024, 1024), "white").save(p); return [p]
    @staticmethod
    def image(nodes): return Image.new("RGB", (1024, 1024), "white")
ws.comfy = FakeComfy

def err(f, *a):
    try: f(*a)
    except ValueError as e: return str(e)
    raise SystemExit("no error")

# order is enforced
assert "painted from the Template" in err(ws.gen_race, "human", "male", "front", 1)   # only the unisex has a mannequin
assert "approve the human unisex front first" in err(ws.gen_race, "elf", "unisex", "front", 1)
assert "front first" in err(ws.gen_race, "human", "unisex", "back", 1)

# unisex = txt2img with the A-pose and the race traits; result 512 px
c = ws.gen_race("human", "unisex", "front", 2, seed=1)
assert len(c) == 2 and calls[-1][0] == "txt2img" and "A-pose" in calls[-1][1] and "rounded human ears" in calls[-1][1]
assert "unclothed" in calls[-1][1] and "Adventurer" not in calls[-1][1] and "shorts" not in calls[-1][1]   # smooth base, no gear
assert Image.open(ak.AI_DIR / c[0]).size == (ak.BIG, ak.BIG)
ws.approve_race("human.unisex", "front", c[0])

# back = edit of the approved front; male and female have no mannequin any more (painted from the Template)
bk = ws.gen_race("human", "unisex", "back", 1)
assert calls[-1][0] == "edit" and calls[-1][1] == Path(c[0]).name and "from behind" in calls[-1][2]
ws.approve_race("human.unisex", "back", bk[0])
assert "Only the unisex" in err(ws.approve_race, "human.male", "front", bk[0])

# elf unisex = edit of the human unisex
e = ws.gen_race("elf", "unisex", "front", 1)
assert calls[-1][0] == "edit" and calls[-1][1] == Path(c[0]).name and "pointed elf ears" in calls[-1][2]

# a new unisex front drops its back; the back made from the old front is stale
ws.approve_race("human.unisex", "front", c[1])
st = ws.race_state("human")
assert set(st["builds"]) == {"unisex"} and ws.load_state()["builds"]["human.unisex"]["back"].get("approved") is None
assert st["builds"]["unisex"]["back"]["stale"][bk[0]] is True and st["builds"]["unisex"]["front"]["stale"][c[0]] is False

# spec validation
doc = ws.races_doc(); doc["races"]["Bad Id"] = {"name": "x", "traits": "x"}
assert "lowercase" in err(ws.save_races, doc)
doc = ws.races_doc(); doc["races"]["orc"] = {"name": "Orc", "traits": "orc", "from": "goblin", "from_edit": "x"}
assert "doesn't exist" in err(ws.save_races, doc)
shutil.rmtree(tmp)
print("ok")
