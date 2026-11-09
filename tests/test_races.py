"""Race mannequin chain in hdart.workshop (ComfyUI stubbed). Run: uv run python tests/test_races.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import os, shutil, tempfile
from pathlib import Path
from PIL import Image
import hdart.workshop as ws
import hdart.races as races

tmp = Path(tempfile.mkdtemp())
os.environ["HD_DATA"] = str(tmp)   # the data root (sprites/ai, archive, ...) is a temp folder
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
races.comfy = FakeComfy

def err(f, *a):
    try: f(*a)
    except ValueError as e: return str(e)
    raise SystemExit("no error")

# order is enforced
assert "painted from the Template" in err(races.gen_race, "human", "male", "front", 1)   # only the unisex has a mannequin
assert "approve the human unisex front first" in err(races.gen_race, "elf", "unisex", "front", 1)
assert "front first" in err(races.gen_race, "human", "unisex", "back", 1)

# unisex = txt2img with the A-pose and the race traits; result 512 px
c = races.gen_race("human", "unisex", "front", 2, seed=1)
assert len(c) == 2 and calls[-1][0] == "txt2img" and "A-pose" in calls[-1][1] and "rounded human ears" in calls[-1][1]
assert "unclothed" in calls[-1][1] and "Adventurer" not in calls[-1][1] and "shorts" not in calls[-1][1]   # smooth base, no gear
assert Image.open(ws.ai_dir() / c[0]).size == (races.BIG, races.BIG)
races.approve_race("human.unisex", "front", c[0])

# back = edit of the approved front; male and female have no mannequin any more (painted from the Template)
bk = races.gen_race("human", "unisex", "back", 1)
assert calls[-1][0] == "edit" and calls[-1][1] == Path(c[0]).name and "from behind" in calls[-1][2]
races.approve_race("human.unisex", "back", bk[0])
assert "Only the unisex" in err(races.approve_race, "human.male", "front", bk[0])

# elf unisex = edit of the human unisex
e = races.gen_race("elf", "unisex", "front", 1)
assert calls[-1][0] == "edit" and calls[-1][1] == Path(c[0]).name and "pointed elf ears" in calls[-1][2]

# a new unisex front drops its back; the back made from the old front is stale
races.approve_race("human.unisex", "front", c[1])
st = races.race_state("human")
assert set(st["builds"]) == {"unisex"} and ws.load_state()["builds"]["human.unisex"]["back"].get("approved") is None
assert st["builds"]["unisex"]["back"]["stale"][bk[0]] is True and st["builds"]["unisex"]["front"]["stale"][c[0]] is False

# spec validation
doc = races.races_doc(); doc["races"]["Bad Id"] = {"name": "x", "traits": "x"}
assert "lowercase" in err(races.save_races, doc)
doc = races.races_doc(); doc["races"]["orc"] = {"name": "Orc", "traits": "orc", "from": "goblin", "from_edit": "x"}
assert "doesn't exist" in err(races.save_races, doc)
shutil.rmtree(tmp)
print("ok")
