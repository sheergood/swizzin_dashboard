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
    """Return cpu package temp, per-core temps and every other sensor found."""
    cpu_pkg, cores, others = None, [], []
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = _read(os.path.join(hw, "name"), "")
        for inp in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
            t = _read_int(inp)
            if t is None or t <= 0:
                continue
            t = t / 1000
            label = _read(inp.replace("_input", "_label"), "") or name
            if name in ("coretemp", "k10temp", "zenpower", "cpu_thermal"):
                if label.startswith("Core"):
                    cores.append(round(t, 1))
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
    return cpu_pkg, cores, others[:8]


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
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]")):
        dev = card + "/device"
        vendor = _read(dev + "/vendor", "")
        if vendor == "0x10de" and gpus:
            continue  # already covered by nvidia-smi
        if vendor not in ("0x1002", "0x8086", "0x10de"):
            continue
        g = {"vendor": {"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA"}[vendor],
             "name": _read(dev + "/product_name") or _lspci_name(dev) or "GPU",
             "util": _read_int(dev + "/gpu_busy_percent"),
             "vram_used": _read_int(dev + "/mem_info_vram_used"),
             "vram_total": _read_int(dev + "/mem_info_vram_total"),
             "temp": None, "power": None, "fan": None, "clock": None}
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
        gpus.append(g)
    _gpu_cache.update(t=now, data=gpus)
    return gpus


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
    dsk = _disk_sectors(disks)
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

    pkg, core_temps, other_temps = _temps()
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
                "temp": pkg, "core_temps": core_temps},
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
        d = {"name": name, "kind": "NVMe" if name.startswith("nvme") else ("HDD" if rot else "SSD"),
             "model": " ".join(model.split()), "capacity": size, "smart": False}
        path = os.path.join(SMART_DIR, name + ".json")
        try:
            with open(path) as f:
                j = json.load(f)
            parsed = _parse_smart(name, j)
            parsed["capacity"] = parsed["capacity"] or size
            parsed["smart"] = True
            parsed["smart_age"] = round(time.time() - os.path.getmtime(path))
            d = parsed
        except Exception:
            pass
        out.append(d)
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

    data = {
        "available": True,
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
