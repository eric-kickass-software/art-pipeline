"""The Item Workshop's public API: every action the Studio's Items tabs, the Workshop Editor and agents can take.

The Item Workshop makes equipment as overlay layers on a race's painted puppet (hdart/puppet_items.py has the how).
Each Workshop piece (`item.<words>`, specs/items.json) is either fitted (zones on the Template's parts, one FLUX pass
on the whole puppet, keyed and split per part) or held (painted alone, put on a hand at its grip). A piece's candidates
live in sprites/ai/items/<race>/<item>/<candidate>/; approving one (per fit key: "all", or the build for per-sex
pieces) is what item-export ships to the game.

Typical flow: items.state → items.generate (queues a job) → items.candidates → items.approve → items.export.

Every function here is an op (hdart/ops.py): GET /api/ops lists them, POST /api/op/items.<action> calls one.
LEGACY maps the page's older /api/ws/items/<action> routes onto them.
"""
from . import jobs, ops

RACE = "human"   # the default race of every op (the only race with painted puppets so far)

ops.param_docs("items.", {
    "item": 'the Workshop piece id, e.g. "item.iron_helm" (items.state lists them under spec.items)',
    "candidate": 'a candidate id: its folder name, "s<seed>_<time>" (items.candidates lists them)',
    "build": '"<race>.<sex>", e.g. "human.male"',
    "view": '"front" or "back"',
    "race": "the race whose painted puppets the items are fitted on",
    "sex": '"male" or "female" for a per-sex fit; omit for the shared one',
    "n": "how many candidates",
    "seed": "the first seed (each further candidate adds 1)",
    "dry_run": "only report what would happen",
})


def _pi():
    """puppet_items as the Studio reloaded it (imported per call so a reload is picked up)."""
    from . import puppet_items
    return puppet_items


# ----------------------------------------------------------------------------- reading

@ops.op("items.state", read_only=True)
def state(race: str = RACE) -> dict:
    """Everything the Items tab shows: the spec (pieces, materials, prompts), candidates, approvals, grips, vocabulary."""
    return _pi().state(race)


@ops.op("items.candidates", read_only=True)
def candidates(item: str, race: str = RACE) -> dict:
    """One piece's candidates (newest first) with their preview images and which fit keys approve them."""
    pi = _pi()
    return {"candidates": pi.candidates(race, item), "approved": pi.load_state(race)["approved"].get(item, {})}


@ops.op("items.prompts", read_only=True)
def prompts(item: str, build: str, race: str = RACE) -> dict:
    """The composed prompts a piece is generated with on one build (what Generate would send)."""
    return _pi().prompts(race, build, item)


@ops.op("items.zone", read_only=True)
def zone(item: str, build: str, view: str, race: str = RACE) -> dict:
    """The zone editor's document: the puppet, the piece's painted zone mask and detail sketch for one view."""
    return _pi().zone_doc(race, item, build, view)


@ops.op("items.grip_prompts", read_only=True)
def grip_prompts(build: str) -> dict:
    """The grip-hand prompts (front, back) composed for one build."""
    return _pi().grip_prompts(build)


@ops.op("items.held_stage", read_only=True)
def held_stage(item: str, build: str, candidate: str | None = None, unsaved: dict | None = None,
               ramps: dict | None = None, race: str = RACE) -> dict:
    """The live grip stage: the puppet's rest pose and the held item's layer, which the page turns on the hand.

    Args:
        candidate: the candidate to show (default: the approved or newest one)
        unsaved: piece settings not saved yet (hand, held_as, grip...) applied for this view only
        ramps: {material: ramp name} to colour the item with
    """
    return _pi().held_stage(race, item, build, candidate, unsaved, ramps)


@ops.op("items.held_preview", read_only=True)
def held_preview(item: str, candidate: str, build: str, unsaved: dict | None = None, ramps: dict | None = None) -> dict:
    """A held item in the hand with unsaved settings: rest + two walk frames per view, as PNG data URLs."""
    return _pi().held_preview(item, candidate, build, unsaved, ramps)


@ops.op("items.layers", read_only=True)
def layers(item: str, candidate: str, race: str = RACE) -> dict:
    """A candidate's per-part layers and band maps, for editing in the Workshop Editor."""
    return _pi().layer_doc(race, item, candidate)


