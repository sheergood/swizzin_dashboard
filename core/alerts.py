"""Alerts to Discord.

Once a minute the panel checks what it already measures and posts to a Discord webhook when
something goes wrong, then once more when it's fixed. While a problem lasts it repeats every
REPEAT seconds, never more often. Rules (each can be turned off):

  disk   a filesystem above 90% full, or heading to full within 3 days at the last 24 hours' rate
  raid   an md array degraded / inactive, or a ZFS pool not ONLINE
  smart  a drive's SMART check failing, or its reallocated / pending sector count going up
  app    an app that should be running has been down for 5 minutes
  cpu    a CPU core at or above its own "high" mark (or 90 °C) for 3 checks in a row
  f2b    30 or more fail2ban bans in the last hour

The webhook is a secret: it's stored in alerts.json (mode 600) next to the panel, never sent back
to the browser in full, and only https://discord.com webhook addresses are accepted.
"""
import json
import os
import re
import threading
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
INTERVAL = 60
REPEAT = 6 * 3600
DISK_PCT = 90.0
DISK_DAYS = 3.0
APP_MINUTES = 5
CPU_CHECKS = 3
F2B_PER_HOUR = 30
RULES = ("disk", "raid", "smart", "app", "cpu", "f2b")
WEBHOOK = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d{5,30}/[A-Za-z0-9_-]{20,120}$")
COLORS = {"fire": 0xD23435, "warn": 0xE0A100, "ok": 0x16A34A, "test": 0x4A9BF2}

_lock = threading.Lock()
_mem = {"cfg": None, "state": None, "streak": {}}


# --------------------------------------------------------------------------- settings
def _paths(cfg):
    base = os.path.dirname(HERE)
    return ((cfg or {}).get("ALERTS_FILE") or os.path.join(base, "alerts.json"),
            (cfg or {}).get("ALERTS_STATE_FILE") or os.path.join(base, "alerts-state.json"))


def _write_private(path, data):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def settings(cfg):
    if _mem["cfg"] is None:
        try:
            with open(_paths(cfg)[0]) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        hook = d.get("webhook") if isinstance(d.get("webhook"), str) and WEBHOOK.match(d.get("webhook", "")) else None
        rules = {r: bool((d.get("rules") or {}).get(r, True)) for r in RULES}
        _mem["cfg"] = {"webhook": hook, "rules": rules}
    return _mem["cfg"]


