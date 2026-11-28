"""App updates on the Applications card: what's available, release notes, and upgrading from the panel.

Where the answers come from
  /var/lib/swizzin/updates.json   written by swizzin's root update check (panel-updates-check) every 6
                                  hours and on "Check now": installed vs newest version, trimmed notes
  /var/lib/swizzin/upgrades/      one <app>.json (progress) and <app>.log per root upgrade

How each app is upgraded
  autobrr, Filebrowser, FileBrowser Quantum, Plex, Navidrome, Netronome, Emby, The Lounge,
  Jellyfin, Ombi
      sudo -n /etc/swizzin/scripts/panel-upgrade <app>: a root-owned script that accepts only these
      names and runs the upgrade as its own systemd job (backup, health check, roll back), so it
      finishes even if the panel restarts
  Sonarr, Radarr, Prowlarr, Lidarr
      their own built-in updater, asked through their API with the key the root collector shares;
      no root needed. The panel watches until the new version answers.
  qBittorrent, rTorrent, ruTorrent, Flood, Transmission, qui (torrent apps)
      never: you update them yourself; the card only says a newer version exists

Only one upgrade runs at a time, nothing starts without the admin pressing a button and confirming,
and every failure comes back as a message rather than an exception.
"""
import json
import os
import re
import subprocess as sp
import threading
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
UPDATES_FILE = os.environ.get("SWZ_TEST_UPDATES", "/var/lib/swizzin/updates.json")
UPGRADES_DIR = os.environ.get("SWZ_TEST_UPGRADES", "/var/lib/swizzin/upgrades")
ARR_FILE = os.environ.get("SWZ_TEST_ARR", "/var/lib/swizzin/arr.json")
PANEL_UPG = "/etc/swizzin/scripts/panel-upgrade"

ROOT_APPS = ("autobrr", "filebrowser", "fbquantum", "plex", "navidrome", "netronome", "emby", "lounge", "jellyfin", "ombi")
ARR_APPS = {"sonarr": "v3", "radarr": "v3", "prowlarr": "v1", "lidarr": "v1"}
# torrent clients and their web UIs: you update them yourself, the card only says a newer version exists
NOTIFY_ONLY = ("qbittorrent", "rtorrent", "rutorrent", "flood", "transmission", "qui")
KNOWN = ROOT_APPS + tuple(ARR_APPS) + NOTIFY_ONLY

CHECK_COOLDOWN = 300        # GitHub allows ~60 anonymous lookups an hour per server
CHECK_WAIT = 300            # how long "Checking…" may show before we give up waiting
ROOT_STALE = 75 * 60        # a root job "running" longer than its 60-minute limit was interrupted
ARR_TIMEOUT = 15 * 60
START_GRACE = 45            # after starting a root job, before its progress file appears
LOG_MAX = 64 * 1024

_lock = threading.Lock()
_sudo = {"checked": 0.0, "ok": False}
_mem = {"check_at": 0.0, "pending": {}}      # pending: app -> time we asked panel-upgrade to start it


# --------------------------------------------------------------------------- small helpers
def _jobs_path(cfg):
    return (cfg or {}).get("UPDATE_JOBS_FILE") or os.path.join(os.path.dirname(HERE), "update-jobs.json")


def _read_json(path, default=None):
    try:
        with open(path) as f:
            v = json.load(f)
        return v
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4]) or (0,)


def _upgrade_cmd(cfg):
    """['sudo', '-n', PANEL_UPG] (tests replace it with a fake)."""
    fake = (cfg or {}).get("UPGRADE_CMD")
    return list(fake) if fake else ["sudo", "-n", PANEL_UPG]


def supported(cfg=None):
    """True once swizzin has installed panel-upgrade and the sudo rule for it (box update)."""
    if (cfg or {}).get("UPGRADE_CMD"):
        return True
    now = time.time()
    if now - _sudo["checked"] > 300:
        _sudo["checked"] = now
        try:
            _sudo["ok"] = os.path.isfile(PANEL_UPG) and sp.run(
                ("sudo", "-n", "-l", PANEL_UPG), stdout=sp.DEVNULL, stderr=sp.DEVNULL, timeout=10).returncode == 0
        except (OSError, sp.SubprocessError):
            _sudo["ok"] = False
    return _sudo["ok"]