@ops.op("items.editor_walk", read_only=True)
def editor_walk(build: str, paint: str, view: str = "front", items: list | None = None, hair: dict | None = None,
                face: dict | None = None, skin: str | None = None, race: str = RACE) -> dict:
    """A painted puppet's walk cycle wearing the given items, hair and face in the chosen ramps (Workshop Editor).

    Args:
        paint: the Paint candidate id of the puppet
        items: [[item, candidate, {material: ramp}], ...] to wear
        hair: {"id", "ramp"} of a hair layer; face: the same for a face
        skin: the skin ramp (default: as painted)
    """
    return _pi().editor_walk(race, build, paint, view, items or [], hair, face, skin)


@ops.op("items.ramp_colours", read_only=True)
def ramp_colours() -> dict:
    """Every ramp of every palettes group as colour lists: {group: {ramp: [[r, g, b], ...]}}."""
    return _pi().ramp_colours()


@ops.op("items.outfit", read_only=True)
def outfit(build: str, items: list | None = None, ramps: dict | None = None, hair: str | None = None, hair_ramp: str | None = None,
           walk: bool = True, candidates: dict | None = None, part: str | None = None, race: str = RACE) -> dict:
    """Dress-up preview: a build wearing several items (rest pose, and the walk unless walk is false).

    Args:
        items: the piece ids to wear
        ramps: {item: {material: ramp}}
        candidates: {item: candidate} to wear instead of the approved ones
        part: "rest" or "walk" for only that half
    """
    return _pi().outfit(race, build, items or [], ramps, hair, hair_ramp, walk, candidates, part)


# ----------------------------------------------------------------------------- editing pieces and zones

@ops.op("items.save_item")
def save_item(item: str, definition: dict, old_id: str | None = None, race: str = RACE) -> dict:
    """Create or change a Workshop piece (specs/items.json); renaming moves its zones and candidates. Returns the state.

    Args:
        definition: the piece's fields (slot, kind, text, materials, depth, bulk, fit, hand, grip...)
        old_id: the piece's current id when renaming it
    """
    _pi().save_item(item, definition, old_id)
    return state(race)


@ops.op("items.save_prompts")
def save_prompts(prompts: dict, race: str = RACE) -> dict:
    """Save the shared prompt wording of the Item Workshop (specs/items.json "prompts"). Returns the state."""
    _pi().save_prompts(prompts)
    return state(race)


@ops.op("items.save_zone")
def save_zone(item: str, view: str, png: str, sex: str | None = None, sketch: str | None = None) -> dict:
    """Save a piece's painted zone (where it sits on the puppet) and optional detail sketch for one view.

    Args:
        png: the zone mask as a PNG data URL
        sex: "male"/"female" for a per-sex fit, else shared
        sketch: the detail sketch (material keys + dark lines) as a PNG data URL
    """
    return _pi().save_zone(item, view, png, sex, sketch)


@ops.op("items.reset_zone")
def reset_zone(item: str, view: str, sex: str | None = None) -> dict:
    """Throw away a piece's painted zone for one view (back to the zones in its definition)."""
    return _pi().reset_zone(item, view, sex)


@ops.op("items.save_grip_spec")
def save_grip_spec(prompt: str | None = None, prompt_back: str | None = None, wrist: float | None = None,
                   race: str = RACE) -> dict:
    """Save the grip hands' prompts and wrist angle (shared by every build). Returns the state."""
    _pi().save_grip_spec(prompt, prompt_back, wrist)
    return state(race)


@ops.op("items.save_layers")
def save_layers(item: str, candidate: str, view: str, layers: dict, race: str = RACE) -> dict:
    """Save hand-edited part layers of a candidate (Workshop Editor); the first edit keeps the originals for revert.

    Args:
        layers: {part: PNG data URL}
    """
    return _pi().layer_save(race, item, candidate, view, layers)


@ops.op("items.revert_layers")
def revert_layers(item: str, candidate: str, race: str = RACE) -> dict:
    """Put a candidate's original (generated) layers back, dropping hand edits."""
    return _pi().layer_revert(race, item, candidate)


