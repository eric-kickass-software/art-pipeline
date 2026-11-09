"""The Dungeon Forge's public API: every action the Dungeon page (/dungeon) and agents can take.

The Dungeon Forge makes a set's isometric tiles and props (the other modules of hdart/tools/dungeon/ have the how). A
set (specs/dungeon_sets/<set>.json) holds pieces; a "family" is a master piece (method "master" / "wall_master") whose
approved master tile is cut into the family's variation x size pieces. Candidates are PNGs under
sprites/dungeon/<set>/candidates/<piece>/, named by `rel` ("candidates/<piece>/s<seed>_<time>.png"); approving one is
what dungeon-export ships to the game. The materials library (hdart/tools/dungeon/library.py) feeds families templates,
palettes and words.

Typical flow: dungeon.state → dungeon.generate (queues a job) → jobs.wait → dungeon.review → dungeon.approve →
dungeon.export.

Every function here is an op (hdart/core/ops.py): GET /api/ops lists them, POST /api/op/dungeon.<action> calls one (GET
for read-only ones). LEGACY / GET_LEGACY map the page's older /api/dun/<action> routes onto them, LIB_LEGACY /
LIB_GET_LEGACY its /api/lib/<action> routes.
"""
from hdart.core import jobs, ops

ops.param_docs("dungeon.", {
    "set": 'the set id, e.g. "crypt" (dungeon.sets lists them)',
    "piece": "a piece id from the set's spec (a family's master piece for family ops)",
    "rel": 'a candidate, as its path in the set folder: "candidates/<piece>/s<seed>_<time>.png"',
    "n": "how many candidates",
    "seed": "the first seed (each further candidate adds 1)",
    "on": "true to set, false to clear",
    "fields": "the spec fields to change (others are kept)",
    "top": "wall units kept as they are at the top (the cap)",
    "bottom": "wall units kept as they are at the bottom (the base)",
    "mode": 'how courses are fitted between the keep lines ("keep": whole courses)',
})


def _png(im, scale: int = 1, cache: str = "max-age=3600") -> ops.Blob:
    from hdart.tools.dungeon import sets as dsets
    return ops.Blob(dsets.png_bytes(im, scale), "image/png", cache)


def _wall_height(set: str, piece: str) -> int:
    from hdart.tools.dungeon import sets as dsets
    spec = dsets.load_set(set)
    return dsets.wall_family_settings(dsets.piece_of(spec, piece), spec)["height"]


def _candidate(set: str, rel: str):
    from hdart.tools.dungeon import sets as dsets
    from PIL import Image
    return Image.open(dsets.candidate_path(set, rel))


# ----------------------------------------------------------------------------- reading

@ops.op("dungeon.state", read_only=True)
def state(set: str) -> dict:
    """Everything the page shows for one set: the set list, its spec, state (candidates, approvals per piece), orphaned
    images, the material sheet, output tile and pixel steps, per-family info, and the style groups."""
    from hdart.tools.dungeon import geometry as geo, sets as dsets, preview as dpreview
    spec = dsets.load_set(set)
    return {"sets": dsets.list_sets(), "spec": spec, "state": dsets.state(set), "orphans": dsets.orphans(set),
            "sheet": dsets.sheet(spec), "output_tile": geo.output_tile(spec), "pixel_steps": geo.pixel_steps(spec),
            "families": {p["id"]: dpreview.family_info(set, spec, p)
                         for p in spec["pieces"] if p.get("method") in ("master", "wall_master")},
            "style": dsets.style(), "set_style": dsets.set_style(spec)["groups"]}


@ops.op("dungeon.sets", read_only=True)
def sets() -> list:
    """Every set: [{id, name, biome, ...}]."""
    from hdart.tools.dungeon import sets as dsets
    return dsets.list_sets()


@ops.op("dungeon.review", read_only=True)
def review(set: str, piece: str) -> list:
    """A family's candidates waiting for a decision (not approved, not discarded)."""
    from hdart.tools.dungeon import generation as gen
    return gen.review_list(set, piece)


@ops.op("dungeon.recipes", read_only=True)
def recipes() -> list:
    """The saved family recipes (library/recipes/), for dungeon.apply_recipe."""
    from hdart.tools.dungeon import generation as gen
    return gen.list_recipes()