def _run_upgrade_cmd(cfg, arg):
    try:
        p = sp.run(_upgrade_cmd(cfg) + [arg], stdout=sp.PIPE, stderr=sp.PIPE, timeout=30, universal_newlines=True)
        return p.returncode, (p.stderr or p.stdout or "").strip()
    except (OSError, sp.SubprocessError) as e:
        return 127, str(e)


def _arr_conf(cfg):
    data = _read_json((cfg or {}).get("ARR_FILE") or ARR_FILE, {})
    out = {}
    for app in ARR_APPS:
        a = data.get(app) if isinstance(data, dict) else None
        if isinstance(a, dict) and re.match(r"^http://127\.0\.0\.1:\d{1,5}(/[A-Za-z0-9._~/-]*)?$", str(a.get("url", ""))) \
                and re.match(r"^[A-Za-z0-9]{20,64}$", str(a.get("key", ""))):
            out[app] = {"url": a["url"].rstrip("/"), "key": a["key"]}
    return out


def _arr(a, app, method, path, body=None, timeout=6):
    url = "{}/api/{}/{}".format(a["url"], ARR_APPS[app], path)
    h = {"X-Api-Key": a["key"], "Accept": "application/json"}
    if method == "POST":
        r = requests.post(url, json=body, headers=h, timeout=timeout, allow_redirects=False)
    else:
        r = requests.get(url, headers=h, timeout=timeout, allow_redirects=False)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- jobs
def _root_job(app):
    st = _read_json(os.path.join(UPGRADES_DIR, app + ".json"))
    if not isinstance(st, dict) or st.get("state") not in ("running", "done", "failed"):
        return None
    job = {k: st.get(k) for k in ("state", "message", "started", "finished")}
    if job["state"] == "running" and time.time() - (job["started"] or 0) > ROOT_STALE:
        job.update(state="failed", message="The upgrade was interrupted; see the log",
                   finished=(job["started"] or 0) + ROOT_STALE)
    return job


def _arr_step(cfg, jobs, app):
    """Move a running Sonarr/Radarr/Prowlarr update along. The app restarts itself part-way, so
    errors while it's down are expected; success is the new version answering."""
    j = jobs[app]
    a = _arr_conf(cfg).get(app)
    now = time.time()
    if not a:
        j.update(state="failed", message="Lost the app's API details", finished=now)
        return
    try:
        ver = (_arr(a, app, "GET", "system/status", timeout=4) or {}).get("version")
    except (requests.RequestException, ValueError):
        ver = None
    if ver and _vtuple(ver) > _vtuple(j.get("from")):
        j.update(state="done", message="Updated to {}".format(ver), finished=now, installed=ver)
        j.setdefault("log", []).append("{}: running {}".format(time.strftime("%H:%M:%S"), ver))
        return
    if ver and j.get("command"):
        try:
            c = _arr(a, app, "GET", "command/{}".format(int(j["command"])), timeout=4) or {}
        except (requests.RequestException, ValueError):
            c = {}
        status = str(c.get("status", "")).lower()
        if status in ("failed", "aborted", "cancelled", "orphaned"):
            msg = str(c.get("exception") or c.get("message") or "the app's updater reported a failure")[:300]
            j.update(state="failed", message=msg, finished=now)
            j.setdefault("log", []).append("{}: {}".format(time.strftime("%H:%M:%S"), msg))
            return
        if status == "completed" and now - j["started"] > 120:
            # finished without a new version answering: nothing was installed (e.g. a major update
            # that needs confirming in the app itself)
            msg = str(c.get("message") or "").strip()
            msg = msg or "{} finished without installing the update; open {} to update it".format(app.capitalize(), app.capitalize())
            j.update(state="failed", message=msg[:300], finished=now)
            j.setdefault("log", []).append("{}: {}".format(time.strftime("%H:%M:%S"), msg[:300]))
            return
    if now - j["started"] > ARR_TIMEOUT:
        j.update(state="failed", message="No new version after 15 minutes; check {}".format(app.capitalize()), finished=now)


def _progress(cfg):
    """Advance and return the panel's own job records (the *arr updates)."""
    path = _jobs_path(cfg)
    jobs = _read_json(path, {})
    if not isinstance(jobs, dict):
        jobs = {}
    changed = False
    for app, j in list(jobs.items()):
        if app not in ARR_APPS or not isinstance(j, dict):
            jobs.pop(app, None)
            changed = True
            continue
        if j.get("state") == "running":
            _arr_step(cfg, jobs, app)
            if j.get("state") != "running":
                changed = True
                _request_check(cfg, force=True)    # cards pick up the new version
    if changed:
        try:
            _write_json(path, jobs)
        except OSError:
            pass
    return jobs


