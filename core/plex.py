"""Plex "Now playing": who is streaming what, from where, and whether Plex is transcoding.

Talks only to the local Plex server (http://127.0.0.1:32400 unless PLEX_URL is set). The token comes
from PLEX_TOKEN in swizzin.cfg, or from /var/lib/swizzin/plex-token, which the root swizzin-host
timer copies out of Plex's Preferences.xml (readable only by the panel user). The token is never
sent to the browser.
"""
import os
import time

import requests

from core import hardware as hw

TOKEN_FILE = os.environ.get("SWZ_TEST_PLEX_TOKEN", "/var/lib/swizzin/plex-token")
_cache = {"t": 0, "key": None, "data": None}


def _token(cfg):
    tok = (cfg or {}).get("PLEX_TOKEN")
    if tok:
        return str(tok).strip()
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _title(m):
    t = m.get("type")
    if t == "episode":
        se = ""
        if m.get("parentIndex") is not None and m.get("index") is not None:
            se = "S{:02d}E{:02d}".format(int(m["parentIndex"]), int(m["index"]))
        return m.get("grandparentTitle") or m.get("title"), " · ".join(filter(None, [se, m.get("title")]))
    if t == "track":
        return m.get("title"), " · ".join(filter(None, [m.get("grandparentTitle"), m.get("parentTitle")]))
    if t == "movie":
        return m.get("title"), str(m["year"]) if m.get("year") else None
    return m.get("title") or "Unknown", m.get("librarySectionTitle")


def _resolution(media):
    r = str(media.get("videoResolution") or "").lower()
    if not r:
        return None
    return {"4k": "4K", "sd": "SD"}.get(r, r + "p" if r.isdigit() else r.upper())


def parse_session(m):
    media = (m.get("Media") or [{}])[0] if isinstance(m.get("Media"), list) else {}
    part = (media.get("Part") or [{}])[0] if isinstance(media.get("Part"), list) else {}
    streams = part.get("Stream") or []
    ts = m.get("TranscodeSession") or None
    player, user, sess = m.get("Player") or {}, m.get("User") or {}, m.get("Session") or {}

    if not ts:
        decision = "direct play"
    elif "transcode" in (ts.get("videoDecision"), ts.get("audioDecision")):
        decision = "transcode"
    else:
        decision = "direct stream"
    reasons = []
    if ts:
        if ts.get("videoDecision") == "transcode":
            src, dst = ts.get("sourceVideoCodec"), ts.get("videoCodec")
            reasons.append("video {}{}".format((src + " → ") if src and src != dst else "", dst or "").strip())
        if ts.get("audioDecision") == "transcode":
            src, dst = ts.get("sourceAudioCodec"), ts.get("audioCodec")
            reasons.append("audio {}{}".format((src + " → ") if src and src != dst else "", dst or "").strip())
    if any(s.get("streamType") == 3 and s.get("decision") == "burn" for s in streams if isinstance(s, dict)):
        reasons.append("subtitles burned in")
    hw_dec, hw_enc = (ts or {}).get("transcodeHwDecoding"), (ts or {}).get("transcodeHwEncoding")

    title, subtitle = _title(m)
    duration, offset = _num(m.get("duration")), _num(m.get("viewOffset"))
    return {
        "type": m.get("type"), "title": title, "subtitle": subtitle,
        "user": user.get("title") or "Unknown",
        "device": player.get("title") or player.get("device") or player.get("product"),
        "product": player.get("product"), "platform": player.get("platform"),
        "state": player.get("state") if player.get("state") in ("playing", "paused", "buffering") else "playing",
        "location": "LAN" if player.get("local") in (True, "1", 1) or sess.get("location") == "lan" else "Remote",
        "relayed": player.get("relayed") in (True, "1", 1),
        "progress": round(100 * offset / duration, 1) if duration and offset is not None else None,
        "duration": duration, "offset": offset,
        "bandwidth": _num(sess.get("bandwidth")),             # kbit/s
        "quality": _resolution(media), "bitrate": _num(media.get("bitrate")),
        "decision": decision, "reasons": reasons,
        "hw": bool(hw_dec or hw_enc), "hw_decode": hw_dec or None, "hw_encode": hw_enc or None,
        "throttled": bool((ts or {}).get("throttled")), "speed": _num((ts or {}).get("speed")),
        "out_quality": "{}p".format(ts["height"]) if ts and ts.get("height") and decision == "transcode" else None,
    }


def now_playing(cfg):
    if not os.path.exists(hw.INSTALL_DIR + "/.plex.lock"):
        return {"installed": False}
    token = _token(cfg)
    if not token:
        return {"installed": True, "status": "no_token"}
    url = str((cfg or {}).get("PLEX_URL") or "http://127.0.0.1:32400").rstrip("/")
    now = time.time()
    if _cache["data"] is not None and _cache["key"] == (url, token) and now - _cache["t"] < 2:
        return _cache["data"]
    try:
        r = requests.get(url + "/status/sessions", timeout=4,
                         headers={"Accept": "application/json", "X-Plex-Token": token,
                                  "X-Plex-Client-Identifier": "swizzin-panel", "X-Plex-Product": "swizzin panel"})
    except requests.RequestException:
        out = {"installed": True, "status": "unreachable"}
    else:
        if r.status_code in (401, 403):
            out = {"installed": True, "status": "unauthorized"}
        elif r.status_code != 200:
            out = {"installed": True, "status": "error", "code": r.status_code}
        else:
            try:
                mc = r.json().get("MediaContainer") or {}
                sessions = [parse_session(m) for m in (mc.get("Metadata") or []) if isinstance(m, dict)]
            except ValueError:
                out = {"installed": True, "status": "error"}
            else:
                order = {"playing": 0, "buffering": 1, "paused": 2}
                sessions.sort(key=lambda s: (order.get(s["state"], 3), s["user"].lower()))
                out = {"installed": True, "status": "ok", "sessions": sessions,
                       "count": len(sessions),
                       "transcodes": sum(1 for s in sessions if s["decision"] == "transcode"),
                       "hw_transcodes": sum(1 for s in sessions if s["decision"] == "transcode" and s["hw"]),
                       "bandwidth": sum(s["bandwidth"] or 0 for s in sessions)}
    _cache.update(t=now, key=(url, token), data=out)
    return out
