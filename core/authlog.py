"""Wrong panel passwords, written for fail2ban (swizzin's swizzin-panel jail bans on 80/443).

One line per failure with only the time and the address, so nothing an attacker types (a user
name, a header) ever reaches the log fail2ban reads:

    2026-11-28T07:44:00+0530 swizzin-panel: login failed from 203.0.113.7

The same address + login + password is written at most once per 10 minutes. A browser that keeps
sending an old saved password (every poll, every image) therefore counts once; only guessing
different passwords adds up to a ban. Loopback (the server itself) is never written. Writing
never raises: if the log can't be written the login still just fails.
"""
import hashlib
import ipaddress
import os
import threading
import time

LOG = os.environ.get("SWZ_AUTH_LOG", "/var/log/swizzin-panel/auth.log")
REPEAT = 600              # seconds during which an identical failed attempt isn't written again
MAX_BYTES = 1 << 20       # then the log moves to auth.log.1 (fail2ban follows the new file)
_seen = {}
_lock = threading.Lock()


def failed(remote_addr, username, password, path=None):
    """Record a wrong password. Returns True when a line was written (for tests)."""
    path = path or LOG
    try:
        addr = ipaddress.ip_address(str(remote_addr or "").strip())
    except ValueError:
        return False
    if addr.is_loopback or addr.is_unspecified:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    key = hashlib.sha256("{}\0{}\0{}".format(addr, username or "", password or "").encode("utf-8", "replace")).digest()
    now = time.time()
    with _lock:
        if now - _seen.get(key, 0) < REPEAT:
            return False
        if len(_seen) > 4096:
            for k in [k for k, t in _seen.items() if now - t >= REPEAT]:
                del _seen[k]
            if len(_seen) > 4096:
                _seen.clear()
        _seen[key] = now
        if not os.path.isdir(os.path.dirname(path)):
            return False        # fail2ban jail not set up on this server (box update adds it)
        try:
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    os.replace(path, path + ".1")
            except OSError:
                pass
            line = "{} swizzin-panel: login failed from {}\n".format(time.strftime("%Y-%m-%dT%H:%M:%S%z"), addr)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
            try:
                os.write(fd, line.encode("ascii"))
            finally:
                os.close(fd)
            return True
        except OSError:
            return False