@ops.op("dungeon.patterns", read_only=True)
def patterns() -> dict:
    """The layout-template patterns with their default parameters (the template editor's list)."""
    from hdart.tools.dungeon import patterns as pt
    return pt.PATTERNS


@ops.op("dungeon.family_palette", read_only=True)
def family_palette(set: str, piece: str) -> dict:
    """A family board's colour drift per image (how far each approved sprite strays from the family's tones)."""
    from hdart.tools.dungeon import generation as gen
    return gen.family_palette(set, piece)


@ops.op("dungeon.family_layout", read_only=True)
def family_layout(set: str, piece: str, seed: int = 0, w: int = 16, h: int = 12) -> dict:
    """Live preview data of a family's floor: which sprite goes where on a w x h board (plus "rev" for image URLs).

    Args:
        seed: the planner's seed
        w: board width in tiles
        h: board height in tiles
    """
    from hdart.tools.dungeon import sets as dsets, preview as dpreview
    lay = dpreview.family_layout(set, piece, seed, w, h)
    return {**lay, "rev": dpreview.preview_rev(set, dsets.load_set(set))}


@ops.op("dungeon.room_layout", read_only=True)
def room_layout(set: str, floor: str | None = None, wall: str | None = None, blockout: bool = False, w: int = 14,
                h: int = 10) -> dict:
    """Live preview data of a room: a family's floor board plus the joined walls and doors (plus "rev").

    Args:
        floor: the floor family to lay (default: the set's floor)
        wall: the wall family
        blockout: use only the given families' approved art (others as blockout)
        w: room width in tiles
        h: room height in tiles
    """
    from hdart.tools.dungeon import geometry as geo, sets as dsets, generation as gen, preview as dpreview
    spec = dsets.load_set(set)
    fb = None
    if floor:
        fp = next((p for p in spec["pieces"] if p["id"] == floor), None)
        fb = fp.get("board", fp["id"]) if fp else None
    inc = gen.blockout_include(set, floor, wall) if blockout else None
    lay = dpreview.room_preview(set, geo.output_tile(spec), 1, inc, dpreview.room_grid(w, h), fb, True)
    return {**lay, "rev": dpreview.preview_rev(set, spec)}


@ops.op("dungeon.elevation_measure", read_only=True)
def elevation_measure(set: str, piece: str, rel: str, top: float | None = None, bottom: float | None = None) -> dict:
    """A wall master's courses in the field between the kept cap and base (top / bottom default to dungeon.KEEP_TOP
    / KEEP_BOTTOM)."""
    from hdart.tools.dungeon import generation as gen
    return gen.measure_elevation(_candidate(set, rel), _wall_height(set, piece), gen.KEEP_TOP if top is None else top,
                                gen.KEEP_BOTTOM if bottom is None else bottom)


@ops.op("dungeon.elevation_joints", read_only=True)
def elevation_joints(set: str, piece: str, rel: str) -> dict:
    """A wall master's horizontal joints (units from the top) and suggested keep bands."""
    from hdart.tools.dungeon import generation as gen
    return gen.elevation_joints(_candidate(set, rel), _wall_height(set, piece))


@ops.op("dungeon.fit_courses_info", read_only=True)
def fit_courses_info(set: str, piece: str, rel: str, top: float = 0.0, bottom: float = 0.0, mode: str = "keep") -> dict:
    """The stretch numbers of fitting a wall master's courses between its keep lines (see
    dungeon.fit_courses_image)."""
    from hdart.tools.dungeon import generation as gen
    return gen.fit_courses(_candidate(set, rel), _wall_height(set, piece), top, bottom, mode)[1]


@ops.op("dungeon.fit_courses_image", read_only=True)
def fit_courses_image(set: str, piece: str, rel: str, top: float = 0.0, bottom: float = 0.0, mode: str = "keep") -> ops.Blob:
    """PNG: a wall master with its courses fitted between the keep lines, repeated sideways twice."""
    from hdart.tools.dungeon import generation as gen
    import numpy as np
    from PIL import Image
    img, _ = gen.fit_courses(_candidate(set, rel), _wall_height(set, piece), top, bottom, mode)
    return _png(Image.fromarray(np.tile(np.asarray(img), (1, 2, 1))))


