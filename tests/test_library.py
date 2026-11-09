"""Materials library (no network): palette extraction, repeats, image templates from a cached material.
Run: uv run python tests/test_library.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import numpy as np
from PIL import Image
from hdart import library as L

# repeats: whole texture repeats per 1.5 m tile, at least 1
assert L.repeats_per_tile([0.5, 0.5]) == 3 and L.repeats_per_tile([2.4, 1.2]) == 1 and L.repeats_per_tile(None) == 1
# palette: joints come from the low parts of the height map
color = Image.fromarray(np.where(np.indices((64, 64)).sum(0)[..., None] % 8 == 0, [40, 40, 40], [180, 120, 90]).astype(np.uint8))
height = Image.fromarray(np.where(np.indices((64, 64)).sum(0) % 8 == 0, 10, 200).astype(np.uint8))
pal = L.extract_palette(color, height, 2)
j = int(pal["mortar"][1:3], 16)
assert j < 90 and all(int(c[1:3], 16) > 120 for c in pal["stones"]), pal
# hosts: only the two libraries
try:
    L._get("https://example.com/x"); raise SystemExit("allowed a foreign host")
except ValueError:
    pass
# image templates from any cached material repeat per tile like the procedural ones
mats = [m for m in L.list_materials() if (L.CACHE / m["id"] / "color.jpg").exists()]
for m in mats[:2]:
    f = L.image_template({"material": m["id"], "repeats": 2}, "floor", 128, 2.45)
    a = np.asarray(f["rgb"]).astype(int)
    assert a.shape[:2] == (128, 128) and 0.5 < f["region"].mean() <= 1
    w = L.image_template({"material": m["id"]}, "wall", 128, 2.45)
    assert w["rgb"].size == (128, round(2.45 * 128))
print(f"library tests passed ({len(mats)} cached materials checked)")
