"""hdart.store: state edits under the lock (threads and a second process never lose an update), atomic writes, the
standard meta.json, unique candidate ids, archiving, approvals; and that the Paint/Face/Item tools use it (approve and
archive through their own functions). Run: uv run python tests/test_store.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, os, subprocess, sys, tempfile, threading, time
from pathlib import Path
from hdart import store

tmp = Path(tempfile.mkdtemp())

# state: 8 threads x 25 read-modify-writes, plus a second process doing the same, lose nothing
st = store.State(tmp / "state.json", {"n": 0, "who": []})
assert st.load() == {"n": 0, "who": []} and not st.path.exists()
child = subprocess.Popen([sys.executable, "-c", f"""
from hdart import store
st = store.State(r"{st.path}", {{"n": 0, "who": []}})
for i in range(40):
    with st.edit() as s:
        s["n"] += 1; s["who"].append("p")
"""])


def bump(k):
    for i in range(25):
        with st.edit() as s:
            n = s["n"]; time.sleep(0.001); s["n"] = n + 1; s["who"].append(k)


ts = [threading.Thread(target=bump, args=(f"t{k}",)) for k in range(8)]
[t.start() for t in ts]; [t.join() for t in ts]
assert child.wait(60) == 0
s = st.load()
assert s["n"] == 8 * 25 + 40 and len(s["who"]) == s["n"], s["n"]
assert not list(tmp.glob("*.tmp")), "temp files left behind"

# re-entrant within a thread; an exception inside edit() writes nothing
with st.edit() as a:
    with st.edit() as b:
        b["inner"] = 1
    a["outer"] = 1
assert st.load().get("outer") == 1   # the outer write wins (it read before the inner one)
try:
    with st.edit() as s:
        s["boom"] = 1; raise KeyError("x")
except KeyError:
    pass
assert "boom" not in st.load()

# candidates: unique ids in the same second, standard meta, folders() skips "_" and unfinished ones
root = tmp / "cands"
ids = [store.new(root, 3)[0] for _ in range(3)]
assert len(set(ids)) == 3 and all(i.startswith("s3_") for i in ids), ids
(root / "_blank").mkdir()
m = store.write_meta(root / ids[0], "item", seed=3, prompts={"front": "a hat"}, inputs={"paint": "s1_1"}, race="human")
for k in store.STANDARD:
    assert k in m, k
assert m["id"] == ids[0] and m["race"] == "human" and m["made"] > 0 and m["inputs"] == {"paint": "s1_1"}
assert [p.name for p in store.folders(root)] == sorted(ids)
assert [p.name for p in store.folders(root, finished=True)] == [ids[0]]
store.update_meta(root / ids[0], edited=True); assert store.read_meta(root / ids[0])["edited"] is True
store.update_meta(root / ids[0], edited=None); assert "edited" not in store.read_meta(root / ids[0])
assert store.read_meta(root / ids[1]) == {}

# archive: moved, never overwritten
arch = tmp / "archive"
store.archive(root / ids[0], arch / ids[0])
(root / ids[0]).mkdir(); store.write_meta(root / ids[0], "item", seed=3)
dst = store.archive(root / ids[0], arch / ids[0])
assert dst != arch / ids[0] and dst.exists() and (arch / ids[0] / "meta.json").exists()
try:
    store.archive(root / "nope", arch / "nope"); raise AssertionError("archived a missing candidate")
except ValueError:
    pass

# approvals
s = {}
store.pick(s, ("approved", "item.hat", "all"), "c1"); assert s == {"approved": {"item.hat": {"all": "c1"}}}
store.pick(s, ("approved", "item.hat", "all"), "c2", on=False); assert s["approved"]["item.hat"] == {"all": "c1"}
store.pick(s, ("approved", "item.hat", "all"), "c1", on=False); assert s["approved"]["item.hat"] == {}
lst = []; store.member(lst, "a"); store.member(lst, "b"); store.member(lst, "a"); store.member(lst, "a", on=False)
assert lst == ["b"]

# the tools on the store: Paint approve/archive, Item approve/archive (to archive/workshop/items/...)
import hdart.workshop as ws
from hdart import config
import hdart.puppet_paint as ppt
import hdart.item_workshop as iw
os.environ["HD_DATA"] = str(tmp / "wdata")   # the Workshop's data root: a temp folder
c1, d1 = store.new(ppt.out_dir("human.male"), 1); store.write_meta(d1, "paint", seed=1, build="human.male")
c2, d2 = store.new(ppt.out_dir("human.male"), 2); store.write_meta(d2, "paint", seed=2, build="human.male")
assert ppt.approve("human.male", c1)["approved"] == c1
assert json.loads(ws.state_path().read_text())["builds"]["human.male"]["paint"]["approved"] == c1
try:
    ppt.archive("human.male", c1); raise AssertionError("archived the approved paint")
except ValueError:
    pass
ppt.archive("human.male", c2)
assert (config.archive_dir() / "workshop" / "paint" / "human.male" / c2 / "meta.json").exists() and not d2.exists()
assert ppt.approve("human.male", c1, on=False)["approved"] is None

i1, e1 = store.new(iw.out_dir("human", "item.hat"), 5); store.write_meta(e1, "item", seed=5, on="human.male")
assert iw.approve("human", "item.hat", i1) == {"item.hat": {"all": i1}}
assert [c["id"] for c in iw.candidates("human", "item.hat")] == [i1]
iw.archive("human", "item.hat", i1)
assert iw.load_state("human")["approved"]["item.hat"] == {} and iw.candidates("human", "item.hat") == []
assert (config.archive_dir() / "workshop" / "items" / "human" / "item.hat" / i1 / "meta.json").exists()
print("store ok")
