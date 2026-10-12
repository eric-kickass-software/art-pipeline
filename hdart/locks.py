"""Locks and caches shared by the Studio's request threads and its job thread. They live in their own module because the Studio
reloads dungeon.py and friends with importlib.reload on every request, which would replace a module-level lock."""
import threading

DUNGEON = threading.RLock()  # every read-modify-write of a set spec or a set's state.json
CELLS = {}  # dungeon.cell_png: (set, piece, candidate, tile, pixel, harmonize) -> {suffix: png bytes}
HARMONY = {}  # dungeon.harmonize references: (set, family, approved images) -> Lab (mean, std)
