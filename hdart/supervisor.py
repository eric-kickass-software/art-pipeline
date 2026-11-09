"""The Studio supervisor: runs the Studio server as a child process and restarts it when the code changes.

    uv run hdart studio                    the supervisor (start-art-tools.cmd uses it); the server is its child
    uv run hdart studio --no-supervisor    the server alone (no restarts)

Why a restart and not module reloads: reloading modules in a running server left it half-reloaded, with stale shared
modules, after most landings. A fresh process always runs one consistent version of the code.

How a restart goes (one path for code changes, `POST /api/op/studio.restart` and `hdart op call studio.restart`):
  1. The supervisor polls `hdart/**/*.py` (and any `--watch` folder) every POLL seconds. A change is debounced: it
     waits until nothing has changed for QUIET seconds (a git checkout touches many files), then calls the child's
     `studio.restart` op.
  2. The op (in the child, hdart/studio_api.py) first imports every Studio module in a fresh process
     (`check_imports()`). If that fails (a syntax error, a broken import), it refuses with the error: the old server
     keeps running and the supervisor tries again on the next change.
  3. Otherwise the child pauses its job queue (no new job starts), waits for the running job to finish (at most
     `wait_minutes`, then the job is interrupted: the queue marks it "interrupted", or re-queues it if it has
     retries) and exits with RESTART_EXIT.
  4. The supervisor starts a new child. Queued jobs carry on from the saved queue (hdart/jobs.py).

A child that exits on its own after it was up is started again; one that dies before it answers (e.g. a broken import
that slipped through) is retried when the code changes again. Every start, change, refusal and exit is printed and appended to
`<data>/work/studio.log`. Ctrl+C (or closing the window) stops both.

This module stays light (standard library only): the supervisor never imports the tools, so a broken tool module
can't take it down.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # the repo (cwd of the child)
PKG = Path(__file__).resolve().parent           # hdart/: watched by default
RESTART_EXIT = 75   # the child's exit code for "restart me"
ENV = "HD_SUPERVISED"   # set in the child: the supervisor's pid
POLL, QUIET, QUIET_MAX = 1.0, 2.0, 30.0   # seconds: file poll, quiet time before acting, longest debounce
START_GRACE = 60.0   # seconds a new child has to answer HTTP before the supervisor stops waiting for it
CHECK_TIMEOUT = 180   # seconds the import check may take


# ----------------------------------------------------------------------------- shared (child and supervisor)

def supervised() -> bool:
    """True in a Studio started by the supervisor (so `studio.restart` can work)."""
    return bool(os.environ.get(ENV))


def log_file() -> Path:
    """`<data>/work/studio.log`: the supervisor's and the restart op's log."""
    from . import config   # light (tomllib); read per call so HD_DATA applies
    return config.data_dir() / "work" / "studio.log"


def log(msg: str, who: str = "supervisor") -> None:
    """Print a timestamped line to the console and append it to the log file (never raises)."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  [{who}] {msg}"
    print(line, flush=True)
    try:
        f = log_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def check_imports(timeout: float = CHECK_TIMEOUT) -> str | None:
    """Import every Studio module (hdart.studio.load_modules) in a fresh process with this process's environment.
    Returns None when that works, else the end of the error output."""
    try:
        r = subprocess.run([sys.executable, "-c", "from hdart import studio; studio.load_modules()"], cwd=ROOT,
                           capture_output=True, text=True, timeout=timeout, env=dict(os.environ, PYTHONWARNINGS="ignore"))
    except subprocess.TimeoutExpired:
        return f"the import check took longer than {timeout:.0f} s"
    if r.returncode == 0:
        return None
    lines = [ln for ln in (r.stderr or r.stdout).strip().splitlines() if "<frozen " not in ln]   # import machinery
    return "\n".join(lines[-10:]) or f"exit code {r.returncode}"


# ----------------------------------------------------------------------------- child side: a graceful restart

_restart = {"pending": None}   # the restart this child is waiting to do: {"reason", "since", "cancel": Event}
_restart_lock = threading.Lock()


