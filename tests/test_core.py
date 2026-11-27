"""Unit tests for the panel's calculations and failure handling (stdlib unittest, no root, no network).

    python -m unittest discover -s tests -v
"""
import datetime as dt
import json
import os
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main(verbosity=2)


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
