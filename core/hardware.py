"""
Hardware telemetry for the glass dashboard.

Everything here reads /proc, /sys or cached files, so the panel can keep running
as the unprivileged `swizzin` user. SMART data needs root, so a root systemd
timer (swizzin-smart.timer, installed by scripts/install/panel.sh) dumps
`smartctl -j -a` per drive into SMART_DIR and this module only reads those files.
"""
import glob
import json
import os
import re
import platform
import shutil
import socket
import subprocess as sp
import threading
import time

def _run(cmd, timeout):
    """stdout of cmd as text ('' on any failure). Python 3.6 compatible."""
    try:
        return sp.run(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, universal_newlines=True, timeout=timeout).stdout or ""
    except Exception:
        return ""


SMART_DIR = "/var/lib/swizzin/smart"

_lock = threading.Lock()
_prev = {"t": None, "cpu": None, "net": None, "disk": None}
_gpu_cache = {"t": 0, "data": []}
DRM = os.environ.get("SWZ_TEST_DRM", "/sys/class/drm")       # overrides only for tests
GROUP_FILE = os.environ.get("SWZ_TEST_GROUP", "/etc/group")
INSTALL_DIR = os.environ.get("SWZ_TEST_INSTALL", "/install")
HWMON = os.environ.get("SWZ_TEST_HWMON", "/sys/class/hwmon")
CPU_SYS = os.environ.get("SWZ_TEST_CPUSYS", "/sys/devices/system/cpu")
_idle_prev = {}   # card -> (time, idle ms) for the iGPU load estimate
_static_cache = {}


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def _read_int(path, default=None):
    v = _read(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- static

def static_info():
    if _static_cache:
        return _static_cache
    model, threads = None, 0
    cores = set()
    phys_id = core_id = None
    for line in (_read("/proc/cpuinfo", "") or "").splitlines():
        if ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        if k == "model name" and not model:
            model = v
        elif k == "processor":
            threads += 1
        elif k == "physical id":
            phys_id = v
        elif k == "core id":
            core_id = v
            cores.add((phys_id, core_id))
    os_name = "Linux"
    for line in (_read("/etc/os-release", "") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
    max_freq = _read_int("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    _static_cache.update({
        "hostname": socket.gethostname(),
        "os": os_name,
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpu_model": " ".join((model or platform.processor() or "Unknown CPU").split()),
        "cpu_threads": threads or os.cpu_count(),
        "cpu_cores": len(cores) or os.cpu_count(),
        "cpu_max_mhz": round(max_freq / 1000) if max_freq else None,
    })
    return _static_cache


# --------------------------------------------------------------------------- samplers

def _cpu_times():
    out = {}
    for line in (_read("/proc/stat", "") or "").splitlines():
        if not line.startswith("cpu"):
            break
        p = line.split()
        vals = [int(x) for x in p[1:9]]
        idle = vals[3] + vals[4]  # idle + iowait
        out[p[0]] = (sum(vals), idle, vals[4])
    return out


def _net_bytes():
    out = {}
    for line in (_read("/proc/net/dev", "") or "").splitlines()[2:]:
        name, data = line.split(":", 1)
        name = name.strip()
        if name == "lo":
            continue
        f = data.split()
        out[name] = (int(f[0]), int(f[8]))
    return out


def _physical_disks():
    disks = []
    for path in sorted(glob.glob("/sys/block/*")):
        name = os.path.basename(path)
        if name.startswith(("loop", "ram", "zram", "dm-", "md", "sr", "fd", "nbd")):
            continue
        if not os.path.exists(os.path.join(path, "device")):
            continue
        disks.append(name)
    return disks


def _disk_sectors(disks):
    out = {}
    for line in (_read("/proc/diskstats", "") or "").splitlines():
        f = line.split()
        if len(f) > 9 and f[2] in disks:
            out[f[2]] = (int(f[5]), int(f[9]), int(f[12]))  # read sect, write sect, ms busy
    return out


def _cpu_freqs():
    freqs = []
    for p in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"),
                    key=lambda s: int(s.split("/cpu")[2].split("/")[0])):
        v = _read_int(p)
        if v:
            freqs.append(v / 1000)
    if not freqs:
        for line in (_read("/proc/cpuinfo", "") or "").splitlines():
            if line.startswith("cpu MHz"):
                freqs.append(float(line.split(":")[1]))
    return freqs


