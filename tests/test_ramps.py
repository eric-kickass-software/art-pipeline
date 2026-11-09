"""Ramp editor backend (hdart.tools.character.workshop) + JS/Python ramp parity. Run: uv run python tests/test_ramps.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, shutil, subprocess, tempfile
from pathlib import Path
import numpy as np
from PIL import Image
import hdart.imaging.levels as levels
import hdart.imaging.palette as palette
from hdart.imaging.palette import Palettes, ramp_from_base, rgb_hex

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())

# skin levels: a figure with 3 skin shades + grey cloth + dark outline on white
a = np.full((64, 64, 3), 255, np.uint8)
a[10:50, 20:44] = (40, 32, 30)                                   # outline
a[12:48, 22:42] = (230, 170, 140); a[12:24, 22:42] = (250, 200, 170); a[36:48, 22:42] = (170, 100, 80)
a[26:34, 22:42] = (110, 110, 112)                                # grey cloth band
lev, cent = levels.skin_levels(Image.fromarray(a), size=3)
assert lev[15, 30] == 2 and lev[30, 30] == -1 and lev[40, 30] == 0 and lev[11, 21] == -1, (lev[15, 30], lev[40, 30])
assert cent[0][0] < cent[1][0] < cent[2][0], cent

# brushwork noise on a smooth light -> dark gradient: smooth bands that only step one way, no speckle
rng = np.random.default_rng(0); g = np.full((128, 128, 3), 255, np.uint8)
ramp = np.linspace(0, 1, 100)[None, :, None] * np.array([90, 80, 70]) + np.array([150, 95, 75])
g[14:114, 14:114] = np.clip(ramp + rng.normal(0, 9, (100, 100, 3)), 0, 255).astype(np.uint8)
lev, _ = levels.skin_levels(Image.fromarray(g), size=5)
row = lev[60, 20:108]
assert (row >= 0).all() and (np.diff(row) >= 0).all(), row

# save / rename / delete keep the rest of the file
doc = palette.save_ramp("skin", "test_tone", base="#825c43", tune={"saturation": 1.4}, colors=["#111111", "#222222", "#333333", "#444444", "#555555"], races=["human"])
e = doc["groups"]["skin"]["test_tone"]
assert e["colors"][2] == "#333333" and e["tune"]["saturation"] == 1.4 and e["races"] == ["human"]
assert "fair" in doc["groups"]["skin"] and "hair" in doc["groups"]
assert Palettes(palette.palettes_path()).ramps["skin.test_tone"].tolist()[0] == [17, 17, 17]   # the game export uses `colors`
doc = palette.save_ramp("skin", "test_tone2", old_name="test_tone")
assert "test_tone" not in doc["groups"]["skin"] and doc["groups"]["skin"]["test_tone2"]["colors"][0] == "#111111"
try: palette.save_ramp("skin", "bad name"); raise SystemExit("no error")
except ValueError: pass
doc = palette.delete_ramp("skin", "test_tone2"); assert "test_tone2" not in doc["groups"]["skin"]

# the pages' JS ramp formula (studio/js/ramps.js) = hdart.imaging.palette.ramp_from_base at the default tune (needs node)
node = shutil.which("node")
if node:
    mod = (ROOT / "studio" / "js" / "ramps.js").as_uri()
    bases = ["#f2c7a5", "#825c43", "#292420", "#7d9a58", "#9aa3b5"]
    prog = (f"import {{ rampFrom, TUNE0, rgb2hex }} from {json.dumps(mod)};\n"
            "console.log(JSON.stringify(" + json.dumps(bases) + ".map(b => rampFrom(b, TUNE0, 5).map(rgb2hex))));")
    out = json.loads(subprocess.run([node, "--input-type=module", "-e", prog], capture_output=True, text=True, check=True).stdout)
    for b, js_r in zip(bases, out):
        py = [rgb_hex(c) for c in ramp_from_base(b, 5)]
        diff = max(abs(int(x[i:i + 2], 16) - int(y[i:i + 2], 16)) for x, y in zip(py, js_r) for i in (1, 3, 5))
        assert diff <= 1, (b, py, js_r)
    print("js parity ok")
shutil.rmtree(tmp)
print("ok")
