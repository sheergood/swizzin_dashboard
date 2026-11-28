#!/usr/bin/env python3
"""Stand-in for the Sonarr/Radarr/Prowlarr built-in updater: python3 fake_servarr.py PORT MODE
/sonarr/api/v3/... (key "s"*32) and /prowlarr/api/v1/... (key "p"*32).
MODE ok: ApplicationUpdate makes the version 4.0.11 after a 1 s "restart" (503 meanwhile);
fail: the command fails; noop: the command completes and nothing changes."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MODE = sys.argv[2] if len(sys.argv) > 2 else "ok"
KEYS = {"sonarr": "s" * 32, "prowlarr": "p" * 32}
ST = {"sonarr": {"version": "4.0.10.2544", "restart_until": 0, "cmd": None},
      "prowlarr": {"version": "1.20.0.4590", "restart_until": 0, "cmd": None}}
NEW = {"sonarr": "4.0.11.2680", "prowlarr": "1.21.0.4700"}
LOCK = threading.Lock()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _app(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        app = parts[0]
        if self.headers.get("X-Api-Key") != KEYS.get(app):
            self._send(401)
            return None, None
        if time.time() < ST[app]["restart_until"]:
            self._send(503)
            return None, None
        return app, parts[3:]

    def do_GET(self):
        app, rest = self._app()
        if not app:
            return
        s = ST[app]
        if rest == ["system", "status"]:
            return self._send(200, {"version": s["version"]})
        if rest == ["update"]:
            return self._send(200, [{"version": NEW[app], "installable": True, "branch": "main",
                                     "changes": {"new": ["Shiny"], "fixed": ["Bugs"]}}, {"version": s["version"], "installed": True}])
        if rest[:1] == ["command"] and len(rest) == 2:
            c = s["cmd"] or {}
            return self._send(200 if c.get("id") == int(rest[1]) else 404, c)
        self._send(404)

    def do_POST(self):
        app, rest = self._app()
        if not app:
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if rest != ["command"] or body.get("name") != "ApplicationUpdate":
            return self._send(400, {"message": "bad"})
        with LOCK:
            s = ST[app]
            if MODE == "ok":
                s["cmd"] = {"id": 41, "status": "started"}
                s["version"], s["restart_until"] = NEW[app], time.time() + 1
            elif MODE == "fail":
                s["cmd"] = {"id": 41, "status": "failed", "exception": "Update folder isn't writable"}
            else:
                s["cmd"] = {"id": 41, "status": "completed", "message": "No update to install"}
        self._send(201, {"id": 41, "name": "ApplicationUpdate", "status": "queued"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    print("ready", flush=True)
    srv.serve_forever()
