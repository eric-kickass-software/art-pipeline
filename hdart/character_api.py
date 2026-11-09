"""The Character Workshop's public API: every action of its tabs (Template, Races + Paint, Face, Ramps) and of the
Workshop Editor's puppet, hair and face layers, for the Studio pages and agents.

The Character Workshop makes each race's puppet (the modules have the how):
  - Template (hdart/puppet_template.py): the rig every puppet is painted on (parts, joints, draw order).
  - Races (hdart/races.py): races.json, and per race a unisex mannequin (front, then back) that sets the look.
  - Paint (hdart/puppet_paint.py): FLUX paints the template per build ("<race>.<sex>") into per-part layers with
    cel bands; the approved paint is what puppet-export ships to the game.
  - Face (hdart/puppet_face.py): racial hair and faces, painted on the approved puppet and kept as sets per race.
  - Ramps (hdart/palette.py, specs/palettes.json; skin keying in hdart/levels.py): the colour ramps of skin, hair
    and items.
Candidates: Paint in sprites/ai/paint/<build>/<id>/, hair and faces in sprites/ai/face/<race>/<kind>/<id>/,
mannequins as PNGs in sprites/ai/candidates/<build>/_base/ (named by `file`, their path under sprites/ai/).

Typical flow: character.race → character.generate_mannequin (front) → jobs.wait → character.approve_mannequin (queues
the back) → character.generate_paint → jobs.wait → character.paint_state → character.approve_paint → hair and faces
the same way (character.generate_face, character.set_face).

Every function here is an op (hdart/ops.py): GET /api/ops lists them, POST /api/op/character.<action> calls one (GET
for read-only ones). LEGACY maps the page's older /api/ws/<action> routes onto them.
"""
from . import jobs, ops

ops.param_docs("character.", {
    "race": 'a race id from races.json, e.g. "human", "halforc"',
    "build": '"<race>.<sex>", e.g. "human.male" (sex: unisex, male, female)',
    "candidate": 'a candidate id: its folder name, "s<seed>_<time>"',
    "view": '"front" or "back"',
    "facing": '"front" or "back"',
    "kind": '"hair" or "face"',
    "spec": "the whole spec document to save (read it first, change it, send it back)",
    "n": "how many candidates",
    "seed": "the first seed (each further candidate adds 1)",
    "on": "true to set, false to clear",
    "png": "a PNG data URL",
    "part": 'a template part name ("head", "armA.upper"...); omit for every part',
    "group": 'a ramp group in specs/palettes.json ("skin", "hair", ...)',
    "name": "a ramp name",
})


def _ws():
    from . import workshop
    return workshop


def _races():
    from . import races
    return races


def _palette():
    from . import palette
    return palette


def _ppt():
    from . import puppet_paint
    return puppet_paint


def _pf():
    from . import puppet_face
    return puppet_face


def _pt():
    from . import puppet_template
    return puppet_template


def _sex(build: str) -> tuple[str, str]:
    race, _, sex = build.partition(".")
    if not sex:
        raise ops.OpError(f"build {build!r} must be '<race>.<sex>'")
    return race, sex


# ----------------------------------------------------------------------------- template

@ops.op("character.template", read_only=True)
def template() -> dict:
    """The template editor's document: the rig spec (parts, joints, draw order per view), the art it was measured from,
    the preview files."""
    return _pt().state()


@ops.op("character.save_template")
def save_template(spec: dict, render: bool = False) -> dict:
    """Save an edited template (validated; existing paints keep the joints they were painted with). Returns the
    template document.

    Args:
        render: also queue rendering the template's preview
    """
    pt = _pt()
    pt.save(spec)
    if render:
        jobs.enqueue("tpl", {}, "template · render preview")
    return pt.state()


@ops.op("character.measure_template", queues="tpl_measure")
def measure_template(build: str = "human.unisex") -> dict:
    """Queue re-measuring the template from a build's approved mannequin art, then a preview render (no GPU).
    Returns {"job": id}."""
    return {"job": jobs.enqueue("tpl_measure", {"build": build}, "template · re-measure")["id"]}


# ----------------------------------------------------------------------------- races and mannequins

@ops.op("character.race", read_only=True)
def race(race: str) -> dict:
    """The Race tab: races.json (spec.races) and the race's unisex mannequin per facing (candidates, approval,
    stale flags)."""
    return _races().race_state(race)


@ops.op("character.save_races")
def save_races(spec: dict) -> dict:
    """Save races.json (every race's words, ramps, flags). Returns what races.save_races returns (the saved doc)."""
    return _races().save_races(spec)