@ops.op("dungeon.elevation_bands", read_only=True)
def elevation_bands(set: str, piece: str, rel: str, top: float | None = None, bottom: float | None = None) -> ops.Blob:
    """PNG: a wall master repeated sideways with the kept cap and base tinted."""
    from hdart.tools.dungeon import generation as gen
    return _png(gen.elevation_bands(set, rel, _wall_height(set, piece), gen.KEEP_TOP if top is None else top,
                                   gen.KEEP_BOTTOM if bottom is None else bottom))


@ops.op("dungeon.template_image", read_only=True)
def template_image(set: str, piece: str) -> ops.Blob:
    """PNG: a family's layout template on its own."""
    from hdart.tools.dungeon import generation as gen
    return _png(gen.template_preview(set, piece))


@ops.op("dungeon.cell_image", read_only=True)
def cell_image(set: str, piece: str, rel: str, suf: str = "") -> ops.Blob:
    """PNG: one cut sprite of a candidate (cached).

    Args:
        suf: which cell of a multi-tile piece ("i<x>j<y>"; "" for a one-tile piece)
    """
    from hdart.tools.dungeon import preview as dpreview
    return ops.Blob(dpreview.cell_png(set, piece, rel, suf), "image/png", "max-age=3600")


@ops.op("dungeon.family_image", read_only=True)
def family_image(set: str, piece: str, seed: int = 0, outline: bool = False, view: str = "iso") -> ops.Blob:
    """PNG: a planner-laid floor from a family.

    Args:
        outline: draw the block outlines
        view: "iso" or "top"
    """
    from hdart.tools.dungeon import preview as dpreview
    return _png(dpreview.family_preview(set, piece, seed, outline, view))


@ops.op("dungeon.seam_image", read_only=True)
def seam_image(set: str, piece: str, rel: str) -> ops.Blob:
    """PNG: a master (or repaint) candidate among copies of the master tile, to judge its seams."""
    from hdart.tools.dungeon import generation as gen
    return _png(gen.seam_preview(set, piece, rel))


@ops.op("dungeon.candidate_image", read_only=True)
def candidate_image(set: str, rel: str) -> ops.Blob:
    """PNG: a candidate as generated (8x the native size)."""
    from hdart.tools.dungeon import sets as dsets
    return ops.Blob(dsets.candidate_path(set, rel).read_bytes(), "image/png", "max-age=3600")


@ops.op("dungeon.native_image", read_only=True)
def native_image(set: str, piece: str, rel: str, tile: int = 0, scale: int = 1) -> ops.Blob:
    """PNG: a candidate at the game's pixel size.

    Args:
        tile: the output tile width (0: the set's)
        scale: nearest-neighbour zoom
    """
    from hdart.tools.dungeon import cells as dcells
    return _png(dcells.native(set, piece, rel, tile or None), scale)


@ops.op("dungeon.room_image", read_only=True)
def room_image(set: str, piece: str | None = None, rel: str | None = None, tile: int = 0, scale: int = 2) -> ops.Blob:
    """PNG: a sample room from the set's approved art (with piece + rel: that candidate tried in it).

    Args:
        tile: the output tile width (0: the set's)
        scale: nearest-neighbour zoom
    """
    from hdart.tools.dungeon import preview as dpreview
    inc = {piece: rel} if piece and rel else None
    return _png(dpreview.room_preview(set, tile or None, scale, inc), cache="no-store")


@ops.op("dungeon.captures", read_only=True)
def captures() -> list:
    """The file names of the last Prototype V capture (dungeon.capture), for dungeon.capture_image."""
    from hdart.tools.dungeon import capture as dcapture
    return sorted(p.name for p in dcapture.captures_dir().glob("v0*.png"))


@ops.op("dungeon.capture_image", read_only=True)
def capture_image(f: str) -> ops.Blob:
    """PNG: one image of the last Prototype V capture.

    Args:
        f: its file name (dungeon.captures lists them)
    """
    from hdart.tools.dungeon import capture as dcapture
    root = dcapture.captures_dir().resolve()
    p = (root / f).resolve()
    if root not in p.parents or p.suffix != ".png":
        raise ops.OpError(f"not a capture: {f!r}")
    return ops.Blob(p.read_bytes(), "image/png", "no-store")


# ----------------------------------------------------------------------------- sets, pieces, style

