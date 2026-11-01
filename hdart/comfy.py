"""Minimal ComfyUI API client plus the Hearthdelve workflows, built as API-format graphs.

ComfyUI runs locally (P:\\Tools\\ComfyUI\\start-comfyui.cmd, http://127.0.0.1:8188). Workflows are
built in code rather than stored as exported JSON so the knobs (prompt, size, seed, LoRA strength)
stay explicit and diffable. Each `build_*` returns a prompt dict; `run()` queues it and downloads
the outputs.
"""
import json
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from . import config

URL = None   # set to point this process at another ComfyUI (tests: hdart.fake_comfy); else config.comfy_url()


class ComfyError(RuntimeError):
    """ComfyUI is down, rejected the workflow, failed in it, or timed out."""


def url():
    return URL or config.comfy_url()


def _m(name):
    return config.model(name)


class Graph:
    def __init__(self):
        self.nodes, self._n = {}, 0

    def add(self, cls, **inputs):
        self._n += 1
        nid = str(self._n)
        self.nodes[nid] = {"class_type": cls, "inputs": inputs}
        return Ref(nid)


class Ref:
    def __init__(self, nid):
        self.nid = nid

    def __getitem__(self, i):
        return [self.nid, i]


def _klein_model(g, lora_strength):
    model = g.add("UNETLoader", unet_name=_m("klein"), weight_dtype="default")
    if lora_strength:
        model = g.add("LoraLoaderModelOnly", model=model[0], lora_name=_m("pixel_lora"), strength_model=lora_strength)
    clip = g.add("CLIPLoader", clip_name=_m("qwen"), type="flux2", device="default")
    vae = g.add("VAELoader", vae_name=_m("flux2_vae"))
    return model, clip, vae


def build_txt2img(prompt, width=1024, height=1024, seed=0, steps=4, lora_strength=1.0, batch=1,
                  prefix="hdart/img", remove_bg=False):
    """FLUX.2-klein text-to-image, optionally with the pixel-art LoRA and BiRefNet background removal."""
    g = Graph()
    model, clip, vae = _klein_model(g, lora_strength)
    pos = g.add("CLIPTextEncode", clip=clip[0], text=prompt)
    neg = g.add("ConditioningZeroOut", conditioning=pos[0])
    guider = g.add("CFGGuider", model=model[0], positive=pos[0], negative=neg[0], cfg=1.0)
    latent = g.add("EmptyFlux2LatentImage", width=width, height=height, batch_size=batch)
    sigmas = g.add("Flux2Scheduler", steps=steps, width=width, height=height)
    noise = g.add("RandomNoise", noise_seed=seed)
    sampler = g.add("KSamplerSelect", sampler_name="euler")
    out = g.add("SamplerCustomAdvanced", noise=noise[0], guider=guider[0], sampler=sampler[0],
                sigmas=sigmas[0], latent_image=latent[0])
    _save(g, g.add("VAEDecode", samples=out[0], vae=vae[0]), prefix, remove_bg)
    return g.nodes


def _save(g, img, prefix, remove_bg=False):
    """SaveImage of a decoded image, optionally cut out by BiRefNet (RGBA)."""
    if remove_bg:
        bgm = g.add("LoadBackgroundRemovalModel", bg_removal_name=_m("birefnet"))
        mask = g.add("RemoveBackground", bg_removal_model=bgm[0], image=img[0])
        inv = g.add("InvertMask", mask=mask[0])
        img = g.add("JoinImageWithAlpha", image=img[0], alpha=inv[0])
    g.add("SaveImage", images=img[0], filename_prefix=prefix)