@ops.op("character.generate_mannequin", queues="race")
def generate_mannequin(race: str, facing: str, sex: str = "unisex", n: int = 4, seed: int = 1) -> dict:
    """Queue n mannequin candidates of a race for one facing (approve a front before making the back).
    Returns {"job": id}.

    Args:
        sex: the build's sex (only the unisex has a mannequin)
    """
    a = {"race": race, "sex": sex, "facing": facing, "n": n, "seed": seed}
    return {"job": jobs.enqueue("race", a, f"{race} · {sex} · {facing}")["id"]}


@ops.op("character.approve_mannequin")
def approve_mannequin(build: str, facing: str, file: str, auto_back: bool = True, n: int = 3) -> dict:
    """Approve a mannequin candidate. A new front drops the back's approval and (auto_back) queues n back views
    made from it. Returns the build's mannequin state (front, back), plus "back_job" when a back was queued.

    Args:
        file: the candidate's path under sprites/ai/ (character.race lists them)
        auto_back: after a front, queue the matching back right away
        n: how many backs to queue
    """
    out = _races().approve_race(build, facing, file)
    if facing == "front" and auto_back:
        race, sex = _sex(build)
        job = jobs.enqueue("race", {"race": race, "sex": sex, "facing": "back", "n": n, "seed": 11},
                           f"{race} · {sex} · back")
        out = {**out, "back_job": job["id"]}
    return out


@ops.op("character.archive_mannequin")
def archive_mannequin(build: str, facing: str, file: str | None = None) -> dict:
    """Archive a mannequin candidate (or, without file, every one that isn't approved). Returns {"archived": count}.

    Args:
        file: the candidate's path under sprites/ai/
    """
    return {"archived": _races().archive_base(build, facing, file)}


# ----------------------------------------------------------------------------- paint

@ops.op("character.paint_state", read_only=True)
def paint_state(build: str) -> dict:
    """A build's Paint candidates (newest first, with previews and fit scores) and which one is approved."""
    return _ppt().state(build)


@ops.op("character.paint_spec", read_only=True)
def paint_spec(build: str | None = None) -> dict:
    """The paint prompts (specs/paint.json): {"spec"}, plus "composed" (the prompts a build is painted with).

    Args:
        build: also compose this build's prompts
    """
    ppt = _ppt()
    out = {"spec": ppt.spec()}
    if build:
        out["composed"] = ppt.composed(build)
    return out


@ops.op("character.save_paint_spec")
def save_paint_spec(spec: dict, build: str | None = None) -> dict:
    """Save the paint prompts. Returns them as character.paint_spec does."""
    _ppt().save_spec(spec)
    return paint_spec(build)


@ops.op("character.paint_ramp_preview", read_only=True)
def paint_ramp_preview(build: str) -> dict:
    """The approved paint of a build in every skin ramp (data URLs), for the Ramps tab."""
    return _ppt().ramp_preview(build)


@ops.op("character.generate_paint", queues="paint")
def generate_paint(build: str, n: int = 1, seed: int = 1) -> dict:
    """Queue n Paint candidates of a build (FLUX paints the template from the race's mannequin). Returns {"job": id}."""
    race, sex = _sex(build)
    return {"job": jobs.enqueue("paint", {"build": build, "n": n, "seed": seed}, f"{race} · {sex} · paint")["id"]}


@ops.op("character.approve_paint")
def approve_paint(build: str, candidate: str, on: bool = True) -> dict:
    """Approve (or un-approve) a Paint candidate; one per build. Returns the build's paint state."""
    return _ppt().approve(build, candidate, on)


@ops.op("character.archive_paint")
def archive_paint(build: str, candidate: str) -> dict:
    """Move a Paint candidate to archive/ (nothing is deleted); it stops being approved."""
    return _ppt().archive(build, candidate)


@ops.op("character.paint_parts", read_only=True)
def paint_parts(build: str, candidate: str) -> dict:
    """A Paint candidate's per-part layers and bands per view, for the Workshop Editor."""
    return _ppt().parts_doc(build, candidate)


@ops.op("character.save_paint_parts", queues="paint_preview")
def save_paint_parts(build: str, candidate: str, view: str, parts: dict) -> dict:
    """Save hand-edited part layers of a Paint candidate (the first edit keeps the originals for revert), then queue
    re-rendering its walk preview.

    Args:
        parts: {part: PNG data URL}
    """
    out = _ppt().save_parts(build, candidate, view, parts)
    jobs.enqueue("paint_preview", {"build": build, "id": candidate}, f"{build} · paint {candidate} · preview")
    return out


@ops.op("character.revert_paint_parts")
def revert_paint_parts(build: str, candidate: str) -> dict:
    """Put a Paint candidate's original (generated) layers back, dropping hand edits."""
    return _ppt().revert_parts(build, candidate)


@ops.op("character.save_paint_bands")
def save_paint_bands(build: str, candidate: str, view: str, bands: dict) -> dict:
    """Save hand-edited skin bands (cel levels) of a Paint candidate's parts.

    Args:
        bands: {part: PNG data URL of the band map}
    """
    return _ppt().save_bands(build, candidate, view, bands)


