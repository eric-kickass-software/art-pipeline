"""Overnight watchdog: keeps a long Studio queue running without anyone watching.

    uv run python -m hdart.watchdog        (Limner starts it by itself when a batch is queued)

Every CHECK seconds, while the Studio's queue has queued or running jobs:
  - keeps Windows awake (SetThreadExecutionState: the system stays on, the screen may sleep);
  - Studio not answering (twice in a row) → stops every 'hdart studio' process and starts a new one; the queue file
    survives, and a job interrupted by the restart is re-queued if it has retries left (hdart/jobs.py);
  - ComfyUI not answering (3×) → stops its processes and starts P:\\Tools\\ComfyUI\\start-comfyui.cmd again;
  - one job running longer than STUCK → interrupts ComfyUI (the job fails and retries); still the same job after
    another STUCK_GRACE → restarts the Studio (its worker is hung on something else).
Exits after IDLE_EXIT with an empty queue (and lets the PC sleep again). One instance at a time (work/watchdog.json
heartbeat). Everything it does goes to work/watchdog.log, plus a summary of the night when it exits.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

from . import config

CHECK, STUCK, STUCK_GRACE, IDLE_EXIT, HEARTBEAT_STALE = 30, 15 * 60, 5 * 60, 10 * 60, 120
STUDIO = "http://127.0.0.1:4321"


def _work():
    d = config.data_dir() / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pidfile():
    return _work() / "watchdog.json"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    with open(_work() / "watchdog.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def alive():
    """A watchdog with a fresh heartbeat exists."""
    try:
        d = json.loads(_pidfile().read_text(encoding="utf-8"))
        return time.time() - d.get("beat", 0) < HEARTBEAT_STALE
    except (OSError, ValueError):
        return False


def status():
    try:
        d = json.loads(_pidfile().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    try:
        lines = (_work() / "watchdog.log").read_text(encoding="utf-8").splitlines()[-6:]
    except OSError:
        lines = []
    return {"alive": alive(), "started": d.get("started"), "beat": d.get("beat"), "log": lines}


# ----------------------------------------------------------------------------- probes and actions

def _jobs():
    """The Studio's jobs, or None when it doesn't answer."""
    try:
        r = requests.get(f"{STUDIO}/api/ws/jobs", timeout=15)
        return r.json()["jobs"] if r.ok else None
    except (requests.RequestException, ValueError, KeyError):
        return None


def _queue_file_active():
    """Jobs still to do according to the saved queue (when the Studio is down)."""
    try:
        items = json.loads((config.data_dir() / "sprites" / "studio_jobs.json").read_text(encoding="utf-8"))["jobs"]
        return sum(1 for j in items if j["status"] in ("queued", "running"))
    except (OSError, ValueError, KeyError):
        return 0


def _comfy_ok():
    try:
        return requests.get(f"{config.comfy_url()}/system_stats", timeout=10).ok
    except requests.RequestException:
        return False


def _kill(pattern):
    """Stop every process whose command line matches the regex (PowerShell; Windows)."""
    ps = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -match '{pattern}' -and $_.ProcessId -ne {os.getpid()} }}"
          " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    return r.stdout.split()


def restart_studio(why):
    log(f"restarting the Studio: {why}")
    killed = _kill(r'hdart(\.exe)?"? studio')
    log(f"  stopped {len(killed)} process(es)")
    time.sleep(3)
    subprocess.run('start "Hearthdelve Studio :4321" /min cmd /c "uv run hdart studio --no-browser"', shell=True,
                   cwd=config.ROOT)
    for _ in range(30):
        time.sleep(2)
        if _jobs() is not None:
            log("  Studio is back")
            return True
    log("  Studio did not come back within 60 s (will try again)")
    return False


def restart_comfy(why):
    log(f"restarting ComfyUI: {why}")
    killed = _kill(r'ComfyUI.*main\.py')
    log(f"  stopped {len(killed)} process(es)")
    time.sleep(3)
    cmd = Path(os.environ.get("COMFYUI", r"P:\Tools\ComfyUI")) / "start-comfyui.cmd"
    subprocess.run(f'start "ComfyUI :8188" /min cmd /c ""{cmd}""', shell=True, cwd=cmd.parent)
    for _ in range(90):
        time.sleep(2)
        if _comfy_ok():
            log("  ComfyUI is back")
            return True
    log("  ComfyUI did not come back within 3 min (will try again)")
    return False


def _awake(on):
    if os.name != "nt":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


# ----------------------------------------------------------------------------- the loop

def main():
    if alive():
        print("a watchdog is already running")
        return
    started = time.time()
    beat = lambda: _pidfile().write_text(json.dumps({"pid": os.getpid(), "started": started, "beat": time.time()}),
                                         encoding="utf-8")
    beat()
    log(f"watchdog started (pid {os.getpid()})")
    studio_miss = comfy_miss = 0
    idle_since, stuck = None, {"id": None, "since": 0, "interrupted": False}
    seen_done, seen_failed, first = set(), set(), True
    try:
        while True:
            beat()
            jobs = _jobs()
            if jobs is None:
                studio_miss += 1
                active = _queue_file_active()
                if studio_miss >= 2 and active:
                    _awake(True)
                    restart_studio(f"not answering, {active} job(s) left")
                    studio_miss = 0
                elif not active:
                    idle_since = idle_since or time.time()
                time.sleep(CHECK)
                continue
            studio_miss = 0
            if first:   # failures from before this watchdog started aren't tonight's news
                seen_failed |= {j["id"] for j in jobs if j["status"] == "failed"}
                first = False
            for j in jobs:
                if j["status"] == "done":
                    seen_done.add(j["id"])
                elif j["status"] == "failed" and j["id"] not in seen_failed:
                    seen_failed.add(j["id"])
                    log(f"job failed: {j['label']}: {(j.get('error') or '')[:300]}")
            active = [j for j in jobs if j["status"] in ("queued", "running")]
            if not active:
                _awake(False)
                idle_since = idle_since or time.time()
                if time.time() - idle_since > IDLE_EXIT:
                    break
                time.sleep(CHECK)
                continue
            idle_since = None
            _awake(True)

            if _comfy_ok():
                comfy_miss = 0
            else:
                comfy_miss += 1
                if comfy_miss >= 3:
                    restart_comfy("not answering")
                    comfy_miss = 0

            run = next((j for j in jobs if j["status"] == "running"), None)
            if run and run["id"] == stuck["id"]:
                took = time.time() - stuck["since"]
                if took > STUCK and not stuck["interrupted"]:
                    log(f"job running {took / 60:.0f} min, interrupting ComfyUI: {run['label']}")
                    try:
                        requests.post(f"{config.comfy_url()}/interrupt", timeout=5)
                    except requests.RequestException:
                        pass
                    stuck["interrupted"], stuck["since"] = True, time.time()
                elif took > STUCK_GRACE and stuck["interrupted"]:
                    restart_studio(f"job still stuck after an interrupt: {run['label']}")
                    stuck = {"id": None, "since": 0, "interrupted": False}
            else:
                stuck = {"id": run["id"] if run else None, "since": (run or {}).get("started") or time.time(), "interrupted": False}
            time.sleep(CHECK)
    finally:
        _awake(False)
        log(f"watchdog done after {(time.time() - started) / 3600:.1f} h: {len(seen_done)} job(s) finished, "
            f"{len(seen_failed)} failed (retried when they had retries left)")
        try:
            _pidfile().unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
