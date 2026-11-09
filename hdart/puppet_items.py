"""Transition shim, not code to use: the Item Workshop's module is hdart.item_workshop. A Studio started before A10
chunk 4 still names this module in its reload lists, so it stays importable until that Studio restarts. Nothing imports
it (tests/test_modules.py checks); delete it once the live Studio has restarted.
"""
from .item_workshop import *  # noqa: F401,F403