@ops.op("character.reset_paint_bands")
def reset_paint_bands(build: str, candidate: str, view: str | None = None, part: str | None = None) -> dict:
    """Redo a Paint candidate's bands from its paint: one part, or all (after a new skin key)."""
    return _ppt().reset_bands(build, candidate, view, part)


@ops.op("character.fix_paint", queues="paint_fix")
def fix_paint(build: str, candidate: str, view: str, part: str, mask: str, prompt: str = "", seed: int = 3) -> dict:
    """Queue a FLUX repaint of a marked area of one part (the Editor's AI fix). Returns {"job": id}.

    Args:
        part: the part to repaint ("head", "armA.upper"...)
        mask: the area to repaint, a PNG data URL (white = repaint)
        prompt: what to paint there
    """
    a = {"build": build, "id": candidate, "view": view, "part": part, "mask": mask, "prompt": prompt, "seed": seed}
    return {"job": jobs.enqueue("paint_fix", a, f"{build} · paint {candidate} · AI fix {part}")["id"]}


# ----------------------------------------------------------------------------- hair and faces

@ops.op("character.faces", read_only=True)
def faces(race: str) -> dict:
    """The Face tab of a race: the face spec, hair and face candidates, the race's sets."""
    return _pf().state(race)


@ops.op("character.save_face_spec")
def save_face_spec(spec: dict, race: str) -> dict:
    """Save the face spec (specs/face.json: hair and face prompts per race). Returns the race's Face tab state.

    Args:
        race: the race whose state to return
    """
    pf = _pf()
    pf.save_spec(spec)
    return pf.state(race)


@ops.op("character.generate_face", queues="face_gen")
def generate_face(race: str, kind: str, sex: str = "male", n: int = 1, seed: int = 1) -> dict:
    """Queue n hair or face candidates for a race, painted on its approved puppet. Returns {"job": id}.

    Args:
        sex: the build painted on
    """
    a = {"what": kind, "race": race, "sex": sex, "n": n, "seed": seed}
    return {"job": jobs.enqueue("face_gen", a, f"{race} · {kind} · face")["id"]}


@ops.op("character.set_face")
def set_face(race: str, kind: str, candidate: str, on: bool = True) -> dict:
    """Add a hair or face candidate to (or remove it from) the race's set, the ones the game gets. Returns the Face
    tab state."""
    return _pf().set_member(race, kind, candidate, on)


@ops.op("character.archive_face")
def archive_face(race: str, kind: str, candidate: str) -> dict:
    """Move a hair or face candidate to archive/ (nothing is deleted); it leaves the set."""
    return _pf().archive(race, kind, candidate)


@ops.op("character.face_layers", read_only=True)
def face_layers(race: str, kind: str, candidate: str) -> dict:
    """A hair or face candidate's layer and bands per view, for the Workshop Editor."""
    return _pf().layer_doc(race, kind, candidate)


@ops.op("character.save_face_layer")
def save_face_layer(race: str, kind: str, candidate: str, view: str, png: str) -> dict:
    """Save a hand-edited hair or face layer for one view (the first edit keeps the original for revert)."""
    return _pf().save_layer(race, kind, candidate, view, png)


@ops.op("character.revert_face_layer")
def revert_face_layer(race: str, kind: str, candidate: str) -> dict:
    """Put a hair or face candidate's original layers back."""
    return _pf().revert_layer(race, kind, candidate)


@ops.op("character.save_face_bands")
def save_face_bands(race: str, kind: str, candidate: str, view: str, png: str) -> dict:
    """Save hand-edited hair bands (cel levels) for one view.

    Args:
        png: the band map, a PNG data URL
    """
    return _pf().save_bands(race, kind, candidate, view, png)


@ops.op("character.reset_face_bands")
def reset_face_bands(race: str, kind: str, candidate: str, view: str | None = None) -> dict:
    """Redo a hair or face candidate's bands from its paint (one view, or both)."""
    return _pf().reset_bands(race, kind, candidate, view)


@ops.op("character.face_ramp_preview", read_only=True)
def face_ramp_preview(build: str) -> dict:
    """The painted puppet wearing the race's hair and face, as three level maps (data URLs), for the Ramps tab."""
    return _pf().ramp_preview(build)


@ops.op("character.face_preview", read_only=True)
def face_preview(race: str, kind: str, candidate: str, ramp: str | None = None) -> dict:
    """A hair or face candidate's previews in another ramp (data URLs; nothing is saved).

    Args:
        ramp: a hair ramp name (default: the hair group's first)
    """
    return _pf().previews(race, kind, candidate, ramp, save=False)


