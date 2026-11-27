#!/usr/bin/env python3
"""Stand-in qBittorrent Web API (/api/v2/sync/maindata with rid deltas) + autobrr (/autobrr/api/release).
python3 fake_qbt.py PORT [refuse]  -- "refuse" answers 403 like qBittorrent without the localhost bypass."""
import json
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

REFUSE = len(sys.argv) > 2 and sys.argv[2] == "refuse"
T0 = time.time()
lock = threading.Lock()
TRK = ["https://tracker.alpha-tracker.org/a/PASSKEY1/announce", "https://flacsfor.me/PASSKEY2/announce",
       "https://beta.cc:443/announce?passkey=PASSKEY3"]
tors = {}
for i, (name, age, trk) in enumerate([("Show.S03E06.1080p.WEB.h264-GRP", 420, 0), ("Artist - Album (2026) [FLAC 24-96]", 1500, 1),
                                      ("Film.2026.2160p.WEB-DL.DDP5.1", 5400, 2), ("Show.S03E05.1080p.WEB.h264-GRP", 86400 * 2, 0),
                                      ("Linux.ISO.collection", 86400 * 30, 2)]):
    tors["h%d" % i] = {"name": name, "state": "uploading", "upspeed": 0, "dlspeed": 0, "uploaded": random.randint(1, 40) * 1e9,
                       "downloaded": 5e9, "ratio": 1.0, "tracker": TRK[trk], "added_on": int(T0 - age), "size": 5e9,
                       "progress": 1, "num_leechs": 12, "num_seeds": 40, "amount_left": 0}
tors["h9"] = {"name": "Big.Pack.Downloading", "state": "downloading", "upspeed": 0, "dlspeed": 30e6, "uploaded": 0, "downloaded": 1e9,
              "ratio": 0, "tracker": TRK[0], "added_on": int(T0 - 60), "size": 40e9, "progress": .1, "amount_left": 36e9}
rid = [0]


def tick():
    while True:
        with lock:
            for h, t in tors.items():
                if t["state"] == "uploading":
                    sp = random.choice([0, 0, 5e6, 30e6, 90e6]) if "Linux" not in t["name"] else 0
                    t["upspeed"] = sp
                    t["uploaded"] += sp * 2
                    t["ratio"] = round(t["uploaded"] / max(1, t["downloaded"]), 3)
            rid[0] += 1
        time.sleep(2)


def maindata(since):
    with lock:
        srv = {"up_info_speed": sum(t["upspeed"] for t in tors.values()), "dl_info_speed": 30e6, "up_info_data": 2.2e12,
               "dl_info_data": 3.1e11, "alltime_ul": 9.4e14, "alltime_dl": 1.9e14, "free_space_on_disk": 6.2e11}
        return {"rid": rid[0], "full_update": since == 0, "server_state": srv,
                "torrents": {h: dict(t) for h, t in tors.items()}}


REL = lambda: {"data": [
    {"name": "Show.S03E06.1080p.WEB.h264-GRP", "indexer": {"name": "Alpha"}, "filter": "TV 1080p",
     "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0 - 420)),
     "action_status": [{"status": "PUSH_APPROVED", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.700Z", time.gmtime(T0 - 420))}]},
    {"name": "Artist - Album (2026) [FLAC 24-96]", "indexer": {"name": "Flacsfor"}, "filter": "Music",
     "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0 - 1500)),
     "action_status": [{"status": "PUSH_APPROVED", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.400Z", time.gmtime(T0 - 1499))}]},
    {"name": "Some.Rejected.Thing", "indexer": {"name": "Beta"}, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0 - 2000)),
     "action_status": [{"status": "PUSH_REJECTED"}]}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/api/v2/sync/maindata":
            if REFUSE:
                return self.send(403, {})
            return self.send(200, maindata(int((parse_qs(u.query).get("rid") or ["0"])[0])))
        if u.path.startswith("/autobrr/api/"):
            if self.headers.get("X-API-Token") != "d" * 32:
                return self.send(401, {})
            if u.path.endswith("/release/stats"):
                return self.send(200, {"total_count": 9120, "filtered_count": 640, "push_approved_count": 412, "push_rejected_count": 228})
            return self.send(200, REL())
        self.send(404, {})


if __name__ == "__main__":
    threading.Thread(target=tick, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    print("ready", flush=True)
    srv.serve_forever()
