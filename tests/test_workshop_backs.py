"""Back mannequin generation in hdart.tools.character.workshop (no ComfyUI: _i2i is stubbed). Run: uv run python tests/test_workshop_backs.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import numpy as np
from PIL import Image
import hdart.imaging.imageops as imageops
import hdart.tools.character.races as races

SKIN, WHITE = (240, 190, 150), (255, 255, 255)
a = np.full((512, 512, 3), 255, np.uint8)
yy, xx = np.mgrid[:512, :512]
a[(yy - 100) ** 2 + (xx - 256) ** 2 < 70 ** 2] = SKIN          # head
a[110:121, 230:241] = WHITE                                      # eye white: enclosed, above the chin
a[170:186, 241:271] = SKIN                                       # neck (the chin row)
a[185:331, 200:313] = SKIN                                       # body
a[190:301, 316:337] = SKIN; a[190:201, 312:337] = SKIN; a[290:301, 312:337] = SKIN  # arm joined at both ends
a[330:481, 205:250] = SKIN; a[330:481, 262:308] = SKIN           # legs (gap open at the bottom)
img = Image.fromarray(a)

fg = imageops.foreground(img)
sil = imageops.figure_mask(img)
assert not fg[115, 235] and sil[115, 235], "eye-white hole above the chin is filled"
assert not sil[250, 314], "the enclosed gap between arm and body stays background"
assert not sil[400, 255], "the gap between the legs stays background"
assert (sil & ~fg).sum() == 11 * 11, (sil & ~fg).sum()

o = races._outline(np.asarray(img), sil)
assert tuple(o[100, 187]) == (40, 32, 30) and tuple(o[100, 256]) == SKIN, "outline on the edge, inside untouched"

calls = []
def fake_i2i(init, refs, prompt, seed, lora, denoise, mask=None):
    calls.append({"seed": seed, "denoise": denoise, "mask": mask})
    out = np.asarray(init).copy()
    if len(calls) == 1:
        out[250:280, 220:260] = 255  # the first pass leaves a hole inside the silhouette
    return out
races._i2i = fake_i2i
back = np.asarray(races.back_from_front(races.mirror(img), seed=5))  # its mirror is mirrored back: same silhouette
assert len(calls) == 2, "first pass + one gap repaint"
assert calls[0]["mask"].dtype == bool and (calls[0]["mask"] == sil).all(), "first pass is masked to the silhouette"
assert calls[1]["seed"] == 105 and calls[1]["denoise"] == races.GAP_DENOISE and calls[1]["mask"][265, 240] == 255
assert (imageops.foreground(Image.fromarray(back)) == sil).all(), "result covers exactly the silhouette"
assert (back[~sil] == 255).all()
print("ok")
