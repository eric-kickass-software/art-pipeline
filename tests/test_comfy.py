"""hdart.core.comfy against the fake ComfyUI (hdart.core.fake_comfy): the shared image-in/image-out wrapper, txt2img outputs,
errors as ComfyError (never SystemExit), the queue check and interrupt. Run: uv run python tests/test_comfy.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import tempfile, threading, time
from pathlib import Path
import numpy as np
from PIL import Image
from hdart.core import comfy
from hdart.core.fake_comfy import FakeComfy, info

tmp = Path(tempfile.mkdtemp())


def raises(fn, text):
    try:
        fn()
    except comfy.ComfyError as e:
        assert text in str(e), str(e)
        return
    raise AssertionError("no ComfyError")


init = Image.new("RGB", (40, 30), (10, 200, 30))
with FakeComfy() as fake:
    assert comfy.alive() and comfy.url() == fake.url and comfy.queue() == {"running": 0, "pending": 0}

    # img2img: mask (bool, float, 0..255 all work) and refs become graph inputs; the fake echoes the init
    for mask in (np.ones((30, 40), bool), np.full((30, 40), 0.5), np.full((30, 40), 255, np.uint8)):
        out = comfy.img2img(init, "make it stone", seed=3, denoise=0.6, refs=[init, init], mask=mask)
        assert out.size == (40, 30) and out.getpixel((5, 5)) == (10, 200, 30)
        i = info(fake.prompts[-1], fake.uploads)
        assert i["prompt"] == "make it stone" and len(i["refs"]) == 2 and i["mask"].all() == (mask.dtype == bool or mask.max() > 127)
        assert "DifferentialDiffusion" in i["classes"]
    k = next(n["inputs"] for n in fake.prompts[-1].values() if n["class_type"] == "KSampler")
    assert k["seed"] == 3 and k["denoise"] == 0.6
    assert len(set(fake.uploads)) == 3 * 4   # unique upload names: two jobs never overwrite each other's inputs

    # repaint with a float mask; the reference latent is on by default
    out = comfy.repaint(init, np.zeros((30, 40)), "moss", seed=1)
    assert out.size == (40, 30) and info(fake.prompts[-1], fake.uploads)["classes"].count("VAEEncode") == 2

    # txt2img: a batch comes back as files; background removal gives RGBA
    paths = comfy.run(comfy.build_txt2img("a crate", 64, 48, seed=5, batch=2), tmp / "a")
    assert len(paths) == 2 and Image.open(paths[0]).size == (64, 48)
    p = comfy.run(comfy.build_txt2img("a crate", 64, 64, seed=5, remove_bg=True), tmp / "b")[0]
    assert Image.open(p).mode == "RGBA" and Image.open(p).getpixel((0, 0))[3] == 0

    # failures are ComfyError (a RuntimeError the Studio's job runner reports), not SystemExit
    fake.fail = "CUDA out of memory"
    raises(lambda: comfy.img2img(init, "x", seed=1, denoise=0.5), "CUDA out of memory")
    raises(lambda: comfy.run(comfy.build_img2img("never_uploaded.png", "x"), tmp), "rejected the workflow")
    assert issubclass(comfy.ComfyError, RuntimeError) and not issubclass(comfy.ComfyError, SystemExit)

    # interrupt stops the running workflow; the caller sees an error
    fake.delay = 5
    threading.Timer(0.3, comfy.interrupt).start()
    t0 = time.time()
    raises(lambda: comfy.img2img(init, "x", seed=1, denoise=0.5), "interrupted")
    assert time.time() - t0 < 3
    fake.delay = 0

    # the queue shows other clients' work
    fake.busy = 3
    assert comfy.queue() == {"running": 1, "pending": 2}
    fake.busy = 0
    url = fake.url
assert comfy.URL is None
comfy.URL = url   # the fake has stopped: "not running"
raises(lambda: comfy.img2img(init, "x", seed=1, denoise=0.5), "not reachable")   # the upload is the first call
raises(lambda: comfy.run(comfy.build_txt2img("x"), tmp), "not running")
assert comfy.queue() is None
comfy.URL = None
print("ok")
