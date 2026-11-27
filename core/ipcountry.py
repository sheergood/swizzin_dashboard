"""Country of an IP address, for "bans by country".

Lookups go to api.country.is (free, no key, HTTPS, MaxMind GeoLite2 data, rate limit 10/s per IP)
in a background thread, a few at a time, and are cached on disk for 30 days, so each banned address
is looked up once. Nothing is looked up when PLACE_LOOKUPS is off in swizzin.cfg, and private or
reserved addresses are never sent anywhere. Page requests only ever read the cache: they never wait
on the network.
"""
import ipaddress
import json
import os
import re
import threading
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.country.is/{}"
TTL = 30 * 86400            # a country rarely changes; re-check monthly
MISS_TTL = 86400            # "no data" answers are retried daily
BATCH = 40                  # at most this many lookups per background run
SPACING = 0.2               # seconds between lookups (5/s, half the service's limit)
MAX_CACHE = 20000
UA = {"User-Agent": "swizzin-panel (+https://github.com/sheergood/swizzin_dashboard)"}
CC = re.compile(r"^[A-Z]{2}$")

_lock = threading.Lock()
_state = {"cache": None, "running": False, "pending": set(), "dirty": False, "blocked_until": 0}


def _file(cfg):
    return (cfg or {}).get("IPCOUNTRY_FILE") or os.path.join(os.path.dirname(HERE), "ipcountry.json")


def public_ip(ip):
    """Normalised address if it's a real public IP worth looking up, else None."""
    try:
        a = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    if a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified:
        return None
    return str(a)


def _load(cfg):
    if _state["cache"] is None:
        try:
            with open(_file(cfg)) as f:
                data = json.load(f)
            _state["cache"] = {k: v for k, v in data.items()
                               if isinstance(v, list) and len(v) == 2 and (v[0] is None or CC.match(str(v[0])))}
        except (OSError, ValueError, AttributeError):
            _state["cache"] = {}
    return _state["cache"]


def _save(cfg):
    cache = _state["cache"]
    if len(cache) > MAX_CACHE:   # keep the newest entries
        keep = sorted(cache.items(), key=lambda kv: kv[1][1], reverse=True)[:MAX_CACHE // 2]
        cache.clear()
        cache.update(keep)
    path = _file(cfg)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, path)
    _state["dirty"] = False


def lookup_one(ip, session=requests):
    """Returns (cc or None, retry_after or None). Raises on network errors."""
    r = session.get(API.format(ip), headers=UA, timeout=6)
    if r.status_code == 429:
        return None, int(r.headers.get("Retry-After") or 60)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    cc = str((r.json() or {}).get("country") or "").upper()
    return (cc if CC.match(cc) else None), None


def _worker(cfg):
    try:
        while True:
            with _lock:
                todo = list(_state["pending"])[:BATCH]
                if not todo or time.time() < _state["blocked_until"]:
                    break
            for ip in todo:
                try:
                    cc, retry = lookup_one(ip)
                except Exception:
                    with _lock:
                        _state["blocked_until"] = time.time() + 300    # network trouble: back off 5 min
                        _state["pending"].clear()
                    return
                with _lock:
                    if retry:
                        _state["blocked_until"] = time.time() + retry
                        _state["pending"].clear()
                        return
                    _state["cache"][ip] = [cc, int(time.time())]
                    _state["pending"].discard(ip)
                    _state["dirty"] = True
                time.sleep(SPACING)
            break   # one batch per run; the next page refresh queues the rest
    finally:
        with _lock:
            try:
                if _state["dirty"]:
                    _save(cfg)
            except OSError:
                pass
            _state["running"] = False


def countries(ips, cfg):
    """{ip: cc} for the addresses already known; queues the rest for a background lookup."""
    out, missing, now = {}, [], time.time()
    with _lock:
        cache = _load(cfg)
        for raw in ips:
            ip = public_ip(raw)
            if not ip:
                continue
            hit = cache.get(ip)
            if hit and now - hit[1] < (TTL if hit[0] else MISS_TTL):
                if hit[0]:
                    out[raw] = hit[0]
            else:
                if hit and hit[0]:
                    out[raw] = hit[0]          # stale but still shown while it's re-checked
                missing.append(ip)
        if missing and (cfg or {}).get("PLACE_LOOKUPS", True) and not _state["running"] and now >= _state["blocked_until"]:
            _state["pending"].update(missing)
            _state["running"] = True
            t = threading.Thread(target=_worker, args=(cfg,), name="ipcountry")
            t.daemon = True
            t.start()
    return out


def summarize(counts, cc_of, top=8):
    """counts: [(ip, bans)] -> [{"cc", "bans", "ips"}] largest first, plus an "unknown" total."""
    agg, unknown = {}, 0
    for ip, n in counts:
        cc = cc_of.get(ip)
        if not cc:
            unknown += n
            continue
        a = agg.setdefault(cc, {"cc": cc, "bans": 0, "ips": 0})
        a["bans"] += n
        a["ips"] += 1
    rows = sorted(agg.values(), key=lambda a: (-a["bans"], a["cc"]))
    return rows[:top], sum(a["bans"] for a in rows[top:]), unknown