@ops.op("items.save_bands")
def save_bands(item: str, candidate: str, view: str, bands: dict, race: str = RACE) -> dict:
    """Save hand-edited cel bands (shade levels) of a candidate's parts.

    Args:
        bands: {part: PNG data URL of the band map}
    """
    return _pi().bands_save(race, item, candidate, view, bands)


@ops.op("items.reset_bands")
def reset_bands(item: str, candidate: str, view: str | None = None, part: str | None = None, race: str = RACE) -> dict:
    """Redo a candidate's bands from its paint: one part, one view, or all."""
    return _pi().bands_reset(race, item, candidate, view, part)


# ----------------------------------------------------------------------------- review

@ops.op("items.approve")
def approve(item: str, candidate: str, key: str = "all", on: bool = True, race: str = RACE) -> dict:
    """Approve (or with on=false, un-approve) a candidate for one fit key. One candidate per key is approved.

    Args:
        key: "all" for a shared fit, or the build ("human.male") for a per-sex piece
    """
    return {"approved": _pi().approve(race, item, candidate, key, on)}


@ops.op("items.archive")
def archive(item: str, candidate: str, race: str = RACE) -> dict:
    """Move a candidate to archive/workshop/items/ (nothing is deleted); it stops being approved."""
    return _pi().archive(race, item, candidate)


@ops.op("items.approve_grip")
def approve_grip(build: str, candidate: str, on: bool = True, race: str = RACE) -> dict:
    """Approve (or un-approve) a grip-hand candidate for one build."""
    return {"grips": _pi().approve_grip(race, build, candidate, on)}


# ----------------------------------------------------------------------------- generation (queued jobs)

def _queue(label, **args):
    return {"job": jobs.enqueue("item", args, label)["id"]}


@ops.op("items.generate", queues="item")
def generate(item: str, build: str | None = None, n: int = 1, seed: int = 1, race: str = RACE) -> dict:
    """Queue n new candidates of a piece (seeds seed, seed+1, ...). Returns {"job": id}; poll the job queue.

    Args:
        build: the build to fit on (default: the piece's fitting build)
    """
    return _queue(f"{item} · {build or 'fit build'} ×{n} (s{seed})", action="generate", race=race, item=item,
                  build=build, n=n, seed=seed)


@ops.op("items.rekey", queues="item")
def rekey(item: str, candidate: str, complete: bool = False, race: str = RACE) -> dict:
    """Queue re-extracting a candidate's layers from its paint (no new paint).

    Args:
        complete: also repaint the hidden areas with FLUX
    """
    what = "re-extract + FLUX hidden" if complete else "re-extract"
    return _queue(f"{item} · {candidate} · {what}", action="rekey", race=race, item=item, id=candidate, complete=complete)


@ops.op("items.relevel", queues="item")
def relevel(item: str, candidate: str, race: str = RACE) -> dict:
    """Queue redoing a candidate's cel levels from its colours (no new paint)."""
    return _queue(f"{item} · {candidate} · re-level", action="relevel", race=race, item=item, id=candidate)


@ops.op("items.generate_grip", queues="item")
def generate_grip(build: str, seed: int = 1, race: str = RACE) -> dict:
    """Queue a new grip-hand candidate (the hand closed around a rod) for one build."""
    return _queue(f"grip hands · {build} (s{seed})", action="grip", race=race, build=build, seed=seed)


@ops.op("items.regrip", queues="item")
def regrip(build: str, candidate: str, race: str = RACE) -> dict:
    """Queue re-splitting a grip-hand candidate into its layers (no new paint)."""
    return _queue(f"grip hands · {build} · re-split {candidate}", action="regrip", race=race, build=build, id=candidate)


def _batch(intake, sets, race, n, dry_run):
    """Shared by queue_design_batch / queue_gear_sets: add the intake's pieces, plan one job per candidate, queue them."""
    pi = _pi()
    out = intake(dry_run=dry_run)
    defs = out.pop("defs", None)
    only = out["added"] if sets else None
    if sets and not only:
        return {**out, "batch": None, "jobs": 0, "items": []}
    bid, plan = pi.plan_design_batch(race, n, items=only, extra=defs if dry_run else None)
    out.update(batch=bid, jobs=len(plan), items=sorted({a["item"] for _, a in plan}))
    if not dry_run:
        from . import limner
        for label, a in plan:
            jobs.enqueue("item", a, label)
        out["watchdog"] = limner.ensure_watchdog() or "already running"
    return out


