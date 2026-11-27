"""Torrent racing: live qBittorrent stats and autobrr's recent grabs, read-only.

qBittorrent (one instance per user) is read through its Web API on 127.0.0.1 with
"Bypass authentication for clients on localhost" turned on; if it's off, the card says so. The
panel only ever calls read endpoints. Addresses (and the autobrr key) come from
/var/lib/swizzin/arr.json, written by the root collector.

A background loop calls qBittorrent's incremental /api/v2/sync/maindata every few seconds, so
after the first call only changes travel even with thousands of torrents. From those changes it
keeps, while the panel runs:
  - upload per tracker per day (from the growth of each torrent's "uploaded"), 8 days
  - each torrent's upload in its first 10 minutes (the racing number), 7 days
"""
import json
import os
import re
import threading
import time
import warnings
from urllib.parse import urlsplit

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
APPS_FILE = os.environ.get("SWZ_TEST_ARR", "/var/lib/swizzin/arr.json")
SYNC_EVERY = 5
AUTOBRR_EVERY = 30
FIRST_WINDOW = 600            # "first 10 minutes"
KEEP_DAYS = 8
FIELDS = ("name", "state", "upspeed", "dlspeed", "uploaded", "downloaded", "ratio", "tracker", "added_on",
          "size", "progress", "num_leechs", "num_seeds", "category", "amount_left")
DL_STATES = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "stalledDL", "queuedDL", "checkingDL", "allocating"}
ERR_STATES = {"error", "missingFiles"}
PAUSED = {"pausedUP", "pausedDL", "stoppedUP", "stoppedDL"}

_lock = threading.Lock()
_inst = {}                    # url -> {"rid", "torrents", "server", "ok", "error", "user", "t"}
_abr = {"t": 0.0, "data": None}
_store = {"data": None, "saved": 0.0}


# --------------------------------------------------------------------------- config
def apps(cfg):
    path = (cfg or {}).get("ARR_FILE") or APPS_FILE
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return [], []
    local = re.compile(r"^https?://127\.0\.0\.1:\d{1,5}(/[A-Za-z0-9._~/-]*)?$")
    q = [a for a in d.get("qbittorrent") or [] if isinstance(a, dict) and local.match(str(a.get("url", "")))]
    b = [a for a in d.get("autobrr") or [] if isinstance(a, dict) and local.match(str(a.get("url", "")))
         and (a.get("key") is None or re.match(r"^[A-Za-z0-9_-]{16,128}$", str(a.get("key"))))]
    return q, b


def _store_file(cfg):
    return (cfg or {}).get("RACING_FILE") or os.path.join(os.path.dirname(HERE), "racing.json")


def _load(cfg):
    if _store["data"] is None:
        try:
            with open(_store_file(cfg)) as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError
            _store["data"] = {"trackers": d.get("trackers") or {}, "first": d.get("first") or {}}
        except (OSError, ValueError):
            _store["data"] = {"trackers": {}, "first": {}}
    return _store["data"]


