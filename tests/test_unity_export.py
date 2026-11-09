"""Unity puppet export (hdart.export.unity_export) of a fake-painted race into a temp project (HD_PROJECT): puppet.json's
rig and options, the cropped parts with their skin index maps, the ramps. Run: uv run python tests/test_unity_export.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, os, shutil, tempfile
from pathlib import Path
import numpy as np
from PIL import Image
import hdart.tools.character.puppet_paint as ppt
import hdart.tools.character.puppet_face as pf
import hdart.export.unity_export as ue
from hdart.core import config
from hdart.core.fake_comfy import FakeComfy

tmp = Path(tempfile.mkdtemp())
os.environ["HD_DATA"] = str(tmp)   # the data root (sprites/ai, archive, ...) is a temp folder
os.environ["HD_PROJECT"] = str(tmp / "game")   # the export lands in this fake project, not the real game

with FakeComfy():
    c = pf.paint("human.male", n=1, seed=1)[0]; ppt.approve("human.male", c)

assert ue.out_root() == tmp / "game" / "Assets/StreamingAssets/Art/Puppets" == config.project().export("puppets")
d = Path(ue.export("human", size=256))
assert d == ue.out_root() / "human"
doc = json.loads((d / "puppet.json").read_text())
assert doc["schemaVersion"] == 1 and doc["race"] == "human" and doc["canvas"] == 256 and doc["rampSize"] == 5
assert list(doc["sexes"]) == ["male"] and doc["sexes"]["male"]["paint"] == c   # no female paint: only the male exported
assert doc["hair"] == [] and doc["faces"] == []
assert {"skin", "hair", "eyes"} == set(doc["ramps"]) and all(len(r["colors"]) == 5 for r in doc["ramps"]["skin"])
assert len(doc["sexes"]["male"]["skinKey"]) == 5

for v in ("front", "back"):
    V = doc["sexes"]["male"]["views"][v]
    names = [p["name"] for p in V["parts"]]
    assert "torso" in names and "head" in names and any(n.startswith("armA") for n in names), names
    assert {"neck", "pelvis"} <= set(V["joints"])
    for p in V["parts"]:
        with Image.open(d / p["file"]) as art, Image.open(d / p["file"].replace(".png", ".idx.png")) as idx:
            size, ia = art.size, np.asarray(idx)
        x, y, w, h = p["rect"]
        assert size == ia.shape[1::-1] == (w, h) and 0 <= x and x + w <= 256 and y + h <= 256, p
        assert len(p["pivot"]) == 2 and (p["end"] is None) == (p["name"] == "head"), p
        assert set(np.unique(ia[..., 1][ia[..., 3] > 0])) <= {ue.SKIN}, p["name"]   # only skin is indexed on bare parts

# a re-export replaces the race's folder (stale files go), other races stay
(d / "stale.png").write_bytes(b"x"); (ue.out_root() / "elf").mkdir()
ue.export("human", size=256)
assert not (d / "stale.png").exists() and (ue.out_root() / "elf").exists()
os.environ.pop("HD_PROJECT"); shutil.rmtree(tmp)
print("ok")
