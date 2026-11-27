"""Runs the M-Lab ndt7 speed test (core/ndt7.py) in a child process and keeps the latest result.

One test at a time, at most one every COOLDOWN seconds (a 1 Gbit/s test moves about 2.5 GB), and it
only ever starts when the admin presses the button. The result is saved so it survives restarts.
"""
import json
import os
import sys
import threading
import time

try:  # under eventlet the panel's threads are green; use the matching non-blocking subprocess
    from eventlet.green import subprocess
except ImportError:  # pragma: no cover
    import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "ndt7.py")
COOLDOWN = 600
MAX_RUNTIME = 75

_state = {"running": False, "phase": None, "mbps": None, "server": None, "error": None, "started": None}
_lock = threading.Lock()


def result_file(cfg):
    return (cfg or {}).get("SPEEDTEST_FILE") or os.path.join(os.path.dirname(HERE), "speedtest.json")


def last_result(cfg):
    try:
        with open(result_file(cfg)) as f:
            r = json.load(f)
        return r if isinstance(r, dict) and r.get("done") else None
    except (OSError, ValueError):
        return None


def status(cfg):
    with _lock:
        s = dict(_state)
    s["last"] = last_result(cfg)
    last_at = (s["last"] or {}).get("at") or 0
    s["cooldown"] = max(0, int(last_at + COOLDOWN - time.time()))
    return s


def start(cfg):
    """Returns (started, message)."""
    with _lock:
        if _state["running"]:
            return False, "A speed test is already running."
        last = last_result(cfg)
        if last and time.time() - last.get("at", 0) < COOLDOWN:
            return False, "Please wait {} more seconds between tests.".format(int(last["at"] + COOLDOWN - time.time()))
        _state.update(running=True, phase="locate", mbps=None, server=None, error=None, started=time.time())
    t = threading.Thread(target=_run, args=(cfg,), name="ndt7")
    t.daemon = True
    t.start()
    return True, "started"


def _run(cfg):
    final, error = None, None
    try:
        p = subprocess.Popen([sys.executable, SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, env=dict(os.environ, **(cfg or {}).get("NDT7_ENV", {})))
        deadline = time.time() + MAX_RUNTIME
        for line in p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with _lock:
                if msg.get("phase"):
                    _state.update(phase=msg["phase"], mbps=msg.get("mbps"))
                    if msg.get("server"):
                        _state["server"] = msg["server"]
            if msg.get("error"):
                error = msg["error"]
            if msg.get("done"):
                final = msg
            if time.time() > deadline:
                p.kill()
                error = "The speed test took too long and was stopped."
                break
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    except Exception as e:  # noqa: BLE001
        error = "Couldn't run the speed test: {}".format(e)
    if final:
        try:
            path = result_file(cfg)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(final, f)
            os.replace(tmp, path)
        except OSError as e:
            error = "Test finished but the result couldn't be saved: {}".format(e)
    elif not error:
        error = "The speed test ended without a result."
    with _lock:
        _state.update(running=False, phase=None, mbps=None, error=error)
