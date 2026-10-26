"""hdart.config: defaults, config.local.toml overrides, env overrides, project profiles. Run: uv run python tests/test_config.py"""
import os, shutil, tempfile
from pathlib import Path
import hdart.config as c

for k in ("HD_PROJECT", "HD_PROFILE", "HD_COMFY_URL", "HD_DATA", "HD_BLENDER", "HD_UNITY"):
    os.environ.pop(k, None)
tmp = Path(tempfile.mkdtemp())
shutil.copy(c.FILES[0], tmp / "config.toml")
c.FILES = (tmp / "config.toml", tmp / "config.local.toml")

# defaults
assert c.comfy_url() == "http://127.0.0.1:8188" and c.model("klein").startswith("flux-2-klein")
assert c.data_dir() == c.ROOT and c.backups_dir() == Path(r"P:\Archive\art-pipeline-backups")
p = c.project()
assert p.name == "hearthdelve" and p.path == Path(r"P:\Projects\Unity\Hearthdelve") and p.contract == p.path / "ArtContract"
assert p.export("puppets") == p.path / "Assets/StreamingAssets/Art/Puppets" and p.unity.name == "Unity.exe"
try:
    p.export("nope"); raise SystemExit("no error")
except KeyError:
    pass

# config.local.toml merges per key; a new file is picked up without a restart
(tmp / "config.local.toml").write_text('[comfy]\nurl = "http://gpu-box:8188"\n[paths]\ndata = \'D:\\\\hd-data\'\n'
                                       '[projects.test]\npath = \'D:\\\\proj\'\n[projects.test.exports]\nitems = "Art/Items"\n')
assert c.comfy_url() == "http://gpu-box:8188" and c.get()["comfy"]["timeout"] == 1800   # the rest of [comfy] stays
assert c.data_dir() == Path(r"D:\hd-data") and c.project("test").export("items") == Path(r"D:\proj\Art\Items")
assert c.project("test").unity is None and c.project("test").contract is None

# env beats files; HD_PROJECT only moves the active profile
os.environ.update(HD_COMFY_URL="http://x:1", HD_PROJECT=str(tmp / "wt"), HD_DATA=str(tmp / "data"))
assert c.comfy_url() == "http://x:1" and c.project().path == tmp / "wt" and c.data_dir() == tmp / "data"
assert c.project("test").path == Path(r"D:\proj")
os.environ["HD_PROFILE"] = "test"
assert c.project().name == "test" and c.project().path == tmp / "wt"
try:
    c.project("missing"); raise SystemExit("no error")
except KeyError:
    pass

# old callers: paths.REPO is read when used, not at import
import hdart.paths as paths
assert paths.REPO == tmp / "wt"
for k in ("HD_PROJECT", "HD_PROFILE", "HD_COMFY_URL", "HD_DATA"):
    os.environ.pop(k)
assert paths.REPO == Path(r"P:\Projects\Unity\Hearthdelve")
shutil.rmtree(tmp)
print("ok")
