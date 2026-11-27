"""Server specifications for the header popover: board, BIOS, CPU, RAM modules, drives, NICs, GPU.

Everything here is read without root. Memory module details need dmidecode, so they come from
/var/lib/swizzin/hostinfo.json, written hourly by the root swizzin-host timer; until it exists the
popover shows the total only. Every lookup is optional: a missing file or tool removes a row,
never the whole popover.
"""
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import time

from core import hardware as hw

DMI = os.environ.get("SWZ_TEST_DMI", "/sys/class/dmi/id")
NET = os.environ.get("SWZ_TEST_NET", "/sys/class/net")
HOSTINFO = os.environ.get("SWZ_TEST_HOSTINFO", "/var/lib/swizzin/hostinfo.json")
PCI_IDS = ("/usr/share/misc/pci.ids", "/usr/share/hwdata/pci.ids", "/usr/share/pci.ids")

_cache = {"t": 0, "data": None}
_pci_names = {}

# Placeholder strings board vendors leave in SMBIOS
_JUNK = re.compile(r"^(|0+|none|n/?a|not (specified|applicable|available)|default string|to be filled by o\.?e\.?m\.?|"
                   r"system (product name|manufacturer|version)|type\d+ - board.*|base board.*|x\.x+|123456789|unknown)$", re.I)

# JEDEC manufacturer codes as dmidecode prints them, plus part-number prefixes, for RAM vendors
_JEDEC = {"2c00": "Micron", "802c": "Micron", "ce00": "Samsung", "80ce": "Samsung", "ad00": "SK hynix", "80ad": "SK hynix",
          "0198": "Kingston", "9801": "Kingston", "859b": "Crucial", "9b85": "Crucial", "029e": "Corsair", "9e02": "Corsair",
          "04cd": "G.Skill", "cd04": "G.Skill", "830b": "Nanya", "0b83": "Nanya", "04cb": "ADATA", "cb04": "ADATA",
          "014f": "Transcend", "4f01": "Transcend", "0443": "Ramaxel", "4304": "Ramaxel", "8551": "Qimonda", "5185": "Qimonda",
          "80c1": "Infineon", "c180": "Infineon"}
_PART_PREFIX = (("MTA", "Micron"), ("MT", "Micron"), ("8KTF", "Micron"), ("16KTF", "Micron"), ("18KSF", "Micron"),
                ("36KSF", "Micron"), ("M3", "Samsung"), ("M4", "Samsung"), ("M471", "Samsung"), ("HMT", "SK hynix"),
                ("HMA", "SK hynix"), ("HMAA", "SK hynix"), ("HMCG", "SK hynix"), ("KVR", "Kingston"), ("KHX", "Kingston"),
                ("KSM", "Kingston"), ("CT", "Crucial"), ("BL", "Crucial"), ("CM", "Corsair"), ("F4-", "G.Skill"), ("F3-", "G.Skill"))


def _clean(v):
    v = " ".join((v or "").split())
    return None if _JUNK.match(v) else v


def _dmi(name):
    return _clean(hw._read(os.path.join(DMI, name)))


def _tidy_vendor(v):
    v = re.sub(r",?\s*(Corporation|Corp\.?|Inc\.?|Co\.,? ?Ltd\.?|Ltd\.?|Technology|Semiconductor|Computer|International|"
               r"Electronics|Systems|Limited|GmbH|LLC)\b\.?", "", v or "", flags=re.I)
    return " ".join(v.replace("Super Micro", "Supermicro").split()).strip(" ,")


def _pci_ids_lookup(vendor, device):
    key = (vendor, device)
    if key in _pci_names:
        return _pci_names[key]
    name = None
    for path in PCI_IDS:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                in_vendor, vname = False, None
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    if not line.startswith("\t"):
                        if in_vendor:
                            break
                        if line[:4].lower() == vendor:
                            in_vendor, vname = True, line[4:].strip()
                    elif in_vendor and not line.startswith("\t\t") and line[1:5].lower() == device:
                        name = "{} {}".format(_tidy_vendor(vname), line[5:].strip())
                        break
            if name:
                break
        except OSError:
            continue
    _pci_names[key] = name
    return name


def pci_name(dev_path):
    """Human name for a PCI device: lspci first, then the pci.ids database, else None."""
    slot = os.path.basename(os.path.realpath(dev_path))
    if shutil.which("lspci"):
        try:
            out = hw._run(["lspci", "-s", slot, "-mm"], 3)
            parts = out.split('"')
            if len(parts) > 5 and parts[5].strip():
                return "{} {}".format(_tidy_vendor(parts[3]), re.sub(r"\s*\(rev [0-9a-f]+\)", "", parts[5])).strip()
        except Exception:
            pass
    vendor = (hw._read(dev_path + "/vendor") or "").replace("0x", "").lower()
    device = (hw._read(dev_path + "/device") or "").replace("0x", "").lower()
    if len(vendor) == 4 and len(device) == 4:
        return _pci_ids_lookup(vendor, device)
    return None


# --------------------------------------------------------------------------- sections

def board():
    vendor, name, ver = _dmi("board_vendor"), _dmi("board_name"), _dmi("board_version")
    product, sys_vendor = _dmi("product_name"), _dmi("sys_vendor")
    out = {}
    if name:
        out["motherboard"] = " ".join(filter(None, [_tidy_vendor(vendor) if vendor else None, name]))
        if ver:
            out["motherboard_version"] = ver
    if product and product != name:
        out["system"] = " ".join(filter(None, [_tidy_vendor(sys_vendor) if sys_vendor and sys_vendor not in product else None, product]))
    bios_ver, bios_date, bios_vendor = _dmi("bios_version"), _dmi("bios_date"), _dmi("bios_vendor")
    if bios_ver:
        out["bios"] = bios_ver
        out["bios_detail"] = " · ".join(filter(None, [_tidy_vendor(bios_vendor) if bios_vendor else None, bios_date]))
    if not out and hw._read("/sys/hypervisor/type"):
        out["system"] = "Virtual machine ({})".format(hw._read("/sys/hypervisor/type"))
    return out


