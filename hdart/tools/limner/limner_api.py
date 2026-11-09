"""The Limner's public API: every action the Limner page (/limner) and agents can take.

The Limner paints the game's icons and portraits (hdart/tools/limner/limner.py has the how). Its spec
(specs/limner.json) layers the prompt: style > category > entry. Categories ("items", "gear", "classes", "statuses",
"portraits", "tokens"...) hold entries (an id from the game's content, or one added by hand); each entry's candidates
live in sprites/limner/<cat>/<entry>/<candidate>/ and the approved one is what limner.export ships to the game.

Typical flow: limner.doc → limner.generate (queues jobs) → jobs.wait → limner.doc → limner.approve → limner.export.
Overnight: limner.plan_batch to see the size, limner.queue_batch to queue it (with retries and the watchdog).

Every function here is an op (hdart/core/ops.py): GET /api/ops lists them, POST /api/op/limner.<action> calls one (GET
for read-only ones). LEGACY / GET_LEGACY map the page's older /api/limner/<action> routes onto them.
"""
from hdart.core import jobs, ops

ops.param_docs("limner.", {
    "cat": 'a category id, e.g. "gear", "portraits" (limner.doc lists them under categories)',
    "entry": 'an entry id in that category, e.g. "item.iron_sword"',
    "candidate": 'a candidate id: its folder name, "s<seed>_<time>"',
    "n": "how many candidates per entry",
    "seed": "the first seed (default: random)",
    "style": "a style key from the spec's styles (default: the category's)",
    "fields": "the fields to change (others are kept)",
    "on": "true to set, false to clear",
    "ids": "entry ids in the category",
    "styles": "style keys (one run per style; default: the category's)",
    "variants": 'variation lines added after the subject ("" = the plain subject)',
    "seeds": '"random", or "sweep" (each run continues from the last one\'s seed, starting at seed)',
})


def _lm():
    """hdart.tools.limner.limner (imported on first use, so reading the op docs, as `hdart mcp` does, doesn't load the
    image stack)."""
    from hdart.tools.limner import limner
    return limner


# ----------------------------------------------------------------------------- reading

@ops.op("limner.doc", read_only=True)
def doc() -> dict:
    """Everything the page shows: styles, categories with their entries (prompt, candidates, approved, new count),
    archetypes and races (portraits), the export folder, batch settings and the watchdog's status."""
    return _lm().doc()


@ops.op("limner.image", read_only=True)
def image(path: str) -> ops.Blob:
    """PNG: a file under sprites/limner/ (a candidate's master.png, icon.png, portrait.png or thumb.png).

    Args:
        path: relative to sprites/limner/: "<cat>/<entry>/<candidate>/<file>.png"
    """
    return ops.Blob(_lm().image(path), "image/png", "no-cache")   # a refinish rewrites the file in place


@ops.op("limner.plan_batch", read_only=True)
def plan_batch(cat: str, ids: list, styles: list | None = None, variants: list | None = None, n: int = 2,
               seeds: str = "random", seed: int | None = None) -> dict:
    """The size of an overnight batch without queueing it: {"estimate": {jobs, images, minutes}}."""
    _, _, est = _lm().plan_batch(cat, ids, styles, variants, n, seeds, seed)
    return {"estimate": est}


# ----------------------------------------------------------------------------- generation (queued jobs)

@ops.op("limner.generate", queues="limner_gen")
def generate(cat: str, ids: list, n: int = 3, seed: int | None = None, style: str | None = None) -> dict:
    """Queue n new candidates for each entry in ids (one job per entry). Returns {"jobs": ids}."""
    out = []
    for eid in ids:
        a = {"cat": cat, "id": eid, "n": n, "seed": seed, "style": style}
        out.append(jobs.enqueue("limner_gen", a, f"Limner · {cat} · {eid} ×{n}")["id"])
    return {"jobs": out}


@ops.op("limner.vary", queues="limner_vary")
def vary(cat: str, entry: str, candidate: str, n: int = 3, denoise: float = 0.45) -> dict:
    """Queue n variations of a candidate (img2img from it). Returns {"jobs": [id]}.

    Args:
        denoise: how far a variation may stray (0..1)
    """
    a = {"cat": cat, "id": entry, "cid": candidate, "n": n, "denoise": denoise}
    return {"jobs": [jobs.enqueue("limner_vary", a, f"Limner · {cat} · {entry} vary ×{n}")["id"]]}


@ops.op("limner.queue_batch", queues="limner_gen")
def queue_batch(cat: str, ids: list, styles: list | None = None, variants: list | None = None, n: int = 2,
                seeds: str = "random", seed: int | None = None) -> dict:
    """Queue an overnight batch: one job per entry x style x variation, retried if it fails, with the watchdog
    (it keeps the PC awake and restarts what gets stuck). Returns {"batch", "estimate", "watchdog"}."""
    lm = _lm()
    bid, plan, est = lm.plan_batch(cat, ids, styles, variants, n, seeds, seed)
    for a in plan:
        jobs.enqueue("limner_gen", a, f"Limner batch {bid} · {cat} · {a['id']} · {a['style'] or 'default'}"
                                      f"{' · ' + a['extra'] if a.get('extra') else ''} ×{a['n']}")
    return {"batch": bid, "estimate": est, "watchdog": lm.ensure_watchdog() or "already running"}