def _pending(app, job):
    """True between asking panel-upgrade to start <app> and its job writing its first progress."""
    t = _mem["pending"].get(app)
    if not t:
        return False
    if time.time() - t >= START_GRACE or (job and (job.get("started") or 0) >= t - 5):
        _mem["pending"].pop(app, None)
        return False
    return True


def _busy(cfg, jobs):
    for app in ROOT_APPS:
        j = _root_job(app)
        if j and j["state"] == "running" or _pending(app, j):
            return app
    for app, j in jobs.items():
        if isinstance(j, dict) and j.get("state") == "running":
            return app
    return None


# --------------------------------------------------------------------------- public
def _request_check(cfg, force=False):
    now = time.time()
    if not force and now - _mem["check_at"] < CHECK_COOLDOWN:
        return False, "Checked {} s ago; try again in a few minutes".format(int(now - _mem["check_at"]))
    if not supported(cfg):
        return False, "Run box update on the server to enable update checks"
    rc, msg = _run_upgrade_cmd(cfg, "check")
    if rc != 0:
        return False, "Couldn't start the check ({})".format(msg[:120] or rc)
    _mem["check_at"] = now
    return True, "Checking for updates…"


def check(cfg):
    with _lock:
        return _request_check(cfg)


def status(cfg):
    with _lock:
        data = _read_json((cfg or {}).get("UPDATES_FILE") or UPDATES_FILE, {})
        if not isinstance(data, dict):
            data = {}
        jobs = _progress(cfg)
        checked = data.get("checked") if isinstance(data.get("checked"), (int, float)) else None
        checking = bool(_mem["check_at"]) and time.time() - _mem["check_at"] < CHECK_WAIT and \
            (checked or 0) < _mem["check_at"] - 2
        sup = supported(cfg)
        arr = _arr_conf(cfg)
        apps = {}
        raw = data.get("apps") if isinstance(data.get("apps"), dict) else {}
        for app, d in raw.items():
            if app not in KNOWN or not isinstance(d, dict):
                continue
            a = {k: d.get(k) for k in ("installed", "latest", "notes", "url", "released", "error", "checked")}
            a["available"] = bool(d.get("available"))
            a["notes"] = str(a["notes"] or "")[:6000]
            if not (isinstance(a["url"], str) and a["url"].startswith("https://")):
                a["url"] = None
            # a new major version of Sonarr/Radarr/Prowlarr (2.x -> 3.x): their updater installs it only
            # when asked to explicitly, so the panel asks the admin separately and never in Update all
            a["major"] = app in ARR_APPS and a["available"] and _vtuple(a["latest"])[0] > _vtuple(a["installed"])[0]
            if app in NOTIFY_ONLY:
                a["action"] = "notify"
            elif app in ARR_APPS:
                a["action"] = "upgrade" if app in arr and d.get("method") != "self-blocked" else "blocked"
            else:
                a["action"] = "upgrade" if sup else "needs-update"
            job = _root_job(app) if app in ROOT_APPS else None
            if app in ARR_APPS and isinstance(jobs.get(app), dict):
                jj = jobs[app]
                job = {k: jj.get(k) for k in ("state", "message", "started", "finished")}
                if jj.get("state") == "done" and jj.get("installed") and (a["checked"] or 0) < (jj.get("finished") or 0):
                    a["installed"], a["available"] = jj["installed"], _vtuple(a["latest"]) > _vtuple(jj["installed"])
            if app in ROOT_APPS and _pending(app, job):
                job = {"state": "running", "message": "Starting…", "started": int(_mem["pending"][app]), "finished": None}
            # a finished upgrade stays on the card for 10 minutes once a later check confirms it (done),
            # or for a day (failed)
            if job and job["state"] == "done" and (a["checked"] or 0) > (job.get("finished") or 0) + 5 \
                    and time.time() - (job.get("finished") or 0) > 600:
                job = None
            elif job and job["state"] == "failed" and time.time() - (job.get("finished") or 0) > 86400:
                job = None
            a["job"] = job
            apps[app] = a
        return {"checked": checked, "checking": checking, "supported": sup, "apps": apps,
                "busy": _busy(cfg, jobs), "cooldown": max(0, int(CHECK_COOLDOWN - (time.time() - _mem["check_at"])))}


