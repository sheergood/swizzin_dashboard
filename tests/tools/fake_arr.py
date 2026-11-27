#!/usr/bin/env python3
"""Stand-in Sonarr + Radarr API for tests: python3 fake_arr.py PORT. Checks X-Api-Key.
/sonarr/api/v3/{queue,calendar} with key "s"*32, /radarr/... with key "r"*32; /broken/... answers 500."""
import datetime as dt
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

NOW = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
DATA = {
    ("sonarr", "queue"): {"records": [
        {"title": "Show.S03E06.1080p", "size": 2e9, "sizeleft": 7e8, "status": "downloading", "trackedDownloadState": "downloading",
         "trackedDownloadStatus": "ok", "estimatedCompletionTime": iso(NOW + dt.timedelta(minutes=12)), "downloadClient": "rTorrent",
         "series": {"title": "The <b>Show</b>"}, "episode": {"seasonNumber": 3, "episodeNumber": 6, "title": "Pilot Light"}},
        {"title": "Stuck.Thing", "size": 0, "sizeleft": 0, "status": "warning", "trackedDownloadStatus": "warning",
         "series": {"title": "Stuck Show"}, "episode": {"seasonNumber": 1, "episodeNumber": 2}}]},
    ("sonarr", "calendar"): [
        {"seasonNumber": 3, "episodeNumber": 7, "title": "Next", "airDateUtc": iso(NOW + dt.timedelta(days=1)), "hasFile": False,
         "series": {"title": "The Show"}}],
    ("radarr", "queue"): {"records": [
        {"title": "Film.2026", "size": 8e9, "sizeleft": 2e9, "trackedDownloadState": "downloading", "movie": {"title": "Film", "year": 2026}}]},
    ("radarr", "calendar"): [
        {"title": "New Film", "year": 2026, "digitalRelease": iso(NOW + dt.timedelta(days=3)), "inCinemas": iso(NOW - dt.timedelta(days=60)), "hasFile": False},
        {"title": "Old Film", "year": 2020, "inCinemas": iso(NOW - dt.timedelta(days=900))}],
}
KEYS = {"sonarr": "s" * 32, "radarr": "r" * 32}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        parts = urlsplit(self.path).path.strip("/").split("/")   # app/api/v3/what
        app, what = parts[0], parts[-1]
        if app == "broken":
            self.send_response(500); self.end_headers(); return
        if self.headers.get("X-Api-Key") != KEYS.get(app):
            self.send_response(401); self.end_headers(); return
        body = json.dumps(DATA.get((app, what), [])).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    print("ready", flush=True)
    srv.serve_forever()
