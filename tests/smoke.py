"""Boot the panel with a throwaway config and hit every page/API. Run from the repo root:
    python tests/smoke.py
Fails (exit 1) if any route errors, so a broken panel never reaches a server."""
import base64
import os
import sys
import tempfile

if os.path.exists("swizzin.cfg"):
    sys.exit("swizzin.cfg exists here — refusing to run (this looks like a live panel). Run from a fresh clone.")
tmp = tempfile.mkdtemp()
htpasswd = os.path.join(tmp, "htpasswd")
import hashlib  # flask-htpasswd accepts {SHA} entries
digest = base64.b64encode(hashlib.sha1(b"smoke").digest()).decode()
open(htpasswd, "w").write("smoke:{SHA}" + digest + "\n")
open("swizzin.cfg", "w").write("ADMIN_USER = 'smoke'\nFLASK_HTPASSWD_PATH = %r\nRATELIMIT_ENABLED = False\nPLACE_LOOKUPS = False\nPREFS_FILE = %r\nSPEEDTEST_FILE = %r\n" % (
    htpasswd, os.path.join(tmp, "prefs.json"), os.path.join(tmp, "speedtest.json")))

sys.path.insert(0, os.getcwd())
try:
    import swizzin
finally:
    os.remove("swizzin.cfg")

client = swizzin.app.test_client()
auth = {"Authorization": "Basic " + base64.b64encode(b"smoke:smoke").decode()}
bad = 0
for path, needs_auth in [("/login", False), ("/", True), ("/classic", True), ("/api/system", True), ("/api/live", True),
                         ("/api/live", True), ("/api/storage", True), ("/api/traffic", True), ("/api/place", True),
                         ("/api/fail2ban", True), ("/api/weather?lat=1&lon=2", True), ("/apps/status", True),
                         ("/api/specs", True), ("/api/plex", True), ("/api/procs", True), ("/api/speedtest", True), ("/api/media", True), ("/api/uptime", True), ("/api/fail2ban", True)]:
    r = client.get(path, headers=auth if needs_auth else {})
    ok = r.status_code == 200
    print("{:4} {}".format(r.status_code, path))
    bad += not ok
r = client.get("/api/system")
print("{:4} /api/system without auth (expect 401/302)".format(r.status_code))
bad += r.status_code not in (401, 302)
# the speed test only starts from our own XHR (cached Basic credentials ride along on any request)
r = client.post("/api/speedtest", headers=auth)
print("{:4} POST /api/speedtest without X-Requested-With (expect forbidden)".format(r.status_code))
bad += r.status_code != 403
xhr = dict(auth, **{"X-Requested-With": "XMLHttpRequest"})
wrong = {"Authorization": "Basic " + base64.b64encode(b"smoke:nope").decode()}
checks = []
# /auth/check is kept (rate limited) for FileBrowser Quantum setups from before v4
# /auth/check (older FileBrowser Quantum setups): right login 200 + user, wrong login 401, other user 403
r = client.get("/auth/check", headers=auth)
checks.append(("GET /auth/check right login", (r.status_code, r.headers.get("X-Swizzin-User")), (200, "smoke")))
checks.append(("GET /auth/check wrong login", client.get("/auth/check", headers=wrong).status_code, 401))
checks.append(("GET /auth/check not the app's user", client.get("/auth/check", headers=dict(auth, **{"X-Swizzin-Require-User": "someone"})).status_code, 403))
# rate limiter: only a VALID login is exempt (any Authorization header used to be)
with swizzin.app.test_request_context("/", headers=wrong):
    checks.append(("wrong login is rate limited", swizzin.check_authorization(), False))
with swizzin.app.test_request_context("/", headers=auth):
    checks.append(("right login is exempt", swizzin.check_authorization(), True))
# preferences: own XHR only, allowlisted keys and values only
checks.append(("POST /api/prefs without XHR", client.post("/api/prefs", headers=auth, json={"sky": "night"}).status_code, 403))
r = client.post("/api/prefs", headers=xhr, json={"sky": "night", "skytheme": "nordic", "evil": "<x>", "FLASK_SECRET": "x"})
checks.append(("POST /api/prefs keeps allowed keys", r.get_json(), {"sky": "night"}))
r = client.post("/api/prefs", headers=xhr, json={"sky": "../../etc/passwd"})
checks.append(("POST /api/prefs ignores bad values", r.get_json(), {"sky": "night"}))
checks.append(("POST /api/prefs junk body", client.post("/api/prefs", headers=xhr, data="not json").status_code, 200))
checks.append(("page carries saved prefs", b'"sky": "night"' in client.get("/", headers=auth).data, True))
# service control: own XHR only, known actions only
checks.append(("POST /apps/service without XHR", client.post("/apps/service", headers=auth, json={"application": "panel", "function": "restart"}).status_code, 403))
checks.append(("POST /apps/service bad action", client.post("/apps/service", headers=xhr, json={"application": "panel", "function": "mask"}).status_code, 400))
checks.append(("POST /apps/service bad name", client.post("/apps/service", headers=xhr, json={"application": "../x", "function": "restart"}).status_code, 400))
checks.append(("GET /api/updates", client.get("/api/updates", headers=auth).status_code, 200))
checks.append(("POST /api/updates without XHR", client.post("/api/updates", headers=auth, json={"action": "check"}).status_code, 403))
checks.append(("POST /api/updates bad action", client.post("/api/updates", headers=xhr, json={"action": "rm"}).status_code, 400))
r = client.post("/api/updates", headers=xhr, json={"action": "upgrade", "app": "qbittorrent"})
checks.append(("POST /api/updates qBittorrent refused", (r.status_code, r.get_json().get("ok")), (200, False)))
r = client.post("/api/updates", headers=xhr, json={"action": "upgrade", "app": "../../x"})
checks.append(("POST /api/updates bad app refused", r.get_json().get("ok"), False))
checks.append(("GET /api/updates log of a bad name", client.get("/api/updates?log=../../etc/passwd", headers=auth).get_json().get("log"), ""))
r = client.get("/", headers=auth)
checks.append(("X-Frame-Options", r.headers.get("X-Frame-Options"), "SAMEORIGIN"))
checks.append(("nosniff", r.headers.get("X-Content-Type-Options"), "nosniff"))
checks.append(("signing secret is not the public default", swizzin.app.config["FLASK_SECRET"] != "What's the password?" and len(swizzin.app.config["FLASK_SECRET"]) >= 32, True))
for name, got, want in checks:
    print("{!s:>5} {} (expect {})".format(got if not isinstance(got, dict) else "ok" if got == want else got, name, want if not isinstance(want, dict) else "ok"))
    bad += got != want
sys.exit(1 if bad else 0)