def build_edit(image_name, prompt, seed=0, steps=4, lora_strength=0.0, prefix="hdart/edit", megapixels=1.0,
               extra_images=(), remove_bg=False):
    """FLUX.2-klein image edit: the input image is a reference (layout, pose, colours) and the prompt
    says what to change. `extra_images` are more references ("image 2", ...). Output is the size of the
    first image scaled to `megapixels`; `remove_bg` cuts it out (RGBA) like build_txt2img."""
    g = Graph()
    model, clip, vae = _klein_model(g, lora_strength)
    load = g.add("LoadImage", image=image_name)
    scaled = g.add("ImageScaleToTotalPixels", image=load[0], upscale_method="nearest-exact", megapixels=megapixels,
                   resolution_steps=1)
    size = g.add("GetImageSize", image=scaled[0])
    ref = g.add("VAEEncode", pixels=scaled[0], vae=vae[0])
    pos = g.add("CLIPTextEncode", clip=clip[0], text=prompt)
    neg = g.add("ConditioningZeroOut", conditioning=pos[0])
    pos_r = g.add("ReferenceLatent", conditioning=pos[0], latent=ref[0])
    neg_r = g.add("ReferenceLatent", conditioning=neg[0], latent=ref[0])
    for name in extra_images:
        li = g.add("LoadImage", image=name)
        si = g.add("ImageScaleToTotalPixels", image=li[0], upscale_method="nearest-exact", megapixels=megapixels,
                   resolution_steps=1)
        lat = g.add("VAEEncode", pixels=si[0], vae=vae[0])
        pos_r = g.add("ReferenceLatent", conditioning=pos_r[0], latent=lat[0])
        neg_r = g.add("ReferenceLatent", conditioning=neg_r[0], latent=lat[0])
    guider = g.add("CFGGuider", model=model[0], positive=pos_r[0], negative=neg_r[0], cfg=1.0)
    latent = g.add("EmptyFlux2LatentImage", width=size[0], height=size[1], batch_size=1)
    sigmas = g.add("Flux2Scheduler", steps=steps, width=size[0], height=size[1])
    noise = g.add("RandomNoise", noise_seed=seed)
    sampler = g.add("KSamplerSelect", sampler_name="euler")
    out = g.add("SamplerCustomAdvanced", noise=noise[0], guider=guider[0], sampler=sampler[0], sigmas=sigmas[0],
                latent_image=latent[0])
    _save(g, g.add("VAEDecode", samples=out[0], vae=vae[0]), prefix, remove_bg)
    return g.nodes


def build_img2img(image_name, prompt, denoise=0.75, seed=0, steps=8, lora_strength=0.0, prefix="hdart/i2i",
                  ref_images=(), mask_name=None):
    """FLUX.2-klein img2img: start from `image_name` (layout + colours) at partial denoise; optional reference
    images (appearance) via ReferenceLatent. Keeps composition, redraws the surface. `mask_name` (greyscale, red
    channel read) limits the sampling: white = redraw, black = keep the init exactly (Differential Diffusion)."""
    g = Graph()
    model, clip, vae = _klein_model(g, lora_strength)
    if mask_name:
        model = g.add("DifferentialDiffusion", model=model[0], strength=1.0)
    load = g.add("LoadImage", image=image_name)
    lat = g.add("VAEEncode", pixels=load[0], vae=vae[0])
    if mask_name:
        lat = g.add("SetLatentNoiseMask", samples=lat[0],
                    mask=g.add("LoadImageMask", image=mask_name, channel="red")[0])
    pos = g.add("CLIPTextEncode", clip=clip[0], text=prompt)
    neg = g.add("ConditioningZeroOut", conditioning=pos[0])
    for name in ref_images:
        li = g.add("LoadImage", image=name)
        rl = g.add("VAEEncode", pixels=li[0], vae=vae[0])
        pos = g.add("ReferenceLatent", conditioning=pos[0], latent=rl[0])
        neg = g.add("ReferenceLatent", conditioning=neg[0], latent=rl[0])
    out = g.add("KSampler", model=model[0], positive=pos[0], negative=neg[0], latent_image=lat[0], seed=seed,
                steps=steps, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=denoise)
    g.add("SaveImage", images=g.add("VAEDecode", samples=out[0], vae=vae[0])[0], filename_prefix=prefix)
    return g.nodes


