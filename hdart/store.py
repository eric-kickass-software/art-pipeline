"""The candidate store every tool shares: state files written under a lock, candidate folders with a standard meta.json,
unique candidate ids, and archiving (nothing is deleted).

State (a tool's approvals, sets, picks) is a JSON file. Read it with State(path).load(); change it only inside

    with State(path).edit() as s:      # holds the file's lock from read to write, so two changes never undo each other
        s["approved"][item] = cid

The lock is per file, for the Studio's request threads and its job thread (a threading lock kept in hdart.locks, which
is never reloaded) and for other processes (the CLI, a test): a <file>.lock beside it. Every write is atomic (a temp
file, then os.replace), so a reader never sees half a file. A long GPU job must not hold the lock: generate first,
then edit the state.

Candidates: <root>/<id>/ with meta.json. new(root, seed) makes the folder; write_meta() writes the standard fields
(tool, id, made, seed, prompts, inputs, params, git) beside the tool's own ones, which stay as they were. A folder whose
name starts with "_" is never a candidate (the tools keep blanks and caches there).
"""
import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config, locks

STANDARD = ("tool", "id", "made", "seed", "prompts", "inputs", "params", "git")
LOCK_TIMEOUT = 30   # seconds to wait for another process's lock
for _k, _v in (("GUARD", threading.Lock()), ("STATE", {}), ("HELD", threading.local())):
    if not hasattr(locks, _k):   # a Studio started before these existed: hdart.locks is never reloaded
        setattr(locks, _k, _v)


# ----------------------------------------------------------------------------- files and locks

def read_json(path, default=None):
    """The JSON in `path`, else `default` (a fresh copy each call when it's a dict or list)."""
    path = Path(path)
    if not path.exists():
        return json.loads(json.dumps(default)) if isinstance(default, (dict, list)) else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, obj, indent=1):
    """Write atomically: a reader sees the old file or the new one, never a torn one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=indent), encoding="utf-8")
    for i in range(20):   # Windows: a reader holding the file open makes os.replace fail for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _thread_lock(path):
    key = str(Path(path).resolve()).lower()
    with locks.GUARD:
        return locks.STATE.setdefault(key, threading.RLock())


@contextlib.contextmanager
def _file_lock(path, timeout=LOCK_TIMEOUT):
    lf = Path(f"{path}.lock")
    lf.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lf, "a+b")
    try:
        t0 = time.time()
        while True:
            try:
                _lock_fd(fh)
                break
            except OSError:
                if time.time() - t0 > timeout:
                    raise TimeoutError(f"{path} is locked by another process (waited {timeout}s): {lf}")
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock_fd(fh)
    finally:
        fh.close()


if os.name == "nt":
    import msvcrt

    def _lock_fd(fh):
        fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fh):
        fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_fd(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_fd(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def lock(path):
    """Hold `path`'s lock (re-entrant within a thread)."""
    key = str(Path(path).resolve()).lower()
    held = locks.HELD.__dict__.setdefault("paths", {})   # the threading lock is re-entrant, the file one isn't
    with _thread_lock(path):
        if held.get(key):
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return
        with _file_lock(path):
            held[key] = 1
            try:
                yield
            finally:
                held.pop(key, None)


class State:
    """One tool's state file. `default`: what load() returns when the file doesn't exist yet."""

    def __init__(self, path, default=None):
        self.path, self.default = Path(path), {} if default is None else default

    def load(self):
        return read_json(self.path, self.default)

    def save(self, s):
        with lock(self.path):
            write_json(self.path, s)

    @contextlib.contextmanager
    def edit(self):
        """Read, let the caller change the dict, write it back, all under the lock. Not written if the block raises."""
        with lock(self.path):
            s = self.load()
            yield s
            write_json(self.path, s)


# ----------------------------------------------------------------------------- candidates

def new(root, seed=None, prefix=None):
    """A new, empty candidate folder under `root`: (id, path). Ids are s<seed>_<unix time> as before; a clash (two in
    the same second) gets a suffix."""
    root = Path(root)
    base = prefix or (f"s{seed}" if seed is not None else "c")
    stem = f"{base}_{int(time.time())}"
    root.mkdir(parents=True, exist_ok=True)
    for i in range(1000):
        cid = stem if i == 0 else f"{stem}_{i}"
        try:
            (root / cid).mkdir()
            return cid, root / cid
        except FileExistsError:
            continue
    raise RuntimeError(f"no free candidate id under {root}")


_git = {"key": None, "sha": None}


def git_sha():
    """The tool's short git sha (re-read when HEAD moves), or None."""
    head = config.ROOT / ".git"
    try:
        key = max((p.stat().st_mtime_ns for p in (head, head / "HEAD", head / "refs" / "heads") if p.exists()), default=None)
    except OSError:
        key = None
    if key is None or key != _git["key"]:
        try:
            sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT, capture_output=True, text=True,
                                 timeout=10).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            sha = None
        _git.update(key=key, sha=sha)
    return _git["sha"]


def write_meta(d, tool, seed=None, prompts=None, inputs=None, params=None, **extra):
    """A candidate's meta.json: the standard fields plus the tool's own (`extra`). `inputs`: the candidates it was made
    from, as {role: id} (e.g. {"paint": "s1_179..."}). `made` and `git` are filled in unless given. Returns the dict."""
    d = Path(d)
    meta = dict(extra)
    meta.update(tool=tool, id=d.name, seed=seed, prompts=prompts or {}, inputs=inputs or {}, params=params or {})
    meta.setdefault("made", int(time.time()))
    meta.setdefault("git", git_sha())
    write_json(d / "meta.json", meta)
    return meta


def read_meta(d):
    """A candidate's meta.json, {} if it has none (yet)."""
    try:
        return read_json(Path(d) / "meta.json", {})
    except ValueError:
        return {}


def update_meta(d, **changes):
    """Merge `changes` into a candidate's meta.json (a value of None removes the key). Returns the new dict."""
    with lock(Path(d) / "meta.json"):
        meta = read_meta(d)
        for k, v in changes.items():
            if v is None:
                meta.pop(k, None)
            else:
                meta[k] = v
        write_json(Path(d) / "meta.json", meta)
    return meta


def save_meta(d, meta):
    """Write a candidate's whole meta.json (after the tool changed the dict it read)."""
    write_json(Path(d) / "meta.json", meta)


def folders(root, finished=False):
    """A root's candidate folders, sorted by name (ids sort by seed, then time). finished=True: only those with a
    meta.json (done being made)."""
    root = Path(root)
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")
                  and (not finished or (p / "meta.json").exists()))


def archive(src, dst):
    """Move a candidate folder (or file) to `dst` in the archive. If `dst` is taken (archived before and restored, or
    the same id), the new one gets a time suffix. Returns where it went."""
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise ValueError(f"No candidate {src.name}.")
    if dst.exists():
        dst = dst.with_name(f"{dst.stem}_{time.strftime('%Y%m%d_%H%M%S')}{dst.suffix}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst


# ----------------------------------------------------------------------------- approvals

def pick(s, keys, cid, on=True):
    """Set (or clear, on=False, only if it's `cid`) the single approved id at s[keys[0]][keys[1]]...; makes the dicts."""
    *path, last = keys
    for k in path:
        s = s.setdefault(k, {})
    if on:
        s[last] = cid
    elif s.get(last) == cid:
        s.pop(last)
    return s


def member(lst, cid, on=True):
    """Add `cid` to (or remove it from) a set kept as a list, keeping the order."""
    if on and cid not in lst:
        lst.append(cid)
    if not on and cid in lst:
        lst.remove(cid)
    return lst
