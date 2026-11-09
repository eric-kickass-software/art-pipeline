"""Locks and caches shared by the Studio's request threads and its job thread, kept in one small module so every tool
module shares the same objects. In memory only: they are rebuilt after a Studio restart."""
import threading

DUNGEON = threading.RLock()  # every read-modify-write of a set spec or a set's state.json
CELLS = {}  # dungeon.cell_png: (set, piece, candidate, tile, pixel, harmonize) -> {suffix: png bytes}
HARMONY = {}  # dungeon.harmonize references: (set, family, approved images) -> Lab (mean, std)
GUARD = threading.Lock()  # guards STATE
STATE = {}  # hdart.core.store: resolved state-file path -> its RLock (the file lock beside it covers other processes)
HELD = threading.local()  # hdart.core.store: the state files this thread holds the file lock of (re-entrant locking)