def build_img2mesh(image_name, seed=42, prefix="hdart/mesh", resolution=1024):
    """TRELLIS.2 image -> textured mesh (vertex colours), following the official template's
    stage settings. `image_name` must already be uploaded (see upload())."""
    g = Graph()
    load = g.add("LoadImage", image=image_name)
    bgm = g.add("LoadBackgroundRemovalModel", bg_removal_name=_m("birefnet"))
    mask = g.add("RemoveBackground", bg_removal_model=bgm[0], image=load[0])
    crop = g.add("ImageCropToMask", images=load[0], masks=mask[0], width=1024, height=1024,
                 pad_factor=1.1, grow_mask=0, background="#000000")
    cv = g.add("CLIPVisionLoader", clip_name=_m("dino"))
    cond = g.add("Trellis2Conditioning", clip_vision_model=cv[0], image=crop[0])
    unet = g.add("UNETLoader", unet_name=_m("trellis"), weight_dtype="default")
    shape_vae = g.add("VAELoader", vae_name="trellis_2_shape_vae_bf16.safetensors")
    tex_vae = g.add("VAELoader", vae_name="trellis_2_texture_vae_bf16.safetensors")
    # stage 1: sparse structure
    m1 = g.add("ModelSamplingSD3", model=g.add("RescaleCFG", model=g.add("CFGOverride", model=unet[0], cfg=1.0,
               start_percent=0.667, end_percent=1.0)[0], multiplier=0.7)[0], shift=5.0)
    s1 = g.add("KSampler", model=m1[0], positive=cond[0], negative=cond[1],
               latent_image=g.add("EmptyTrellis2LatentStructure", batch_size=1)[0], seed=seed, steps=12, cfg=7.5,
               sampler_name="euler", scheduler="normal", denoise=1.0)
    voxel = g.add("VaeDecodeStructureTrellis2", samples=s1[0], vae=shape_vae[0], resolution="32")
    # stage 2: shape, then upsample
    m2 = g.add("RescaleCFG", model=g.add("CFGOverride", model=unet[0], cfg=1.0, start_percent=0.769,
               end_percent=1.0)[0], multiplier=0.5)
    shp = g.add("Trellis2ShapeStage", positive=cond[0], negative=cond[1], voxel=voxel[0])
    s2 = g.add("KSampler", model=m2[0], positive=shp[0], negative=shp[1], latent_image=shp[2], seed=seed,
               steps=20, cfg=7.5, sampler_name="euler", scheduler="normal", denoise=1.0)
    up = g.add("Trellis2UpsampleStage", positive=shp[0], negative=shp[1], shape_latent=s2[0], vae=shape_vae[0],
               target_resolution=resolution)
    s3 = g.add("KSampler", model=m2[0], positive=up[0], negative=up[1], latent_image=up[2], seed=seed,
               steps=12, cfg=7.5, sampler_name="euler", scheduler="simple", denoise=1.0)
    mesh = g.add("VaeDecodeShapeTrellis", samples=s3[0], vae=shape_vae[0])
    # stage 3: texture (voxel colours painted onto the mesh)
    tx = g.add("Trellis2TextureStage", positive=up[0], negative=up[1], shape_latent=s3[0])
    s4 = g.add("KSampler", model=unet[0], positive=tx[0], negative=tx[1], latent_image=tx[2], seed=seed,
               steps=12, cfg=1.0, sampler_name="euler", scheduler="normal", denoise=1.0)
    colors = g.add("VaeDecodeTextureTrellis", samples=s4[0], vae=tex_vae[0], shape_subdivides=mesh[1])
    smooth = g.add("MeshSmoothNormals", mesh=mesh[0], crease_angle=180.0)
    painted = g.add("PaintMesh", mesh=smooth[0], voxel_colors=colors[0])
    g.add("SaveGLB", mesh=painted[0], filename_prefix=prefix)
    g.add("SaveImage", images=crop[0], filename_prefix=prefix + "_input")
    return g.nodes