def _save(cfg):
    d = _store["data"]
    if d is None:
        return
    path = _store_file(cfg)
    try:
        with open(path + ".tmp", "w") as f:
            json.dump(d, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def tracker_name(url):
    """'https://tracker.example.org:443/a/<passkey>/announce' -> 'example.org' (never the passkey)."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    if not host:
        return ""
    parts = host.split(".")
    while len(parts) > 2 and parts[0] in ("tracker", "trackers", "announce", "t", "tr", "flacsfor", "bt", "www"):
        parts = parts[1:]
    return ".".join(parts)


# --------------------------------------------------------------------------- qBittorrent sync
def _day(ts=None):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def apply_maindata(st, data, store, now=None):
    """Merge one /sync/maindata answer into st; credit upload growth to trackers and first-10-minute windows."""
    now = now or time.time()
    full = bool(data.get("full_update"))
    if full:
        st["torrents"], st["server"] = {}, {}
    tors = st["torrents"]
    day = _day(now)
    trackers = store["trackers"]
    for h, t in (data.get("torrents") or {}).items():
        if not isinstance(t, dict):
            continue
        cur = tors.setdefault(h, {})
        before = cur.get("uploaded")
        cur.update({k: t[k] for k in FIELDS if k in t})
        up = cur.get("uploaded")
        if not full and isinstance(before, (int, float)) and isinstance(up, (int, float)) and up > before:
            name = tracker_name(cur.get("tracker") or "") or "(no tracker)"
            trackers.setdefault(name, {})
            trackers[name][day] = trackers[name].get(day, 0) + (up - before)
    for h in data.get("torrents_removed") or []:
        tors.pop(h, None)
    # first-10-minute upload: recorded only for torrents we watched while they were under 10 minutes
    # old, frozen at the first check after the 10-minute mark (within 2 minutes of it, or not at all:
    # a value taken later would include more than the first 10 minutes). All torrents are checked,
    # not only the ones in this update: a torrent nobody is leeching never appears in a delta.
    for h, cur in tors.items():
        added, up = cur.get("added_on") or 0, cur.get("uploaded")
        if not added or not isinstance(up, (int, float)):
            continue
        age = now - added
        if age < FIRST_WINDOW:
            cur["_young"] = True
        elif cur.get("_young") and age < FIRST_WINDOW + 120:
            store["first"].setdefault(st["user"] + ":" + h, [int(up), int(added)])
    st["server"].update({k: v for k, v in (data.get("server_state") or {}).items()})
    st["rid"] = data.get("rid", st.get("rid", 0))


def sync_once(cfg, session=requests):
    qbts, _ = apps(cfg)
    now = time.time()
    with _lock:
        store = _load(cfg)
        seen = set()
        for a in qbts:
            url = a["url"].rstrip("/")
            seen.add(url)
            st = _inst.setdefault(url, {"rid": 0, "torrents": {}, "server": {}, "ok": False, "error": None,
                                        "user": str(a.get("user") or "")[:40], "t": 0})
            try:
                with warnings.catch_warnings():     # self-signed qBittorrent HTTPS on 127.0.0.1 only
                    warnings.simplefilter("ignore")
                    r = session.get(url + "/api/v2/sync/maindata", params={"rid": st["rid"]}, timeout=8,
                                    verify=not url.startswith("https://127.0.0.1"), allow_redirects=False)
                if r.status_code in (401, 403):
                    st.update(ok=False, error="bypass-off", rid=0)
                    continue
                r.raise_for_status()
                apply_maindata(st, r.json(), store, now)
                st.update(ok=True, error=None, t=now)
            except (requests.RequestException, ValueError):
                st.update(ok=False, error="not answering", rid=0)
        for url in [u for u in _inst if u not in seen]:
            del _inst[url]
        cutoff = _day(now - KEEP_DAYS * 86400)
        for name in list(store["trackers"]):
            days = {d: v for d, v in store["trackers"][name].items() if d > cutoff}
            if days:
                store["trackers"][name] = days
            else:
                del store["trackers"][name]
        for k in [k for k, v in store["first"].items() if now - v[1] > 7 * 86400]:
            del store["first"][k]
        if now - _store["saved"] > 300:
            _save(cfg)
            _store["saved"] = now


# --------------------------------------------------------------------------- autobrr
def _abr_get(a, path, params=None, session=requests):
    r = session.get(a["url"].rstrip("/") + path, params=params or {}, headers={"X-API-Token": a["key"]},
                    timeout=6, allow_redirects=False)
    if r.status_code in (401, 403):
        raise PermissionError("API key rejected")
    r.raise_for_status()
    return r.json()


def _ts(v):
    """autobrr timestamps are RFC 3339 strings -> epoch seconds (None if unreadable)."""
    if not isinstance(v, str):
        return None
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d+)?(Z|[+-]\d\d:?\d\d)?$", v)
    if not m:
        return None
    import calendar
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    t = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) + float(m.group(7) or 0)
    tz = m.group(8)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        t -= sign * (hh * 3600 + mm * 60)
    return t


def parse_releases(data):
    rows = data.get("data") if isinstance(data, dict) else data if isinstance(data, list) else []
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        idx = r.get("indexer")
        if isinstance(idx, dict):
            idx = idx.get("name") or idx.get("identifier")
        t0 = _ts(r.get("timestamp"))
        pushed = [a for a in r.get("action_status") or [] if isinstance(a, dict)]
        ok = [a for a in pushed if a.get("status") == "PUSH_APPROVED"]
        status = "grabbed" if ok else "rejected" if any(a.get("status") == "PUSH_REJECTED" for a in pushed) \
            else "error" if any(a.get("status") == "PUSH_ERROR" for a in pushed) else (r.get("filter_status") or "").lower()
        t1 = _ts(ok[0].get("timestamp")) if ok else None
        out.append({"name": str(r.get("name") or r.get("torrent_name") or r.get("title") or "")[:200],
                    "indexer": str(idx or "")[:60], "filter": str(r.get("filter") or "")[:60],
                    "time": t0, "status": status[:20], "size": r.get("size") if isinstance(r.get("size"), (int, float)) else None,
                    "grab_secs": round(t1 - t0, 1) if t0 and t1 and 0 <= t1 - t0 < 3600 else None,
                    "client": str((ok[0].get("client") if ok else "") or "")[:60]})
    return out


def autobrr_once(cfg, session=requests):
    _, abrs = apps(cfg)
    out = []
    for a in abrs:
        info = {"user": str(a.get("user") or "")[:40], "ok": False}
        if not a.get("key"):
            info["error"] = "no-key"
            out.append(info)
            continue
        try:
            info["releases"] = parse_releases(_abr_get(a, "/api/release", {"limit": 25}, session))[:25]
            try:
                s = _abr_get(a, "/api/release/stats", None, session)
                info["stats"] = {k: s.get(k) for k in ("total_count", "filtered_count", "filter_rejected_count",
                                                        "push_approved_count", "push_rejected_count", "push_error_count")
                                 if isinstance(s.get(k), int)} if isinstance(s, dict) else {}
            except (requests.RequestException, ValueError, PermissionError):
                info["stats"] = {}
            info["ok"] = True
        except PermissionError as e:
            info["error"] = str(e)
        except (requests.RequestException, ValueError):
            info["error"] = "not answering"
        out.append(info)
    with _lock:
        _abr.update(t=time.time(), data=out)


# --------------------------------------------------------------------------- report
def report(cfg, now=None):
    now = now or time.time()
    qbts, abrs = apps(cfg)
    if not qbts and not abrs:
        return {"available": False}
    with _lock:
        store = _load(cfg)
        insts, tors = [], []
        tot = {"up": 0, "down": 0, "session_up": 0, "session_down": 0, "alltime_up": 0, "alltime_down": 0, "free": None}
        for url, st in _inst.items():
            insts.append({"user": st["user"], "ok": st["ok"], "error": st["error"], "count": len(st["torrents"])})
            if not st["ok"]:
                continue
            s = st["server"]
            tot["up"] += s.get("up_info_speed") or 0
            tot["down"] += s.get("dl_info_speed") or 0
            tot["session_up"] += s.get("up_info_data") or 0
            tot["session_down"] += s.get("dl_info_data") or 0
            tot["alltime_up"] += s.get("alltime_ul") or 0
            tot["alltime_down"] += s.get("alltime_dl") or 0
            if isinstance(s.get("free_space_on_disk"), (int, float)):
                tot["free"] = s["free_space_on_disk"] if tot["free"] is None else min(tot["free"], s["free_space_on_disk"])
            for h, t in st["torrents"].items():
                tors.append((st["user"], h, t))
        counts = {"total": len(tors), "uploading": 0, "downloading": 0, "stalled": 0, "paused": 0, "errored": 0}
        left = 0
        for _, _, t in tors:
            state = t.get("state") or ""
            if state in ERR_STATES:
                counts["errored"] += 1
            elif state in PAUSED:
                counts["paused"] += 1
            elif state in DL_STATES:
                counts["downloading"] += 1
                left += t.get("amount_left") or max(0, (t.get("size") or 0) * (1 - (t.get("progress") or 0)))
            elif state == "stalledUP":
                counts["stalled"] += 1
            if (t.get("upspeed") or 0) > 0:
                counts["uploading"] += 1

        def row(user, h, t):
            key = user + ":" + h
            first = store["first"].get(key)
            age = now - (t.get("added_on") or now)
            return {"name": str(t.get("name") or "")[:200], "user": user, "tracker": tracker_name(t.get("tracker") or ""),
                    "upspeed": t.get("upspeed") or 0, "dlspeed": t.get("dlspeed") or 0, "uploaded": t.get("uploaded") or 0,
                    "ratio": round(t.get("ratio") or 0, 2), "size": t.get("size") or 0, "progress": t.get("progress") or 0,
                    "age": max(0, round(age)), "leechers": t.get("num_leechs") or 0, "state": t.get("state") or "",
                    "first10": first[0] if first else (t.get("uploaded") if age < FIRST_WINDOW else None)}
        top = [row(*x) for x in sorted(tors, key=lambda x: -(x[2].get("upspeed") or 0)) if (x[2].get("upspeed") or 0) > 0][:8]
        new = [row(*x) for x in sorted(tors, key=lambda x: -(x[2].get("added_on") or 0))
               if now - (x[2].get("added_on") or 0) < 86400][:10]
        today = _day(now)
        per_tracker = sorted(({"tracker": n, "today": d.get(today, 0), "week": sum(v for k, v in d.items()
                                                                                    if k > _day(now - 7 * 86400))}
                              for n, d in store["trackers"].items()), key=lambda r: -r["today"])
        ratio = tot["alltime_up"] / tot["alltime_down"] if tot["alltime_down"] else None
        abr = _abr["data"]
    return {"available": True, "instances": insts, "totals": tot, "ratio": round(ratio, 2) if ratio else None,
            "counts": counts, "left_to_download": left, "top": top, "new": new,
            "trackers": [r for r in per_tracker if r["week"] > 0][:8], "autobrr": abr,
            "has_autobrr": bool(abrs), "sync_every": SYNC_EVERY}


def loop(cfg, sleep):
    """Background: sync qBittorrent every SYNC_EVERY s and autobrr every AUTOBRR_EVERY s."""
    last_abr = 0
    while True:
        try:
            if apps(cfg)[0]:
                sync_once(cfg)
            if time.time() - last_abr >= AUTOBRR_EVERY:
                last_abr = time.time()
                if apps(cfg)[1]:
                    autobrr_once(cfg)
        except Exception:  # noqa: BLE001 - the loop must never die
            pass
        sleep(SYNC_EVERY)
