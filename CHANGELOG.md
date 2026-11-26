# Changelog — sheergood/swizzin_dashboard

Fork of [liaralabs/swizzin_dashboard](https://github.com/liaralabs/swizzin_dashboard), tuned for
seedbox servers. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — `custom` branch

### Added
- **Server time, IP and weather strip** above the hero tiles (`GET /api/place`, admin-only):
  - Live clock in the server's timezone (IANA name + UTC offset), with your local time and how
    far ahead/behind the server is.
  - Public IP with copy button, interface IPv4/IPv6, provider + ASN and location with flag.
  - Current weather at the server's location: conditions icon, temperature, feels-like, high/low,
    humidity, wind, chance of rain, sunrise–sunset.
  - Lookups are made by the server and cached (IP location 1 h via ipwho.is → ipinfo.io fallback;
    weather 15 min via Open-Meteo), no API keys. Configure in `swizzin.cfg`:
    `WEATHER_LAT`, `WEATHER_LON`, `WEATHER_PLACE` (fixed location), `WEATHER_UNITS = "imperial"`,
    or `PLACE_LOOKUPS = False` to never contact those services.
- **Data transferred card** — upload (▲) and download (▼) with ratio for today, yesterday, this
  week (Mon–today), last 7 days, this month and all time, plus a 7-day grouped bar chart (hover for
  exact values) with a table view. Backed by `GET /api/traffic`, which reads vnstat 1.x or 2.x
  JSON for the default interface and caches it for 60 s.
- **Drive cards by type** — SSD/NVMe cards lead with wear level (% used, life left, estimated
  remaining lifetime at the current write rate, average writes per day); HDD cards show a SMART
  attribute table (raw read errors, reallocated sectors/events, seek errors, spin retries,
  reported uncorrectable, command timeouts, pending, offline uncorrectable, CRC errors) with
  OK / Watch / Failing status per attribute and the last self-test result.
- Sizes and rates now use binary units (KiB, MiB, GiB, TiB), matching vnstat and qBittorrent.
- **Liquid-glass dashboard** (`templates/index.html`, `static/css/glass.css`, `static/js/glass.js`).
  Frosted, refracting glass panels over an animated ambient light field, with zero CDN dependencies
  (no Bootstrap, jQuery or Socket.IO client needed for the new UI).
  - Hero tiles: CPU ring, memory ring, live network rates, load average vs. thread capacity.
  - Processor card: model, cores/threads, package temperature, fastest-core clock, iowait,
    3-minute CPU history chart with hover crosshair, per-thread load heat grid.
  - Throughput card: download/upload chart (B/s and Mbps on hover), totals since boot, up/down
    ratio, link speed, TCP congestion control and qdisc.
  - Drives card: one panel per physical disk with NVMe/SSD/HDD type, model, firmware, capacity,
    SMART health verdict, endurance left (wear), temperature, power-on time, data written/read,
    available spare, unsafe shutdowns, reallocated/pending sectors and live read/write/busy %.
  - Graphics card (shown only when a GPU exists): NVIDIA via `nvidia-smi`, AMD/Intel via sysfs —
    load, VRAM, temperature, power, clocks, fan, NVENC sessions.
  - Memory & storage card: RAM split into app memory vs. reclaimable cache, swap, every mount's
    usage, and any other hwmon temperature sensors.
  - Applications grid: icon, status, open link, restart button and enable/disable switch, with
    a filter box.
  - User menu toggles for the refraction effect and ambient motion (remembered per browser);
    respects `prefers-reduced-motion`.
- **Hardware API** (`core/hardware.py`), admin-only:
  - `GET /api/system` — hostname, OS, kernel, CPU model/cores/threads, TCP CC, qdisc, link speed.
  - `GET /api/live` — CPU total/per-thread/iowait/clocks/temps, memory, per-interface rates,
    per-disk I/O, GPUs, sensors. Rates are computed between polls; no background threads.
  - `GET /api/storage` — mount usage and SMART-derived drive health.
  - SMART needs root, so the panel never calls `smartctl` itself: it reads JSON written to
    `/var/lib/swizzin/smart/` by the root `swizzin-smart.timer` (installed by the fork's
    `scripts/install/panel.sh`). Without that timer the drives card still shows live I/O.
- **Glass login page** (`templates/login.html`) with inline error messages, no CDN assets.
- `/classic` route keeps the original Bootstrap dashboard available from the user menu.

### Changed
- `/` now renders the glass dashboard; the old template moved to `templates/classic.html`.

### Fixed
- `core/util.py`: invalid `\.` escape in the IP-mount regex (SyntaxWarning on Python 3.12+).
- `.gitignore` now excludes `__pycache__/` and the per-server `swizzin.cfg`.