def build_repaint(image_name, mask_name, prompt, denoise=1.0, seed=0, steps=8, lora_strength=0.0,
                  prefix="hdart/repaint", reference=True):
    """FLUX.2-klein masked repaint. The mask is a greyscale image (its red channel is read): black = keep exactly,
    white = repaint fully, grey in between (Differential Diffusion turns it into a per-pixel change strength, so
    a soft edge blends instead of leaving a seam). The model sees the whole image, so the repaint continues what's
    around it; `reference` also feeds the image in as a ReferenceLatent so materials and colours stay the same."""
    g = Graph()
    model, clip, vae = _klein_model(g, lora_strength)
    model = g.add("DifferentialDiffusion", model=model[0], strength=1.0)
    load = g.add("LoadImage", image=image_name)
    mask = g.add("LoadImageMask", image=mask_name, channel="red")
    lat = g.add("VAEEncode", pixels=load[0], vae=vae[0])
    lat = g.add("SetLatentNoiseMask", samples=lat[0], mask=mask[0])
    pos = g.add("CLIPTextEncode", clip=clip[0], text=prompt)
    neg = g.add("ConditioningZeroOut", conditioning=pos[0])
    if reference:
        ref = g.add("VAEEncode", pixels=load[0], vae=vae[0])
        pos = g.add("ReferenceLatent", conditioning=pos[0], latent=ref[0])
        neg = g.add("ReferenceLatent", conditioning=neg[0], latent=ref[0])
    out = g.add("KSampler", model=model[0], positive=pos[0], negative=neg[0], latent_image=lat[0], seed=seed,
                steps=steps, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=denoise)
    g.add("SaveImage", images=g.add("VAEDecode", samples=out[0], vae=vae[0])[0], filename_prefix=prefix)
    return g.nodes


# ----------------------------------------------------------------------------- transport

def alive():
    try:
        return requests.get(f"{url()}/system_stats", timeout=2).ok
    except requests.RequestException:
        return False


def queue():
    """ComfyUI's queue: {"running": n, "pending": n}, or None when it's down."""
    try:
        q = requests.get(f"{url()}/queue", timeout=2).json()
    except (requests.RequestException, ValueError):
        return None
    return {"running": len(q.get("queue_running", [])), "pending": len(q.get("queue_pending", []))}


def interrupt():
    """Stop the workflow ComfyUI is running now (whoever queued it)."""
    try:
        requests.post(f"{url()}/interrupt", timeout=5)
    except requests.RequestException:
        pass


def _drop(pid):
    """Take our own prompt out of ComfyUI after a timeout: off the queue if it's still waiting, interrupted if it's the
    one running (never someone else's)."""
    try:
        requests.post(f"{url()}/queue", json={"delete": [pid]}, timeout=5)
        q = requests.get(f"{url()}/queue", timeout=5).json()
        if any(len(r) > 1 and r[1] == pid for r in q.get("queue_running", [])):
            requests.post(f"{url()}/interrupt", timeout=5)
    except (requests.RequestException, ValueError):
        pass


def _http(method, path, **kw):
    """One request to ComfyUI; a dropped connection is a ComfyError like every other failure."""
    try:
        return requests.request(method, f"{url()}{path}", **kw)
    except requests.RequestException as e:
        raise ComfyError(f"ComfyUI is not reachable at {url()} ({type(e).__name__}). "
                         "Start P:\\Tools\\ComfyUI\\start-comfyui.cmd") from e


def image(nodes, timeout=None):
    """run() into a private temp folder: the first output as a PIL image (loaded, so the folder can go)."""
    with tempfile.TemporaryDirectory(prefix="hdart_") as d:
        paths = run(nodes, d, timeout)
        if not paths:
            raise ComfyError("ComfyUI returned no image")
        with Image.open(paths[0]) as im:
            im.load()
            return im.copy()


def upload(path, name=None):
    with open(path, "rb") as f:
        r = _http("POST", "/upload/image", files={"image": (name or Path(path).name, f)}, data={"overwrite": "true"})
    if not r.ok:
        raise ComfyError(f"ComfyUI refused the upload: {r.status_code} {r.text[:500]}")
    return r.json()["name"]


