"""Unit tests for the panel's calculations and failure handling (stdlib unittest, no root, no network).

    python -m unittest discover -s tests -v
"""
import datetime as dt
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.hardware as hw  # noqa: E402
import core.place as place  # noqa: E402

TiB = 2 ** 40


# ----------------------------------------------------------------------------- SMART
class Smart(unittest.TestCase):
    def nvme(self, **log):
        base = {"percentage_used": 23, "available_spare": 100, "data_units_written": 1000000, "data_units_read": 2000000,
                "media_errors": 0, "unsafe_shutdowns": 3, "temperature": 41}
        base.update(log)
        return {"device": {"protocol": "NVMe"}, "model_name": "X", "smart_status": {"passed": True},
                "power_on_time": {"hours": 100}, "nvme_smart_health_information_log": base}

    def ata(self, attrs, rot=0, lb=512):
        return {"device": {"protocol": "ATA"}, "model_name": "Y", "rotation_rate": rot, "logical_block_size": lb,
                "smart_status": {"passed": True}, "temperature": {"current": 35},
                "ata_smart_attributes": {"table": [dict(id=i, name=n, value=v, worst=v, thresh=t, raw={"value": r, "string": str(r)})
                                                   for i, n, v, t, r in attrs]}}

    def test_nvme_units(self):
        d = hw._parse_smart("nvme0n1", self.nvme())
        self.assertEqual(d["kind"], "NVMe")
        self.assertEqual(d["written"], 1000000 * 512000)        # NVMe data unit = 1000 x 512 bytes
        self.assertEqual(d["read"], 2000000 * 512000)
        self.assertEqual(d["wear_used"], 23)
        self.assertEqual(d["temp"], 41)                           # falls back to the NVMe log temperature

    def test_nvme_wear_over_100(self):                            # percentage_used may exceed 100 on worn drives
        d = hw._parse_smart("nvme0n1", self.nvme(percentage_used=117))
        self.assertEqual(d["wear_used"], 117)

    def test_sata_ssd_lbas_and_wear_priority(self):
        d = hw._parse_smart("sda", self.ata([(241, "Total_LBAs_Written", 100, 0, 1000), (177, "Wear_Leveling_Count", 90, 0, 50),
                                             (233, "Media_Wearout_Indicator", 80, 0, 0)]))
        self.assertEqual(d["kind"], "SSD")
        self.assertEqual(d["written"], 1000 * 512)
        self.assertEqual(d["wear_used"], 20)                      # 233 has priority over 177

    def test_sata_4k_logical_blocks(self):
        d = hw._parse_smart("sda", self.ata([(241, "Total_LBAs_Written", 100, 0, 10)], lb=4096))
        self.assertEqual(d["written"], 40960)

    def test_vendor_write_units(self):
        g = hw._parse_smart("sda", self.ata([(241, "Host_Writes_GiB", 100, 0, 7)]))
        m = hw._parse_smart("sda", self.ata([(241, "Host_Writes_32MiB", 100, 0, 3)]))
        self.assertEqual(g["written"], 7 * 2 ** 30)
        self.assertEqual(m["written"], 3 * 32 * 2 ** 20)

    def test_hdd_attributes_and_states(self):
        d = hw._parse_smart("sdb", self.ata([(5, "Reallocated_Sector_Ct", 100, 5, 8), (197, "Current_Pending_Sector", 100, 0, 0),
                                             (10, "Spin_Retry_Count", 50, 60, 0), (9, "Power_On_Hours", 90, 0, 1)], rot=7200))
        self.assertEqual(d["kind"], "HDD")
        self.assertIsNone(d["wear_used"])                         # no wear for spinning disks
        states = {a["id"]: a["state"] for a in d["attrs"]}
        self.assertEqual(states[5], "warn")                       # reallocated sectors present
        self.assertEqual(states[197], "good")
        self.assertEqual(states[10], "crit")                      # normalized value <= threshold
        self.assertEqual(d["realloc"], 8)

    def test_garbage_smart_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "vda.json"), "w") as f:
                f.write("{not json")
            with mock.patch.object(hw, "SMART_DIR", tmp):
                drives = hw.drives()                              # must not raise
            self.assertTrue(all(isinstance(d, dict) for d in drives))


# ----------------------------------------------------------------------------- RAID
MDSTAT = """Personalities : [raid1] [raid6] [raid5] [raid4] [raid10]
md127 : inactive sdc1[1](S)
      976630464 blocks super 1.2

md5 : active raid5 sdf1[3] sde1[1] sdd1[0] sdg1[4](S)
      3906764800 blocks super 1.2 level 5, 512k chunk, algorithm 2 [3/3] [UUU]
      [=======>.............]  resync = 38.2% (746210816/1953382400) finish=101.3min speed=198582K/sec

md4 : active (auto-read-only) raid1 nvme1n1p2[1] nvme0n1p2[0]
      523264 blocks super 1.2 [2/2] [UU]
        resync=PENDING

md1 : active raid1 sdb2[2](F) sda2[0]
      523264 blocks super 1.2 [2/1] [U_]

md0 : active raid1 sdb3[2] sda3[0]
      5860000000 blocks super 1.2 [2/1] [U_]
      [=>...................]  recovery =  8.5% (498100000/5860000000) finish=596.2min speed=149872K/sec

unused devices: <none>
"""


