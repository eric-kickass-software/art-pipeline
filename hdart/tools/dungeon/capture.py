"""Dungeon Forge capture: export a set and run Prototype V's batch capture in Unity (job kind "dun_capture").
"""
from pathlib import Path


from hdart.core import config, jobs
from hdart.tools.dungeon.export import export


def captures_dir() -> Path:
    """<data>/work/protov: Unity Prototype V captures (review images and the log)."""
    return config.data_dir() / "work" / "protov"


def capture(name, tiles=(32, 64)):
    """Export the set and run Prototype V's batch capture with it. Returns the screenshot file names."""
    import subprocess
    export(name, tiles)
    captures_dir().mkdir(parents=True, exist_ok=True)
    log = captures_dir() / "capture.log"
    proj = config.project()
    if not proj.unity:
        raise RuntimeError(f"no Unity exe for project {proj.name} (config.toml: projects.{proj.name}.unity)")
    r = subprocess.run([str(proj.unity), "-batchmode", "-projectPath", str(proj.path), "-executeMethod",
                        "Hearthdelve.Editor.PrototypeVSetup.Capture", "-reviewOut", str(captures_dir()), "-artSet", name,
                        "-logFile", str(log), "-quit"], timeout=1800)
    if r.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:] if log.exists() else []
        raise RuntimeError(f"Unity exited {r.returncode} (is this project open in the editor?)\n" + "\n".join(tail))
    return sorted(p.name for p in captures_dir().glob("v0*.png"))


@jobs.kind("dun_capture", gpu=False)   # Unity batch run, not ComfyUI
def _job_capture(a):
    return capture(a["set"])
