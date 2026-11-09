"""Archiving mannequin candidates in hdart.workshop, on temp folders. Run: uv run python tests/test_workshop_archive.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import shutil, tempfile
from pathlib import Path
from PIL import Image
import hdart.ai_kit as ak
import hdart.workshop as ws

tmp = Path(tempfile.mkdtemp())
ak.AI_DIR = tmp / "ai"; ws.CAND = ak.AI_DIR / "candidates"; ws.STATE = ak.AI_DIR / "workshop.json"; ws.ARCHIVE = tmp / "archive"

out = ws.CAND / "slim" / "_base"; out.mkdir(parents=True)
for n in ("front_s1_1", "front_s2_1", "front_s3_1", "front_s9_0", "back_card_s1_1"):  # front_s9_0: older, no longer listed
    Image.new("RGB", (4, 4)).save(out / f"{n}.png")
r = lambda n: f"candidates/slim/_base/{n}.png"
ws.save_state({"builds": {"slim": {"description": "x", "front": {"candidates": [r("front_s1_1"), r("front_s2_1"), r("front_s3_1")],
               "approved": r("front_s1_1")}, "back": {"candidates": [r("back_card_s1_1")]}}}, "parts": {}})

assert ws.archive_base("slim", "front", r("front_s2_1")) == 1
assert (ws.ARCHIVE / "workshop" / r("front_s2_1")).exists() and not (ak.AI_DIR / r("front_s2_1")).exists()
try: ws.archive_base("slim", "front", r("front_s1_1")); raise SystemExit("approved archived")
except ValueError: pass
assert ws.archive_base("slim", "front") == 2  # s3 + the unlisted s9; back untouched
s = ws.load_state()["builds"]["slim"]
assert s["front"]["candidates"] == [r("front_s1_1")] and s["front"]["approved"] == r("front_s1_1"), s
assert (ak.AI_DIR / r("front_s1_1")).exists() and (ak.AI_DIR / r("back_card_s1_1")).exists()
assert (ws.ARCHIVE / "workshop" / r("front_s9_0")).exists()
assert ws.archive_base("slim", "back") == 1 and ws.load_state()["builds"]["slim"]["back"]["candidates"] == []
shutil.rmtree(tmp)
print("ok")