def run(nodes, out_dir, timeout=None):
    """Queue a prompt, wait for it, download every output file into out_dir. Returns the paths. Raises ComfyError."""
    base, timeout = url(), timeout or config.comfy_timeout()
    if not alive():
        raise ComfyError(f"ComfyUI is not running at {base}. Start P:\\Tools\\ComfyUI\\start-comfyui.cmd")
    r = _http("POST", "/prompt", json={"prompt": nodes, "client_id": str(uuid.uuid4())})
    if not r.ok:
        raise ComfyError(f"ComfyUI rejected the workflow: {r.text[:2000]}")
    pid = r.json()["prompt_id"]
    t0 = time.time()
    while True:
        h = _http("GET", f"/history/{pid}").json()
        if pid in h:
            entry = h[pid]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m[0] in ("execution_error", "execution_interrupted")]
                raise ComfyError(f"ComfyUI error: {json.dumps(msgs)[:3000]}")
            if status.get("completed", True):
                break
        if time.time() - t0 > timeout:
            _drop(pid)
            raise ComfyError(f"ComfyUI timed out after {timeout:.0f} s")
        time.sleep(0.2 if URL else 1.0)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for node_out in entry["outputs"].values():
        for key in ("images", "3d", "meshes", "files", "result"):
            for f in node_out.get(key, []) or []:
                if not isinstance(f, dict) or "filename" not in f:
                    continue
                q = {"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
                data = _http("GET", "/view", params=q).content
                p = out / f["filename"]
                p.write_bytes(data)
                paths.append(p)
    return paths


# ----------------------------------------------------------------------------- image in, image out
# The one wrapper every tool uses for its passes: save the inputs to a private temp folder, upload them under unique
# names (two jobs never overwrite each other's inputs), run, read the first output back, delete the temp files.

def _mask_image(mask):
    """A mask as an 8-bit greyscale image: PIL as is, bool -> 0/255, float 0..1 -> 0..255, ints as given."""
    if isinstance(mask, Image.Image):
        return mask.convert("L")
    m = np.asarray(mask)
    if m.dtype == bool:
        m = np.where(m, 255, 0)
    elif m.dtype.kind == "f":
        m = np.clip(m, 0, 1) * 255
    return Image.fromarray(m.astype(np.uint8))


def _pass(images, build, mode="RGB"):
    """Upload `images` ({key: PIL image}), call build(names) -> nodes, run it, return the first output (PIL, `mode`)."""
    with tempfile.TemporaryDirectory(prefix="hdart_") as d:
        d, tag = Path(d), uuid.uuid4().hex[:12]
        names = {}
        for k, im in images.items():
            f = d / f"hd_{tag}_{k}.png"
            im.save(f)
            names[k] = upload(f)
        outs = run(build(names), d / "out")
        if not outs:
            raise ComfyError("ComfyUI returned no image")
        with Image.open(outs[0]) as im:
            return im.convert(mode)


def img2img(init, prompt, *, seed, denoise, lora=0.0, steps=8, refs=(), mask=None, prefix="hdart/i2i"):
    """FLUX img2img of a PIL image (build_img2img), optional reference images and a redraw mask (see _mask_image).
    Returns the output at ComfyUI's size: callers resize it to theirs."""
    images = {"init": init.convert("RGB")}
    images.update({f"ref{k}": r for k, r in enumerate(refs)})
    if mask is not None:
        images["mask"] = _mask_image(mask)
    return _pass(images, lambda n: build_img2img(n["init"], prompt, denoise=denoise, seed=seed, steps=steps,
                                                 lora_strength=lora, prefix=prefix, mask_name=n.get("mask"),
                                                 ref_images=[n[f"ref{k}"] for k in range(len(refs))]))


def repaint(img, mask, prompt, *, seed, denoise=1.0, lora=0.0, steps=8, reference=True, prefix="hdart/repaint"):
    """FLUX masked repaint of a PIL image (build_repaint); `mask` as in img2img. Returns the output at ComfyUI's size."""
    return _pass({"img": img, "mask": _mask_image(mask)},
                 lambda n: build_repaint(n["img"], n["mask"], prompt, denoise=denoise, seed=seed, steps=steps,
                                         lora_strength=lora, prefix=prefix, reference=reference))