@ops.op("dungeon.save_style")
def save_style(groups: dict | None = None, prop_view: str | None = None, prop_framing: str | None = None,
               prop_medium: str | None = None) -> dict:
    """Save the shared prompt style (specs/dungeon_style.json): style groups and the prop wording. Returns {"style"}.

    Args:
        groups: {group: words} for the style groups (medium, style, view, rendering, framing)
        prop_view: the props' view words
        prop_framing: the props' framing words
        prop_medium: the props' medium words
    """
    from hdart.tools.dungeon import sets as dsets
    return {"style": dsets.save_style(groups, prop_view, prop_framing, prop_medium)}


@ops.op("dungeon.new_set")
def new_set(id: str, name: str | None = None, biome: str = "", copy_from: str = "crypt") -> dict:
    """Make a new set by copying another set's pieces. Returns {"spec"}.

    Args:
        id: the new set's id (lowercase, a file name)
        name: its display name (default: from the id)
        biome: the biome words for its prompts
        copy_from: the set whose pieces it starts from
    """
    from hdart.tools.dungeon import sets as dsets
    return {"spec": dsets.new_set(id, name, biome, copy_from)}


@ops.op("dungeon.update_set")
def update_set(set: str, fields: dict) -> dict:
    """Change a set's own fields (name, biome, pixel, harmonize, tile...). Returns {"spec"}."""
    from hdart.tools.dungeon import sets as dsets
    return {"spec": dsets.update_set(set, fields)}


@ops.op("dungeon.save_piece")
def save_piece(set: str, piece: dict, old_id: str | None = None) -> dict:
    """Create or change a piece in the set's spec; renaming moves its candidates. Returns {"spec"}.

    Args:
        piece: the piece's definition (id, kind, prompt, method, family...)
        old_id: its current id when renaming it
    """
    from hdart.tools.dungeon import sets as dsets
    return {"spec": dsets.upsert_piece(set, piece, old_id)}


@ops.op("dungeon.delete_piece")
def delete_piece(set: str, piece: str) -> dict:
    """Remove a piece from the spec; its images go to archive/ (nothing is deleted). Returns {"spec"}."""
    from hdart.tools.dungeon import sets as dsets
    return {"spec": dsets.delete_piece(set, piece)}


@ops.op("dungeon.restore_piece")
def restore_piece(set: str, piece: str) -> dict:
    """Put an orphaned piece (images whose piece left the spec) back into the spec. Returns {"spec"}."""
    from hdart.tools.dungeon import sets as dsets
    return {"spec": dsets.restore_piece(set, piece)}


@ops.op("dungeon.archive_orphan")
def archive_orphan(set: str, piece: str) -> dict:
    """Move an orphaned piece's images to archive/."""
    from hdart.tools.dungeon import sets as dsets
    dsets.archive_orphan(set, piece)
    return {"ok": True}


@ops.op("dungeon.archive_set")
def archive_set(set: str) -> dict:
    """Move a whole set (spec and images) to archive/. Returns {"path", "sets"}."""
    from hdart.tools.dungeon import sets as dsets
    return {"path": str(dsets.archive_set(set)), "sets": dsets.list_sets()}


# ----------------------------------------------------------------------------- review

@ops.op("dungeon.approve")
def approve(set: str, piece: str, rel: str, on: bool = True) -> dict:
    """Approve (or with on=false, un-approve) a candidate of a piece. Returns {"piece": its state}."""
    from hdart.tools.dungeon import sets as dsets
    return {"piece": dsets.approve(set, piece, rel, on)}


@ops.op("dungeon.discard")
def discard(set: str, piece: str, rel: str) -> dict:
    """Move a candidate to archive/ (nothing is deleted). Returns {"piece": its state}."""
    from hdart.tools.dungeon import sets as dsets
    return {"piece": dsets.discard(set, piece, rel)}


@ops.op("dungeon.star")
def star(set: str, piece: str, rel: str, on: bool = True) -> dict:
    """Star (or unstar) a candidate, to keep it in view. Returns {"piece": its state}."""
    from hdart.tools.dungeon import generation as gen
    return {"piece": gen.star(set, piece, rel, on)}


@ops.op("dungeon.promote")
def promote(set: str, piece: str, rel: str) -> dict:
    """Make a candidate the family's active master (its variations and sizes are cut from it). Returns {"piece"}."""
    from hdart.tools.dungeon import generation as gen
    return {"piece": gen.promote(set, piece, rel)}


