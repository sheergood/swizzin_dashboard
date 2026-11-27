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
open("swizzin.cfg", "w").write("ADMIN_USER = 'smoke'\nFLASK_HTPASSWD_PATH = %r\nRATELIMIT_ENABLED = False\nPLACE_LOOKUPS = False\n" % htpasswd)

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
                         ("/api/specs", True), ("/api/plex", True), ("/api/procs", True)]:
    r = client.get(path, headers=auth if needs_auth else {})
    ok = r.status_code == 200
    print("{:4} {}".format(r.status_code, path))
    bad += not ok
r = client.get("/api/system")
print("{:4} /api/system without auth (expect 401/302)".format(r.status_code))
bad += r.status_code not in (401, 302)
# single sign-on endpoint used by nginx auth_request (FileBrowser Quantum)
wrong = {"Authorization": "Basic " + base64.b64encode(b"smoke:nope").decode()}
checks = [("no login", client.get("/auth/check").status_code, 401),
          ("wrong password", client.get("/auth/check", headers=wrong).status_code, 401),
          ("right login", client.get("/auth/check", headers=auth).status_code, 200),
          ("right login, other required user", client.get("/auth/check", headers=dict(auth, **{"X-Swizzin-Require-User": "someoneelse"})).status_code, 403)]
r = client.get("/auth/check", headers=dict(auth, **{"X-Swizzin-Require-User": "smoke"}))
checks.append(("user header", r.headers.get("X-Swizzin-User"), "smoke"))
for name, got, want in checks:
    print("{:4} /auth/check {} (expect {})".format(got, name, want))
    bad += got != want
sys.exit(1 if bad else 0)