class Raid(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile("w", delete=False)
        self.f.write(MDSTAT)
        self.f.close()
        self.p = mock.patch.object(hw, "MDSTAT", self.f.name)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        os.unlink(self.f.name)

    def test_parse(self):
        a = {x["name"]: x for x in hw.mdraid()}
        self.assertEqual(set(a), {"md127", "md5", "md4", "md1", "md0"})
        self.assertEqual(a["md127"]["health"], "inactive")
        self.assertEqual(a["md5"]["level"], "raid5")
        self.assertEqual(a["md5"]["health"], "checking")          # resync on a complete array
        self.assertAlmostEqual(a["md5"]["progress"], 38.2)
        self.assertEqual(sum(m["spare"] for m in a["md5"]["members"]), 1)
        self.assertEqual(a["md4"]["level"], "raid1")              # "(auto-read-only)" must not hide the level
        self.assertEqual([m["disk"] for m in a["md4"]["members"]], ["nvme1n1", "nvme0n1"])
        self.assertEqual(a["md1"]["health"], "degraded")
        self.assertTrue(any(m["failed"] for m in a["md1"]["members"]))
        self.assertEqual(a["md0"]["health"], "rebuilding")
        self.assertAlmostEqual(a["md0"]["finish"], 596.2 * 60)
        self.assertEqual(a["md0"]["size"], 5860000000 * 1024)
        self.assertEqual(a["md0"]["speed"], 149872 * 1024)

    def test_membership(self):
        mo = hw.raid()["member_of"]
        self.assertIn("md1 ✗", mo["sdb"])
        self.assertIn("md0", mo["sda"])

    def test_missing_or_garbage_mdstat(self):
        with mock.patch.object(hw, "MDSTAT", "/nonexistent/mdstat"):
            self.assertEqual(hw.mdraid(), [])
        with open(self.f.name, "w") as f:
            f.write("md9 : garbage line without anything\n   ??? \n")
        self.assertIsInstance(hw.mdraid(), list)                  # never raises

    def test_parent_disk(self):
        self.assertEqual(hw._parent_disk("nvme0n1p3"), "nvme0n1")
        self.assertEqual(hw._parent_disk("sda12"), "sda")
        self.assertEqual(hw._parent_disk("mmcblk0p1"), "mmcblk0")


# ----------------------------------------------------------------------------- traffic (vnstat)
def vnstat_payload(mode, v2=True, today=None):
    today = today or dt.date.today()
    unit = 1 if v2 else 1024
    days = []
    for i in range(40):
        d = today - dt.timedelta(days=i)
        days.append({"date": {"year": d.year, "month": d.month, "day": d.day}, "rx": (i + 1) * 1000 // unit * unit // unit, "tx": (i + 1) * 3000 // unit})
    months = [{"date": {"year": today.year, "month": today.month}, "rx": 111, "tx": 222}]
    now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    hours = []
    for i in range(30):
        t = now - dt.timedelta(hours=i)
        e = {"date": {"year": t.year, "month": t.month, "day": t.day}, "rx": i + 1, "tx": 2 * (i + 1)}
        if v2:
            e["time"] = {"hour": t.hour, "minute": 0}
        else:
            e["id"] = t.hour
        hours.append(e)
    key = {"d": "day", "m": "month", "h": "hour"} if v2 else {"d": "days", "m": "months", "h": "hours"}
    traffic = {"total": {"rx": 5000, "tx": 9000}, key[mode]: {"d": days, "m": months, "h": hours}[mode]}
    return {"jsonversion": "2" if v2 else "1", "interfaces": [{"name": "eth0", "created": {"date": {"year": 2024, "month": 3, "day": 14}}, "traffic": traffic}]}


class Traffic(unittest.TestCase):
    def run_with(self, v2):
        hw._traffic_cache.update(t=0, data=None)
        with mock.patch.object(hw, "_vnstat_json", side_effect=lambda m: vnstat_payload(m, v2)), \
             mock.patch.object(hw.shutil, "which", return_value="/usr/bin/vnstat"), \
             mock.patch.object(hw, "_default_iface", return_value="eth0"):
            return hw.traffic()

    def test_v2_totals(self):
        t = self.run_with(True)
        self.assertTrue(t["available"])
        self.assertEqual(t["today"], {"rx": 1000, "tx": 3000})
        self.assertEqual(t["yesterday"], {"rx": 2000, "tx": 6000})
        self.assertEqual(t["last7"]["tx"], sum((i + 1) * 3000 for i in range(7)))
        wd = dt.date.today().weekday()                            # Monday..today
        self.assertEqual(t["week"]["tx"], sum((i + 1) * 3000 for i in range(wd + 1)))
        self.assertEqual(t["all"], {"rx": 5000, "tx": 9000})
        self.assertEqual(t["month"], {"rx": 111, "tx": 222})
        self.assertEqual(len(t["days"]), 7)
        self.assertEqual(t["days"][-1]["date"], dt.date.today().isoformat())
        self.assertEqual(len(t["hours"]), 24)
        self.assertEqual(t["hours"][-1], dict(time=t["hours"][-1]["time"], rx=1, tx=2))   # current hour
        self.assertEqual(t["hours"][0]["tx"], 2 * 24)                                      # 23 hours ago
        self.assertEqual(t["since"], "2024-03-14")

    def test_v1_kib_units(self):
        t = self.run_with(False)
        self.assertEqual(t["all"], {"rx": 5000 * 1024, "tx": 9000 * 1024})
        self.assertEqual(t["hours"][-1]["tx"], 2 * 1024)

    def test_vnstat_missing_or_failing(self):
        hw._traffic_cache.update(t=0, data=None)
        with mock.patch.object(hw.shutil, "which", return_value=None):
            self.assertFalse(hw.traffic()["available"])
        hw._traffic_cache.update(t=0, data=None)
        with mock.patch.object(hw.shutil, "which", return_value="/usr/bin/vnstat"), \
             mock.patch.object(hw, "_vnstat_json", side_effect=ValueError("bad json")):
            self.assertFalse(hw.traffic()["available"])


# ----------------------------------------------------------------------------- other collectors
class Collectors(unittest.TestCase):
    def test_fail2ban_missing_and_garbage(self):
        with mock.patch.object(hw, "F2B_FILE", "/nonexistent.json"):
            self.assertFalse(hw.fail2ban()["available"])
        with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
            f.write("{broken"); f.flush()
            with mock.patch.object(hw, "F2B_FILE", f.name):
                self.assertFalse(hw.fail2ban()["available"])

    def test_live_twice_has_rates(self):
        hw.live()
        d = hw.live()
        self.assertIsNotNone(d["cpu"]["total"])
        self.assertTrue(0 <= d["cpu"]["total"] <= 100)
        self.assertTrue(all(c is None or 0 <= c <= 100 for c in d["cpu"]["cores"]))
        self.assertGreater(d["mem"]["total"], 0)

    def test_processes_shape(self):
        hw.processes()
        p = hw.processes()
        self.assertGreater(p["count"], 0)
        self.assertTrue(all(r["cpu"] >= 0 and r["rss"] >= 0 for r in p["procs"]))
        self.assertTrue(all("key" not in r for r in p["procs"]))   # internal key never leaks to JSON

    def test_place_offline_and_bad_coords(self):
        cfg = {"PLACE_LOOKUPS": False}
        out = place.place(cfg, None)
        self.assertIsNone(out["geo"])
        self.assertIn("tz", out["time"])
        with self.assertRaises(ValueError):
            place.device_weather({}, 123, 0)                       # latitude out of range
        self.assertIsNone(place.device_weather({"PLACE_LOOKUPS": False}, 25, 81))

    def test_public_ip_filters(self):
        self.assertFalse(place._is_public("192.168.1.2"))
        self.assertFalse(place._is_public("fe80::1"))
        self.assertTrue(place._is_public("51.38.71.204"))



# ----------------------------------------------------------------------------- integrated GPU (fake sysfs)
class IGpu(unittest.TestCase):
    def make(self, vendor, slot, files, driver="i915", render=True):
        root = tempfile.mkdtemp()
        drm, devs = os.path.join(root, "drm"), os.path.join(root, "devices", slot)
        os.makedirs(drm); os.makedirs(devs); os.makedirs(os.path.join(root, "drivers", driver))
        os.symlink(os.path.join(root, "drivers", driver), os.path.join(devs, "driver"))
        if render:
            os.makedirs(os.path.join(devs, "drm", "renderD128"))
        card = os.path.join(drm, "card0"); os.makedirs(card)
        os.symlink(devs, os.path.join(card, "device"))
        files = dict(files, **{"device/vendor": vendor})
        for rel, val in files.items():
            p = os.path.join(card, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(str(val))
        return root, card

    def gpus(self, root, plex=False, plex_in_group=False):
        inst = tempfile.mkdtemp()
        if plex:
            open(os.path.join(inst, ".plex.lock"), "w").close()
        grp = os.path.join(inst, "group")
        with open(grp, "w") as f:
            f.write("render:x:993:%s\nvideo:x:44:\n" % ("plex" if plex_in_group else ""))
        hw._gpu_cache.update(t=0, data=[]); hw._idle_prev.clear()
        with mock.patch.object(hw, "DRM", os.path.join(root, "drm")), mock.patch.object(hw, "INSTALL_DIR", inst), \
             mock.patch.object(hw, "GROUP_FILE", grp), mock.patch.object(hw.shutil, "which", return_value=None):
            return hw._gpus()

    def test_intel_igpu_detected_with_clock_and_load(self):
        root, card = self.make("0x8086", "0000:00:02.0", {"gt/gt0/rc6_residency_ms": 1000, "gt/gt0/rps_act_freq_mhz": 350,
                                                          "gt/gt0/rps_RP0_freq_mhz": 1150})
        g = self.gpus(root)[0]
        self.assertTrue(g["integrated"])
        self.assertEqual((g["vendor"], g["name"], g["driver"]), ("Intel", "Intel integrated graphics", "i915"))
        self.assertEqual((g["clock"], g["clock_max"]), (350, 1150))
        self.assertEqual(g["render_node"], "/dev/dri/renderD128")
        self.assertIsNone(g["util"])                                  # first sample: no delta yet
        # 2 s later the GPU slept 1.5 s of them -> 25 % busy
        g2 = {"util": None}
        hw._intel_metrics(card, g2, 100.0)
        with open(card + "/gt/gt0/rc6_residency_ms", "w") as f:
            f.write("2500")
        hw._intel_metrics(card, g2, 102.0)
        self.assertAlmostEqual(g2["util"], 25.0)

    def test_older_kernel_paths_and_clamping(self):
        root, card = self.make("0x8086", "0000:00:02.0", {"power/rc6_residency_ms": 0, "gt_act_freq_mhz": 300, "gt_RP0_freq_mhz": 1200})
        g = {"util": None}
        hw._idle_prev.clear()
        hw._intel_metrics(card, g, 10.0)
        with open(card + "/power/rc6_residency_ms", "w") as f:
            f.write("999999")                                       # counter jump -> clamp, never negative
        hw._intel_metrics(card, g, 11.0)
        self.assertEqual(g["util"], 0.0)
        self.assertEqual((g["clock"], g["clock_max"]), (300, 1200))

    def test_intel_arc_is_not_integrated(self):
        root, _ = self.make("0x8086", "0000:03:00.0", {}, driver="xe")
        self.assertFalse(self.gpus(root)[0]["integrated"])

    def test_amd_apu_vs_discrete(self):
        apu, _ = self.make("0x1002", "0000:05:00.0", {"device/mem_info_vram_total": 512 * 2 ** 20, "device/gpu_busy_percent": 7}, driver="amdgpu")
        dgpu, _ = self.make("0x1002", "0000:03:00.0", {"device/mem_info_vram_total": 8 * 2 ** 30}, driver="amdgpu")
        a = self.gpus(apu)[0]
        self.assertTrue(a["integrated"]); self.assertEqual(a["util"], 7)
        self.assertFalse(self.gpus(dgpu)[0]["integrated"])

    def test_no_gpu_means_no_card(self):
        root = tempfile.mkdtemp(); os.makedirs(os.path.join(root, "drm"))
        self.assertEqual(self.gpus(root), [])

    def test_plex_hw_transcode_hint(self):
        root, _ = self.make("0x8086", "0000:00:02.0", {})
        self.assertNotIn("hw_transcode", {k for k, v in self.gpus(root)[0].items() if v is not None})  # no Plex: no hint
        self.assertEqual(self.gpus(root, plex=True)[0]["hw_transcode"], {"group": "render", "ok": False})
        self.assertEqual(self.gpus(root, plex=True, plex_in_group=True)[0]["hw_transcode"], {"group": "render", "ok": True})


# ----------------------------------------------------------------------------- Plex now playing
import core.plex as plex  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "plex_sessions.json")


class Plex(unittest.TestCase):
    def sessions(self):
        with open(FIXTURE) as f:
            return [plex.parse_session(m) for m in json.load(f)["MediaContainer"]["Metadata"]]

    def test_decisions(self):
        ep, mv, tr = self.sessions()
        self.assertEqual((ep["decision"], ep["hw"], ep["location"]), ("direct play", False, "LAN"))
        self.assertEqual((ep["title"], ep["subtitle"]), ("Northern Lights", "S02E07 · The Long Night"))
        self.assertAlmostEqual(ep["progress"], 38.3)
        self.assertEqual((mv["decision"], mv["hw"], mv["quality"], mv["out_quality"]), ("transcode", True, "4K", "1080p"))
        self.assertIn("video hevc → h264", mv["reasons"]); self.assertIn("subtitles burned in", mv["reasons"])
        self.assertEqual(mv["location"], "Remote")
        self.assertEqual((tr["decision"], tr["state"], tr["throttled"]), ("direct stream", "paused", True))
        self.assertEqual((tr["title"], tr["subtitle"]), ("Blue Hour", "Lumen · Night Drive"))

    def test_garbage_session_does_not_crash(self):
        s = plex.parse_session({"Media": "nope", "Player": None, "duration": "x"})
        self.assertEqual(s["decision"], "direct play"); self.assertIsNone(s["progress"])

    def test_states(self):
        inst = tempfile.mkdtemp()
        with mock.patch.object(hw, "INSTALL_DIR", inst):
            self.assertEqual(plex.now_playing({}), {"installed": False})
            open(os.path.join(inst, ".plex.lock"), "w").close()
            with mock.patch.object(plex, "TOKEN_FILE", "/nonexistent"):
                self.assertEqual(plex.now_playing({})["status"], "no_token")
            plex._cache.update(data=None)
            with mock.patch.object(plex.requests, "get", side_effect=plex.requests.ConnectionError()):
                self.assertEqual(plex.now_playing({"PLEX_TOKEN": "t"})["status"], "unreachable")
            plex._cache.update(data=None)
            with mock.patch.object(plex.requests, "get", return_value=mock.Mock(status_code=401)):
                self.assertEqual(plex.now_playing({"PLEX_TOKEN": "t2"})["status"], "unauthorized")
            plex._cache.update(data=None)
            with open(FIXTURE) as f:
                payload = json.load(f)
            with mock.patch.object(plex.requests, "get", return_value=mock.Mock(status_code=200, json=lambda: payload)):
                d = plex.now_playing({"PLEX_TOKEN": "t3"})
            self.assertEqual((d["count"], d["transcodes"], d["hw_transcodes"]), (3, 1, 1))
            self.assertEqual(d["bandwidth"], 12450 + 8200 + 320)
            self.assertEqual([s["state"] for s in d["sessions"]], ["playing", "playing", "paused"])
            self.assertNotIn("t3", json.dumps(d))                  # the token never reaches the browser


# ----------------------------------------------------------------------------- server specs
import core.specs as specs  # noqa: E402


class Specs(unittest.TestCase):
    def test_board_and_junk_filtering(self):
        dmi = tempfile.mkdtemp()
        for k, v in {"board_vendor": "Intel Corporation", "board_name": "DH67BL", "board_version": "AAG10189-213",
                     "product_name": "To Be Filled By O.E.M.", "bios_version": "BLH6710H.86A.0163.2018.1023.1559",
                     "bios_date": "10/23/2018", "bios_vendor": "Intel Corp."}.items():
            with open(os.path.join(dmi, k), "w") as f:
                f.write(v + "\n")
        with mock.patch.object(specs, "DMI", dmi):
            b = specs.board()
        self.assertEqual(b["motherboard"], "Intel DH67BL"); self.assertEqual(b["motherboard_version"], "AAG10189-213")
        self.assertNotIn("system", b)                               # OEM placeholder dropped
        self.assertEqual(b["bios"], "BLH6710H.86A.0163.2018.1023.1559")

    def test_memory_grouping_and_vendor_codes(self):
        mods = [{"size": 8 * 2 ** 30, "type": "DDR3", "speed": "1333 MT/s", "manufacturer": "0x2C00", "part": "16KTF1G64AZ-1G9P1"}] * 2 + \
               [{"size": 8 * 2 ** 30, "type": "DDR3", "speed": "1333 MT/s", "manufacturer": None, "part": "16KTF1G64AZ-1G9P1"}] * 2
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump({"memory": mods}, f); f.close()
        with mock.patch.object(specs, "HOSTINFO", f.name):
            m = specs.memory()
        self.assertEqual(m["modules"], 4)
        self.assertEqual([(g["vendor"], g["count"]) for g in m["groups"]], [("Micron", 4)])
        with mock.patch.object(specs, "HOSTINFO", "/nonexistent"):
            self.assertEqual(specs.memory()["groups"], [])          # no root scan yet: total only

    def test_storage_groups_raid_members(self):
        drives = [{"name": "sda", "model": "SAMSUNG MZ7LM480HMHQ-00005", "capacity": 480103981056, "kind": "SSD"},
                  {"name": "sdb", "model": "SAMSUNG MZ7LM480HMHQ-00005", "capacity": 480103981056, "kind": "SSD"}]
        raid = {"md": [{"name": "md2", "level": "raid0"}], "zfs": [], "member_of": {"sda": ["md2"], "sdb": ["md2"]}}
        with mock.patch.object(hw, "drives", return_value=drives), mock.patch.object(hw, "raid", return_value=raid):
            st = specs.storage()
        self.assertEqual(len(st), 1)
        self.assertEqual((st[0]["count"], st[0]["raid"]), (2, [{"name": "md2", "level": "raid0"}]))

    def test_specs_never_raise(self):
        with mock.patch.object(specs, "storage", side_effect=RuntimeError("boom")):
            specs._cache.update(data=None)
            d = specs.specs({"SERVER_BANDWIDTH": "1Gbit/s unmetered and guaranteed"})
        self.assertIsNone(d["storage"]); self.assertEqual(d["bandwidth_note"], "1Gbit/s unmetered and guaranteed")
        self.assertTrue(d["hostname"])


# ----------------------------------------------------------------------------- speed test (fake M-Lab)
class SpeedTest(unittest.TestCase):
    """Runs the real ndt7 client against tests/tools/fake_ndt7.py on localhost (plain ws, 1 s each way)."""

    @classmethod
    def setUpClass(cls):
        import socket
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        env = dict(os.environ, FAKE_NDT7_MBPS="80", FAKE_NDT7_SECONDS="1")
        cls.srv = subprocess.Popen([sys.executable, os.path.join(here, "tools", "fake_ndt7.py"), str(cls.port)],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, universal_newlines=True)
        cls.srv.stdout.readline()
        cls.env = {"NDT7_LOCATE": "http://127.0.0.1:{}/v2/nearest/ndt/ndt7".format(cls.port), "NDT7_DURATION": "1",
                   "no_proxy": "*", "NO_PROXY": "*"}

    @classmethod
    def tearDownClass(cls):
        cls.srv.kill()
        cls.srv.wait()
        cls.srv.stdout.close()

    def run_client(self, **extra):
        import subprocess
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "ndt7.py")
        out = subprocess.run([sys.executable, script], stdout=subprocess.PIPE, timeout=60, universal_newlines=True,
                             env=dict(os.environ, **dict(self.env, **extra)))
        return out.returncode, [json.loads(l) for l in out.stdout.splitlines()]

    def test_full_run(self):
        code, lines = self.run_client()
        self.assertEqual(code, 0)
        final = lines[-1]
        self.assertTrue(final["done"])
        self.assertGreater(final["download"], 40)          # server paces at 80 Mbit/s
        self.assertLess(final["download"], 100)
        self.assertGreater(final["upload"], 1)
        self.assertEqual(final["upload_measured_by"], "server")
        self.assertEqual(final["latency_ms"], 4.2)
        self.assertEqual(final["server"]["city"], "Amsterdam")
        self.assertEqual([l["phase"] for l in lines if "phase" in l][0], "locate")

    def test_handshake_matches_rfc6455(self):
        # the worked example in RFC 6455 section 1.3; checked independently of our fake server
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
        import ndt7
        self.assertEqual(ndt7.accept_for(b"dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_unreachable_is_a_message(self):
        code, lines = self.run_client(NDT7_LOCATE="http://127.0.0.1:9/v2/nearest/ndt/ndt7")
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach the M-Lab locate service", lines[-1]["error"])

    def test_cooldown_and_single_run(self):
        import core.speedtest as st
        with tempfile.TemporaryDirectory() as d:
            cfg = {"SPEEDTEST_FILE": os.path.join(d, "s.json"), "NDT7_ENV": self.env}
            with mock.patch.object(st.threading, "Thread") as T:   # don't really run; check the guard
                ok, _ = st.start(cfg)
                self.assertTrue(ok)
                ok, msg = st.start(cfg)
                self.assertFalse(ok)
                self.assertIn("already running", msg)
            st._run(cfg)                                          # the real run, in this thread
            s = st.status(cfg)
            self.assertFalse(s["running"])
            self.assertIsNone(s["error"])
            self.assertGreater(s["last"]["download"], 40)
            self.assertGreater(s["cooldown"], 590)
            ok, msg = st.start(cfg)
            self.assertFalse(ok)
            self.assertIn("wait", msg)
            with open(cfg["SPEEDTEST_FILE"], "w") as f:
                f.write("{broken")
            self.assertIsNone(st.status(cfg)["last"])               # a damaged file never breaks the panel


# ----------------------------------------------------------------------------- per-core temperatures (fake sysfs)
class CoreTemps(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        hw_ = os.path.join(self.d, "hwmon", "hwmon3")
        os.makedirs(hw_)
        files = {"name": "coretemp", "temp1_label": "Package id 0", "temp1_input": "41000",
                 "temp2_label": "Core 0", "temp2_input": "38000", "temp2_max": "85000",
                 "temp3_label": "Core 1", "temp3_input": "44000", "temp3_max": "85000",
                 "temp10_label": "Core 8", "temp10_input": "36000"}   # core_id numbering can have gaps
        for k, v in files.items():
            with open(os.path.join(hw_, k), "w") as f:
                f.write(v + "\n")
        for cpu, core in enumerate([0, 1, 8, 0, 1, 8]):                # siblings far apart, as Linux numbers them
            t = os.path.join(self.d, "cpu", "cpu%d" % cpu, "topology")
            os.makedirs(t)
            for k, v in (("physical_package_id", "0"), ("core_id", str(core))):
                with open(os.path.join(t, k), "w") as f:
                    f.write(v + "\n")
        self.p = [mock.patch.object(hw, "HWMON", os.path.join(self.d, "hwmon")),
                  mock.patch.object(hw, "CPU_SYS", os.path.join(self.d, "cpu")),
                  mock.patch.dict(hw._topology, clear=True)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        import shutil
        shutil.rmtree(self.d)

    def test_threads_follow_topology(self):
        pkg, cores, by_core, _ = hw._temps()
        self.assertEqual(pkg, 41.0)
        self.assertEqual(sorted(cores), [36.0, 38.0, 44.0])
        tt = hw._thread_temps(6, by_core)
        self.assertEqual([t["c"] for t in tt], [38.0, 44.0, 36.0, 38.0, 44.0, 36.0])
        self.assertEqual(tt[0]["high"], 85.0)
        self.assertIsNone(tt[2]["high"])

    def test_no_sensors_means_no_temps(self):
        self.assertEqual(hw._thread_temps(6, {}), [])

    def test_missing_topology_is_none_not_a_crash(self):
        _, _, by_core, _ = hw._temps()
        tt = hw._thread_temps(8, by_core)                          # cpu6/cpu7 don't exist here
        self.assertIsNone(tt[6])
        self.assertIsNone(tt[7])


# ----------------------------------------------------------------------------- bans by country
class IpCountry(unittest.TestCase):
    def setUp(self):
        import core.ipcountry as ic
        self.ic = ic
        self.d = tempfile.mkdtemp()
        self.cfg = {"IPCOUNTRY_FILE": os.path.join(self.d, "c.json")}
        ic._state.update(cache=None, running=False, pending=set(), dirty=False, blocked_until=0)

    def run_sync(self):
        """Run the background worker inline so the test is deterministic."""
        with mock.patch.object(self.ic.threading, "Thread") as T, mock.patch.object(self.ic.time, "sleep"):
            out = self.ic.countries(self.ips, self.cfg)
            if T.called:
                self.ic._worker(self.cfg)
        return out

    def test_private_addresses_never_leave(self):
        for ip in ("10.0.0.1", "192.168.1.9", "127.0.0.1", "::1", "fe80::1", "not-an-ip", "0.0.0.0"):
            self.assertIsNone(self.ic.public_ip(ip))
        self.assertEqual(self.ic.public_ip(" 8.8.8.8 "), "8.8.8.8")

    def test_lookup_cache_and_summary(self):
        self.ips = ["1.1.1.1", "8.8.8.8", "10.0.0.1"]
        calls = []
        def fake(ip, session=None):
            calls.append(ip)
            return {"1.1.1.1": "AU", "8.8.8.8": "US"}[ip], None
        with mock.patch.object(self.ic, "lookup_one", side_effect=fake):
            self.assertEqual(self.run_sync(), {})                       # first call only queues
            self.assertEqual(sorted(calls), ["1.1.1.1", "8.8.8.8"])     # private one never looked up
            got = self.run_sync()
        self.assertEqual(got, {"1.1.1.1": "AU", "8.8.8.8": "US"})
        self.assertEqual(len(calls), 2)                                 # cached: no second lookup
        self.ic._state["cache"] = None                                  # survives a restart (file)
        with mock.patch.object(self.ic, "lookup_one", side_effect=AssertionError("should be cached")):
            self.assertEqual(self.run_sync(), {"1.1.1.1": "AU", "8.8.8.8": "US"})
        top, other, unknown = self.ic.summarize([("1.1.1.1", 5), ("8.8.8.8", 9), ("9.9.9.9", 2)], got)
        self.assertEqual([r["cc"] for r in top], ["US", "AU"])
        self.assertEqual((top[0]["bans"], other, unknown), (9, 0, 2))

    def test_rate_limit_backs_off(self):
        self.ips = ["1.1.1.1", "8.8.8.8"]
        with mock.patch.object(self.ic, "lookup_one", return_value=(None, 120)) as L:
            self.run_sync()
            self.assertEqual(L.call_count, 1)                           # stops at the first 429
            self.run_sync()
            self.assertEqual(L.call_count, 1)                           # and waits out Retry-After
        self.assertGreater(self.ic._state["blocked_until"], time.time() + 100)

    def test_lookups_off(self):
        self.ips = ["1.1.1.1"]
        self.cfg["PLACE_LOOKUPS"] = False
        with mock.patch.object(self.ic, "lookup_one", side_effect=AssertionError("lookups are off")):
            self.assertEqual(self.run_sync(), {})

    def test_bad_answers_are_ignored(self):
        r = mock.Mock(status_code=200, headers={}, json=lambda: {"country": "<script>"})
        r.raise_for_status = lambda: None
        self.assertEqual(self.ic.lookup_one("1.1.1.1", session=mock.Mock(get=lambda *a, **k: r)), (None, None))


# ----------------------------------------------------------------------------- app uptime
class Uptime(unittest.TestCase):
    def setUp(self):
        import core.uptime as up
        self.up = up
        self.d = tempfile.mkdtemp()
        self.cfg = {"UPTIME_FILE": os.path.join(self.d, "u.json")}
        up._state.update(data=None, saved=0.0)

    def test_up_down_off_and_gaps(self):
        t0 = 1_800_000_000 // 3600 * 3600          # start of an hour
        for m in range(60):                       # hour 1: plex down for 15 minutes, sonarr off on purpose
            self.up.record([{"name": "plex", "active": m >= 15, "enabled": True},
                            {"name": "sonarr", "active": False, "enabled": False}], self.cfg, now=t0 + m * 60)
        # hour 2: panel not running (no samples); hour 3: all fine
        for m in range(60):
            self.up.record([{"name": "plex", "active": True, "enabled": True}], self.cfg, now=t0 + 7200 + m * 60)
        r = self.up.report(self.cfg, ["plex", "sonarr", "radarr"], now=t0 + 7200 + 3599)
        plex = r["apps"][0]
        self.assertEqual(plex["hours"][-3], {"up": 45, "expected": 60, "off": 0})
        self.assertIsNone(plex["hours"][-2])                     # gap is "no data", not downtime
        self.assertEqual(plex["hours"][-1]["up"], 60)
        self.assertEqual(plex["pct"], round(100 * 105 / 120, 2))
        sonarr = r["apps"][1]
        self.assertEqual(sonarr["hours"][-3], {"up": 0, "expected": 0, "off": 60})
        self.assertIsNone(sonarr["pct"])                         # off on purpose: no uptime figure
        self.assertEqual(r["apps"][2]["name"], "radarr")          # installed but never sampled yet
        self.assertEqual(r["apps"][2]["pct"], None)

    def test_persists_and_prunes(self):
        t0 = 1_800_000_000
        self.up.record([{"name": "plex", "active": True, "enabled": True}], self.cfg, now=t0)
        self.up.save(self.cfg)
        self.up._state["data"] = None
        self.up.record([{"name": "plex", "active": True, "enabled": True}], self.cfg, now=t0 + 60 * 3600)
        hours = [k for k in self.up._state["data"]["apps"]["plex"] if not k.startswith("_")]
        self.assertEqual(len(hours), 1)                          # the 60-hour-old bucket was pruned

    def test_damaged_file(self):
        with open(self.cfg["UPTIME_FILE"], "w") as f:
            f.write("{nope")
        self.assertEqual(self.up.report(self.cfg)["apps"], [])


# ----------------------------------------------------------------------------- media queue (fake Sonarr/Radarr)
class MediaQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.srv = subprocess.Popen([sys.executable, os.path.join(here, "tools", "fake_arr.py"), str(cls.port)],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
        cls.srv.stdout.readline()

    @classmethod
    def tearDownClass(cls):
        cls.srv.kill()
        cls.srv.wait()
        cls.srv.stdout.close()

    def setUp(self):
        import core.arr as arr
        self.arr = arr
        arr._cache.update(t=0, data=None)
        self.d = tempfile.mkdtemp()
        self.cfg = {"ARR_FILE": os.path.join(self.d, "arr.json")}

    def write(self, conf):
        with open(self.cfg["ARR_FILE"], "w") as f:
            json.dump(conf, f)

    def test_queue_and_upcoming(self):
        base = "http://127.0.0.1:{}".format(self.port)
        self.write({"sonarr": {"url": base + "/sonarr", "key": "s" * 32}, "radarr": {"url": base + "/radarr", "key": "r" * 32}})
        d = self.arr.media(self.cfg)
        self.assertTrue(d["available"])
        self.assertTrue(all(a["ok"] for a in d["apps"]))
        q = {x["title"]: x for x in d["queue"]}
        self.assertEqual(q["The <b>Show</b>"]["sub"], "S03E06 · Pilot Light")   # raw text; the page escapes it
        self.assertEqual(q["The <b>Show</b>"]["pct"], 65.0)
        self.assertEqual(q["Film"]["pct"], 75.0)
        self.assertTrue(q["Stuck Show"]["warn"])
        self.assertIsNone(q["Stuck Show"]["pct"])
        up = [u["title"] for u in d["upcoming"]]
        self.assertEqual(up, ["The Show", "New Film (2026)"])                    # old film outside the week is dropped
        self.assertEqual(d["upcoming"][1]["sub"], "Digital release")

    def test_one_app_down_or_wrong_key(self):
        base = "http://127.0.0.1:{}".format(self.port)
        self.write({"sonarr": {"url": base + "/sonarr", "key": "x" * 32}, "radarr": {"url": base + "/broken", "key": "r" * 32}})
        d = self.arr.media(self.cfg)
        errs = {a["name"]: a.get("error") for a in d["apps"]}
        self.assertEqual(errs, {"sonarr": "API key rejected", "radarr": "not answering"})
        self.assertEqual(d["queue"], [])

    def test_only_local_addresses_are_used(self):
        self.write({"sonarr": {"url": "http://evil.example:80", "key": "s" * 32},
                    "radarr": {"url": "http://127.0.0.1:7878/radarr", "key": "bad key!"}})
        self.assertEqual(self.arr.media(self.cfg), {"available": False, "reason": "no-apps"})
        self.assertEqual(self.arr.media({"ARR_FILE": os.path.join(self.d, "missing.json")})["reason"], "not-configured")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Updates(unittest.TestCase):
    """App updates: merging the root check's answer, starting upgrades, *arr self-updates."""
    @classmethod
    def setUpClass(cls):
        import socket
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        cls.srv = {}
        for mode in ("ok", "fail", "noop"):
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            p = subprocess.Popen([sys.executable, os.path.join(here, "tools", "fake_servarr.py"), str(port), mode],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
            p.stdout.readline()
            cls.srv[mode] = (p, port)

    @classmethod
    def tearDownClass(cls):
        for p, _ in cls.srv.values():
            p.kill()
            p.wait()
            p.stdout.close()

    def setUp(self):
        import core.updates as up
        self.up = up
        self.d = tempfile.mkdtemp()
        up._mem.update(check_at=0.0, pending={})
        self.saved_dir = up.UPGRADES_DIR
        up.UPGRADES_DIR = os.path.join(self.d, "upgrades")
        os.makedirs(up.UPGRADES_DIR)
        self.calls = os.path.join(self.d, "calls")
        self.rc = os.path.join(self.d, "rc")
        fake = os.path.join(self.d, "fake-upgrade")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\necho "$1" >> "{}"\nrc=$(cat "{}" 2>/dev/null || echo 0)\n'
                    '[ "$rc" = 4 ] && echo "panel-upgrade: another upgrade is running" >&2\nexit "$rc"\n'.format(self.calls, self.rc))
        os.chmod(fake, 0o755)
        self.cfg = {"UPDATES_FILE": os.path.join(self.d, "updates.json"), "ARR_FILE": os.path.join(self.d, "arr.json"),
                    "UPDATE_JOBS_FILE": os.path.join(self.d, "jobs.json"), "UPGRADE_CMD": [fake]}
        now = int(time.time()) - 60
        self.write("updates.json", {"checked": now, "apps": {
            "autobrr": {"installed": "1.48.0", "latest": "1.50.0", "available": True, "notes": "- fix", "url": "https://github.com/x", "checked": now},
            "plex": {"installed": "1.41.3.9314", "latest": "1.41.3.9314", "available": False, "checked": now},
            "qbittorrent": {"installed": "4.6.7", "latest": "5.0.4", "available": True, "url": "javascript:alert(1)", "checked": now},
            "sonarr": {"installed": "4.0.10.2544", "latest": "4.0.11.2680", "available": True, "method": "self", "checked": now},
            "evil": {"installed": "1", "latest": "2", "available": True}}})

    def tearDown(self):
        self.up.UPGRADES_DIR = self.saved_dir

    def write(self, name, obj):
        with open(os.path.join(self.d, name), "w") as f:
            json.dump(obj, f)

    def arr(self, mode):
        port = self.srv[mode][1]
        self.write("arr.json", {"sonarr": {"url": "http://127.0.0.1:{}/sonarr".format(port), "key": "s" * 32}})

    def calls_made(self):
        try:
            with open(self.calls) as f:
                return f.read().split()
        except OSError:
            return []

    def test_status_merges_and_filters(self):
        st = self.up.status(self.cfg)
        self.assertNotIn("evil", st["apps"])
        self.assertEqual(st["apps"]["qbittorrent"]["action"], "notify")
        self.assertIsNone(st["apps"]["qbittorrent"]["url"])               # only https links reach the page
        self.assertEqual(st["apps"]["autobrr"]["action"], "upgrade")
        self.assertEqual(st["apps"]["sonarr"]["action"], "blocked")        # no API details yet
        self.assertFalse(st["checking"])

    def test_without_sudo_rule_root_apps_wait_for_box_update(self):
        cfg = dict(self.cfg)
        del cfg["UPGRADE_CMD"]
        with mock.patch.object(self.up, "supported", return_value=False):
            st = self.up.status(cfg)
            self.assertEqual(st["apps"]["autobrr"]["action"], "needs-update")
            ok, msg = self.up.upgrade(cfg, "autobrr")
        self.assertFalse(ok)
        self.assertIn("box update", msg)

    def test_qbittorrent_and_unknown_apps_are_refused(self):
        for app in ("qbittorrent", "nginx", "../x", ""):
            ok, _ = self.up.upgrade(self.cfg, app)
            self.assertFalse(ok, app)
        self.assertEqual(self.calls_made(), [])

    def test_no_update_waiting_is_refused(self):
        ok, msg = self.up.upgrade(self.cfg, "plex")
        self.assertFalse(ok)
        self.assertEqual(self.calls_made(), [])

    def test_root_upgrade_starts_once(self):
        ok, msg = self.up.upgrade(self.cfg, "autobrr")
        self.assertTrue(ok, msg)
        self.assertEqual(self.calls_made(), ["autobrr"])
        st = self.up.status(self.cfg)
        self.assertEqual(st["apps"]["autobrr"]["job"]["state"], "running")   # "Starting…" before the job writes
        self.assertEqual(st["busy"], "autobrr")
        self.arr("ok")
        ok, msg = self.up.upgrade(self.cfg, "sonarr")                         # one at a time
        self.assertFalse(ok)
        self.assertIn("one at a time", msg)
        with open(os.path.join(self.up.UPGRADES_DIR, "autobrr.json"), "w") as f:  # job ran and finished fast
            json.dump({"state": "done", "message": "autobrr upgraded", "started": int(time.time()), "finished": int(time.time())}, f)
        st = self.up.status(self.cfg)
        self.assertIsNone(st["busy"])                                         # not held up by the start grace
        self.assertEqual(st["apps"]["autobrr"]["job"]["state"], "done")

    def test_root_job_progress_and_stale(self):
        path = os.path.join(self.up.UPGRADES_DIR, "autobrr.json")
        with open(path, "w") as f:
            json.dump({"app": "autobrr", "state": "running", "message": "Upgrading", "started": int(time.time()), "finished": None}, f)
        self.assertEqual(self.up.status(self.cfg)["busy"], "autobrr")
        with open(path, "w") as f:
            json.dump({"app": "autobrr", "state": "running", "started": int(time.time()) - 3 * 3600, "finished": None}, f)
        st = self.up.status(self.cfg)
        self.assertEqual(st["apps"]["autobrr"]["job"]["state"], "failed")     # killed part-way: not stuck "running"
        self.assertIsNone(st["busy"])

    def test_busy_server_message(self):
        with open(self.rc, "w") as f:
            f.write("4")
        ok, msg = self.up.upgrade(self.cfg, "autobrr")
        self.assertFalse(ok)
        self.assertIn("Another upgrade is running", msg)
        self.assertIsNone(self.up.status(self.cfg)["busy"])

    def test_arr_self_update_succeeds(self):
        self.arr("ok")
        self.assertEqual(self.up.status(self.cfg)["apps"]["sonarr"]["action"], "upgrade")
        ok, msg = self.up.upgrade(self.cfg, "sonarr")
        self.assertTrue(ok, msg)
        deadline = time.time() + 10
        while time.time() < deadline:
            st = self.up.status(self.cfg)
            if st["apps"]["sonarr"]["job"]["state"] != "running":
                break
            time.sleep(0.3)
        a = st["apps"]["sonarr"]
        self.assertEqual(a["job"]["state"], "done")
        self.assertEqual(a["installed"], "4.0.11.2680")
        self.assertFalse(a["available"])
        self.assertIn("check", self.calls_made())                             # asks the server for a fresh check
        self.assertIn("4.0.11", self.up.log(self.cfg, "sonarr"))

    def test_arr_updater_failure_is_reported(self):
        self.arr("fail")
        self.assertTrue(self.up.upgrade(self.cfg, "sonarr")[0])
        a = self.up.status(self.cfg)["apps"]["sonarr"]
        self.assertEqual(a["job"]["state"], "failed")
        self.assertIn("isn't writable", a["job"]["message"])

    def test_arr_noop_ends_as_failed_not_forever(self):
        self.arr("noop")
        self.assertTrue(self.up.upgrade(self.cfg, "sonarr")[0])
        self.assertEqual(self.up.status(self.cfg)["apps"]["sonarr"]["job"]["state"], "running")
        with open(self.cfg["UPDATE_JOBS_FILE"]) as f:
            jobs = json.load(f)
        jobs["sonarr"]["started"] -= 300
        with open(self.cfg["UPDATE_JOBS_FILE"], "w") as f:
            json.dump(jobs, f)
        j = self.up.status(self.cfg)["apps"]["sonarr"]["job"]
        self.assertEqual(j["state"], "failed")
        self.assertIn("open Sonarr", j["message"])

    def test_arr_address_must_be_local(self):
        self.write("arr.json", {"sonarr": {"url": "http://evil.example:80", "key": "s" * 32}})
        self.assertEqual(self.up.status(self.cfg)["apps"]["sonarr"]["action"], "blocked")
        self.assertFalse(self.up.upgrade(self.cfg, "sonarr")[0])

    def test_check_has_a_cooldown(self):
        self.assertTrue(self.up.check(self.cfg)[0])
        self.assertTrue(self.up.status(self.cfg)["checking"])
        ok, msg = self.up.check(self.cfg)
        self.assertFalse(ok)
        self.assertEqual(self.calls_made(), ["check"])

    def test_log_is_capped_and_clean(self):
        with open(os.path.join(self.up.UPGRADES_DIR, "plex.log"), "w") as f:
            f.write("\x1b[1;32mok\x1b[0m\n" + "x" * 100 + "\n" + ("line\n" * 20000))
        text = self.up.log(self.cfg, "plex")
        self.assertLessEqual(len(text), self.up.LOG_MAX + 4)
        self.assertNotIn("\x1b", text)
        self.assertEqual(self.up.log(self.cfg, "../../etc/passwd"), "")

    def test_arr_already_updated_elsewhere_is_not_reinstalled(self):
        self.arr("ok")
        self.write("updates.json", {"checked": int(time.time()), "apps": {
            "sonarr": {"installed": "4.0.9.1", "latest": "4.0.10.2544", "available": True, "method": "self"}}})
        ok, msg = self.up.upgrade(self.cfg, "sonarr")                         # the app already runs 4.0.10
        self.assertFalse(ok)
        self.assertIn("already on", msg)
        self.assertEqual(self.calls_made(), ["check"])
