# Changelog — sheergood/swizzin_dashboard

Fork of [liaralabs/swizzin_dashboard](https://github.com/liaralabs/swizzin_dashboard), tuned for
seedbox servers. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — `custom` branch

### Added
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