def pending() -> dict | None:
    """The restart this Studio is waiting to do ({"reason", "since"}), or None."""
    p = _restart["pending"]
    return {"reason": p["reason"], "since": p["since"]} if p else None


def schedule_restart(queue, reason: str, wait_s: float, poll: float = 0.5) -> dict:
    """Restart this Studio once its running job has finished: pause `queue` (hdart.jobs.Jobs), wait for its running
    job (at most `wait_s` seconds), then exit the process with RESTART_EXIT (the OS closes the port; the queue file is
    already saved, and nothing else writes while the queue is paused). Returns at once with what it waits for. A
    second call while one is pending only reports it."""
    with _restart_lock:
        if _restart["pending"]:
            return {"restarting": True, "already": True, **pending()}
        cancel = threading.Event()
        _restart["pending"] = {"reason": reason, "since": time.time(), "cancel": cancel}
    queue.pause()
    job = queue.running()

    def wait_then_exit():
        time.sleep(1)   # let the op's HTTP answer go out first
        end = time.monotonic() + wait_s
        if job:
            log(f"restart ({reason}) waits for job #{job['id']} ({job['label']}), at most {wait_s / 60:.0f} min", "studio")
        while queue.running() and time.monotonic() < end and not cancel.is_set():
            time.sleep(poll)
        if cancel.is_set():
            return
        still = queue.running()
        log(f"restarting ({reason})" + (f"; job #{still['id']} is interrupted" if still else ""), "studio")
        sys.stdout.flush()
        os._exit(RESTART_EXIT)

    threading.Thread(target=wait_then_exit, daemon=True).start()
    return {"restarting": True, "already": False, "reason": reason,
            "waiting_for": {"id": job["id"], "label": job["label"]} if job else None}


def cancel_restart(queue) -> bool:
    """Call off a pending restart (the code broke again while it waited) and start the queue again."""
    with _restart_lock:
        p = _restart["pending"]
        if not p:
            return False
        p["cancel"].set()
        _restart["pending"] = None
    queue.pause(False)
    return True


# ----------------------------------------------------------------------------- the supervisor

def snapshot(roots) -> dict:
    """{path: mtime_ns} of every .py file under `roots` (added and deleted files count as changes too)."""
    out = {}
    for r in roots:
        for p in Path(r).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            try:
                out[str(p)] = p.stat().st_mtime_ns
            except OSError:
                pass
    return out


def rel(p) -> str:
    """A path relative to the repo when it is inside it, else as given."""
    p = Path(p)
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def changed(a: dict, b: dict) -> list:
    """The files that differ between two snapshots (relative to the repo where possible)."""
    return [rel(n) for n in sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))]


