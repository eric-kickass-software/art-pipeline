"""Where the pipeline lives and which project it writes into. Settings are in hdart/config.py (config.toml);
`REPO` is kept for old callers and resolves the active project's path when read, not at import."""
from .config import ROOT, data_dir, project  # noqa: F401  (ROOT = this tool, P:/Tools/art-pipeline)


def __getattr__(name):
    if name == "REPO":
        return project().path
    raise AttributeError(name)