# ----------------------------------------------------------------------------- ramps

@ops.op("character.palettes", read_only=True)
def palettes() -> dict:
    """The palettes document (specs/palettes.json): every ramp group (skin, hair, items...) with its ramps (colors darkest first, base, tune,
    races)."""
    return _palette().palettes_doc()


@ops.op("character.ramp_preview", read_only=True)
def ramp_preview(build: str) -> dict:
    """A build's approved paint shown in the race's skin ramps (data URLs)."""
    return _races().ramp_preview(build)


@ops.op("character.save_ramp")
def save_ramp(group: str, name: str, base: str | None = None, tune: dict | None = None, colors: list | None = None,
              races: list | None = None, old_name: str | None = None) -> dict:
    """Create or change one ramp (specs/palettes.json). Returns the palettes document.

    Args:
        name: the ramp name (letters, digits, _ and -)
        base: the base colour the ramp editor built it from (hex)
        tune: the editor's tuning values
        colors: the resolved ramp, hex colours darkest first (rampSize of them); what the game uses
        races: the races it's offered to ([] = all)
        old_name: its current name when renaming it
    """
    return _palette().save_ramp(group, name, base, tune, colors, races, old_name)


@ops.op("character.delete_ramp")
def delete_ramp(group: str, name: str) -> dict:
    """Delete a ramp. Returns the palettes document."""
    return _palette().delete_ramp(group, name)


# ----------------------------------------------------------------------------- images

@ops.op("character.image", read_only=True)
def image(path: str) -> ops.Blob:
    """A PNG (or GIF) under sprites/ai/: candidates, layers, previews (the pages' image URLs; items too).

    Args:
        path: relative to sprites/ai/
    """
    return ops.Blob(_ws().image(path), "image/gif" if path.endswith(".gif") else "image/png", "max-age=3600")


# ----------------------------------------------------------------------------- export

@ops.op("character.export")
def export(race: str = "human", size: int | None = None) -> dict:
    """Export a race's puppet into the project: its approved paint per sex (else the newest), the hair and face sets
    and the skin, hair and eye ramps, with index maps (the race's pack folder, rebuilt whole). Returns {"path"}.

    Args:
        size: canvas px (default: the rig canvas, 576)
    """
    from . import unity_export
    return {"path": str(unity_export.export(race, size))}


# ----------------------------------------------------------------------------- the page's older routes

def _paint_spec_route(b: dict) -> dict:
    """paint/spec saves when the body has a spec, else only reads."""
    if b.get("spec"):
        return ops.call("character.save_paint_spec", {"spec": b["spec"], "build": b.get("build")})
    return ops.call("character.paint_spec", {"build": b.get("build")})


# POST /api/ws/<action> -> (op, {old argument name: op argument name}[, fixed arguments]) or a function (see
# ops.call_alias). Kept so studio/workshop.html works unchanged; new callers use /api/op/<name>.
_ID = {"id": "candidate"}
LEGACY = {
    "tpl/state": ("character.template", {}),
    "tpl/save": ("character.save_template", {}),
    "race/state": ("character.race", {}),
    "race/spec": ("character.save_races", {}),
    "race/approve": ("character.approve_mannequin", {}),
    "base/archive": ("character.archive_mannequin", {}),
    "paint/state": ("character.paint_state", {}),
    "paint/spec": _paint_spec_route,
    "paint/ramp_preview": ("character.paint_ramp_preview", {}),
    "paint/approve": ("character.approve_paint", _ID),
    "paint/archive": ("character.archive_paint", _ID),
    "paint/parts": ("character.paint_parts", _ID),
    "paint/save": ("character.save_paint_parts", _ID),
    "paint/revert": ("character.revert_paint_parts", _ID),
    "paint/bands_save": ("character.save_paint_bands", _ID),
    "paint/bands_reset": ("character.reset_paint_bands", _ID),
    "face/state": ("character.faces", {}),
    "face/spec": ("character.save_face_spec", {}),
    "face/set": ("character.set_face", _ID),
    "face/archive": ("character.archive_face", _ID),
    "face/layer_doc": ("character.face_layers", _ID),
    "face/layer_save": ("character.save_face_layer", _ID),
    "face/layer_revert": ("character.revert_face_layer", _ID),
    "face/bands_save": ("character.save_face_bands", _ID),
    "face/bands_reset": ("character.reset_face_bands", _ID),
    "face/ramp_preview": ("character.face_ramp_preview", {}),
    "face/preview": ("character.face_preview", _ID),
    "ramp/preview": ("character.ramp_preview", {}),
    "ramp/palettes": ("character.palettes", {}),
    "ramp/save": ("character.save_ramp", {}),
    "ramp/delete": ("character.delete_ramp", {}),
}
GET_LEGACY = {
    "img": ("character.image", {}),
}
