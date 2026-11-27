"""Media queue: what Sonarr and Radarr are downloading now and what's coming in the next 7 days.

The local address and API key of each app come from /var/lib/swizzin/arr.json, which the root
collector (panel-host-collect) copies out of the app's own config.xml and makes readable only by the
panel. Every call goes to 127.0.0.1 with a short timeout, results are cached for a minute, and one
app being down never hides the other.
"""
import datetime as dt
import json
import os
import re
import threading
import time

import requests

ARR_FILE = os.environ.get("SWZ_TEST_ARR", "/var/lib/swizzin/arr.json")
TTL = 60
DAYS_AHEAD = 7
_cache = {"t": 0.0, "data": None}
_lock = threading.Lock()


def _config(cfg):
    path = (cfg or {}).get("ARR_FILE") or ARR_FILE
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}
    out = {}
    for app in ("sonarr", "radarr"):
        a = data.get(app) if isinstance(data, dict) else None
        if isinstance(a, dict) and re.match(r"^http://127\.0\.0\.1:\d{1,5}(/[A-Za-z0-9._~/-]*)?$", str(a.get("url", ""))) \
                and re.match(r"^[A-Za-z0-9]{20,64}$", str(a.get("key", ""))):
            out[app] = {"url": a["url"].rstrip("/"), "key": a["key"]}
    return out


def _get(a, path, params):
    r = requests.get(a["url"] + path, params=params, headers={"X-Api-Key": a["key"], "Accept": "application/json"},
                     timeout=4, allow_redirects=False)
    if r.status_code == 401:
        raise PermissionError("API key rejected")
    r.raise_for_status()
    return r.json()


def _num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ep(e):
    if not isinstance(e, dict):
        return ""
    s, n = e.get("seasonNumber"), e.get("episodeNumber")
    code = "S{:02d}E{:02d}".format(int(s), int(n)) if isinstance(s, int) and isinstance(n, int) else ""
    return " · ".join(x for x in (code, e.get("title") or "") if x)


def _queue(app, a):
    params = {"pageSize": 20, "includeUnknownSeriesItems": "false"}
    params.update({"includeSeries": "true", "includeEpisode": "true"} if app == "sonarr" else {"includeMovie": "true"})
    data = _get(a, "/api/v3/queue", params)
    recs = data.get("records", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    out = []
    for r in recs[:20]:
        if not isinstance(r, dict):
            continue
        size, left = _num(r.get("size")), _num(r.get("sizeleft"))
        if app == "sonarr":
            title = ((r.get("series") or {}).get("title")) or r.get("title") or "Unknown"
            sub = _ep(r.get("episode"))
        else:
            m = r.get("movie") or {}
            title = m.get("title") or r.get("title") or "Unknown"
            sub = str(m.get("year") or "")
        state = (r.get("trackedDownloadState") or r.get("status") or "").lower()
        warn = (r.get("trackedDownloadStatus") or "").lower() in ("warning", "error")
        out.append({"app": app, "title": str(title)[:200], "sub": str(sub)[:200],
                    "pct": round(100 * (1 - left / size), 1) if size > 0 else None,
                    "size": size, "left": left, "eta": r.get("estimatedCompletionTime"),
                    "state": state[:40], "warn": warn, "client": str(r.get("downloadClient") or "")[:60]})
    return out


def _upcoming(app, a, now):
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + dt.timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"start": start, "end": end}
    if app == "sonarr":
        params["includeSeries"] = "true"
    data = _get(a, "/api/v3/calendar", params)
    out = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        if app == "sonarr":
            when = r.get("airDateUtc")
            title = (r.get("series") or {}).get("title") or "Unknown"
            sub = _ep(r)
        else:
            # the next release of the movie inside the window (digital, then physical, then cinema)
            when = None
            for k in ("digitalRelease", "physicalRelease", "inCinemas"):
                v = r.get(k)
                if v and start <= v[:19] + "Z" <= end:
                    when, sub = v, {"digitalRelease": "Digital release", "physicalRelease": "Blu-ray / DVD",
                                    "inCinemas": "In cinemas"}[k]
                    break
            title = "{}{}".format(r.get("title") or "Unknown", " ({})".format(r["year"]) if r.get("year") else "")
            if not when:
                continue
        if not when:
            continue
        out.append({"app": app, "title": str(title)[:200], "sub": str(sub)[:200], "when": when,
                    "has_file": bool(r.get("hasFile"))})
    return out


def media(cfg, now=None):
    with _lock:
        if _cache["data"] is not None and time.time() - _cache["t"] < TTL and now is None:
            return _cache["data"]
    conf = _config(cfg)
    if conf is None:
        return {"available": False, "reason": "not-configured"}
    if not conf:
        return {"available": False, "reason": "no-apps"}
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).replace(microsecond=0)
    apps, queue, upcoming = [], [], []
    for app, a in conf.items():
        info = {"name": app, "ok": True}
        try:
            queue += _queue(app, a)
            upcoming += _upcoming(app, a, now)
        except PermissionError as e:
            info.update(ok=False, error=str(e))
        except requests.RequestException:
            info.update(ok=False, error="not answering")
        except ValueError:
            info.update(ok=False, error="unexpected answer")
        apps.append(info)
    queue.sort(key=lambda q: (q["pct"] is None, -(q["pct"] or 0)))
    upcoming.sort(key=lambda u: u["when"])
    data = {"available": True, "apps": apps, "queue": queue, "upcoming": upcoming[:30], "days": DAYS_AHEAD}
    with _lock:
        _cache.update(t=time.time(), data=data)
    return data
