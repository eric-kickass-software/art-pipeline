"""The rig space every character tool shares (Template, Paint, Face, Item Workshop, exports): canvas sizes, joints read
from a template view, the waistband, composing parts and the boxes the paint passes crop to.

    PX = 576     the rig canvas (rest pose), in rig px
    S = 2        paint px per rig px: parts, layers and level maps are N = PX * S = 1152 px
    joint(V, name), pivots(spec, view, imgs), waist(V, s, torso_cov), face_ellipse(spec)
    compose(parts, order), head_box(parts), figure_box(comp), inner_head(head)

A template view V is spec["views"][view] of specs/puppet_template.json (hdart.tools.character.puppet_template): joints
in rig px, limbs (from / to joint names), seams, shapes and the draw order. Parts are {name: (rgba, guides, pivot)} as
puppet_template.render() returns them.
"""
import numpy as np
from PIL import Image, ImageFilter

PX = 576        # the rig canvas (rest pose); was 512 until 2026-10-31
S = 2           # paint px per rig px
N = PX * S      # the paint canvas (1152 px)


def joint(V: dict, name: str) -> np.ndarray:
    """A joint of template view V in rig px: "neck", "pelvis", or "<limb>.<joint>" (e.g. "armA.end")."""
    if "." in name:
        k, j = name.split(".")
        return np.array(V["joints"][k][j], float)
    return np.array(V["joints"][name], float)


def pivots(spec: dict, view: str, imgs: dict) -> dict:
    """{part: (rgba, None, pivot in rig px)} for part images on the paint canvas, in the shape
    puppet_template.render() returns, so the rig previews (puppet_template.rig_from) can walk them."""
    V = spec["views"][view]
    piv = {n: joint(V, L["from"]) for n, L in V["limbs"].items()}
    piv["torso"], piv["head"] = joint(V, "pelvis"), joint(V, "neck")
    return {n: (imgs[n], None, piv[n]) for n in V["order"]}


def waist(V: dict, s: float, torso_cov: np.ndarray) -> list:
    """The waistband as a bowed line (rig px points) across the torso's width at the waist seam. `torso_cov` is the
    torso's coverage on a canvas of PX * s px."""
    sm = V["seams"]["waist"]; y = sm["y"]
    row = np.nonzero(torso_cov[int(y * s)] > 0.5)[0]
    if not len(row):
        return []
    x0, x1 = row.min() / s, row.max() / s; cx, hw = (x0 + x1) / 2, (x1 - x0) / 2
    return [(cx + hw * u, y + sm["bow"] * hw * (1 - u * u)) for u in np.linspace(-1, 1, 25)]


def compose(parts: dict, order, guides: bool = False) -> Image.Image:
    """The parts drawn over each other in `order` (RGBA, the parts' canvas size); with guides, each part's guide layer
    on top of it."""
    n_px = next(iter(parts.values()))[0].shape[0]
    cv = Image.new("RGBA", (n_px, n_px))
    for n in order:
        cv.alpha_composite(Image.fromarray(parts[n][0]))
        if guides:
            cv.alpha_composite(Image.fromarray(parts[n][1]))
    return cv


def head_box(parts: dict, grow_up: float = 0.45, grow_side: float = 0.3, grow_down: float = 0.55) -> tuple:
    """A square box (x0, y0, x1, y1) on the paint canvas around the head part grown by fractions of its size, and the
    head's mask. `parts` maps names to RGBA arrays ({"head": rgba} is enough)."""
    a = parts["head"][..., 3] > 127; ys, xs = np.nonzero(a)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max(); h, w = y1 - y0, x1 - x0
    bx0, bx1 = int(x0 - grow_side * w), int(x1 + grow_side * w); by0, by1 = int(y0 - grow_up * h), int(y1 + grow_down * h)
    side = max(bx1 - bx0, by1 - by0); cx, cy = (bx0 + bx1) // 2, (by0 + by1) // 2
    bx0 = int(np.clip(cx - side // 2, 0, N - side)); by0 = int(np.clip(cy - side // 2, 0, N - side))
    return (bx0, by0, bx0 + side, by0 + side), a


def figure_box(comp: Image.Image | np.ndarray, pad: int = 30) -> tuple:
    """The Paint step's figure-pass framing on the paint canvas: the whole figure in a square (the scale its faces came
    out well at)."""
    a = np.asarray(comp)[..., 3] > 127; ys, xs = np.nonzero(a)
    x0, y0, x1, y1 = xs.min() - pad, ys.min() - pad, xs.max() + pad, ys.max() + pad
    side = max(x1 - x0, y1 - y0); cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    bx = int(np.clip(cx - side // 2, 0, N - side)); by = int(np.clip(cy - side // 2, 0, N - side))
    return (bx, by, bx + side, by + side)


def face_ellipse(spec: dict, scale: float = 1.0) -> np.ndarray:
    """The face area (an ellipse around the eyes and mouth, from the template's face guides) as a paint-canvas mask."""
    fc = spec["views"]["front"]["shapes"]["head"].get("face")
    if not fc:
        raise ValueError("The Template has no face guides (Template tab).")
    e0, e1, m = (np.array(p) * S for p in (fc["eyes"][0], fc["eyes"][1], fc["mouth"]))
    c = (e0 + e1 + m) / 3; w = np.linalg.norm(e1 - e0)
    rx, ry = 0.95 * w * scale, 0.85 * (np.linalg.norm(m - (e0 + e1) / 2) + 0.35 * w) * scale
    yy, xx = np.mgrid[:N, :N]
    return ((xx - c[0]) / rx) ** 2 + ((yy - c[1]) / ry) ** 2 <= 1


def inner_head(head: np.ndarray, px: int = 12) -> np.ndarray:
    """The head part's mask shrunk by px: the face pass and the blank-face fill stay off its outline (no chin / jaw
    redraw)."""
    a = Image.fromarray(np.where(head[..., 3] > 127, 255, 0).astype(np.uint8))
    return np.asarray(a.filter(ImageFilter.MinFilter(2 * px + 1))) > 0
