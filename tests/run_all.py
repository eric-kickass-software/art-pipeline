"""Run every test (each in its own process) without the GPU: HD_COMFY_URL points at a closed port, so a test that
reaches for ComfyUI without the fake (hdart.fake_comfy) fails instead of queueing behind the live Studio.
Run: uv run python tests/run_all.py [name-filter]"""
import os, subprocess, sys, time
from pathlib import Path

here = Path(__file__).parent
env = dict(os.environ, HD_COMFY_URL="http://127.0.0.1:9", PYTHONWARNINGS="ignore::ResourceWarning")
env.pop("HD_PROJECT", None)
env.pop("HD_SUPERVISED", None)   # tests start their own Studios; none of them is supervised unless it says so
pick = sys.argv[1] if len(sys.argv) > 1 else ""
failed = []
for t in sorted(here.glob("test_*.py")):
    if pick not in t.stem:
        continue
    t0 = time.time()
    r = subprocess.run([sys.executable, str(t)], cwd=here.parent, env=env, capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    ok = r.returncode == 0
    print(f"{'ok  ' if ok else 'FAIL'} {time.time() - t0:5.0f}s  {t.stem}  {'' if ok else (r.stderr.strip().splitlines() or [''])[-1][:160] or last}")
    if not ok:
        failed.append(t.stem)
print(f"\n{len(failed)} failed: {', '.join(failed)}" if failed else "\nall passed")
sys.exit(1 if failed else 0)
