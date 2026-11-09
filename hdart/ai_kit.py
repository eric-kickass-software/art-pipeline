"""Transition shim, not code to use: a Studio started before A10 chunk 4 still names this module in its reload lists,
so it stays importable until that Studio restarts. Its parts now live in hdart.workshop (AI_DIR), hdart.races (BIG),
hdart.imageops (foreground, clean) and hdart.config (ROOT). Nothing imports it (tests/test_modules.py checks); delete
it once the live Studio has restarted.
"""
from .config import ROOT  # noqa: F401
from .imageops import clean, foreground  # noqa: F401
from .races import BIG  # noqa: F401
from .workshop import AI_DIR  # noqa: F401
