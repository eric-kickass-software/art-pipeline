"""hdart.ops: ops declared from typed functions (params, types, defaults, docs), argument checks and coercion, discovery;
and the Item Workshop's surface (hdart/items_api.py): every op documented, every older /api/ws/items/<action> the page
calls mapped onto an op, approve/archive through the legacy route, and generation queued as the page queues it.
Run: uv run python tests/test_ops.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import re, tempfile
from pathlib import Path
from hdart import jobs, ops


# ----------------------------------------------------------------------------- the registry

@ops.op("t.paint", queues="t")
def paint(item: str, n: int = 1, scale: float = 1.0, soft: bool = False, tags: list | None = None, note: str | None = None):
    """Paint something.

    More detail on a second paragraph.

    Args:
        item: what to paint
        n: how many,
            continued on a second line
    """
    return {"item": item, "n": n, "scale": scale, "soft": soft, "tags": tags, "note": note}


d = ops.get("t.paint").describe()
assert d["summary"] == "Paint something." and "second paragraph" in d["doc"] and "Args" not in d["doc"]
P = {p["name"]: p for p in d["params"]}
assert P["item"] == {"name": "item", "type": "string", "required": True, "nullable": False, "doc": "what to paint"}
assert P["n"]["type"] == "integer" and P["n"]["default"] == 1 and P["n"]["doc"] == "how many, continued on a second line"
assert P["scale"]["type"] == "number" and P["soft"]["type"] == "boolean"
assert P["tags"]["type"] == "array" and P["tags"]["nullable"] and P["note"]["type"] == "string"
assert d["queues"] == "t" and not d["read_only"]
assert [o["name"] for o in ops.describe("t.")] == ["t.paint"]

# coercion (what HTTP query strings and loose JSON send)
r = ops.call("t.paint", {"item": "helm", "n": "3", "scale": 2, "soft": "true", "tags": ["a"], "note": None})
assert r == {"item": "helm", "n": 3, "scale": 2.0, "soft": True, "tags": ["a"], "note": None}, r
assert ops.call("t.paint", {"item": "x", "soft": 0})["soft"] is False


def refused(name, args, status=400, has=""):
    try:
        ops.call(name, args)
    except ops.OpError as e:
        assert e.status == status and has in str(e), (e.status, str(e))
        return
    raise AssertionError(f"{name} {args} wasn't refused")


refused("t.paint", {}, has="missing argument 'item'")
refused("t.paint", {"item": "x", "colour": "red"}, has="unknown argument(s) colour")
refused("t.paint", {"item": "x", "n": "lots"}, has="n must be integer")
refused("t.paint", {"item": "x", "n": 1.5}, has="n must be integer")
refused("t.paint", {"item": {"a": 1}}, has="item must be string")
refused("t.paint", {"item": "x", "tags": "a,b"}, has="tags must be array")
refused("t.paint", {"item": None}, has="can't be null")
refused("t.nothing", {}, 404, "unknown op")
assert ops.call("t.paint", {"item": "x", "colour": "red"}, lenient=True)["item"] == "x"

try:   # every op needs a docstring (its summary is what agents see)
    ops.op("t.bad")(lambda x: x)
    raise AssertionError("an op without a docstring was registered")
except TypeError:
    pass
ops.OPS.pop("t.paint")


# ----------------------------------------------------------------------------- the Item Workshop's ops

import hdart.ai_kit as ak
from hdart import items_api

items = ops.describe("items.")
assert len(items) >= 30, len(items)
for o in items:
    assert o["summary"] and o["summary"][0].isupper(), o["name"]
    assert o["queues"] in (None, "item"), o["name"]

# every Item Workshop action the page still calls has an alias, and every alias names a real op
page = (Path(__file__).parent.parent / "studio" / "workshop.html").read_text(encoding="utf-8")
used = set(re.findall(r'itApi\("([\w/]+)"', page)) | set(re.findall(r'/api/ws/items/([\w/]+)', page))
assert used and used <= set(items_api.LEGACY), sorted(used - set(items_api.LEGACY))
assert all(e[0] in ops.OPS for e in items_api.LEGACY.values())

# approve / un-approve / archive through the page's route, on a temp data folder
tmp = Path(tempfile.mkdtemp()); ak.AI_DIR = tmp / "ai"
from hdart import puppet_items as pi, workshop as ws
ws.ARCHIVE = tmp / "archive"
cand = pi.out_dir("human", "item.test_helm") / "s1_100"; cand.mkdir(parents=True)
(cand / "meta.json").write_text('{"seed": 1}', encoding="utf-8")
r = items_api.legacy_call("approve", {"race": "human", "item": "item.test_helm", "id": "s1_100", "key": "all", "on": True})
assert r == {"approved": {"item.test_helm": {"all": "s1_100"}}}, r
assert ops.call("items.candidates", {"item": "item.test_helm"})["approved"] == {"all": "s1_100"}
items_api.legacy_call("archive", {"race": "human", "item": "item.test_helm", "id": "s1_100"})
assert not cand.exists() and (ws.ARCHIVE / "workshop" / "items" / "human" / "item.test_helm" / "s1_100").exists()
assert ops.call("items.candidates", {"item": "item.test_helm"}) == {"candidates": [], "approved": {}}

# generation is queued as a job of kind "item" with the args the job handler (puppet_items.job) reads
try:
    ops.call("items.generate", {"item": "item.test_helm"})
    raise AssertionError("queued without a Studio")
except RuntimeError as e:
    assert "Studio" in str(e)
jobs.QUEUE = jobs.Jobs(None, autostart=False)
jid = ops.call("items.generate", {"item": "item.test_helm", "build": "human.male", "n": "2", "seed": 7})["job"]
j = jobs.QUEUE.get(jid)
assert j["kind"] == "item" and j["args"] == {"action": "generate", "race": "human", "item": "item.test_helm",
                                             "build": "human.male", "n": 2, "seed": 7}, j
assert ops.call("items.rekey", {"item": "item.test_helm", "candidate": "s1_100", "complete": True})["job"] == jid + 1
assert jobs.QUEUE.get(jid + 1)["args"] == {"action": "rekey", "race": "human", "item": "item.test_helm", "id": "s1_100",
                                          "complete": True}
jobs.QUEUE = None
print("ok")
