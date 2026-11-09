"""The Studio supervisor (hdart/server/supervisor.py) and studio.restart, on a throwaway Studio: temp data and specs, a free
port, no GPU. A fake job module in a temp folder (loaded through HD_STUDIO_MODULES, watched with --watch) stands in for
the code: editing it restarts the Studio; a restart waits for a running job, and the job queued behind it runs after;
a syntax error leaves the old server running until the file is fixed; studio.restart restarts; a server that dies is
started again.
Run: uv run python tests/test_supervisor.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, os, signal, socket, subprocess, sys, tempfile, time
import urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
data, plug = tmp / "data", tmp / "plug"
plug.mkdir()
FAKE = '''"""Fake job kinds for test_supervisor ({tag})."""
import time
from hdart.core import jobs


@jobs.kind("sv_sleep", gpu=False)
def sleep(a):
    time.sleep(float(a.get("s", 0)))
    return "{tag}"
'''
(plug / "sv_fake.py").write_text(FAKE.format(tag="v1"), encoding="utf-8")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
base = f"http://127.0.0.1:{port}"
env = dict(os.environ, HD_DATA=str(data), HD_STUDIO_MODULES="sv_fake", PYTHONPATH=str(plug),
           HD_COMFY_URL="http://127.0.0.1:9", PYTHONWARNINGS="ignore",
           PYTHONDONTWRITEBYTECODE="1")   # a same-size rewrite within a second would load a stale .pyc
env.pop("HD_SUPERVISED", None)
out = open(tmp / "supervisor.out", "w", encoding="utf-8")
sup = subprocess.Popen([sys.executable, "-m", "hdart.cli", "studio", "--no-browser", "--port", str(port),
                        "--watch", str(plug)], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)


def op(name, args=None, timeout=30):
    """GET (args None) or POST one op; the JSON result, or {"error", "status"}; None when the Studio doesn't answer."""
    url = f"{base}/api/op/{name}"
    req = url if args is None else urllib.request.Request(url, json.dumps(args).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {**json.loads(e.read()), "status": e.code}
    except OSError:
        return None


def wait(cond, timeout=90, what=""):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        assert sup.poll() is None, "the supervisor died:\n" + (tmp / "supervisor.out").read_text(encoding="utf-8")[-3000:]
        v = cond()
        if v:
            return v
        time.sleep(0.3)
    raise AssertionError(f"timed out: {what}\n" + (tmp / "supervisor.out").read_text(encoding="utf-8")[-3000:])


def pid():
    i = op("studio.info")
    return i and i.get("pid")


def touch(tag):
    time.sleep(0.05)
    (plug / "sv_fake.py").write_text(FAKE.format(tag=tag), encoding="utf-8")


def log_text():
    return (data / "work" / "studio.log").read_text(encoding="utf-8")


try:
    p1 = wait(pid, what="first start")
    info = op("studio.info")
    assert info["supervised"] and info["restart_pending"] is None and p1 != sup.pid

    # 1. a code change restarts the server
    touch("v2")
    p2 = wait(lambda: (p := pid()) and p != p1 and p, what="restart after a change")

    # 2. a restart waits for the running job; the job queued behind it runs after the restart
    r1 = op("jobs.queue", {"kind": "sv_sleep", "args": {"s": 8}, "label": "long"})
    r2 = op("jobs.queue", {"kind": "sv_sleep", "args": {"s": 0}, "label": "after"})
    assert "job" in r1 and "job" in r2, (r1, r2, (tmp / "supervisor.out").read_text(encoding="utf-8"))
    j1, j2 = r1["job"], r2["job"]
    wait(lambda: (op(f"jobs.get?id={j1}") or {}).get("status") == "running", 10, "job 1 runs")
    touch("v3")
    wait(lambda: (op("studio.info") or {}).get("restart_pending"), 30, "restart pending")
    assert pid() == p2 and op(f"jobs.get?id={j1}")["status"] == "running" and op(f"jobs.get?id={j2}")["status"] == "queued"
    p3 = wait(lambda: (p := pid()) and p != p2 and p, what="restart after the job")
    g1 = op(f"jobs.get?id={j1}"); assert g1["status"] == "done" and g1["result"] == "v2", (g1, (tmp / "supervisor.out").read_text(encoding="utf-8"))
    wait(lambda: op(f"jobs.get?id={j2}")["status"] == "done", 20, "job 2 after the restart")
    assert op(f"jobs.get?id={j2}")["result"] == "v3"   # the new code ran it

    # 3. a syntax error: refused, the old server keeps running; fixing the file restarts it
    (plug / "sv_fake.py").write_text("def broken(:\n", encoding="utf-8")
    wait(lambda: "doesn't import" in log_text(), 60, "the refusal is logged")
    assert pid() == p3 and op("studio.info")["restart_pending"] is None
    assert op("jobs.list") is not None   # still serving
    touch("v4")
    p4 = wait(lambda: (p := pid()) and p != p3 and p, what="restart after the fix")

    # 4. studio.restart (what a landing session calls)
    r = op("studio.restart", {"reason": "test"}, timeout=120)
    assert r["restarting"] and r["waiting_for"] is None, r
    p5 = wait(lambda: (p := pid()) and p != p4 and p, what="studio.restart")
    assert "restarting (test)" in log_text()
    wait(lambda: "Studio up" in log_text().split("restarting (test)")[-1], 30, "the supervisor saw it up")

    # 5. a server that dies is started again
    os.kill(p5, signal.SIGTERM)
    wait(lambda: (p := pid()) and p != p5 and p, what="start again after a crash")
    assert "starting it again" in log_text()
finally:
    child = pid()
    sup.terminate(); sup.wait(10)
    if child:
        try:
            os.kill(child, signal.SIGTERM)
        except OSError:
            pass
    out.close()
print("ok")
