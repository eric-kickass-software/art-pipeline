"""The MCP server (hdart/mcp_server.py) and `hdart op` (hdart/cli.py) against a throwaway Studio: temp data and specs,
a free port, ComfyUI on a closed port (no GPU). The MCP server runs as Claude Code starts it (`hdart mcp`, stdio) and
talks to the Studio over HTTP: tool list, state reads, candidates, view_candidate images, approve, a patch_spec the op
refuses and one it saves, generate + jobs_wait (the job fails fast without ComfyUI), list_ops / call_op, and a clear
error when the Studio isn't running.
Run: uv run python tests/test_mcp.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import asyncio, base64, io, json, os, socket, subprocess, sys, tempfile, threading, time
import urllib.request
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
game, data = tmp / "game", tmp / "data"
os.environ.update(HD_DATA=str(data), HD_PROJECT=str(game), HD_COMFY_URL="http://127.0.0.1:9")
(game / "ArtContract").mkdir(parents=True)
(game / "ArtContract" / "requirements.json").write_text(json.dumps({"damageTypes": ["fire"], "wanted": {"icons": {
    "items": [{"id": "item.bandage", "name": "Bandage", "kind": "consumable"}]}}}), encoding="utf-8")

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def png(path, size, colour=(200, 40, 40, 255)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, colour).save(path)


# candidates to look at: a Limner icon, a dungeon tile, an item preview (made by hand; nothing is generated)
lim = data / "sprites" / "limner" / "items" / "item.bandage" / "s1_100"
png(lim / "icon.png", (256, 256))
(lim / "meta.json").write_text(json.dumps({"seed": 1, "made": 100}), encoding="utf-8")
rel = "candidates/tile.crypt_floor/s1_100.png"
png(data / "sprites" / "dungeon" / "crypt" / rel, (512, 256))
(data / "sprites" / "dungeon" / "crypt" / "state.json").write_text(json.dumps(
    {"pieces": {"tile.crypt_floor": {"candidates": [rel], "approved": []}}}), encoding="utf-8")
it = data / "sprites" / "ai" / "items" / "human" / "item.cloth_trousers" / "s1_100"
png(it / "prev_male_front.png", (576, 576))
(it / "meta.json").write_text(json.dumps({"seed": 1, "made": 100}), encoding="utf-8")

# the throwaway Studio, in this process (like test_tool_ops)
from hdart import studio
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
threading.Thread(target=studio.serve, kwargs={"port": port, "open_browser": False}, daemon=True).start()
base = f"http://127.0.0.1:{port}"
for _ in range(100):
    try:
        urllib.request.urlopen(base + "/api/ops?prefix=jobs.", timeout=5).read()
        break
    except OSError:
        time.sleep(0.1)
with socket.socket() as sk:   # a port nothing listens on: "the Studio isn't running"
    sk.bind(("127.0.0.1", 0)); closed = sk.getsockname()[1]

env = dict(os.environ, HD_STUDIO_URL=base, PYTHONWARNINGS="ignore")

# ----------------------------------------------------------------------------- hdart op (CLI)

def cli(*args, url=base):
    r = subprocess.run([sys.executable, "-m", "hdart.cli", "op", *args], cwd=ROOT, capture_output=True, text=True,
                       env=dict(env, HD_STUDIO_URL=url), encoding="utf-8")
    return r.returncode, r.stdout, r.stderr


code, out, err = cli("list", "jobs.")
assert code == 0 and "jobs.wait" in out and "jobs.cancel" in out and "dungeon." not in out, (code, out, err)
code, out, err = cli("call", "dungeon.sets")
assert code == 0 and "crypt" in json.loads(out), (out, err)
code, out, err = cli("call", "limner.plan_batch", "cat=items", 'ids=["item.bandage"]', "n=2")
assert code == 0 and json.loads(out)["estimate"]["images"] == 2, (out, err)
code, out, err = cli("show", "items.approve")
assert code == 0 and "candidate: string" in out, out
code, out, err = cli("call", "items.save_item", "item=item.cloth_trousers", 'definition={"slot": "nope"}')
assert code == 1 and "slot must be one of" in err and "HTTP 400" in err, (code, err)
img = tmp / "icon.png"
code, out, err = cli("call", "limner.image", "path=items/item.bandage/s1_100/icon.png", "--out", str(img))
assert code == 0 and Image.open(img).size == (256, 256), (out, err)
code, out, err = cli("list", url=f"http://127.0.0.1:{closed}")
assert code == 1 and "isn't running" in err, err


# ----------------------------------------------------------------------------- the MCP server (stdio, as Claude Code runs it)

from mcp import Client, StdioServerParameters


def text(r):
    return "".join(c.text for c in r.content if c.type == "text")


def as_json(r):
    assert not r.is_error, text(r)
    return json.loads(text(r))


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "hdart.cli", "mcp"], cwd=str(ROOT), env=env)
    async with Client(params) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert {"state", "candidates", "view_candidate", "generate", "approve", "archive", "get_spec", "patch_spec",
                "jobs_list", "jobs_wait", "jobs_cancel", "export", "export_check", "list_ops", "describe_op",
                "call_op"} <= set(tools), sorted(tools)
        assert "items.approve: Approve" in tools["approve"].description      # summaries from the op docstrings
        assert tools["state"].annotations.read_only_hint and not tools["patch_spec"].annotations.read_only_hint

        got = as_json(await c.call_tool("list_ops", {"prefix": "jobs."}))
        assert {o["name"] for o in got} >= {"jobs.wait", "jobs.list"} and all(o["name"].startswith("jobs.") for o in got)
        assert as_json(await c.call_tool("describe_op", {"name": "jobs.wait"}))["params"]

        # state reads (compact)
        st = as_json(await c.call_tool("state", {"tool": "limner"}))
        assert st["categories"]["items"]["candidates"] == 1, st["categories"]["items"]
        st = as_json(await c.call_tool("state", {"tool": "dungeon", "target": "crypt"}))
        floor = next(p for p in st["pieces"] if p["id"] == "tile.crypt_floor")
        assert floor["candidates"] == 1 and floor["approved"] == 0, floor
        st = as_json(await c.call_tool("state", {"tool": "items"}))
        assert st["pieces"]["item.cloth_trousers"]["candidates"] == 1
        st = as_json(await c.call_tool("state", {"tool": "character"}))
        assert st["race"] == "human" and "human" in st["races"]

        # candidates + view_candidate: images come back downscaled
        cs = as_json(await c.call_tool("candidates", {"tool": "limner", "target": "items/item.bandage"}))
        assert [x["id"] for x in cs["candidates"]] == ["s1_100"]
        r = await c.call_tool("view_candidate", {"tool": "limner", "target": "items/item.bandage", "candidate": "s1_100",
                                                 "max_size": 64})
        assert not r.is_error, text(r)
        im = next(x for x in r.content if x.type == "image")
        assert im.mime_type == "image/png" and Image.open(io.BytesIO(base64.b64decode(im.data))).size == (64, 64)
        assert "256x256" in text(r)
        r = await c.call_tool("view_candidate", {"tool": "dungeon", "target": "crypt/tile.crypt_floor",
                                                 "candidate": "s1_100"})
        im = next(x for x in r.content if x.type == "image")
        assert Image.open(io.BytesIO(base64.b64decode(im.data))).size == (384, 192), text(r)
        cs = as_json(await c.call_tool("candidates", {"tool": "items", "target": "item.cloth_trousers"}))
        assert cs["candidates"][0]["views"] == ["male_front"], cs
        r = await c.call_tool("view_candidate", {"tool": "items", "target": "item.cloth_trousers", "candidate": "s1_100"})
        assert not r.is_error and any(x.type == "image" for x in r.content), text(r)
        r = await c.call_tool("view_candidate", {"tool": "items", "target": "item.cloth_trousers", "candidate": "nope"})
        assert r.is_error and "no candidate" in text(r), text(r)

        # approve, through the ops
        as_json(await c.call_tool("approve", {"tool": "limner", "target": "items/item.bandage", "candidate": "s1_100"}))
        cs = as_json(await c.call_tool("candidates", {"tool": "limner", "target": "items/item.bandage"}))
        assert cs["approved"] == "s1_100"
        as_json(await c.call_tool("approve", {"tool": "dungeon", "target": "crypt/tile.crypt_floor", "candidate": "s1_100"}))
        cs = as_json(await c.call_tool("candidates", {"tool": "dungeon", "target": "crypt/tile.crypt_floor"}))
        assert cs["candidates"][0]["approved"] is True, cs

        # specs: a refused patch saves nothing; a good one is saved and read back
        before = as_json(await c.call_tool("get_spec", {"tool": "items", "target": "item.cloth_trousers"}))
        r = await c.call_tool("patch_spec", {"tool": "items", "target": "item.cloth_trousers", "patch": {"slot": "nope"}})
        assert r.is_error and "slot must be one of" in text(r), text(r)
        assert as_json(await c.call_tool("get_spec", {"tool": "items", "target": "item.cloth_trousers"})) == before
        r = as_json(await c.call_tool("patch_spec", {"tool": "limner", "target": "items/item.bandage",
                                                     "patch": {"subject": "a rolled linen bandage"}}))
        assert r["op"] == "limner.save_entry" and r["spec"]["subject"] == "a rolled linen bandage", r
        r = as_json(await c.call_tool("patch_spec", {"tool": "dungeon", "target": "crypt",
                                                     "patch": {"name": "Crypt (test)"}}))
        assert r["spec"]["name"] == "Crypt (test)" and "pieces" not in r["spec"], r
        r = await c.call_tool("get_spec", {"tool": "character", "target": "nope"})
        assert r.is_error and "targets:" in text(r), text(r)

        # generate queues a job; without ComfyUI it fails fast, and jobs_wait reports it
        g = as_json(await c.call_tool("generate", {"tool": "limner", "target": "items/item.bandage", "n": 1}))
        assert g["op"] == "limner.generate" and len(g["jobs"]) == 1, g
        w = as_json(await c.call_tool("jobs_wait", {"ids": g["jobs"], "timeout": 60}))
        assert w["done"] and w["jobs"][0]["status"] == "failed", w
        assert as_json(await c.call_tool("jobs_list", {"active_only": True})) == {"jobs": []}

        # call_op: JSON, images, and the op's own refusals
        assert "crypt" in as_json(await c.call_tool("call_op", {"name": "dungeon.sets"}))
        r = await c.call_tool("call_op", {"name": "limner.image", "args": {"path": "items/item.bandage/s1_100/icon.png"}})
        assert any(x.type == "image" for x in r.content), text(r)
        r = await c.call_tool("call_op", {"name": "jobs.wait", "args": {}})
        assert r.is_error and "give id or ids" in text(r), text(r)
        r = await c.call_tool("call_op", {"name": "nope.nope"})
        assert r.is_error and "unknown op" in text(r), text(r)

    # the Studio isn't running: tools list fine, calls say so
    down = StdioServerParameters(command=sys.executable, args=["-m", "hdart.cli", "mcp"], cwd=str(ROOT),
                                 env=dict(env, HD_STUDIO_URL=f"http://127.0.0.1:{closed}"))
    async with Client(down) as c:
        assert len((await c.list_tools()).tools) >= 16
        r = await c.call_tool("state", {"tool": "limner"})
        assert r.is_error and "isn't running" in text(r) and "start-art-tools.cmd" in text(r), text(r)


asyncio.run(main())
print("ok")
