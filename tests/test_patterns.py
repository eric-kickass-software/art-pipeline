"""Layout templates (hdart/patterns.py): every pattern repeats exactly once per tile, at its set scale. Run: uv run python tests/test_patterns.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import numpy as np
from hdart import iso, patterns as P

for name in P.PATTERNS:
    two = P.render(name, None, None, 2, 2.0, 96, 3, True)
    a = np.asarray(two["rgb"]).astype(int)
    assert np.abs(a[:, :96] - a[:, 96:]).max() == 0, f"{name}: not periodic across"
    assert np.abs(a[:96] - a[96:]).max() == 0, f"{name}: not periodic down"
    assert 0.6 < two["region"].mean() < 1.0, f"{name}: odd stone/joint ratio {two['region'].mean():.2f}"
    w = P.render(name, None, None, 2, 3 * iso._KZ, 96, 3, False)
    b = np.asarray(w["rgb"]).astype(int)
    assert np.abs(b[:, :96] - b[:, 96:]).max() == 0, f"{name}: wall not periodic across"
# scale: running bond with 4 across has exactly 4 bricks per tile per course (count joint crossings along a row)
r = P.render("running_bond", {"across": 4, "course": 0.1}, None, 1, 1.0, 400, 1, True)["region"]
row = r[20]  # inside the first course
assert ((row[1:] == 0) & (row[:-1] == 1)).sum() in (4, 5), "4 bricks per tile"
print("pattern tests passed")