@ops.op("limner.cancel_batch")
def cancel_batch(batch: str | None = None) -> dict:
    """Cancel the queued jobs of a batch (default: of every Limner batch); the running one finishes.
    Returns {"cancelled": how many}.

    Args:
        batch: the batch id ("b1107_225626")
    """
    return {"cancelled": jobs.current().cancel_where(f"Limner batch {batch}" if batch else "Limner batch")}


@ops.op("limner.start_watchdog")
def start_watchdog() -> dict:
    """Start the watchdog unless it runs already. Returns {"started": bool}."""
    return {"started": _lm().ensure_watchdog()}


# ----------------------------------------------------------------------------- review

@ops.op("limner.approve")
def approve(cat: str, entry: str, candidate: str, on: bool = True) -> dict:
    """Approve (or with on=false, un-approve) a candidate; one per entry."""
    return _lm().approve(cat, entry, candidate, on)


@ops.op("limner.archive")
def archive(cat: str, entry: str, candidate: str) -> dict:
    """Move a candidate to archive/limner/ (nothing is deleted); it stops being approved."""
    return _lm().archive(cat, entry, candidate)


@ops.op("limner.mark_reviewed")
def mark_reviewed(cat: str) -> dict:
    """Mark every candidate of a category as seen (the "new" counts start from now)."""
    return _lm().mark_reviewed(cat)


@ops.op("limner.refinish")
def refinish(cat: str, ids: list | None = None) -> dict:
    """Redo the finish (crop, background, outline, sizes) of the candidates from their masters, without the GPU.

    Args:
        ids: the entry ids (default: all)
    """
    return _lm().refinish(cat, ids)


# ----------------------------------------------------------------------------- editing the spec

@ops.op("limner.save_entry")
def save_entry(cat: str, entry: str, fields: dict) -> dict:
    """Change an entry's own settings: subject, style, seed, finish ({mirror, crop...})."""
    return _lm().save_entry(cat, entry, fields)


@ops.op("limner.save_category")
def save_category(cat: str, fields: dict) -> dict:
    """Change a category's settings: style, prefix, finish."""
    return _lm().save_category(cat, fields)


@ops.op("limner.save_style")
def save_style(key: str, fields: dict) -> dict:
    """Create or change a style (name, prefix, suffix, negative...).

    Args:
        key: the style key
    """
    return _lm().save_style(key, fields)


@ops.op("limner.add_entry")
def add_entry(cat: str, name: str = "", entry: str | None = None, subject: str = "", sub: str = "") -> dict:
    """Add an entry by hand (Items, Gear, UI...). Returns {"ok", "id": the entry id}.

    Args:
        name: its display name (default: from the id)
        entry: its id (default: from the name; the category's prefix is added when missing)
        subject: the subject words of its prompt
        sub: its group within the category
    """
    return _lm().add_entry(cat, entry, name, subject, sub)


@ops.op("limner.remove_entry")
def remove_entry(cat: str, entry: str) -> dict:
    """Remove an entry added by hand (its candidates are kept)."""
    return _lm().remove_entry(cat, entry)


@ops.op("limner.save_look")
def save_look(archetype: str, look: str, text: str = "") -> dict:
    """Set (or with an empty text, remove) a portrait look of an archetype.

    Args:
        archetype: the archetype key (a class)
        look: the look key
        text: its prompt words
    """
    return _lm().save_look(archetype, look, text)


@ops.op("limner.save_race")
def save_race(key: str, fields: dict) -> dict:
    """Change a portrait race's settings (name, noun, traits).

    Args:
        key: the race key
    """
    return _lm().save_race(key, fields)


# ----------------------------------------------------------------------------- export

@ops.op("limner.export")
def export(cats: list | None = None) -> dict:
    """Export the approved icons and portraits into the project (Art/Icons/<cat>/, Art/Portraits/; each folder
    rebuilt whole).

    Args:
        cats: the categories (default: all)
    """
    return _lm().export(cats)


# ----------------------------------------------------------------------------- the page's older routes

# POST /api/limner/<action> -> (op, {old argument name: op argument name}[, fixed arguments]) (see ops.call_alias).
# Kept so studio/limner.html works unchanged; new callers use /api/op/<name>.
_IDS = {"id": "entry", "cid": "candidate"}
LEGACY = {
    "generate": ("limner.generate", {}),
    "vary": ("limner.vary", _IDS),
    "batch": ("limner.queue_batch", {}),
    "batch/plan": ("limner.plan_batch", {}),
    "batch/cancel": ("limner.cancel_batch", {}),
    "watchdog": ("limner.start_watchdog", {}),
    "reviewed": ("limner.mark_reviewed", {}),
    "approve": ("limner.approve", _IDS),
    "archive": ("limner.archive", _IDS),
    "entry": ("limner.save_entry", _IDS),
    "category": ("limner.save_category", {}),
    "style": ("limner.save_style", {}),
    "add": ("limner.add_entry", _IDS),
    "remove": ("limner.remove_entry", _IDS),
    "look": ("limner.save_look", {}),
    "race": ("limner.save_race", {}),
    "refinish": ("limner.refinish", {}),
    "export": ("limner.export", {}),
}
GET_LEGACY = {
    "doc": ("limner.doc", {}),
    "img": ("limner.image", {}),
}
