"""The Studio's own ops: what is running, and a graceful restart through the supervisor (hdart/supervisor.py).

    uv run hdart op call studio.restart            restart once the running job has finished (after a landing)
    POST /api/op/studio.restart {"reason": "..."}

Every function here is an op (hdart/ops.py).
"""
import os
import time

from . import jobs, ops, supervisor

STARTED = time.time()


@ops.op("studio.info", read_only=True)
def info() -> dict:
    """This Studio process: {"pid", "started" (epoch seconds), "supervised", "restart_pending" ({"reason", "since"} or
    null)}. A new pid after studio.restart means the restart has happened."""
    return {"pid": os.getpid(), "started": STARTED, "supervised": supervisor.supervised(),
            "restart_pending": supervisor.pending()}


@ops.op("studio.restart")
def restart(reason: str = "asked for", wait_minutes: float = 30.0, check: bool = True) -> dict:
    """Restart the Studio so it runs the current code: no new job starts, the running one may finish (at most
    wait_minutes, then it is interrupted), then the supervisor starts a fresh server. Queued jobs carry on after.
    Returns {"restarting": true, "waiting_for": the running job or null}; poll studio.info for a new pid.

    Refused (409) when the Studio runs without the supervisor (`hdart studio --no-supervisor`), or when the code on
    disk doesn't import: then the Studio keeps running and the error says why.

    Args:
        reason: why (logged in work/studio.log)
        wait_minutes: how long the running job may take before it is interrupted
        check: import every Studio module in a fresh process first (false skips that)
    """
    if not supervisor.supervised():
        raise ops.OpError("this Studio runs without the supervisor (hdart studio --no-supervisor): stop it and start "
                          "it again by hand", 409)
    q = jobs.current()
    if check:
        err = supervisor.check_imports()
        if err:
            cancelled = supervisor.cancel_restart(q)
            supervisor.log(f"restart refused ({reason}): the code doesn't import"
                           + ("; the pending restart is called off" if cancelled else "") + f"\n{err}", "studio")
            raise ops.OpError(f"the code on disk doesn't import, so the Studio keeps running:\n{err}", 409)
    return supervisor.schedule_restart(q, reason, max(0.0, wait_minutes) * 60)
