"""Per-user dashboard preferences kept on the server (theme follows you to any browser).

Stored as one small JSON file keyed by username. Usernames are only ever dictionary keys, never
part of a path, and every value is checked against an allowlist, so a request can't store anything
but the known settings.
"""
import json
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
_lock = threading.Lock()

ALLOWED = {
    "sky": {"auto", "day", "night"},
    "skytheme": {"obsidian", "teal", "glacier", "aurora", "sandsea", "graphite", "monsoon", "lotus", "nordic", "ember"},
}
MAX_USERS = 500


def _file(cfg):
    return (cfg or {}).get("PREFS_FILE") or os.path.join(os.path.dirname(HERE), "prefs.json")


def _load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def clean(values):
    """Keep only known keys with allowed values."""
    if not isinstance(values, dict):
        return {}
    return {k: v for k, v in values.items() if k in ALLOWED and isinstance(v, str) and v in ALLOWED[k]}


def get(cfg, user):
    return clean(_load(_file(cfg)).get(user) or {})


def update(cfg, user, values):
    """Merge allowed values into the user's prefs; returns the stored prefs."""
    new = clean(values)
    path = _file(cfg)
    with _lock:
        data = _load(path)
        cur = clean(data.get(user) or {})
        if not new or all(cur.get(k) == v for k, v in new.items()):
            return cur
        cur.update(new)
        data[user] = cur
        if len(data) > MAX_USERS:   # never grows without bound
            data = {user: cur}
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return cur