def _ram_vendor(m):
    raw = (m.get("manufacturer") or "").strip()
    code = raw.lower().replace("0x", "")[:4]
    if code in _JEDEC:
        return _JEDEC[code]
    if raw and not re.fullmatch(r"(0x)?[0-9A-Fa-f]{2,16}", raw):
        return raw
    part = (m.get("part") or "").upper()
    for prefix, vendor in _PART_PREFIX:
        if part.startswith(prefix):
            return vendor
    return None


def memory():
    total = hw._mem().get("total")
    out = {"total": total, "modules": None, "groups": []}
    try:
        with open(HOSTINFO) as f:
            mods = json.load(f).get("memory")
    except (OSError, ValueError):
        return out
    if not isinstance(mods, list):
        return out
    groups = {}
    for m in mods:
        if not isinstance(m, dict) or not m.get("size"):
            continue
        key = (_ram_vendor(m), m.get("part"), m.get("size"), m.get("type"), m.get("speed"))
        groups[key] = groups.get(key, 0) + 1
    out["modules"] = sum(groups.values())
    out["groups"] = [{"vendor": k[0], "part": k[1], "size": k[2], "type": k[3], "speed": k[4], "count": n}
                     for k, n in sorted(groups.items(), key=lambda kv: -kv[1])]
    out["slots_used"] = out["modules"]
    return out


def storage():
    try:
        drives = [d for d in hw.drives() if not d.get("behind_controller") or d.get("smart")]
        raid = hw.raid()
    except Exception:
        return []
    member_of = raid.get("member_of", {})
    levels = {a["name"]: a.get("level") for a in raid.get("md", [])}
    groups = {}
    for d in drives:
        if d.get("kind") == "RAID" and not d.get("behind_controller"):
            continue                      # the controller's virtual disk; its physical disks are listed
        arrays = tuple(sorted(set(a.split()[0] for a in member_of.get(d.get("name"), []))))
        key = (d.get("model") or "Unknown", d.get("capacity"), d.get("kind"), arrays)
        groups.setdefault(key, []).append(d.get("name"))
    out = []
    for (model, cap, kind, arrays), names in sorted(groups.items(), key=lambda kv: (-(kv[0][1] or 0), kv[0][0])):
        out.append({"model": model, "capacity": cap, "kind": kind, "count": len(names), "disks": names,
                    "raid": [{"name": a, "level": levels.get(a)} for a in arrays]})
    for pool in raid.get("zfs", []):
        out.append({"model": "ZFS pool " + pool["name"], "capacity": pool.get("size"), "kind": "ZFS", "count": 1,
                    "disks": [], "raid": [], "health": pool.get("health")})
    return out


def network():
    default = hw._default_iface()
    out = []
    for path in sorted(glob.glob(NET + "/*")):
        iface = os.path.basename(path)
        if not os.path.exists(path + "/device"):
            continue                       # lo, bridges, tunnels, docker veths
        if os.path.exists(path + "/wireless"):
            kind = "Wi-Fi"
        else:
            kind = "Ethernet"
        speed = hw._read_int(path + "/speed")
        drv = os.path.basename(os.path.realpath(path + "/device/driver")) if os.path.exists(path + "/device/driver") else None
        name = pci_name(path + "/device")
        if not name and drv == "virtio_net":
            name = "VirtIO network device (virtual)"
        out.append({"iface": iface, "name": name or drv or iface, "driver": drv, "kind": kind,
                    "speed": speed if speed and speed > 0 else None,
                    "up": hw._read(path + "/operstate") == "up", "default": iface == default})
    out.sort(key=lambda n: (not n["default"], not n["up"], n["iface"]))
    return out


def graphics():
    """GPUs the kernel exposes, excluding the BMC's remote-console VGA (ASPEED, Matrox G200)."""
    out = []
    try:
        for g in hw._gpus():
            out.append({"name": g.get("name"), "vendor": g.get("vendor"), "integrated": bool(g.get("integrated")),
                        "driver": g.get("driver"), "state": "enabled" if g.get("driver") else "present, no driver"})
    except Exception:
        pass
    return out


def specs(cfg=None):
    now = time.time()
    if _cache["data"] and now - _cache["t"] < 300:
        data = dict(_cache["data"])
    else:
        s = hw.static_info()
        data = {"hostname": s.get("hostname") or socket.gethostname(), "fqdn": None, "os": s.get("os"),
                "kernel": s.get("kernel"), "arch": s.get("arch"), "cpu": s.get("cpu_model"),
                "cpu_cores": s.get("cpu_cores"), "cpu_threads": s.get("cpu_threads"), "cpu_max_mhz": s.get("cpu_max_mhz")}
        try:
            fq = socket.getfqdn()
            data["fqdn"] = fq if fq and fq != data["hostname"] and "." in fq and not fq.startswith("localhost") else None
        except Exception:
            pass
        for name, fn in (("board", board), ("memory", memory), ("storage", storage), ("network", network), ("graphics", graphics)):
            try:
                data[name] = fn()
            except Exception:
                data[name] = None
        _cache.update(t=now, data=dict(data))
    up = hw._read("/proc/uptime")
    data["uptime"] = int(float(up.split()[0])) if up else None
    note = (cfg or {}).get("SERVER_BANDWIDTH")
    data["bandwidth_note"] = str(note)[:120] if note else None
    return data