@ops.op("dungeon.archive_unapproved")
def archive_unapproved(set: str, piece: str) -> dict:
    """Move every candidate of a piece that isn't approved (or starred) to archive/. Returns {"piece", "count"}."""
    from hdart.tools.dungeon import sets as dsets
    ps, n = dsets.archive_unapproved(set, piece)
    return {"piece": ps, "count": n}


@ops.op("dungeon.even")
def even(set: str, piece: str, rel: str, strength: float | None = None) -> dict:
    """Make a new master from a candidate with stone-sized tone differences evened out (instant, no FLUX).
    Returns {"rel": the new candidate, "piece": its state}.

    Args:
        strength: 0..1 (default: dungeon.EVEN_TONE)
    """
    from hdart.tools.dungeon import sets as dsets, generation as gen
    rel = gen.even_master(set, piece, rel, dsets.EVEN_TONE if strength is None else strength)
    return {"rel": rel, "piece": dsets.state(set)["pieces"].get(piece)}


@ops.op("dungeon.refresh_previews")
def refresh_previews() -> dict:
    """Forget every cached cut sprite and colour reference (the preview's force refresh)."""
    from hdart.tools.dungeon import preview as dpreview
    dpreview.clear_preview_caches()
    return {"ok": True}


# ----------------------------------------------------------------------------- generation (queued jobs)

def _queue(kind: str, args: dict, label: str) -> dict:
    return {"job": jobs.enqueue(kind, {k: v for k, v in args.items() if v is not None}, label)["id"]}


@ops.op("dungeon.generate", queues="dun")
def generate(set: str, piece: str, n: int = 3, seed: int = 1, prompt: str | None = None, denoise: float | None = None,
             lora: float | None = None, variation: str | None = None, draft: bool = False) -> dict:
    """Queue n new candidates of a piece. Returns {"job": id}.

    Args:
        prompt: the prompt to use instead of the piece's own
        denoise: img2img strength (default: the piece's)
        lora: LoRA strength (default: the piece's)
        variation: a family variation id to generate
        draft: a quick low-step draft (dungeon.finalize makes a full master from it)
    """
    return _queue("dun", dict(set=set, piece=piece, n=n, seed=seed, prompt=prompt, denoise=denoise, lora=lora,
                              variation=variation, draft=draft or None),
                  f"{set} · {piece} ×{n}{' draft' if draft else ''}")


@ops.op("dungeon.finalize", queues="dun_finalize")
def finalize(set: str, piece: str, rel: str, seed: int | None = None) -> dict:
    """Queue a full master from a draft candidate (same composition). Returns {"job": id}."""
    return _queue("dun_finalize", dict(set=set, piece=piece, rel=rel, seed=seed), f"{set} · {piece} finalize draft")


@ops.op("dungeon.edit", queues="dun_edit")
def edit(set: str, piece: str, rel: str, prompt: str = "", denoise: float = 0.5, seed: int = 1) -> dict:
    """Queue a prompted repaint of a candidate (a new candidate; the old one stays). Returns {"job": id}.

    Args:
        prompt: what to change
        denoise: how much FLUX may change
    """
    return _queue("dun_edit", dict(set=set, piece=piece, rel=rel, prompt=prompt, denoise=denoise, seed=seed),
                  f"{set} · {piece} edit: {prompt[:30]}")


@ops.op("dungeon.rescale", queues="dun_rescale")
def rescale(set: str, piece: str, rel: str, factor: float | None = None, fx: float | None = None,
            fy: float | None = None, fit: bool = False, mode: str = "keep", keep_top: float | None = None,
            keep_bottom: float | None = None, redetail: bool = False, seed: int = 1) -> dict:
    """Queue a rescaled master: by factor (stones bigger or smaller), by fx/fy (a wall's brick width/height, cap and
    base kept), or with fit=true (a wall's courses fitted between the keep lines). Returns {"job": id}.

    Args:
        factor: uniform scale
        fx: wall master: horizontal scale (with fy)
        fy: wall master: vertical scale (with fx)
        fit: wall master: fit whole courses between keep_top and keep_bottom
        keep_top: wall units kept as they are at the top (the cap)
        keep_bottom: wall units kept as they are at the bottom (the base)
        redetail: a light FLUX pass afterwards to restore detail
    """
    if fit:
        label = f"fit courses ({mode})"
    elif fx is not None or fy is not None:
        if fx is None or fy is None:
            raise ops.OpError("dungeon.rescale: give both fx and fy")
        label = f"×{fx} wide ×{fy} tall"
    elif factor is not None:
        label = f"×{factor}"
    else:
        raise ops.OpError("dungeon.rescale: give factor, fx + fy, or fit=true")
    return _queue("dun_rescale", dict(set=set, piece=piece, rel=rel, factor=factor, fx=fx, fy=fy, fit=fit or None,
                                      mode=mode, keep_top=keep_top, keep_bottom=keep_bottom, redetail=redetail,
                                      seed=seed), f"{set} · {piece} {label}")


