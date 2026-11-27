# Changelog — sheergood/swizzin_dashboard

Fork of [liaralabs/swizzin_dashboard](https://github.com/liaralabs/swizzin_dashboard), tuned for
seedbox servers. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [sheergood-1] — Nov 26 2026

### Production safety
- Every `/api/*` endpoint is wrapped: an error is logged and returned as JSON instead of taking
  the panel down; the dashboard shows "unavailable" for that card only.
- Python 3.6+ compatible (no 3.7-only `subprocess` arguments); checked by `vermin` in CI.
- `tests/smoke.py` boots the panel with a throwaway config and requests every page and API;
  it refuses to run where a real `swizzin.cfg` exists.
- CI (`.github/workflows/ci.yml`) on Python 3.8 / 3.10 / 3.12: compile, 3.6 compatibility, JS
  syntax, smoke test.

### Added
- **Living sky theme for the whole UI** (`static/js/sky.js`, shared by the dashboard and login):
  the background follows the sun's real elevation at the server's location (or this device's when
  📍 is on), re-evaluated every minute — night → dawn → golden hour → day → golden hour → dusk —
  with warmer sunsets than sunrises and smooth 6 s colour glides. At night a constellation fades in
  behind the glass; by day it fades out and stops drawing. Glass darkens and secondary text lightens
  as the sky brightens so everything stays readable. User menu → **Sky**: Auto, Dawn, Day, Dusk, Night.
  The login page reuses the last location seen by the dashboard (or a time-zone estimate).
- **Live disk I/O, btop-style** — every drive row shows an IO% meter (busy time) and live read and
  write rates with one-minute bar graphs, plus total read/write in the card header. Columns adapt to
  the card's width (container queries), dropping graphs and temperature on narrow layouts.
- **Moon phase** in the weather popover: phase name, % lit and day of the lunar cycle, with the
  netdiag icon drawn at that phase (night weather icons already show the real phase).
- **Every screen from phones to 8K** — tested on 15 viewports (360 px phones, phone landscape,
  tablets, 720p, laptops incl. HiDPI, 1080p, 1440p, 4K at 100 %/150 %, TVs, 8K at 100 %/200 %) for
  horizontal scrolling, off-screen elements, clipped text and errors:
  - 4K/5K/8K screens and TV browsers without OS scaling: the UI scales 1.5×–3× (CSS `zoom`) so it
    lays out like a ~2560 px screen; charts, tooltips and popovers are zoom-aware.
  - 8K-class screens (~33 M device pixels) use pre-blended frosted glass and a static background
    instead of live blur and animated light blobs, which made GPU tiles fail to paint.
  - Browsers without `backdrop-filter` (older TV browsers) get solid glass.
  - Phones: the top bar truncates the OS line instead of overlapping the user button; the login
    card fits/scrolls on short landscape screens; bar-chart value labels hide when too narrow.
- **Login page: constellation theme** — drifting stars that link to their neighbours and to the
  pointer, twinkling glow stars and the odd shooting star over a deep-night nebula, behind the
  frosted liquid-glass card. Pauses in background tabs, draws one still frame with reduced motion,
  and turns the refraction filter off by itself if the device can't hold ~35 fps.
- **Weather icons from netdiag** (`static/js/wxicons.js`) — all 29 Open-Meteo weather codes, day
  and night, animated, with the real moon phase at night; used in the status bar and a large
  version in the weather popover.
- **Compact drives** — one row per drive (type, model, wear % for SSD/NVMe or bad-sector count
  for HDD, temperature, health). Click a row for the full wear level or SMART attribute table,
  power-on time and live read/write in a popover.
- **Compact Memory & storage** card (tighter rows, scrolling filesystem list); it now always
  sits beside the Security card, whose ban list scrolls after ~8 entries.
- **App sidebar** like the original panel: every installed app with its icon, name and a
  running/stopped dot, opening in a new tab. A button at the bottom collapses it to icons only
  (remembered per browser); on phones it becomes a slide-in drawer opened from the ☰ button.
- **Slim status bar** replaces the three large time / IP / weather tiles: one 48 px row with the
  clock (AM/PM), public IPv4 with copy button and location, and current weather with the 📍
  toggle. Clicking a section opens a small popover with the details (date, offset from your
  time, 12/24-hour switch; IPv4 + IPv6 with copy, local IPs, provider; feels-like, high/low,
  humidity, wind, rain chance, sunrise–sunset).
- Country shown as a small code chip (e.g. `NL`) instead of a flag emoji, which Windows
  displays as plain letters.
- **Security card** — fail2ban status (protecting / not running), banned now, bans in 24 h and
  7 d, currently failing, per-jail counts and the 25 most recent bans with jail, failures, repeat
  count, time and whether the ban is still active with time left. Data comes from
  `/var/lib/swizzin/fail2ban.json` written by the root `swizzin-f2b.timer` (`GET /api/fail2ban`).
- **Clock** shows 12-hour time with AM/PM by default; click it to switch to 24-hour.
- **IP tile** shows public IPv4 and IPv6 separately, each with a copy button (interface
  addresses when they are public, otherwise ipify over the matching protocol), plus local
  addresses when they differ (NAT).
- **Weather 📍 button** switches to the browser's location (needs https; coordinates rounded to
  ~1 km and sent only to your panel, which asks Open-Meteo; place name via BigDataCloud). Click
  again to go back to the server's location; the choice is remembered per browser.
- **Login page** gets a stronger liquid-glass treatment: heavier refraction, frosted grain,
  a moving specular sweep, pointer sheen with a gentle 3D tilt and drifting glass lenses
  (motion respects `prefers-reduced-motion`).
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
- App icons could stay blank: the grid was rebuilt every 10 s (lazy images were replaced before
  loading) and relied on inline `onerror`. Cards are now built once and updated in place, and
  every icon has the app's initial underneath that shows if the image doesn't load.
- `core/util.py`: invalid `\.` escape in the IP-mount regex (SyntaxWarning on Python 3.12+).
- `.gitignore` now excludes `__pycache__/` and the per-server `swizzin.cfg`.
