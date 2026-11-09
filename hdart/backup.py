"""`hdart backup`: copy the generated sources that can't be regenerated cheaply to a dated folder outside the tool.

What's copied (relative to the data root, config.data_dir()):
  sprites/                  every tool's state and layers, except scratch (tmp/, _tmp*, the Blender mesh cache)
                            and, per dungeon set, the candidates nobody approved (state.json names the kept ones)
  specs/                    the specs as they are now (git has them too, but not edits made since the last commit)
Not copied: archive/ (already a dump of retired work), work/ (previews, captures), textures/, kit/.

The destination is config.backups_dir() (config.toml [paths] backups) / <YYYY-MM-DD_HHMMSS>/, with a manifest.json.
It must be outside the tool and the data root, so a worktree cleanup or a reset never takes the backup with it.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

from . import config

SKIP_DIRS = {"tmp", "_meshes", "__pycache__"}


def _scratch(rel):
    return any(p in SKIP_DIRS or p.startswith("_tmp") for p in rel.parts) or rel.suffix in (".lock", ".tmp")


def _kept_dungeon(state):
    """Candidate paths a dungeon set's state.json refers to other than in its `candidates` lists (approved, masters…)."""
    keep = set()

    def walk(v, key=None):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, str) and v.startswith("candidates/") and key != "candidates":
            keep.add(v)
    walk(state)
    return {_stem(x) for x in keep}


def _stem(rel):
    d, _, name = rel.rpartition("/")
    return f"{d}/{name.split('.', 1)[0]}"


def plan(data=None):
    """[(source, path relative to the backup)] of everything a backup copies."""
    data = Path(data or config.data_dir())
    files = []
    sprites = data / "sprites"
    dun = sprites / "dungeon"
    kept = {}
    for st in dun.glob("*/state.json"):
        try:
            kept[st.parent] = _kept_dungeon(json.loads(st.read_text(encoding="utf-8")))
        except ValueError:
            kept[st.parent] = None   # unreadable state: keep every candidate of that set
    for f in sorted(sprites.rglob("*")) if sprites.exists() else ():
        rel = f.relative_to(data)
        if not f.is_file() or _scratch(rel.relative_to("sprites")):
            continue
        if f.is_relative_to(dun) and len(rel.parts) > 4 and rel.parts[3] == "candidates":
            k = kept.get(dun / rel.parts[2])
            if k is not None and _stem("/".join(rel.parts[3:])) not in k:   # sidecars (.npy) share the image's stem
                continue
        files.append((f, rel))
    specs = config.specs_dir()
    files += [(f, Path("specs") / f.relative_to(specs)) for f in sorted(specs.rglob("*"))
              if f.is_file() and f.suffix != ".lock"]   # .lock: the store's lock files, not specs
    return files


def _git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def backup(dest_root=None, data=None, dry_run=False):
    """Copy plan() to <dest_root>/<stamp>/. Returns {"dest", "files", "bytes"}."""
    data = Path(data or config.data_dir()).resolve()
    dest_root = Path(dest_root or config.backups_dir()).resolve()
    for inside in (config.ROOT.resolve(), data):
        if dest_root == inside or dest_root.is_relative_to(inside):
            raise ValueError(f"the backup folder {dest_root} is inside {inside}: pick one outside the tool and its data")
    files = plan(data)
    total = sum(f.stat().st_size for f, _ in files)
    dest = dest_root / time.strftime("%Y-%m-%d_%H%M%S")
    if not dry_run:
        for src, rel in files:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
        (dest / "manifest.json").write_text(json.dumps({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "data": str(data), "tool": str(config.ROOT), "git": _git_sha(),
            "files": len(files), "bytes": total}, indent=1), encoding="utf-8")
    return {"dest": str(dest), "files": len(files), "bytes": total}