@ops.op("dungeon.trial", queues="dun_trial")
def trial(set: str, piece: str, rel: str, variation: str = "", seed: int = 1) -> dict:
    """Queue a trial of a variation wording on a master candidate. Returns {"job": id}.

    Args:
        variation: the variation's words
    """
    return _queue("dun_trial", dict(set=set, piece=piece, rel=rel, variation=variation, seed=seed),
                  f"{set} · {piece} trial: {variation[:30]}")


@ops.op("dungeon.generate_family", queues="dun")
def generate_family(set: str, piece: str, variations: list | None = None, sizes: list | None = None, n: int = 1,
                    doors: bool = False) -> dict:
    """Queue a family's variations x sizes x n from its active master (doors: the door rows). Returns {"jobs": ids,
    "spec"}.

    Args:
        variations: the variation ids (default: all)
        sizes: the sizes (default: all)
        doors: generate the door rows instead
    """
    from hdart.tools.dungeon import sets as dsets, generation as gen
    ids = [jobs.enqueue("dun", j, f"{set} · {j['piece']} · {j['variation']}")["id"]
           for j in gen.variant_jobs(set, piece, variations, sizes, n, doors)]
    return {"jobs": ids, "spec": dsets.load_set(set)}


@ops.op("dungeon.fill_family", queues="dun")
def fill_family(set: str, piece: str, n: int = 1) -> dict:
    """Queue only a family's gaps (variation x size cells and doors without a candidate). Returns {"jobs": ids}."""
    from hdart.tools.dungeon import generation as gen
    return {"jobs": [jobs.enqueue("dun", j, f"{set} · {j['piece']} · {j['variation']} (fill)")["id"]
                     for j in gen.fill_jobs(set, piece, n)]}


@ops.op("dungeon.build_walls", queues="dun_build")
def build_walls(set: str, piece: str) -> dict:
    """Queue projecting a wall family's elevations onto its wall pieces (runs, post, doorway). Returns {"job": id}."""
    return _queue("dun_build", dict(set=set, piece=piece), f"{set} · {piece} build walls")


@ops.op("dungeon.capture", queues="dun_capture")
def capture(set: str) -> dict:
    """Queue a Unity batch capture of Prototype V with the set's exported art (Unity must not have the project open).
    Returns {"job": id}; dungeon.captures lists the images."""
    return _queue("dun_capture", dict(set=set), f"{set} · Prototype V capture")


# ----------------------------------------------------------------------------- families, sheet, recipes

@ops.op("dungeon.apply_material")
def apply_material(set: str, piece: str, material: str, use: list | None = None, strength: float = 0.65) -> dict:
    """Use a library material on a family. Returns {"spec"}.

    Args:
        material: the material id (dungeon.materials lists them)
        use: what to take: any of "template", "palette", "words" (default: template and words)
        strength: how strongly the template guides FLUX
    """
    from hdart.tools.dungeon import generation as gen
    return {"spec": gen.apply_material(set, piece, material, use or ["template", "words"], strength)}


@ops.op("dungeon.link_family")
def link_family(set: str, piece: str, on: bool = True) -> dict:
    """Link (or unlink) a family's layout template to the set's material sheet. Returns {"spec"}."""
    from hdart.tools.dungeon import sets as dsets, generation as gen
    with dsets.LOCK:
        spec = dsets.load_set(set)
        return {"spec": dsets.upsert_piece(set, gen.link_family(spec, dsets.piece_of(spec, piece), on))}


@ops.op("dungeon.matching_wall")
def matching_wall(set: str, piece: str, slug: str | None = None) -> dict:
    """Make (or find) a wall family that goes with a floor family. Returns {"piece": the wall family}.

    Args:
        slug: the new family's id part (default: from the floor's)
    """
    from hdart.tools.dungeon import generation as gen
    return {"piece": gen.matching_wall_family(set, piece, slug)}


