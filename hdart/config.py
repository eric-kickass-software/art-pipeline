"""Pipeline settings in one place: ComfyUI, models, programs, the data root, and a profile per project the tools serve.

Defaults are in config.toml (git); config.local.toml (git-ignored, same keys) overrides them per machine; env vars beat
both (HD_PROJECT, HD_PROFILE, HD_COMFY_URL, HD_DATA, HD_BLENDER, HD_UNITY). Call the functions when you need a value:
the files are re-read when they change, so nothing is frozen at import time.
"""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # this tool
FILES = (ROOT / "config.toml", ROOT / "config.local.toml")
_cache = {"key": None, "cfg": None}


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def get():
    """The merged settings (config.toml, then config.local.toml), without env overrides."""
    key = tuple(f.stat().st_mtime_ns if f.exists() else None for f in FILES)
    if _cache["key"] != key:
        cfg = {}
        for f in FILES:
            if f.exists():
                cfg = _merge(cfg, tomllib.loads(f.read_text(encoding="utf-8")))
        _cache.update(key=key, cfg=cfg)
    return _cache["cfg"]


def comfy_url():
    return os.environ.get("HD_COMFY_URL") or get()["comfy"]["url"]


def comfy_timeout():
    return int(get()["comfy"].get("timeout", 1800))


def model(name):
    """A model file name by its key in [models] (klein, qwen, flux2_vae, pixel_lora, trellis, dino, birefnet)."""
    return get()["models"][name]


def blender():
    return os.environ.get("HD_BLENDER") or get()["programs"]["blender"]


def data_dir():
    """Where the generated sources live (sprites/, work/, archive/). Not in git, so a worktree has its own, empty one."""
    d = os.environ.get("HD_DATA") or get()["paths"].get("data") or ""
    return Path(d) if d else ROOT


def specs_dir():
    """The specs folder (prompts, races, ramps, items, zones, dungeon sets, Limner): HD_SPECS, else <tool>/specs.
    Tests point HD_SPECS at a copy of tests/fixtures/specs, so Eric's live edits never break them."""
    d = os.environ.get("HD_SPECS")
    return Path(d) if d else ROOT / "specs"


def archive_dir() -> Path:
    """<data>/archive: where every tool's archive (the "removed" candidates, sets, spec history) goes."""
    return data_dir() / "archive"


def library_dir() -> Path:
    """The Dungeon Forge's materials library (recipes/ and materials/ in git, cache/ downloads): HD_LIBRARY, else
    <tool>/library."""
    d = os.environ.get("HD_LIBRARY")
    return Path(d) if d else ROOT / "library"


def backups_dir():
    return Path(get()["paths"]["backups"])


@dataclass
class Project:
    name: str
    path: Path
    contract: Path = None
    content: Path = None
    unity: Path = None
    exports: dict = field(default_factory=dict)

    def export(self, kind):
        """The export root of one kind of art (art, puppets, dungeon, items) in this project."""
        if kind not in self.exports:
            raise KeyError(f"project {self.name} has no '{kind}' export path in config.toml")
        return self.path / self.exports[kind]


def profile():
    return os.environ.get("HD_PROFILE") or get()["profile"]["default"]


def project(name=None):
    """The active project's profile (HD_PROFILE, else [profile] default). HD_PROJECT overrides its path, so a run can
    point at a worktree of the game."""
    name = name or profile()
    p = get().get("projects", {}).get(name)
    if p is None:
        raise KeyError(f"no project profile '{name}' in config.toml")
    path = Path(os.environ["HD_PROJECT"]) if os.environ.get("HD_PROJECT") and name == profile() else Path(p["path"])
    unity = os.environ.get("HD_UNITY") or p.get("unity")
    return Project(name, path, path / p["contract"] if p.get("contract") else None, path / p["content"] if p.get("content") else None,
                   Path(unity) if unity else None, dict(p.get("exports", {})))
