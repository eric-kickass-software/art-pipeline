"""Compose heroes and render them as voxel sheets (see blender/render_voxel_sheet.py)."""
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from .compose import Kit
from .preview import random_apps


def export_frame(kit, app, view, clip, direction, frame, out_base):
    color, _, depth = kit.compose_full(app, view)
    vc = kit.manifest["views"][view]
    w, h = vc["cell"]
    row = next(r for r in vc["rows"] if r["clip"] == clip and (direction is None or r["dir"] == direction))
    ys, xs = row["row"] * h, frame * w
    c = color[ys:ys + h, xs:xs + w]
    d = depth[ys:ys + h, xs:xs + w].clip(0, 255).astype(np.uint8)
    Image.fromarray(c).save(f"{out_base}.png")
    Image.fromarray(np.dstack([d, d, d, np.full_like(d, 255)])).save(f"{out_base}_depth.png")
    return f"{out_base}.png", f"{out_base}_depth.png"


def render(jobs, blender, script, jobs_path):
    Path(jobs_path).write_text(json.dumps(jobs, indent=1), encoding="utf-8")
    cmd = [blender, "-b", "--factory-startup", "-P", str(script), "--", "--jobs", str(jobs_path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode or "Traceback" in p.stdout:
        print(p.stdout[-3000:], p.stderr[-3000:])
        raise SystemExit("voxel sheet render failed")


def contact(paths, out, cols=4, bg=(13, 11, 18)):
    ims = [Image.open(p).convert("RGBA") for p in paths]
    w, h = ims[0].size
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * w, rows * h), bg + (255,))
    for i, im in enumerate(ims):
        sheet.alpha_composite(im, ((i % cols) * w, (i // cols) * h))
    sheet.save(out)
    return out
