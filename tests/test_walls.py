"""Wall family geometry in the Dungeon Forge (hdart/tools/dungeon) (no GPU): elevation projection, masks, door slice. Run: uv run python tests/test_walls.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import os
import numpy as np
from PIL import Image
from hdart.tools.dungeon import sets as dsets, generation as gen, preview as dpreview
from hdart.tools.dungeon import iso

w, h = gen.elev_size()
assert (w, h) == (512, 1248), (w, h)  # one tile wide, 3 units tall
# a synthetic elevation: brightness = height (top dark, base bright), so projected rows can be checked
elev = Image.fromarray(np.repeat(np.linspace(40, 220, h)[:, None, None], w, 1).repeat(3, 2).astype(np.uint8))
for L in (1, 2, 4):
    run = {"id": "r", "kind": "tile", "shape": "run", "length": L, "height": dsets.WALL_UNITS, "thickness": 0.25}
    img, m = gen.project_elevation(run, 64, elev, 1)
    assert m.any() and img.size == m.shape[::-1]
    a = np.asarray(img).astype(float)[..., 0]
    # every column through the front face gets darker going up (texture sampled bottom = base, top = cap)
    cols = [x for x in range(0, m.shape[1], max(1, m.shape[1] // 12)) if m[:, x].sum() > 40]
    assert cols and all(a[np.nonzero(m[:, x])[0][-5], x] > a[np.nonzero(m[:, x])[0][15], x] for x in cols), L
door = {"id": "d", "kind": "tile", "shape": "run", "opening": "door", "length": 1, "height": dsets.WALL_UNITS,
        "door_height": dsets.DOOR_UNITS, "door_width": dsets.DOOR_WIDTH, "thickness": 0.25}
img, m = gen.project_elevation(door, 64, elev.crop((0, 0, w, h)), 1)
assert m.any()
# wall variation mask keeps the edges, cap and base; a door rect sits on the floor, 2 of 3 units tall
mask = gen.elev_keep_mask(1024, h, 512, iso.KZ * 512)
assert mask[:, :30].max() == 0 and mask[:, -30:].max() == 0 and mask[:40].max() == 0 and mask[-30:].max() == 0 and mask[h // 2, 512] == 1
fam = {"door_width": 0.5, "door_height": 2.0}
x0, x1, y0, y1 = gen.door_rect(fam, 512, iso.KZ * 512, h)
assert x0 == 128 and x1 == 384 and y1 == h and abs((h - y0) / h - 2 / 3) < 0.01
# course measurement (wall rescale): exact on templates, whatever the brick tones
for across, course in [(5, 0.11), (3, 0.2), (4, 0.08), (6, 0.1), (2, 0.15)]:
    im = gen.template_render({"pattern": "running_bond", "params": {"across": across, "course": course, "mortar": 0.012, "bevel": 0.012}}, "wall", 512)["rgb"].resize((w, h))
    m = gen.measure_elevation(im)
    rows = (dsets.WALL_UNITS - gen.KEEP_TOP - gen.KEEP_BOTTOM) * iso.KZ / course
    assert m["across"] == across and abs(m["rows"] - rows) < 0.6, (across, course, m, rows)
assert gen.measure_elevation(Image.new("RGB", (w, h), (120, 120, 120)))["rows"] is None
# keep-band guides: joints of the whole wall, and a suggested cap / base where the regular courses stop
im = np.asarray(gen.template_render({"pattern": "running_bond", "params": {"across": 4, "course": 0.1, "mortar": 0.012, "bevel": 0.012}}, "wall", 512)["rgb"].resize((w, h))).copy()
unit = h / dsets.WALL_UNITS
cap = int(0.3 * unit)
im[:cap] = (175, 170, 160); im[cap - 4:cap] = (40, 40, 45)  # a plain 0.3-unit cap, its underside a dark joint
J = gen.elevation_joints(Image.fromarray(im))
assert len(J["joints"]) > 15 and abs(J["suggest"]["top"] - 0.3) < 0.05, J["suggest"]
plain = gen.elevation_joints(gen.template_render({"pattern": "stack_bond", "params": {"across": 4, "course": 0.125, "mortar": 0.012, "bevel": 0.012}}, "wall", 512)["rgb"].resize((w, h)))
assert plain["suggest"]["bottom"] < 0.2, plain["suggest"]
# fit courses: uneven courses between two joints come out even; keep mode leaves the cap exact; still wraps sideways
rng = np.random.default_rng(2)
ys = np.cumsum(rng.uniform(0.75, 1.25, 40) * 0.12 * unit).astype(int)
wall = np.full((h, w, 3), 150, np.uint8)
for y in ys[ys < h - 4]:
    wall[y - 2:y + 3] = 40
wall[:cap] = (175, 170, 160); wall[cap - 3:cap + 1] = (40, 40, 45)
J = gen.elevation_joints(Image.fromarray(wall))
first = min(j for j in J["joints"] if j > 0.35); last = max(j for j in J["joints"] if j < 2.9)
fit, info = gen.fit_courses(Image.fromarray(wall), dsets.WALL_UNITS, first, dsets.WALL_UNITS - last, "keep")
f = np.asarray(fit)
assert (f[:int(first * unit) - 1] == wall[:int(first * unit) - 1]).all()  # above the top line: untouched
assert (f[:, 0] == f[:, -1]).all() == (wall[:, 0] == wall[:, -1]).all()  # vertical only: columns stay as they were
fj = [j for j in gen.elevation_joints(fit)["joints"] if first + 0.05 < j < last - 0.05]
g = np.diff(fj)
assert len(g) > 5 and g.std() / g.mean() < 0.08, (g.round(3), info)  # evened (from about +-15%)
crop, ci = gen.fit_courses(Image.fromarray(wall), dsets.WALL_UNITS, first, dsets.WALL_UNITS - last, "crop")
assert ci["overall"] > 0 and ci["courses"] == info["courses"]
# multi-cell variations: one strong zone that crosses a cell boundary, a light repaint elsewhere
for N in (2, 3, 4):
    for seed in range(30):
        z = gen.feature_zone(N * 384, 384, N, seed); x = np.flatnonzero(z > 0.99) / 384
        assert z.min() >= 0.3 - 1e-9 and x.size and np.floor(x.min()) < np.floor(x.max()), (N, seed)
assert 'continues past' in gen.wall_texture_prompt(dsets.style(), 'x') and gen.WALL_SEAMLESS in gen.wall_texture_prompt(dsets.style(), 'x')
# bands view: kept cap and base tinted, repeated sideways
import shutil, tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
os.environ["HD_DATA"] = str(tmp)   # sprites/dungeon and archive in a temp folder; the sets are the fixture copy
dsets.new_set("zw", biome="test")
mid = "tile.zw_wall_master"
dsets.upsert_piece("zw", {"id": mid, "kind": "elevation", "shape": "elevation", "method": "wall_master", "variants": 1, "prompt": "bricks"})
def cand(pid, tag, meta=None):
    f = dsets.set_dir("zw") / "candidates" / pid / f"{tag}.png"; f.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), (100, 100, 100)).save(f); np.save(f.with_suffix(".npy"), np.ones((4, 4), bool))
    rel = f"candidates/{pid}/{tag}.png"; s = dsets.state("zw"); ps = s["pieces"].setdefault(pid, {"candidates": [], "approved": []})
    ps["candidates"].append(rel); ps.setdefault("meta", {})[rel] = meta or {}; dsets.save_state("zw", s); return rel
m1, m2 = cand(mid, "m1"), cand(mid, "m2")
b = np.asarray(gen.elevation_bands("zw", m1, dsets.WALL_UNITS, 0.25, 0.1))
assert b.shape == (h, 2 * w, 3) and b[5, 5, 2] > b[h // 2, 5, 2]
# build status: not built -> built and current -> stale after a master switch or a new approval
gen.promote("zw", mid, m1)
spec = dsets.load_set("zw"); master = dsets.piece_of(spec, mid)
assert dpreview.wall_build_status("zw", spec, master)["built"] is False
pcs = gen.ensure_wall_pieces("zw", mid)
st = dsets.state("zw")
for k in ("run1", "run2", "run3", "run4", "post"):
    pid = pcs[k]["id"]; r = f"candidates/{pid}/b.png"
    st["pieces"][pid] = {"candidates": [r], "approved": [r], "meta": {r: {"built_from": m1, "master": m1}}}
dsets.save_state("zw", st)
spec = dsets.load_set("zw")
assert dpreview.wall_build_status("zw", spec, dsets.piece_of(spec, mid)) == {"built": True, "stale": False, "why": ""}
v = cand(pcs["var2"]["id"], "v", {"master": m1}); dsets.approve("zw", pcs["var2"]["id"], v)
assert "approvals" in dpreview.wall_build_status("zw", spec, dsets.piece_of(spec, mid))["why"]
gen.promote("zw", mid, m2)
assert "another master" in dpreview.wall_build_status("zw", spec, dsets.piece_of(spec, mid))["why"]
shutil.rmtree(tmp)
print("wall tests passed")