class Supervisor:
    """Runs `python -m hdart.cli studio --no-supervisor --port <port>` and restarts it (see the module docstring).
    `watch`: extra folders whose .py files count as code; `env`: extra environment for the child."""

    def __init__(self, port: int = 4321, open_browser: bool = True, watch=(), env: dict | None = None):
        self.port, self.open_browser = port, open_browser
        self.roots = [PKG] + [Path(w) for w in watch]
        self.env = {**os.environ, **(env or {}), ENV: str(os.getpid())}
        self.base = f"http://127.0.0.1:{port}"
        self.child, self.up, self.waiting_for_change = None, False, False

    # ---------------------------------------------------------------- child process
    def start(self):
        cmd = [sys.executable, "-m", "hdart.cli", "studio", "--no-supervisor", "--no-browser", "--port", str(self.port)]
        self.child, self.up = subprocess.Popen(cmd, cwd=ROOT, env=self.env), False
        log(f"Studio starting (pid {self.child.pid})")
        t0 = time.monotonic()
        while time.monotonic() - t0 < START_GRACE:
            if self.child.poll() is not None:
                return False
            if self.call("studio.info", None, timeout=3) is not None:
                self.up = True
                log(f"Studio up on {self.base} (pid {self.child.pid}, {time.monotonic() - t0:.1f} s)")
                if self.open_browser:
                    webbrowser.open(self.base + "/workshop")
                    self.open_browser = False
                return True
            time.sleep(0.3)
        log(f"Studio (pid {self.child.pid}) doesn't answer after {START_GRACE:.0f} s; leaving it running")
        return True

    def stop_child(self):
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(15)
            except subprocess.TimeoutExpired:
                self.child.kill()

    def call(self, op: str, args: dict | None, timeout: float = 30):
        """Call one of the child's ops: the JSON result, {"error": ...} when it refuses, None when it doesn't answer."""
        url = f"{self.base}/api/op/{op}"
        req = urllib.request.Request(url, data=json.dumps(args).encode(), method="POST",
                                     headers={"Content-Type": "application/json"}) if args is not None else url
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return {"error": json.loads(e.read()).get("error", str(e))}
            except ValueError:
                return {"error": str(e)}
        except (OSError, ValueError):
            return None

    # ---------------------------------------------------------------- code changes
    def on_change(self, files: list):
        shown = ", ".join(files[:6]) + (f" (+{len(files) - 6} more)" if len(files) > 6 else "")
        log(f"code changed: {shown}")
        if self.child is None or self.child.poll() is not None:
            self.launch()
            return
        r = self.call("studio.restart", {"reason": f"code changed: {shown}"}, timeout=CHECK_TIMEOUT + 30)
        if r is None:   # hung or not up yet: check the code here, then replace it
            err = check_imports()
            if err:
                log(f"the new code doesn't import; the Studio keeps running the old code:\n{err}")
                return
            log("the Studio doesn't answer; stopping it")
            self.stop_child()
            self.launch()
        elif "error" in r:
            log(f"restart refused; the Studio keeps running the old code: {str(r['error']).split(chr(10))[0]}")
        elif r.get("waiting_for"):
            w = r["waiting_for"]
            log(f"restart waits for job #{w['id']} ({w['label']})")

    def launch(self):
        """Start a child; when it dies at start, wait for the next code change before trying again."""
        ok = self.start()
        self.waiting_for_change = not ok
        if not ok:
            log(f"the Studio failed to start (exit code {self.child.returncode}); fix the code: I retry when a file "
                "changes")

    def on_exit(self, code: int):
        if code == RESTART_EXIT:
            log("restarting the Studio")
            self.launch()
        elif not self.up:   # it never answered: broken code, so wait for a fix
            self.waiting_for_change = True
            log(f"the Studio stopped at start (exit code {code}); I retry when a file changes")
        else:
            log(f"the Studio stopped (exit code {code}); starting it again")
            time.sleep(2)
            self.launch()

    # ---------------------------------------------------------------- main loop
    def run(self):
        log(f"supervisor (pid {os.getpid()}) watching {', '.join(rel(r) for r in self.roots)}")
        seen = snapshot(self.roots)
        self.launch()
        try:
            while True:
                time.sleep(POLL)
                if self.child and not self.waiting_for_change and self.child.poll() is not None:
                    self.on_exit(self.child.returncode)
                    continue   # `seen` stays: a file changed during the restart still counts on the next pass
                now = snapshot(self.roots)
                if now == seen:
                    continue
                t0, last = time.monotonic(), now   # debounce: until QUIET seconds pass with no further change
                while time.monotonic() - t0 < QUIET_MAX:
                    time.sleep(QUIET)
                    nxt = snapshot(self.roots)
                    if nxt == last:
                        break
                    last = nxt
                files, seen = changed(seen, last), last
                if files:
                    try:
                        self.on_change(files)
                    except Exception as e:   # the supervisor must outlive anything a change does
                        log(f"error while handling the change: {e!r}")
        except KeyboardInterrupt:
            log("stopping (Ctrl+C)")
        finally:
            self.stop_child()


def main(port: int = 4321, open_browser: bool = True, watch=()) -> None:
    """`hdart studio`: run the supervisor until Ctrl+C."""
    Supervisor(port, open_browser, watch).run()