def upgrade(cfg, app, major=False):
    """Start upgrading one app. Returns (ok, message). A new major version of Sonarr/Radarr/Prowlarr
    is installed only with major=True (the admin confirmed that version in the dialog)."""
    if app in NOTIFY_ONLY:
        return False, "Torrent apps are updated by you, not from the panel"
    if app not in ROOT_APPS and app not in ARR_APPS:
        return False, "This app can't be upgraded from the panel"
    with _lock:
        data = _read_json((cfg or {}).get("UPDATES_FILE") or UPDATES_FILE, {})
        info = ((data or {}).get("apps") or {}).get(app) if isinstance(data, dict) else None
        if not isinstance(info, dict) or not info.get("available"):
            return False, "No update is waiting for this app; press Check now first"
        jobs = _progress(cfg)
        busy = _busy(cfg, jobs)
        if busy:
            return False, "{} is upgrading; one at a time".format(busy.capitalize())
        if app in ROOT_APPS:
            if not supported(cfg):
                return False, "Run box update on the server to enable upgrades from the panel"
            rc, msg = _run_upgrade_cmd(cfg, app)
            if rc == 0:
                _mem["pending"][app] = time.time()
                return True, "Upgrade started"
            if rc == 4:
                return False, msg.replace("panel-upgrade: ", "").capitalize() or "Something else is running on the server"
            if rc == 3:
                return False, "That app isn't installed"
            return False, "Couldn't start the upgrade ({})".format(msg[:160] or rc)
        a = _arr_conf(cfg).get(app)
        if not a:
            return False, "The panel can't reach {}'s API yet".format(app.capitalize())
        if info.get("method") == "self-blocked":
            return False, "{} says this update can't be installed by its updater".format(app.capitalize())
        try:
            cur = (_arr(a, app, "GET", "system/status") or {}).get("version")
            is_major = _vtuple(info.get("latest"))[0] > _vtuple(cur or info.get("installed"))[0]
            if is_major and not major:
                return False, "{} {} is a new major version; confirm it in its own Update dialog".format(
                    app.capitalize(), info.get("latest"))
            if cur and _vtuple(cur) >= _vtuple(info.get("latest")):
                # already updated some other way since the last check: refresh instead
                _request_check(cfg, force=True)
                return False, "{} is already on {}; refreshing the check".format(app.capitalize(), cur)
            body = {"name": "ApplicationUpdate"}
            if is_major:
                body["installMajorUpdate"] = True     # what the app's own "install anyway" button sends
            c = _arr(a, app, "POST", "command", body, timeout=10) or {}
        except requests.HTTPError as e:
            return False, "{} refused the update (HTTP {})".format(app.capitalize(), getattr(e.response, "status_code", "?"))
        except (requests.RequestException, ValueError):
            return False, "{} isn't answering".format(app.capitalize())
        now = time.time()
        jobs[app] = {"state": "running", "message": "{} is installing the update".format(app.capitalize()),
                     "started": now, "finished": None, "from": cur, "to": info.get("latest"),
                     "command": c.get("id") if isinstance(c.get("id"), int) else None,
                     "log": ["{}: asked {} {} to install {}{}".format(time.strftime("%H:%M:%S"), app.capitalize(), cur, info.get("latest"),
                                                                      " (major version, confirmed)" if is_major else "")]}
        try:
            _write_json(_jobs_path(cfg), jobs)
        except OSError:
            pass
        return True, "Upgrade started"


def log(cfg, app):
    """The last part of an upgrade's output (already cut to 400 lines by the runner)."""
    if app in ROOT_APPS:
        path = os.path.join(UPGRADES_DIR, app + ".log")
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - LOG_MAX))
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return ""
        if size > LOG_MAX:
            text = "…\n" + text.split("\n", 1)[-1]
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\(B", "", text)
    if app in ARR_APPS:
        jobs = _read_json(_jobs_path(cfg), {})
        j = jobs.get(app) if isinstance(jobs, dict) else None
        lines = list((j or {}).get("log") or [])[-50:]
        if j and j.get("state") != "running" and j.get("message"):
            lines.append(j["message"])
        return "\n".join(str(x) for x in lines)
    return ""
