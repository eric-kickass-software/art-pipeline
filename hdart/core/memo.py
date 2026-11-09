"""Small in-memory caches for the Studio's previews (one long-running process, many repeated renders).

    stamp(*paths)   a cheap fingerprint of files / folders' files (count + newest mtime): part of a cache key, so editing,
                    re-saving or replacing a file on disk invalidates what was built from it
    LRU(n)          a thread-safe least-recently-used dict (the Studio serves requests on threads)
    frozen(arr)     the array marked read-only, so a cached image can't be changed in place by a caller
"""
import os, threading
from collections import OrderedDict


def stamp(*paths):
    """(file count, newest mtime_ns) over the given files and the files directly inside the given folders; missing paths
    count as nothing."""
    n, newest = 0, 0
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if os.path.isdir(p):
            with os.scandir(p) as it:
                for e in it:
                    if e.is_file():
                        n += 1; newest = max(newest, e.stat().st_mtime_ns)
        else:
            n += 1; newest = max(newest, st.st_mtime_ns)
    return n, newest


class LRU:
    def __init__(self, size):
        self.size, self.d, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key, default=None):
        with self.lock:
            if key not in self.d:
                return default
            self.d.move_to_end(key)
            return self.d[key]

    def put(self, key, value):
        with self.lock:
            self.d[key] = value; self.d.move_to_end(key)
            while len(self.d) > self.size:
                self.d.popitem(last=False)
        return value

    def clear(self):
        with self.lock:
            self.d.clear()


def frozen(a):
    a.setflags(write=False)
    return a
