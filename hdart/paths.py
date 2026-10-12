"""Where the pipeline and the Unity project it writes into live. The pipeline is in P:\Tools\art-pipeline, outside
the Hearthdelve repo, so the project is found by path: HD_PROJECT, or the root checkout by default."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent                                   # this tool (P:\Tools\art-pipeline)
REPO = Path(os.environ.get("HD_PROJECT", r"P:\Projects\Unity\Hearthdelve"))     # Unity project root
