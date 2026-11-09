"""Character Workshop, shared core: the data folder, the shared workshop state, the style prompts and page images.

    ai_dir() (<data>/sprites/ai)  the character tools' data: workshop.json, candidates/, paint/, face/, items/, ...
    workshop.json                 shared state: race mannequin candidates and approvals ("builds"), read by the races,
                                  puppet, Paint, Face and Item Workshop modules through load_state() / edit_state()
    specs/ai_style.json           the style groups every race prompt starts from (style(), style_text())

The tabs live in their own modules: races (hdart.tools.character.races), Template (puppet_template), Paint
(puppet_paint), Face (puppet_face), Item Workshop (item_workshop); ramps in hdart.imaging.palette, skin keying in
hdart.imaging.levels.
"""
import base64
import io
import json
from pathlib import Path

from PIL import Image

from hdart.core import config, store

ORDER = ("medium", "style", "proportions", "view", "rendering", "framing", "world")


# ----------------------------------------------------------------------------- paths (read per call: HD_DATA applies)

def ai_dir() -> Path:
    """<data>/sprites/ai: the Character and Item Workshops' data."""
    return config.data_dir() / "sprites" / "ai"


def state_path() -> Path:
    """workshop.json, the shared Workshop state."""
    return ai_dir() / "workshop.json"


def candidates_dir() -> Path:
    """Race mannequin candidates: candidates/<race>/<sex>/."""
    return ai_dir() / "candidates"


# ----------------------------------------------------------------------------- state & prompts

def _state():
    return store.State(state_path(), {"builds": {}, "parts": {}})


def load_state() -> dict:
    """workshop.json as it is now (read without the lock: use edit_state() to change it)."""
    return _state().load()


def save_state(s: dict) -> None:
    """Write workshop.json whole (tests and one-off fixes; tools change it with edit_state())."""
    _state().save(s)


def edit_state():
    """`with edit_state() as s:` changes workshop.json under its lock (Workshop, Paint, Face and Item Workshop share it)."""
    return _state().edit()


def style() -> dict:
    """specs/ai_style.json: the style groups ({"groups": {group: {"text": ...}}})."""
    return json.loads((config.specs_dir() / "ai_style.json").read_text(encoding="utf-8"))


def style_text(st: dict | None = None, groups=ORDER) -> str:
    """The style groups' texts joined into one prompt fragment (empty groups left out)."""
    st = st or style()
    return ", ".join(st["groups"][g]["text"] for g in groups if st["groups"][g]["text"].strip())


def rel(p) -> str:
    """A path under ai_dir() as the forward-slash relative path the state files store."""
    return str(Path(p).resolve().relative_to(ai_dir().resolve())).replace("\\", "/")


# ----------------------------------------------------------------------------- images for the pages

def url(img: Image.Image) -> str:
    """An image as a PNG data URL."""
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def image(relpath: str) -> bytes:
    """The bytes of a file under ai_dir() (the pages' /api/ws/img); refuses paths outside it."""
    p = (ai_dir() / relpath).resolve()
    if ai_dir().resolve() not in p.parents:
        raise ValueError("outside the AI folder")
    return p.read_bytes()