def public_settings(cfg):
    s = settings(cfg)
    hook = s["webhook"]
    return {"configured": bool(hook), "webhook_hint": ("…" + hook[-6:]) if hook else None, "rules": dict(s["rules"]),
            "limits": {"disk_pct": DISK_PCT, "disk_days": DISK_DAYS, "app_minutes": APP_MINUTES,
                       "f2b_per_hour": F2B_PER_HOUR, "repeat_hours": REPEAT // 3600}}


def update_settings(cfg, data):
    """data: {"webhook": str|"" (clear)|absent (keep), "rules": {rule: bool}} -> (ok, message)."""
    if not isinstance(data, dict):
        return False, "bad request"
    with _lock:
        s = dict(settings(cfg))
        if "webhook" in data:
            hook = (data.get("webhook") or "").strip()
            if hook and not WEBHOOK.match(hook):
                return False, "That isn't a Discord webhook address (https://discord.com/api/webhooks/…)."
            s["webhook"] = hook or None
        if isinstance(data.get("rules"), dict):
            s["rules"] = {r: bool(data["rules"].get(r, s["rules"][r])) for r in RULES}
        _write_private(_paths(cfg)[0], s)
        _mem["cfg"] = s
    return True, "saved"


# --------------------------------------------------------------------------- sending
def send(cfg, title, text, kind, host, session=requests):
    hook = settings(cfg)["webhook"]
    if not hook:
        return False, "no webhook"
    body = {"username": "swizzin · {}".format(host)[:80], "allowed_mentions": {"parse": []},
            "embeds": [{"title": title[:250], "description": text[:3800], "color": COLORS.get(kind, COLORS["warn"]),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "footer": {"text": host[:100]}}]}
    for attempt in range(2):
        try:
            r = session.post(hook, json=body, timeout=10, allow_redirects=False)
        except requests.RequestException as e:
            return False, "couldn't reach Discord ({})".format(e.__class__.__name__)
        if r.status_code == 429 and attempt == 0:
            try:
                wait = float((r.json() or {}).get("retry_after", 2))
            except ValueError:
                wait = 2
            time.sleep(min(wait, 10))
            continue
        if r.status_code in (200, 204):
            return True, "sent"
        if r.status_code in (401, 404):
            return False, "Discord says this webhook doesn't exist any more"
        return False, "Discord answered HTTP {}".format(r.status_code)
    return False, "Discord is rate limiting"


# --------------------------------------------------------------------------- checks
def _gib(n):
    return "{:.1f} GiB".format(n / 2 ** 30) if n < 2 ** 40 else "{:.2f} TiB".format(n / 2 ** 40)


def evaluate(obs, state, now):
    """obs: what the panel sees now; returns {key: (title, text, kind)} for every problem present.
    Pure: the caller handles streaks, history and sending, so this is easy to test."""
    out = {}
    rules = obs.get("rules", {})
    if rules.get("disk", True):
        for m in obs.get("mounts", []):
            total, used = m.get("total") or 0, m.get("used") or 0
            if total < 2 ** 30 or m.get("readonly"):     # squashfs & co. are always "100% full"; tiny mounts are noise
                continue
            pct = 100.0 * used / total
            hist = state.setdefault("disk", {}).setdefault(m["mount"], [])
            if not hist or now - hist[-1][0] >= 3600:
                hist.append([int(now), int(used)])
                del hist[:-26]
            days = None
            old = [h for h in hist if now - h[0] >= 20 * 3600]
            if old:
                t0, u0 = old[-1]
                rate = (used - u0) / max(1, now - t0)            # bytes per second over ~a day
                if rate > 0:
                    days = (total - used) / rate / 86400
            if pct >= DISK_PCT:
                out["disk:" + m["mount"]] = ("{} is {:.0f}% full".format(m["mount"], pct),
                                             "{} free of {}.".format(_gib(total - used), _gib(total)), "fire")
            elif days is not None and days < DISK_DAYS:
                out["disk:" + m["mount"]] = ("{} will be full in about {:.1f} days".format(m["mount"], days),
                                             "{:.0f}% used, growing {} a day.".format(pct, _gib(rate * 86400)), "warn")
    if rules.get("raid", True):
        for a in obs.get("md", []):
            if a.get("health") in ("degraded", "inactive", "rebuilding"):
                failed = [x.get("dev") for x in a.get("members", []) if x.get("failed")]
                out["raid:" + a["name"]] = ("RAID {} is {}".format(a["name"], a["health"]),
                                            "{} {}{}".format(a.get("level", ""), a.get("map", "") or "",
                                                             " · failed: " + ", ".join(failed) if failed else ""), "fire")
        for p in obs.get("zfs", []):
            if (p.get("health") or "ONLINE") != "ONLINE":
                out["raid:zfs:" + p["name"]] = ("ZFS pool {} is {}".format(p["name"], p["health"]), "", "fire")
    if rules.get("smart", True):
        seen = state.setdefault("smart", {})
        for d in obs.get("drives", []):
            if not d.get("smart"):
                continue
            name = d.get("name")
            if d.get("healthy") is False:
                out["smart:" + name] = ("Drive {} is failing its SMART check".format(name), d.get("model") or "", "fire")
            bad = sum(d.get(k) or 0 for k in ("realloc", "pending"))
            prev = seen.get(name)
            if prev is not None and bad > prev:
                # a one-off event: kept (and shown) for a day, never "resolved" - the count doesn't go back down
                state.setdefault("events", {})["smart:sectors:" + name] = {
                    "t": now, "alert": ["Drive {} has new bad sectors".format(name),
                                        "{} · reallocated + pending went from {} to {}.".format(d.get("model") or "", prev, bad), "warn"]}
            if prev is None or bad != prev:
                seen[name] = bad
        for key, ev in list(state.get("events", {}).items()):
            if now - ev["t"] < 86400:
                out[key] = tuple(ev["alert"])
            else:
                del state["events"][key]
    if rules.get("app", True):
        for a in obs.get("apps", []):
            down_since = a.get("down_since")
            if down_since and now - down_since >= APP_MINUTES * 60:
                out["app:" + a["name"]] = ("{} is down".format(a.get("pretty") or a["name"]),
                                           "Stopped for {} minutes but enabled to run.".format(int((now - down_since) // 60)), "fire")
    if rules.get("cpu", True) and obs.get("cpu_hot"):
        out["cpu"] = ("CPU is running hot", obs["cpu_hot"], "warn")
    if rules.get("f2b", True) and (obs.get("bans_1h") or 0) >= F2B_PER_HOUR:
        out["f2b"] = ("{} fail2ban bans in the last hour".format(obs["bans_1h"]),
                      "Someone is hammering the server; fail2ban is blocking them.", "warn")
    return out


def run_once(cfg, obs, host, now=None, sender=None):
    """Compare with what's already firing; send new / repeated / resolved alerts. Returns what was sent."""
    now = now or time.time()
    sender = sender or (lambda title, text, kind: send(cfg, title, text, kind, host))
    sent = []
    with _lock:
        _, state_path = _paths(cfg)
        if _mem["state"] is None:
            try:
                with open(state_path) as f:
                    _mem["state"] = json.load(f)
            except (OSError, ValueError):
                _mem["state"] = {}
        state = _mem["state"]
        firing = state.setdefault("firing", {})
        obs = dict(obs, rules=settings(cfg)["rules"])
        current = evaluate(obs, state, now)
    configured = bool(settings(cfg)["webhook"])
    for key, (title, text, kind) in current.items():
        f = firing.get(key)
        if f is None or now - f["last_sent"] >= REPEAT:
            ok, _ = sender(title, text, kind) if configured else (False, "")
            if f is None:
                firing[key] = {"since": now, "last_sent": now if ok else 0, "alert": [title, text, kind]}
            elif ok:
                f["last_sent"] = now
            if ok:
                sent.append(title)
        else:
            f["alert"] = [title, text, kind]
    for key in [k for k in firing if k not in current]:
        f = firing.pop(key)
        if f.get("last_sent") and not key.startswith("smart:sectors:"):
            mins = int((now - f["since"]) // 60)
            ok, _ = sender("Resolved: " + f["alert"][0], "Back to normal after {} minutes.".format(mins), "ok") \
                if configured else (False, "")
            if ok:
                sent.append("Resolved: " + f["alert"][0])
    with _lock:
        try:
            _write_private(state_path, state)
        except OSError:
            pass
    return sent


def active(cfg):
    with _lock:
        st = _mem["state"] or {}
        return [{"title": f["alert"][0], "text": f["alert"][1], "kind": f["alert"][2], "since": f["since"]}
                for f in (st.get("firing") or {}).values()]
