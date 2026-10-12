"""Wall family geometry in hdart.dungeon (no GPU): elevation projection, masks, door slice. Run: uv run python tests/test_walls.py"""
import numpy as np
from PIL import Image
import hdart.dungeon as d

w, h = d.elev_size()
assert (w, h) == (512, 1248), (w, h)  # one tile wide, 3 units tall
# a synthetic elevation: brightness = height (top dark, base bright), so projected rows can be checked
elev = Image.fromarray(np.repeat(np.linspace(40, 220, h)[:, None, None], w, 1).repeat(3, 2).astype(np.uint8))
for L in (1, 2, 4):
    run = {"id": "r", "kind": "tile", "shape": "run", "length": L, "height": d.WALL_UNITS, "thickness": 0.25}
    img, m = d.project_elevation(run, 64, elev, 1)
    assert m.any() and img.size == m.shape[::-1]
    a = np.asarray(img).astype(float)[..., 0]
    # every column through the front face gets darker going up (texture sampled bottom = base, top = cap)
    cols = [x for x in range(0, m.shape[1], max(1, m.shape[1] // 12)) if m[:, x].sum() > 40]
    assert cols and all(a[np.nonzero(m[:, x])[0][-5], x] > a[np.nonzero(m[:, x])[0][15], x] for x in cols), L
door = {"id": "d", "kind": "tile", "shape": "run", "opening": "door", "length": 1, "height": d.WALL_UNITS,
        "door_height": d.DOOR_UNITS, "door_width": d.DOOR_WIDTH, "thickness": 0.25}
img, m = d.project_elevation(door, 64, elev.crop((0, 0, w, h)), 1)
assert m.any()
# wall variation mask keeps the edges, cap and base; a door rect sits on the floor, 2 of 3 units tall
mask = d.elev_keep_mask(1024, h, 512, d.iso._KZ * 512)
assert mask[:, :30].max() == 0 and mask[:, -30:].max() == 0 and mask[:40].max() == 0 and mask[-30:].max() == 0 and mask[h // 2, 512] == 1
fam = {"door_width": 0.5, "door_height": 2.0}
x0, x1, y0, y1 = d.door_rect(fam, 512, d.iso._KZ * 512, h)
assert x0 == 128 and x1 == 384 and y1 == h and abs((h - y0) / h - 2 / 3) < 0.01
print("wall tests passed")