@ops.op("dungeon.archive_family")
def archive_family(set: str, piece: str) -> dict:
    """Delete a family: its pieces leave the spec, its images go to archive/families/. Returns {"path"}."""
    from hdart.tools.dungeon import sets as dsets
    return {"path": str(dsets.archive_family(set, piece))}


@ops.op("dungeon.update_sheet")
def update_sheet(set: str, fields: dict) -> dict:
    """Set material sheet fields; the linked families' templates follow. Returns {"spec"}."""
    from hdart.tools.dungeon import generation as gen
    return {"spec": gen.update_sheet(set, fields)}


@ops.op("dungeon.sheet_from_floor")
def sheet_from_floor(set: str, piece: str) -> dict:
    """Fill the material sheet from a floor family's master and template, and link that family. Returns {"spec"}."""
    from hdart.tools.dungeon import generation as gen
    return {"spec": gen.sheet_from_floor(set, piece)}


@ops.op("dungeon.save_recipe")
def save_recipe(set: str, pieces: list, title: str, with_sheet: bool = True, notes: str = "") -> dict:
    """Save families (and the sheet) as a reusable recipe (library/recipes/). Returns {"recipe"}.

    Args:
        pieces: the families' master piece ids
        title: the recipe's name
        with_sheet: include the material sheet
        notes: free text kept with the recipe
    """
    from hdart.tools.dungeon import generation as gen
    return {"recipe": gen.save_recipe(set, pieces, title, with_sheet, notes)}


@ops.op("dungeon.apply_recipe")
def apply_recipe(set: str, recipe: str, use_sheet: bool = True, replace_sheet: bool = False) -> dict:
    """Add new families to a set from a recipe. Returns {"pieces": the new master ids}.

    Args:
        recipe: the recipe id (dungeon.recipes lists them)
        use_sheet: take the recipe's material sheet when the set has none yet
        replace_sheet: take the recipe's material sheet even over the set's own
    """
    from hdart.tools.dungeon import generation as gen
    return {"pieces": gen.apply_recipe(set, recipe, "replace" if replace_sheet else use_sheet)}


# ----------------------------------------------------------------------------- export

@ops.op("dungeon.export")
def export(set: str) -> dict:
    """Export a set's approved art into the active project (its dungeon pack folder, rebuilt whole). Returns {"path"}."""
    from hdart.tools.dungeon import export as dexport
    return {"path": str(dexport.export(set))}


# ----------------------------------------------------------------------------- the materials library

@ops.op("dungeon.materials", read_only=True)
def materials() -> list:
    """Every material in the library (library/materials/*.json)."""
    from hdart.tools.dungeon import library
    return library.list_materials()


@ops.op("dungeon.search_materials", read_only=True)
def search_materials(q: str = "", src: str = "all") -> list:
    """Search the CC0 libraries (ambientCG, Poly Haven) for textures to import.

    Args:
        q: search words
        src: "all", "ambientcg" or "polyhaven"
    """
    from hdart.tools.dungeon import library
    return library.search(q, src)


@ops.op("dungeon.material_image", read_only=True)
def material_image(id: str, kind: str = "color", size: int | None = None) -> ops.Blob:
    """PNG: a library material's map.

    Args:
        id: the material id
        kind: "color" or "height"
        size: square size in px (default: as stored)
    """
    import io
    from PIL import Image
    from hdart.tools.dungeon import library
    im = Image.open(library.image_path(id, kind)).convert("RGB")
    if size:
        im = im.resize((size, size))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return ops.Blob(buf.getvalue(), "image/png", "no-store")


@ops.op("dungeon.import_material", queues="lib_import")
def import_material(source: str, asset: str, hit: dict | None = None) -> dict:
    """Queue downloading a texture into the library (no GPU). Returns {"job": id}.

    Args:
        source: "ambientcg" or "polyhaven"
        asset: the asset id there
        hit: the search result it came from (name, tags...)
    """
    return {"job": jobs.enqueue("lib_import", {"source": source, "asset": asset, "hit": hit},
                                f"library · import {source} {asset}")["id"]}


