"""
Server identity for the glass dashboard: clock/timezone, IP addresses, IP location and weather.

Public IP + location come from ipwho.is (fallback ipinfo.io) and weather from Open-Meteo; neither
needs an API key. Both are fetched by the server (not the browser) and cached, so the panel makes
at most one location lookup per hour and one weather lookup per 15 minutes.

Optional swizzin.cfg settings:
    PLACE_LOOKUPS = False            # never contact ipwho.is / ipinfo.io / open-meteo
    WEATHER_LAT = 25.43              # show weather for a fixed place instead of the server's IP location
    WEATHER_LON = 81.85
    WEATHER_PLACE = "Prayagraj"
    WEATHER_UNITS = "metric"         # or "imperial"
"""
import fcntl
import json
import os
import shutil
import socket
import struct
import subprocess as sp
import threading
import time

def _run(cmd, timeout):
    """stdout of cmd as text ('' on any failure). Python 3.6 compatible."""
    try:
        return sp.run(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, universal_newlines=True, timeout=timeout).stdout or ""
    except Exception:
        return ""


import requests

_lock = threading.Lock()
_geo = {"t": 0, "data": None}
_wx = {"t": 0, "key": None, "data": None}
UA = {"User-Agent": "swizzin-panel (sheergood fork)"}