@ops.op("items.queue_design_batch", queues="item")
def queue_design_batch(n: int = 2, dry_run: bool = False, race: str = RACE) -> dict:
    """Make Workshop pieces for every approved Limner gear icon that lacks one, then queue n candidates per piece and
    build (an overnight batch with retries and the watchdog). dry_run only reports what would happen.
    """
    return _batch(_pi().limner_intake, False, race, n, dry_run)


@ops.op("items.queue_gear_sets", queues="item")
def queue_gear_sets(n: int = 2, dry_run: bool = False, race: str = RACE) -> dict:
    """Add the missing cloth/leather/plate set pieces (GEAR_SETS) and queue n candidates of each new one."""
    return _batch(_pi().gear_set_intake, True, race, n, dry_run)


# ----------------------------------------------------------------------------- export

@ops.op("items.export")
def export(items: list | None = None, race: str = RACE) -> dict:
    """Export game items with approved art into the project: art packs, Art/index.json, ramps, and intake files for
    ids the game doesn't have yet. Returns {exported, skipped, index}.

    Args:
        items: game item ids (default: every one with approved art)
    """
    from . import item_export
    return item_export.export(items or None, race)


@ops.op("items.export_check", read_only=True)
def export_check() -> dict:
    """Validate what's exported (manifests, files, index maps, index.json, intake files). Returns {ok, problems}."""
    from . import item_export
    problems = item_export.check()
    return {"ok": not problems, "problems": problems}


# ----------------------------------------------------------------------------- the page's older routes

# /api/ws/items/<action> -> (op, {old argument name: op argument name}[, fixed arguments]). Kept so studio/workshop.html
# works unchanged; new callers use /api/op/<name>.
LEGACY = {
    "state": ("items.state", {}),
    "prompts": ("items.prompts", {}),
    "item/save": ("items.save_item", {"def": "definition"}),
    "spec/save": ("items.save_prompts", {}),
    "zone/get": ("items.zone", {}),
    "zone/save": ("items.save_zone", {}),
    "zone/reset": ("items.reset_zone", {}),
    "approve": ("items.approve", {"id": "candidate"}),
    "archive": ("items.archive", {"id": "candidate"}),
    "grip/approve": ("items.approve_grip", {"id": "candidate"}),
    "grip/spec": ("items.save_grip_spec", {}),
    "grip/prompts": ("items.grip_prompts", {}),
    "held/stage": ("items.held_stage", {"id": "candidate", "def": "unsaved"}),
    "held/preview": ("items.held_preview", {"id": "candidate", "def": "unsaved"}),
    "layer_doc": ("items.layers", {"id": "candidate"}),
    "layer_save": ("items.save_layers", {"id": "candidate"}),
    "layer_revert": ("items.revert_layers", {"id": "candidate"}),
    "bands_save": ("items.save_bands", {"id": "candidate"}),
    "bands_reset": ("items.reset_bands", {"id": "candidate"}),
    "editor_walk": ("items.editor_walk", {"pcid": "paint"}),
    "ramp_colours": ("items.ramp_colours", {}),
    "export": ("items.export", {}),
    "outfit": ("items.outfit", {"cids": "candidates"}),
    "design_batch": ("items.queue_design_batch", {}),
    "design_batch/plan": ("items.queue_design_batch", {}, {"dry_run": True}),
    "set_batch": ("items.queue_gear_sets", {}),
    "set_batch/plan": ("items.queue_gear_sets", {}, {"dry_run": True}),
}


def legacy_call(action, body):
    """Run an old /api/ws/items/<action> request through its op (renamed arguments, extra keys ignored)."""
    if action not in LEGACY:
        raise ops.OpError(f"unknown items action {action!r}", 404)
    name, renames, *fixed = LEGACY[action]
    args = {renames.get(k, k): v for k, v in body.items()}
    args.update(*fixed)
    return ops.call(name, args, lenient=True)