def _temps():
    """Return cpu package temp, per-core temps, {(package, core_id): {c, high}} and every other sensor."""
    cpu_pkg, cores, by_core, others = None, [], {}, []
    for hw in sorted(glob.glob(os.path.join(HWMON, "hwmon*"))):
        name = _read(os.path.join(hw, "name"), "")
        inputs = sorted(glob.glob(os.path.join(hw, "temp*_input")), key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
        labels = {inp: _read(inp.replace("_input", "_label"), "") or name for inp in inputs}
        # coretemp has one hwmon per CPU package, labelled "Package id N"; its "Core K" labels use the
        # kernel's core_id, which is how hyper-threads are matched to their physical core below.
        pkg_id = None
        for lab in labels.values():
            m = re.match(r"Package id (\d+)", lab)
            if m:
                pkg_id = int(m.group(1))
        if pkg_id is None:
            m = re.search(r"\.(\d+)$", os.path.basename(os.path.realpath(os.path.join(hw, "device"))))
            pkg_id = int(m.group(1)) if m and name == "coretemp" else 0
        for inp in inputs:
            t = _read_int(inp)
            if t is None or t <= 0:
                continue
            t = t / 1000
            label = labels[inp]
            if name in ("coretemp", "k10temp", "zenpower", "cpu_thermal"):
                if label.startswith("Core"):
                    cores.append(round(t, 1))
                    m = re.match(r"Core\s*(\d+)", label)
                    if m:
                        high = _read_int(inp.replace("_input", "_max")) or _read_int(inp.replace("_input", "_crit"))
                        by_core[(pkg_id, int(m.group(1)))] = {"c": round(t, 1), "high": high / 1000 if high and high > 0 else None}
                elif cpu_pkg is None or label.startswith(("Package", "Tctl", "Tdie")):
                    cpu_pkg = round(t, 1)
            elif name not in ("nvme", "drivetemp", "amdgpu"):
                others.append({"name": "{} {}".format(name, label).strip(), "c": round(t, 1)})
    if cpu_pkg is None:
        for tz in glob.glob("/sys/class/thermal/thermal_zone*"):
            if "pkg" in (_read(tz + "/type", "") or "") or "cpu" in (_read(tz + "/type", "") or ""):
                v = _read_int(tz + "/temp")
                if v:
                    cpu_pkg = round(v / 1000, 1)
                    break
    return cpu_pkg, cores, by_core, others[:8]


_topology = {}


def _thread_temps(n, by_core):
    """Temperature of the physical core under each logical CPU (cpu0..cpu{n-1}), or None.

    Linux numbers hyper-threads so that siblings are usually far apart (on a 4-core/8-thread Xeon
    cpu0 and cpu4 share core 0), so this reads the real topology instead of guessing from order."""
    if not by_core:
        return []
    out = []
    for i in range(n):
        if i not in _topology:
            base = os.path.join(CPU_SYS, "cpu%d" % i, "topology")
            _topology[i] = (_read_int(base + "/physical_package_id"), _read_int(base + "/core_id"))
        pkg, core = _topology[i]
        hit = by_core.get((pkg or 0, core)) if core is not None else None
        out.append(hit)
    return out if any(out) else []


def _gpus():
    now = time.time()
    if now - _gpu_cache["t"] < 3:
        return _gpu_cache["data"]
    gpus = []
    if shutil.which("nvidia-smi"):
        try:
            q = "name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.gr,encoder.stats.sessionCount"
            out = _run(["nvidia-smi", "--query-gpu=" + q, "--format=csv,noheader,nounits"], 3)
            for line in out.strip().splitlines():
                f = [x.strip() for x in line.split(",")]

                def num(i):
                    try:
                        return float(f[i])
                    except (ValueError, IndexError):
                        return None
                gpus.append({"vendor": "NVIDIA", "name": f[0], "util": num(1), "mem_util": num(2),
                             "vram_used": num(3) and num(3) * 1048576, "vram_total": num(4) and num(4) * 1048576,
                             "temp": num(5), "power": num(6), "power_limit": num(7), "fan": num(8),
                             "clock": num(9), "encoders": num(10)})
        except Exception:
            pass
    for card in sorted(glob.glob(DRM + "/card[0-9]")):
        dev = card + "/device"
        vendor = _read(dev + "/vendor", "")
        if vendor == "0x10de" and gpus:
            continue  # already covered by nvidia-smi
        if vendor not in ("0x1002", "0x8086", "0x10de"):
            continue
        slot = os.path.basename(os.path.realpath(dev))
        g = {"vendor": {"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA"}[vendor],
             "name": _read(dev + "/product_name") or _lspci_name(dev) or None,
             "util": _read_int(dev + "/gpu_busy_percent"),
             "vram_used": _read_int(dev + "/mem_info_vram_used"),
             "vram_total": _read_int(dev + "/mem_info_vram_total"),
             "temp": None, "power": None, "fan": None, "clock": None, "slot": slot,
             "driver": os.path.basename(os.path.realpath(dev + "/driver")) if os.path.exists(dev + "/driver") else None}
        for hw in glob.glob(dev + "/hwmon/hwmon*"):
            t = _read_int(hw + "/temp1_input")
            if t:
                g["temp"] = t / 1000
            p = _read_int(hw + "/power1_average") or _read_int(hw + "/power1_input")
            if p:
                g["power"] = p / 1e6
            fan = _read_int(hw + "/fan1_input")
            if fan:
                g["fan_rpm"] = fan
        cur = _read(dev + "/pp_dpm_sclk", "")
        for line in (cur or "").splitlines():
            if line.endswith("*"):
                try:
                    g["clock"] = float(line.split()[1].lower().replace("mhz", ""))
                except Exception:
                    pass
        # Integrated graphics: Intel's iGPU always sits at PCI 00:02.0; an AMD APU reports a small
        # carve-out (<= 2 GiB) as "VRAM" where discrete cards have their own memory.
        g["integrated"] = (vendor == "0x8086" and slot.endswith(":00:02.0")) or \
            (vendor == "0x1002" and g["vram_total"] is not None and g["vram_total"] <= 2 * 2 ** 30)
        if vendor == "0x8086":
            _intel_metrics(card, g, now)
        if not g["name"]:
            try:
                from core import specs as _specs      # pci.ids lookup when lspci isn't installed
                g["name"] = _specs.pci_name(dev)
            except Exception:
                pass
        if g["integrated"] and not g["name"]:
            pid = (_read(dev + "/device") or "").replace("0x", "")
            g["name"] = "{} integrated graphics{}".format(g["vendor"], " ({}:{})".format(vendor[2:], pid) if pid else "")
        g["name"] = g["name"] or "GPU"
        render = _render_node(card)
        if render:
            g["render_node"] = render
            g["hw_transcode"] = _plex_can_use(render)
        gpus.append(g)
    _gpu_cache.update(t=now, data=gpus)
    return gpus


def _first_int(paths):
    for p in paths:
        v = _read_int(p)
        if v is not None:
            return v, p
    return None, None


def _intel_metrics(card, g, now):
    """Load and clock for Intel graphics from sysfs (no root, no intel_gpu_top).

    Load comes from the GPU's idle (RC6) residency: the share of wall time the GPU was NOT asleep
    since the last reading. Media engines (Quick Sync) count, since they keep the GT awake; on
    chips with a separate media GT (Meteor Lake+) the busier GT is shown."""
    dev = card + "/device"
    gts = sorted(glob.glob(card + "/gt/gt[0-9]")) or [None]
    best = None
    for gt in gts:
        cands = ([gt + "/rc6_residency_ms"] if gt else []) + [card + "/power/rc6_residency_ms"]
        xe = sorted(glob.glob(dev + "/tile0/gt[0-9]/gtidle/idle_residency_ms"))
        idle, path = _first_int(cands + xe)
        if idle is None:
            continue
        prev = _idle_prev.get(path)
        _idle_prev[path] = (now, idle)
        if prev and now > prev[0]:
            busy = 100.0 * (1 - (idle - prev[1]) / ((now - prev[0]) * 1000.0))
            busy = max(0.0, min(100.0, busy))
            best = busy if best is None else max(best, busy)
    if g["util"] is None:
        g["util"] = None if best is None else round(best, 1)
    gt0 = gts[0]
    act, _ = _first_int(([gt0 + "/rps_act_freq_mhz"] if gt0 else []) +
                        [card + "/gt_act_freq_mhz", card + "/gt_cur_freq_mhz"] +
                        sorted(glob.glob(dev + "/tile0/gt0/freq0/act_freq")))
    mx, _ = _first_int(([gt0 + "/rps_RP0_freq_mhz"] if gt0 else []) +
                       [card + "/gt_RP0_freq_mhz", card + "/gt_max_freq_mhz"] +
                       sorted(glob.glob(dev + "/tile0/gt0/freq0/rp0_freq")) +
                       sorted(glob.glob(dev + "/tile0/gt0/freq0/max_freq")))
    if act is not None:
        g["clock"] = act
    if mx:
        g["clock_max"] = mx


def _render_node(card):
    nodes = sorted(glob.glob(card + "/device/drm/renderD*"))
    return "/dev/dri/" + os.path.basename(nodes[0]) if nodes else None


def _plex_can_use(render):
    """Can Plex hardware-transcode on this GPU? Plex has to be a member of the group that owns
    the render node (usually "render", sometimes "video"). None when Plex isn't installed."""
    if not os.path.exists(INSTALL_DIR + "/.plex.lock"):
        return None
    try:
        import grp
        group = grp.getgrgid(os.stat(render).st_gid).gr_name
    except Exception:
        group = "render"
    members = set()
    for line in (_read(GROUP_FILE, "") or "").splitlines():
        f = line.split(":")
        if len(f) >= 4 and f[0] == group:
            members = set(filter(None, f[3].split(",")))
    return {"group": group, "ok": "plex" in members}


def _lspci_name(dev):
    slot = os.path.basename(os.path.realpath(dev))
    if not shutil.which("lspci"):
        return None
    try:
        out = _run(["lspci", "-s", slot, "-mm"], 2)
        parts = out.split('"')
        return "{} {}".format(parts[3], parts[5]).strip() if len(parts) > 5 else None
    except Exception:
        return None


def _mem():
    m = {}
    for line in (_read("/proc/meminfo", "") or "").splitlines():
        k, v = line.split(":", 1)
        m[k] = int(v.split()[0]) * 1024
    total = m.get("MemTotal", 0)
    avail = m.get("MemAvailable", 0)
    cached = m.get("Cached", 0) + m.get("SReclaimable", 0) - m.get("Shmem", 0)
    buffers = m.get("Buffers", 0)
    return {"total": total, "used": total - avail, "available": avail,
            "apps": max(total - avail, 0), "cache": max(cached + buffers, 0),
            "swap_total": m.get("SwapTotal", 0), "swap_used": m.get("SwapTotal", 0) - m.get("SwapFree", 0)}


def _default_iface():
    for line in (_read("/proc/net/route", "") or "").splitlines()[1:]:
        f = line.split()
        if f[1] == "00000000" and int(f[3], 16) & 2:
            return f[0]
    return None


# --------------------------------------------------------------------------- public

def live():
    """Fast-changing numbers. Rates are computed against the previous call."""
    now = time.monotonic()
    cpu = _cpu_times()
    net = _net_bytes()
    disks = _physical_disks()
    mds = [os.path.basename(p) for p in glob.glob("/sys/block/md*")]
    dsk = _disk_sectors(disks + mds)
    with _lock:
        prev = dict(_prev)
        _prev.update(t=now, cpu=cpu, net=net, disk=dsk)
    dt = (now - prev["t"]) if prev["t"] else None

    def pct(name):
        if not prev["cpu"] or name not in prev["cpu"] or name not in cpu:
            return None
        tot = cpu[name][0] - prev["cpu"][name][0]
        idle = cpu[name][1] - prev["cpu"][name][1]
        return round(100 * (tot - idle) / tot, 1) if tot > 0 else 0.0

    iowait = None
    if prev["cpu"] and "cpu" in prev["cpu"]:
        tot = cpu["cpu"][0] - prev["cpu"]["cpu"][0]
        iowait = round(100 * (cpu["cpu"][2] - prev["cpu"]["cpu"][2]) / tot, 1) if tot > 0 else 0.0

    cores = [pct("cpu%d" % i) for i in range(len(cpu) - 1)]
    iface = _default_iface()
    netrates = {}
    for name, (rx, tx) in net.items():
        r = {"rx_total": rx, "tx_total": tx, "rx": None, "tx": None}
        if dt and prev["net"] and name in prev["net"]:
            r["rx"] = max(rx - prev["net"][name][0], 0) / dt
            r["tx"] = max(tx - prev["net"][name][1], 0) / dt
        netrates[name] = r

    disk_io = {}
    for name, (rs, ws, busy) in dsk.items():
        d = {"read": None, "write": None, "busy": None}
        if dt and prev["disk"] and name in prev["disk"]:
            p = prev["disk"][name]
            d["read"] = max(rs - p[0], 0) * 512 / dt
            d["write"] = max(ws - p[1], 0) * 512 / dt
            d["busy"] = min(round(max(busy - p[2], 0) / (dt * 10), 1), 100)
        disk_io[name] = d

    pkg, core_temps, by_core, other_temps = _temps()
    freqs = _cpu_freqs()
    load = (_read("/proc/loadavg", "0 0 0") or "0 0 0").split()[:3]
    uptime = float((_read("/proc/uptime", "0") or "0").split()[0])
    return {
        "ts": time.time(),
        "uptime": uptime,
        "load": [float(x) for x in load],
        "cpu": {"total": pct("cpu"), "iowait": iowait, "cores": cores,
                "freq_avg": round(sum(freqs) / len(freqs)) if freqs else None,
                "freq_max": round(max(freqs)) if freqs else None,
                "temp": pkg, "core_temps": core_temps, "thread_temps": _thread_temps(len(cores), by_core)},
        "sensors": other_temps,
        "mem": _mem(),
        "net": {"iface": iface, "ifaces": netrates},
        "disk_io": disk_io,
        "gpus": _gpus(),
    }


def _attr(table, *ids):
    """First attribute present, in the priority order given by ids."""
    by_id = {a.get("id"): a for a in table}
    for i in ids:
        if i in by_id:
            return by_id[i]
    return None


def _parse_smart(name, j):
    dev = j.get("device", {})
    proto = (dev.get("protocol") or dev.get("type") or "").upper()
    rot = j.get("rotation_rate")
    kind = "NVMe" if "NVME" in proto else ("HDD" if rot and rot > 0 else "SSD")
    cap = (j.get("user_capacity") or {}).get("bytes")
    if not cap and j.get("nvme_total_capacity"):
        cap = j["nvme_total_capacity"]
    d = {"name": name, "kind": kind,
         "model": j.get("model_name") or j.get("model_family") or "Unknown",
         "firmware": j.get("firmware_version"),
         "capacity": cap,
         "rpm": rot if kind == "HDD" else None,
         "healthy": (j.get("smart_status") or {}).get("passed"),
         "temp": (j.get("temperature") or {}).get("current"),
         "power_on_hours": (j.get("power_on_time") or {}).get("hours"),
         "power_cycles": j.get("power_cycle_count"),
         "wear_used": None, "spare": None, "written": None, "read": None,
         "media_errors": None, "unsafe_shutdowns": None,
         "realloc": None, "pending": None, "uncorrectable": None, "crc": None}
    nv = j.get("nvme_smart_health_information_log")
    if nv:
        d.update(wear_used=nv.get("percentage_used"), spare=nv.get("available_spare"),
                 written=(nv.get("data_units_written") or 0) * 512000,
                 read=(nv.get("data_units_read") or 0) * 512000,
                 media_errors=nv.get("media_errors"), unsafe_shutdowns=nv.get("unsafe_shutdowns"))
        if d["temp"] is None:
            d["temp"] = nv.get("temperature")
        return d
    table = (j.get("ata_smart_attributes") or {}).get("table", [])
    lb = j.get("logical_block_size") or 512

    def raw(*ids):
        a = _attr(table, *ids)
        return (a.get("raw") or {}).get("value") if a else None

    if kind != "HDD":
        # Remaining-life style attributes report a normalized value of 100 -> 0.
        life = _attr(table, 231, 233, 202, 177, 169, 173)
        if life and life.get("value") is not None and life["value"] <= 100:
            d["wear_used"] = max(0, 100 - life["value"])
    lbas = raw(241)
    if lbas is not None:
        name241 = (_attr(table, 241) or {}).get("name", "")
        if "GiB" in name241 or "_32MiB" in name241:
            d["written"] = lbas * (1073741824 if "GiB" in name241 else 33554432)
        else:
            d["written"] = lbas * lb
    lbr = raw(242)
    if lbr is not None:
        d["read"] = lbr * lb
    d.update(realloc=raw(5), pending=raw(197), uncorrectable=raw(198), crc=raw(199),
             unsafe_shutdowns=raw(192, 174))
    # Attributes that predict HDD failure (Backblaze / smartmontools guidance), in display order.
    watch = {1: "count-vendor", 3: "info", 4: "info", 5: "bad", 7: "count-vendor", 9: "info", 10: "bad",
             12: "info", 184: "bad", 187: "bad", 188: "bad", 190: "info", 194: "info", 196: "bad",
             197: "bad", 198: "bad", 199: "bad", 200: "bad"}
    attrs = []
    for a in table:
        aid = a.get("id")
        if aid not in watch:
            continue
        rawv = (a.get("raw") or {}).get("value")
        raws = (a.get("raw") or {}).get("string") or ("" if rawv is None else str(rawv))
        val, thr = a.get("value"), a.get("thresh")
        failed = bool(a.get("when_failed")) or (thr not in (None, 0) and val is not None and val <= thr)
        if failed:
            state = "crit"
        elif watch[aid] == "bad" and rawv:
            state = "warn"
        else:
            state = "good"
        attrs.append({"id": aid, "name": (a.get("name") or "").replace("_", " "), "value": val,
                      "worst": a.get("worst"), "thresh": thr, "raw": raws.split(" ")[0],
                      "prefail": bool((a.get("flags") or {}).get("prefailure")), "state": state})
    order = list(watch)
    d["attrs"] = sorted(attrs, key=lambda x: order.index(x["id"]))
    d["self_test"] = None
    log = (j.get("ata_smart_self_test_log") or {}).get("standard", {}).get("table") or []
    if log:
        t0 = log[0]
        d["self_test"] = {"type": (t0.get("type") or {}).get("string"),
                          "passed": (t0.get("status") or {}).get("passed"),
                          "status": (t0.get("status") or {}).get("string"),
                          "hours": t0.get("lifetime_hours")}
    return d


def drives():
    """Slow-changing drive inventory + SMART health from the root collector."""
    out = []
    for name in _physical_disks():
        base = "/sys/block/" + name
        size = (_read_int(base + "/size") or 0) * 512
        rot = _read_int(base + "/queue/rotational")
        model = _read(base + "/device/model") or _read(base + "/device/../model") or name
        model = " ".join(model.split())
        hw = any(k in model.upper() for k in HW_RAID_MODELS)
        d = {"name": name, "kind": "RAID" if hw else "NVMe" if name.startswith("nvme") else ("HDD" if rot else "SSD"),
             "model": model, "capacity": size, "smart": False, "hw_raid": hw}
        path = os.path.join(SMART_DIR, name + ".json")
        try:
            with open(path) as f:
                j = json.load(f)
            parsed = _parse_smart(name, j)
            parsed["capacity"] = parsed["capacity"] or size
            parsed["smart"] = True
            parsed["smart_age"] = round(time.time() - os.path.getmtime(path))
            parsed["hw_raid"] = hw
            if hw:
                parsed["kind"] = "RAID"
            d = parsed
        except Exception:
            pass
        out.append(d)
    # physical disks behind a hardware RAID controller (collector writes hw-<type>-<n>.json)
    for path in sorted(glob.glob(os.path.join(SMART_DIR, "hw-*.json"))):
        try:
            with open(path) as f:
                j = json.load(f)
            tag = os.path.basename(path)[3:-5]                     # e.g. megaraid-0
            parsed = _parse_smart(tag, j)
            parsed.update(smart=True, behind_controller=True, name=tag.replace("-", ",", 1),
                          smart_age=round(time.time() - os.path.getmtime(path)))
            out.append(parsed)
        except Exception:
            pass
    r = raid()
    for d in out:
        d["arrays"] = r["member_of"].get(d["name"], [])
    return out


def mounts_usage(mounts):
    res = []
    for m in mounts:
        try:
            u = shutil.disk_usage(m)
            res.append({"mount": m, "total": u.total, "used": u.used, "free": u.free})
        except Exception:
            pass
    return res


def system():
    info = dict(static_info())
    info["tcp_cc"] = _read("/proc/sys/net/ipv4/tcp_congestion_control")
    info["qdisc"] = _read("/proc/sys/net/core/default_qdisc")
    iface = _default_iface()
    info["iface"] = iface
    info["link_speed"] = _read_int("/sys/class/net/{}/speed".format(iface)) if iface else None
    info["boot"] = time.time() - float((_read("/proc/uptime", "0") or "0").split()[0])
    return info


# --------------------------------------------------------------------------- traffic (vnstat)

_traffic_cache = {"t": 0, "data": None}


def _vnstat_json(mode):
    out = _run(["vnstat", "--json", mode], 10)
    return json.loads(out)


def traffic():
    """Upload/download totals per day, week, month and all time from vnstat's database."""
    now = time.time()
    if _traffic_cache["data"] and now - _traffic_cache["t"] < 60:
        return _traffic_cache["data"]
    if not shutil.which("vnstat"):
        return {"available": False, "reason": "vnstat is not installed"}
    try:
        dj = _vnstat_json("d")
        mj = _vnstat_json("m")
        try:
            hj = _vnstat_json("h")
        except Exception:
            hj = {}
    except Exception as e:
        return {"available": False, "reason": "vnstat failed: {}".format(e)}

    v2 = str(dj.get("jsonversion", "1")) == "2"
    mult = 1 if v2 else 1024  # vnstat 1.x reports KiB
    want = _default_iface()

    def pick(j):
        ifs = j.get("interfaces") or []
        for i in ifs:
            if i.get("name") == want or i.get("id") == want:
                return i
        return ifs[0] if ifs else None

    di, mi = pick(dj), pick(mj)
    if not di:
        return {"available": False, "reason": "vnstat has no interfaces yet"}
    t = di.get("traffic", {})
    days_raw = t.get("day" if v2 else "days") or []
    months_raw = (mi or {}).get("traffic", {}).get("month" if v2 else "months") or []

    import datetime as dt
    days = {}
    for d in days_raw:
        dd = d.get("date", {})
        try:
            key = dt.date(dd["year"], dd["month"], dd["day"])
        except Exception:
            continue
        days[key] = (d.get("rx", 0) * mult, d.get("tx", 0) * mult)
    months = {}
    for m in months_raw:
        md = m.get("date", {})
        if "year" in md and "month" in md:
            months[(md["year"], md["month"])] = (m.get("rx", 0) * mult, m.get("tx", 0) * mult)

    today = dt.date.today()

    def total(keys):
        rx = sum(days.get(k, (0, 0))[0] for k in keys)
        tx = sum(days.get(k, (0, 0))[1] for k in keys)
        return {"rx": rx, "tx": tx}

    week_start = today - dt.timedelta(days=today.weekday())  # Monday
    last7 = []
    for i in range(6, -1, -1):
        d = today - dt.timedelta(days=i)
        rx, tx = days.get(d, (0, 0))
        last7.append({"date": d.isoformat(), "rx": rx, "tx": tx})
    month = months.get((today.year, today.month))
    if month is None:
        month = (sum(v[0] for k, v in days.items() if k.year == today.year and k.month == today.month),
                 sum(v[1] for k, v in days.items() if k.year == today.year and k.month == today.month))
    pm = (today.replace(day=1) - dt.timedelta(days=1))
    prev_month = months.get((pm.year, pm.month), (0, 0))
    tot = t.get("total", {})
    created = di.get("created", {}).get("date") if v2 else di.get("created", {}).get("date")
    since = None
    if created and "year" in created:
        since = "{:04d}-{:02d}-{:02d}".format(created["year"], created.get("month", 1), created.get("day", 1))

    # last 24 hours (vnstat 2: traffic.hour with date+time; vnstat 1: traffic.hours with id = hour)
    hi = pick(hj) if hj else None
    hours_raw = (hi or {}).get("traffic", {}).get("hour" if v2 else "hours") or []
    hmap = {}
    for h in hours_raw:
        hd = h.get("date", {})
        hr = (h.get("time") or {}).get("hour", h.get("id"))
        try:
            hmap[dt.datetime(hd["year"], hd["month"], hd["day"], int(hr))] = (h.get("rx", 0) * mult, h.get("tx", 0) * mult)
        except Exception:
            continue
    now_h = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    hours24 = []
    for i in range(23, -1, -1):
        t = now_h - dt.timedelta(hours=i)
        rx, tx = hmap.get(t, (0, 0))
        hours24.append({"time": t.strftime("%Y-%m-%dT%H:00"), "rx": rx, "tx": tx})

    # this calendar month day by day (vnstat 2 keeps 62 days by default), and a straight-line
    # projection of the month's total from the days so far
    import calendar as _cal
    month_days = []
    for dnum in range(1, today.day + 1):
        dd = today.replace(day=dnum)
        rx, tx = days.get(dd, (0, 0))
        month_days.append({"date": dd.isoformat(), "rx": rx, "tx": tx, "known": dd in days})
    dim = _cal.monthrange(today.year, today.month)[1]
    elapsed = today.day - 1 + (dt.datetime.now().hour * 3600 + dt.datetime.now().minute * 60) / 86400.0
    proj = None
    if elapsed >= 1:
        proj = {"rx": month[0] / elapsed * dim, "tx": month[1] / elapsed * dim}

    data = {
        "available": True,
        "hours": hours24,
        "month_days": month_days,
        "month_len": dim,
        "month_projection": proj,
        "iface": di.get("name") or di.get("id"),
        "since": since,
        "today": total([today]),
        "yesterday": total([today - dt.timedelta(days=1)]),
        "week": total([week_start + dt.timedelta(days=i) for i in range((today - week_start).days + 1)]),
        "last7": {"rx": sum(d["rx"] for d in last7), "tx": sum(d["tx"] for d in last7)},
        "month": {"rx": month[0], "tx": month[1]},
        "prev_month": {"rx": prev_month[0], "tx": prev_month[1]},
        "all": {"rx": tot.get("rx", 0) * mult, "tx": tot.get("tx", 0) * mult},
        "days": last7,
        "months": [{"month": "{:04d}-{:02d}".format(y, m), "rx": v[0], "tx": v[1]}
                   for (y, m), v in sorted(months.items())[-12:]],
    }
    _traffic_cache.update(t=now, data=data)
    return data


# --------------------------------------------------------------------------- fail2ban

F2B_FILE = "/var/lib/swizzin/fail2ban.json"


def fail2ban():
    """Recent bans written by the root swizzin-f2b.timer (fail2ban's socket and DB are root-only)."""
    try:
        with open(F2B_FILE) as f:
            data = json.load(f)
        data["available"] = True
        data["age"] = round(time.time() - os.path.getmtime(F2B_FILE))
        return data
    except FileNotFoundError:
        return {"available": False, "reason": "collector not installed"}
    except Exception as e:
        return {"available": False, "reason": str(e)}


# --------------------------------------------------------------------------- RAID (mdadm, hardware, ZFS)

MDSTAT = os.environ.get("SWZ_TEST_MDSTAT", "/proc/mdstat")   # override only for tests
HW_RAID_MODELS = ("PERC", "MEGARAID", "MR9", "LSI", "LOGICAL VOLUME", "SMART ARRAY", "RAID", "AVAGO", "ADAPTEC", "ARECA", "HPSA", "VIRTUAL DISK")


def _parent_disk(part):
    """sda3 -> sda, nvme0n1p2 -> nvme0n1 (sysfs first, name rules as fallback)."""
    try:
        real = os.path.realpath("/sys/class/block/" + part)
        if os.path.exists(os.path.join(real, "partition")):
            return os.path.basename(os.path.dirname(real))
    except Exception:
        pass
    import re
    m = re.match(r"^((?:nvme\d+n\d+|mmcblk\d+))p\d+$", part) or re.match(r"^([a-z]+)\d+$", part)
    return m.group(1) if m else part


def mdraid():
    import re
    text = _read(MDSTAT, "") or ""
    arrays, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^(md\w+)\s*:\s*(\w+)\s*(?:\((?:auto-)?read-only\)\s*)?(raid\d+|linear|multipath)?\s*(.*)$", line)
        if m:
            name, state, level, rest = m.groups()
            members = []
            for mm in re.finditer(r"(\S+?)\[(\d+)\](\((?:F|S|W|R)\))?", rest):
                dev, slot, flag = mm.groups()
                members.append({"dev": dev, "disk": _parent_disk(dev), "slot": int(slot),
                                "failed": flag == "(F)", "spare": flag == "(S)"})
            cur = {"name": name, "state": state, "level": level or "unknown", "members": members,
                   "size": None, "want": None, "have": None, "map": None, "action": None, "progress": None,
                   "finish": None, "speed": None}
            arrays.append(cur)
            continue
        if cur is None:
            continue
        m = re.search(r"(\d+) blocks", line)
        if m and cur["size"] is None:
            cur["size"] = int(m.group(1)) * 1024
        m = re.search(r"\[(\d+)/(\d+)\]\s*\[([U_]+)\]", line)
        if m:
            cur["want"], cur["have"], cur["map"] = int(m.group(1)), int(m.group(2)), m.group(3)
        m = re.search(r"(recovery|resync|reshape|check|repair)\s*=\s*([\d.]+)%.*?finish=([\d.]+)min(?:.*?speed=(\d+)K/sec)?", line)
        if m:
            cur["action"], cur["progress"] = m.group(1), float(m.group(2))
            cur["finish"] = float(m.group(3)) * 60
            cur["speed"] = int(m.group(4)) * 1024 if m.group(4) else None
        elif re.search(r"(resync|recovery)\s*=\s*(DELAYED|PENDING)", line):
            cur["action"] = "pending"
    for a in arrays:
        failed = any(x["failed"] for x in a["members"])
        degraded = bool(a["map"] and "_" in a["map"]) or failed
        a["health"] = ("rebuilding" if a["action"] in ("recovery", "resync", "reshape", "pending") and degraded else
                       "checking" if a["action"] in ("check", "repair", "resync") else
                       "degraded" if degraded else "inactive" if a["state"] != "active" else "healthy")
    return arrays


def zfs_pools():
    if not shutil.which("zpool"):
        return []
    out = _run(["zpool", "list", "-H", "-p", "-o", "name,size,alloc,free,health,frag,cap"], 5)
    pools = []
    for line in out.strip().splitlines():
        f = line.split("\t")
        if len(f) >= 7:
            def num(x):
                try:
                    return int(x)
                except ValueError:
                    return None
            pools.append({"name": f[0], "size": num(f[1]), "alloc": num(f[2]), "free": num(f[3]),
                          "health": f[4], "frag": num(f[5].rstrip("%")), "cap": num(f[6].rstrip("%"))})
    return pools


def raid():
    md = mdraid()
    member_of = {}
    for a in md:
        for m in a["members"]:
            member_of.setdefault(m["disk"], []).append(a["name"] + (" ✗" if m["failed"] else ""))
    return {"md": md, "zfs": zfs_pools(), "member_of": member_of}


# --------------------------------------------------------------------------- processes (btop-style)

_proc_prev = {"total": None, "ticks": {}}
_users = {}


def _user(uid):
    if uid not in _users:
        try:
            import pwd
            _users[uid] = pwd.getpwuid(uid).pw_name
        except Exception:
            _users[uid] = str(uid)
    return _users[uid]


def processes(limit=40):
    """Top processes by CPU and memory. CPU% is per core like top/btop (can exceed 100 % for
    multi-threaded programs); computed against the previous call."""
    total = _cpu_times().get("cpu", (0, 0, 0))[0]
    ncpu = os.cpu_count() or 1
    page = os.sysconf("SC_PAGE_SIZE")
    mem_total = _mem()["total"] or 1
    prev_total, prev = _proc_prev["total"], _proc_prev["ticks"]
    dt_core = (total - prev_total) / ncpu if prev_total else 0
    rows, ticks = [], {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % pid) as f:
                st = f.read()
            comm = st[st.index("(") + 1:st.rindex(")")]
            f2 = st[st.rindex(")") + 2:].split()
            utime, stime, threads, start, rss = int(f2[11]), int(f2[12]), int(f2[17]), int(f2[19]), int(f2[21])
            key = (pid, start)
            t = utime + stime
            ticks[key] = t
            cpu = 100.0 * (t - prev[key]) / dt_core if dt_core > 0 and key in prev else 0.0
            uid = None
            with open("/proc/%s/status" % pid) as f:
                for line in f:
                    if line.startswith("Uid:"):
                        uid = int(line.split()[1])
                        break
            rows.append({"pid": int(pid), "name": comm, "state": f2[0], "threads": threads,
                         "cpu": round(max(cpu, 0.0), 1), "rss": rss * page, "mem": round(100.0 * rss * page / mem_total, 2),
                         "user": _user(uid) if uid is not None else "?", "key": key})
        except Exception:
            continue   # process exited while we read it, or no permission
    _proc_prev.update(total=total, ticks=ticks)
    by_cpu = sorted(rows, key=lambda r: (r["cpu"], r["rss"]), reverse=True)[:limit]
    by_mem = sorted(rows, key=lambda r: r["rss"], reverse=True)[:limit]
    pick = {r["key"]: r for r in by_cpu + by_mem}
    for r in pick.values():
        try:
            with open("/proc/%d/cmdline" % r["pid"], "rb") as f:
                r["cmd"] = f.read(400).replace(b"\0", b" ").decode("utf-8", "replace").strip() or "[" + r["name"] + "]"
        except Exception:
            r["cmd"] = r["name"]
        r.pop("key", None)
    return {"count": len(rows), "warming": dt_core <= 0, "procs": list(pick.values())}