def timezone_info():
    """IANA zone name + current offset, matching what the process's localtime() uses."""
    tz = os.environ.get("TZ", "").lstrip(":") or None
    if tz and "/" not in tz and tz.upper() != "UTC":
        tz = None  # POSIX-style TZ strings aren't usable as an IANA name
    if not tz:
        try:
            with open("/etc/timezone") as f:
                tz = f.read().strip() or None
        except Exception:
            pass
    if not tz:
        try:
            link = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in link:
                tz = link.split("zoneinfo/", 1)[1]
        except Exception:
            pass
    lt = time.localtime()
    return {"tz": tz or time.tzname[0], "abbr": time.strftime("%Z", lt),
            "offset_min": int(lt.tm_gmtoff // 60), "epoch_ms": int(time.time() * 1000)}


def _ipv4_of(iface):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        packed = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", iface[:15].encode()))  # SIOCGIFADDR
        return socket.inet_ntoa(packed[20:24])
    except Exception:
        return None


def local_addresses(iface):
    """Global-scope IPv4/IPv6 addresses on the default interface."""
    v4, v6 = [], []
    if iface and shutil.which("ip"):
        try:
            out = _run(["ip", "-j", "addr", "show", "dev", iface], 3)
            for link in json.loads(out or "[]"):
                for a in link.get("addr_info", []):
                    if a.get("scope") != "global":
                        continue
                    (v4 if a.get("family") == "inet" else v6).append(a.get("local"))
        except Exception:
            pass
    if iface and not v4:
        ip = _ipv4_of(iface)
        if ip:
            v4.append(ip)
    if iface and not v6:
        try:
            with open("/proc/net/if_inet6") as f:
                for line in f:
                    p = line.split()
                    if p[5] == iface and p[3] == "00":  # scope global
                        v6.append(":".join(p[0][i:i + 4] for i in range(0, 32, 4)))
            v6 = [socket.inet_ntop(socket.AF_INET6, socket.inet_pton(socket.AF_INET6, a)) for a in v6]
        except Exception:
            pass
    return {"v4": v4, "v6": v6}


def _is_public(ip):
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
        return a.is_global
    except Exception:
        return False


def _public_ips(local):
    """Public IPv4 and IPv6. Interface addresses are used when they are already public (typical on
    dedicated servers); otherwise ask ipify over the matching protocol."""
    v4 = next((a for a in local["v4"] if _is_public(a)), None)
    v6 = next((a for a in local["v6"] if _is_public(a)), None)
    if not v4:
        try:
            v4 = requests.get("https://api.ipify.org", headers=UA, timeout=5).text.strip() or None
        except Exception:
            v4 = None
    if not v6:
        try:
            v6 = requests.get("https://api6.ipify.org", headers=UA, timeout=4).text.strip() or None
        except Exception:
            v6 = None
    return {"v4": v4 if v4 and ":" not in v4 else None, "v6": v6 if v6 and ":" in v6 else None}


def _lookup_geo(ip=None):
    try:
        r = requests.get("https://ipwho.is/" + (ip or ""), headers=UA, timeout=6).json()
        if r.get("success"):
            conn = r.get("connection") or {}
            return {"ip": r.get("ip"), "city": r.get("city"), "region": r.get("region"),
                    "country": r.get("country"), "cc": r.get("country_code"),
                    "lat": r.get("latitude"), "lon": r.get("longitude"),
                    "isp": conn.get("isp") or conn.get("org"), "asn": conn.get("asn"),
                    "flag": (r.get("flag") or {}).get("emoji")}
    except Exception:
        pass
    try:
        r = requests.get("https://ipinfo.io/{}json".format(ip + "/" if ip else ""), headers=UA, timeout=6).json()
        lat, lon = (r.get("loc") or ",").split(",")
        org = r.get("org") or ""
        asn, _, isp = org.partition(" ")
        cc = r.get("country")
        flag = "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc) if cc and len(cc) == 2 else None
        return {"ip": r.get("ip"), "city": r.get("city"), "region": r.get("region"), "country": cc, "cc": cc,
                "lat": float(lat) if lat else None, "lon": float(lon) if lon else None,
                "isp": isp or org, "asn": asn.lstrip("AS") if asn.startswith("AS") else None, "flag": flag}
    except Exception:
        return None


WMO = {0: ("Clear sky", "clear"), 1: ("Mainly clear", "clear"), 2: ("Partly cloudy", "partly"),
       3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Freezing fog", "fog"),
       51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy drizzle", "drizzle"),
       56: ("Freezing drizzle", "drizzle"), 57: ("Freezing drizzle", "drizzle"),
       61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
       66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
       71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
       80: ("Rain showers", "rain"), 81: ("Rain showers", "rain"), 82: ("Violent showers", "rain"),
       85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
       95: ("Thunderstorm", "storm"), 96: ("Thunderstorm, hail", "storm"), 99: ("Thunderstorm, hail", "storm")}


def _lookup_weather(lat, lon, imperial):
    params = {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
              "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,"
                         "wind_speed_10m,wind_direction_10m,is_day,precipitation",
              "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max"}
    if imperial:
        params.update(temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch")
    r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, headers=UA, timeout=8).json()
    c, d = r.get("current") or {}, r.get("daily") or {}
    text, icon = WMO.get(c.get("weather_code"), ("–", "cloud"))

    def first(k):
        v = d.get(k) or [None]
        return v[0]
    return {"temp": c.get("temperature_2m"), "feels": c.get("apparent_temperature"),
            "humidity": c.get("relative_humidity_2m"), "wind": c.get("wind_speed_10m"),
            "wind_dir": c.get("wind_direction_10m"), "precip": c.get("precipitation"),
            "is_day": bool(c.get("is_day", 1)), "code": c.get("weather_code"), "text": text, "icon": icon,
            "high": first("temperature_2m_max"), "low": first("temperature_2m_min"),
            "rain_chance": first("precipitation_probability_max"),
            "sunrise": first("sunrise"), "sunset": first("sunset"),
            "units": {"temp": "°F" if imperial else "°C", "wind": "mph" if imperial else "km/h"},
            "local_tz": r.get("timezone")}


_rev = {}


def _reverse_name(lat, lon):
    key = (round(lat, 2), round(lon, 2))
    if key in _rev:
        return _rev[key]
    name = None
    try:
        r = requests.get("https://api.bigdatacloud.net/data/reverse-geocode-client",
                         params={"latitude": lat, "longitude": lon, "localityLanguage": "en"},
                         headers=UA, timeout=6).json()
        name = ", ".join(x for x in (r.get("city") or r.get("locality"), r.get("countryCode")) if x) or None
    except Exception:
        pass
    if len(_rev) > 50:
        _rev.clear()
    _rev[key] = name
    return name


_dev_wx = {}


def device_weather(config, lat, lon):
    """Weather for coordinates sent by the browser (the 📍 button). Rounded to ~1 km and cached."""
    lat, lon = round(float(lat), 2), round(float(lon), 2)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinates out of range")
    if not config.get("PLACE_LOOKUPS", True):
        return None
    imperial = str(config.get("WEATHER_UNITS", "metric")).lower() == "imperial"
    key, now = (lat, lon, imperial), time.time()
    hit = _dev_wx.get(key)
    if hit and now - hit[0] < 900:
        return hit[1]
    data = dict(_lookup_weather(lat, lon, imperial), place=_reverse_name(lat, lon) or "Your location")
    if len(_dev_wx) > 50:
        _dev_wx.clear()
    _dev_wx[key] = (now, data)
    return data


def place(config, iface):
    local = local_addresses(iface)
    out = {"time": timezone_info(), "local": local, "iface": iface, "public": {
               "v4": next((a for a in local["v4"] if _is_public(a)), None),
               "v6": next((a for a in local["v6"] if _is_public(a)), None)},
           "geo": None, "weather": None, "lookups": config.get("PLACE_LOOKUPS", True)}
    if not out["lookups"]:
        return out
    now = time.time()
    with _lock:
        if _geo["data"] is None or now - _geo["t"] > 3600:
            pub = _public_ips(local)
            g = _lookup_geo(pub["v4"] or pub["v6"])
            if g or _geo["data"] is None:
                _geo.update(t=now if g else now - 3300, data=g, pub=pub)  # retry in 5 min on failure
        out["geo"] = _geo["data"]
        out["public"] = _geo.get("pub") or out["public"]
        lat, lon = config.get("WEATHER_LAT"), config.get("WEATHER_LON")
        where = config.get("WEATHER_PLACE")
        if lat is None or lon is None:
            g = out["geo"] or {}
            lat, lon = g.get("lat"), g.get("lon")
            where = where or ", ".join(x for x in (g.get("city"), g.get("country")) if x)
        imperial = str(config.get("WEATHER_UNITS", "metric")).lower() == "imperial"
        key = (lat, lon, imperial)
        if lat is not None and lon is not None:
            if _wx["key"] != key or _wx["data"] is None or now - _wx["t"] > 900:
                try:
                    _wx.update(t=now, key=key, data=_lookup_weather(lat, lon, imperial))
                except Exception:
                    _wx.update(t=now - 600, key=key)  # retry in 5 min, keep last good data
            if _wx["data"] is not None:
                out["weather"] = dict(_wx["data"], place=where)
    return out