@ops.op("dungeon.save_material")
def save_material(material: dict) -> dict:
    """Save a library material's fields. Returns {"material"}.

    Args:
        material: the whole material document (id, name, tags, descriptors, size_m, palette...)
    """
    from hdart.tools.dungeon import library
    return {"material": library.save(material)}


# ----------------------------------------------------------------------------- the page's older routes

# POST /api/dun/<action> -> (op, {old argument name: op argument name}[, fixed arguments]) (see ops.call_alias).
# Kept so studio/dungeon.html works unchanged; new callers use /api/op/<name>.
LEGACY = {
    "style": ("dungeon.save_style", {}),
    "set/new": ("dungeon.new_set", {"from": "copy_from"}),
    "set/update": ("dungeon.update_set", {}),
    "set/archive": ("dungeon.archive_set", {}),
    "piece/save": ("dungeon.save_piece", {}),
    "piece/delete": ("dungeon.delete_piece", {}),
    "piece/restore": ("dungeon.restore_piece", {}),
    "orphan/archive": ("dungeon.archive_orphan", {}),
    "generate": ("dungeon.generate", {}),
    "finalize": ("dungeon.finalize", {}),
    "approve": ("dungeon.approve", {}),
    "discard": ("dungeon.discard", {}),
    "star": ("dungeon.star", {}),
    "promote": ("dungeon.promote", {}),
    "edit": ("dungeon.edit", {}),
    "even": ("dungeon.even", {}),
    "preview/refresh": ("dungeon.refresh_previews", {}),
    "rescale": ("dungeon.rescale", {}),
    "trial": ("dungeon.trial", {}),
    "family/material": ("dungeon.apply_material", {}),
    "family/fill": ("dungeon.fill_family", {}),
    "family/build": ("dungeon.build_walls", {}),
    "family/generate": ("dungeon.generate_family", {}),
    "family/link": ("dungeon.link_family", {}),
    "family/matching_wall": ("dungeon.matching_wall", {}),
    "family/archive": ("dungeon.archive_family", {}),
    "archive_unapproved": ("dungeon.archive_unapproved", {}),
    "sheet/update": ("dungeon.update_sheet", {}),
    "sheet/from_floor": ("dungeon.sheet_from_floor", {}),
    "recipe/save": ("dungeon.save_recipe", {}),
    "recipe/apply": lambda b: ops.call("dungeon.apply_recipe", {   # the page sent use_sheet "replace"
        "set": b.get("set"), "recipe": b.get("recipe"), "use_sheet": b.get("use_sheet", True) not in (False, None),
        "replace_sheet": b.get("use_sheet") == "replace"}),
    "export": ("dungeon.export", {}),
    "capture": ("dungeon.capture", {}),
}

# GET /api/dun/<path>?<query> -> op. The page's query names are the ops' argument names.
GET_LEGACY = {
    "state": ("dungeon.state", {}),
    "sets": ("dungeon.sets", {}),
    "room_layout": ("dungeon.room_layout", {}),
    "template": ("dungeon.template_image", {}),
    "patterns": ("dungeon.patterns", {}),
    "family_palette": ("dungeon.family_palette", {}),
    "recipes": ("dungeon.recipes", {}),
    "review": ("dungeon.review", {}),
    "family_layout": ("dungeon.family_layout", {}),
    "elev_measure": ("dungeon.elevation_measure", {}),
    "elev_joints": ("dungeon.elevation_joints", {}),
    "elev_fit": ("dungeon.fit_courses_image", {}),
    "elev_fit_info": ("dungeon.fit_courses_info", {}),
    "elev_bands": ("dungeon.elevation_bands", {}),
    "cell": ("dungeon.cell_image", {}),
    "family": ("dungeon.family_image", {}),
    "seam": ("dungeon.seam_image", {}),
    "img": ("dungeon.candidate_image", {}),
    "native": ("dungeon.native_image", {}),
    "room": ("dungeon.room_image", {}),
    "capture": ("dungeon.capture_image", {}),
    "captures": ("dungeon.captures", {}),
}

# /api/lib/<action> (the Library panel of the Dungeon page)
LIB_LEGACY = {
    "import": ("dungeon.import_material", {}),
    "save": ("dungeon.save_material", {}),
}
LIB_GET_LEGACY = {
    "list": ("dungeon.materials", {}),
    "search": ("dungeon.search_materials", {}),
    "img": ("dungeon.material_image", {}),
}
