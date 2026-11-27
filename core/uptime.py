"""App uptime history: is each installed app running, sampled once a minute.

Samples are kept as hourly buckets (48 hours) and saved to a small JSON file every few minutes, so a
panel restart keeps the history (the minutes the panel itself was down simply have no samples and
show as "no data", never as downtime). An app that is stopped *and* disabled on purpose counts as
"off", not as down.
"""
import json
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_HOURS = 48
SAVE_EVERY = 300
INTERVAL = 60

_lock = threading.Lock()
_state = {"data": None, "saved": 0.0}


def _file(cfg):
    return (cfg or {}).get("UPTIME_FILE") or os.path.join(os.path.dirname(HERE), "uptime.json")


def _load(cfg):
    if _state["data"] is None:
        try:
            with open(_file(cfg)) as f:
                d = json.load(f)
            if not isinstance(d, dict) or not isinstance(d.get("apps"), dict):
                raise ValueError
            _state["data"] = {"apps": d["apps"], "panel": d.get("panel") or {}}
        except (OSError, ValueError):
            _state["data"] = {"apps": {}, "panel": {}}
    return _state["data"]


def _hour(ts):
    return str(int(ts // 3600 * 3600))


def record(statuses, cfg, now=None):
    """statuses: [{"name", "active", "enabled"}] from util.apps_status."""
    now = time.time() if now is None else now
    h = _hour(now)
    with _lock:
        d = _load(cfg)
        p = d["panel"]
        p[h] = p.get(h, 0) + 1
        for s in statuses:
            name = str(s.get("name") or "")[:40]
            if not name:
                continue
            a = d["apps"].setdefault(name, {})
            up, expected, off = a.get(h, [0, 0, 0])
            if s.get("active"):
                up, expected = up + 1, expected + 1
            elif s.get("enabled"):
                expected += 1          # should be running but isn't: downtime
            else:
                off += 1               # stopped and disabled on purpose
            a[h] = [up, expected, off]
            a["_last"] = "up" if s.get("active") else "down" if s.get("enabled") else "off"
            if a["_last"] == "down":
                a.setdefault("_down_since", now)
            else:
                a.pop("_down_since", None)
        cutoff = int(now // 3600 * 3600) - KEEP_HOURS * 3600
        for a in list(d["apps"].values()) + [p]:
            for k in [k for k in a if k[0].isdigit() and int(k) < cutoff]:
                del a[k]
        if now - _state["saved"] > SAVE_EVERY:
            save(cfg)
            _state["saved"] = now


def save(cfg):
    d = _state["data"]
    if d is None:
        return
    path = _file(cfg)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, path)
    except OSError:
        pass


def report(cfg, installed=None, now=None):
    """Last 24 hours per app: hourly [{t, up, expected, off}] (None = no samples) and overall %."""
    now = time.time() if now is None else now
    first = int(now // 3600 * 3600) - 23 * 3600
    hours = [first + i * 3600 for i in range(24)]
    out = []
    with _lock:
        d = _load(cfg)
        names = sorted(d["apps"]) if installed is None else [n for n in installed if n in d["apps"]] + \
            [n for n in installed if n not in d["apps"]]
        for name in names:
            a = d["apps"].get(name, {})
            cells, up_t, exp_t = [], 0, 0
            for t in hours:
                v = a.get(str(t))
                if not v:
                    cells.append(None)
                    continue
                up, expected, off = (list(v) + [0, 0, 0])[:3]
                cells.append({"up": up, "expected": expected, "off": off})
                up_t += up
                exp_t += expected
            out.append({"name": name, "hours": cells, "now": a.get("_last"),
                        "pct": round(100.0 * up_t / exp_t, 2) if exp_t else None})
        panel = [d["panel"].get(str(t), 0) for t in hours]
    return {"start": first, "apps": out, "panel_samples": panel, "interval": INTERVAL}


def down_since(cfg):
    """{app: epoch} for apps that should be running and aren't, since when (for alerts)."""
    with _lock:
        d = _load(cfg)
        return {n: a["_down_since"] for n, a in d["apps"].items() if a.get("_down_since")}
