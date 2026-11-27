# Changelog — sheergood/swizzin_dashboard

Fork of [liaralabs/swizzin_dashboard](https://github.com/liaralabs/swizzin_dashboard), tuned for
seedbox servers. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [sheergood-11] — Nov 27 2026

### Added
- **Bans by country** (Security card): the last 7 days of fail2ban bans grouped by country with
  flags, and a flag on every recent ban. Countries come from api.country.is (free, no key, HTTPS,
  no request logging), looked up in the background a few at a time and cached for 30 days, so each
  address is looked up once and the page never waits on it. Private addresses are never sent, and
  nothing is looked up when `PLACE_LOOKUPS = False`.
- **App uptime, last 24 hours**: a strip per installed app, one block per hour (running, partly
  down, down, or stopped on purpose), with the uptime percentage and per-hour detail on hover. The
  panel checks every minute and keeps 48 hours on disk, so restarts don't lose the history; time
  the panel itself was down shows as "no data", never as downtime.
- **Media queue** (when Sonarr or Radarr is installed): what's downloading (progress, size left,
  time left, items needing attention) and what's coming in the next 7 days (episodes with air
  times, films' digital / disc / cinema dates). One app being down never hides the other.
- **This month / 12 months** on the Data transferred card: each day of the month (busiest day
  called out), each of the last 12 months, and a straight-line "on pace for" month total.
- **Command palette** (Ctrl+K / ⌘K, or the Search button): open any app, jump to any card, start /
  stop / restart services, switch theme or sky theme, run the speed test, copy the IP. Stop and
  restart ask for a second Enter. Fully keyboard driven and returns focus where it was.

### Fixed
- Python compatibility: replaced `datetime.utcfromtimestamp` / `utcnow`, which are deprecated and
  due to be removed; the first runs when the panel starts, so a future Python would have stopped
  the panel from starting.
- The speed test no longer leaks a file handle per run.

## [sheergood-10] — Nov 27 2026

### Added
- **Country flags.** The IP address card, its popover and the speed test's M-Lab server now show the
  country's flag. Flags are bundled SVGs (`static/img/flags`, from flag-icons 7.6.0, MIT; checked for
  scripts and external links) rather than emoji, which Windows shows as two plain letters. If a flag
  ever fails to load, the country-code chip takes its place.

## [sheergood-9] — Nov 27 2026

### Added
- **Nine sky themes for Auto**: Teal Sky (the original), Glacier, Aurora, Sand & Sea, Graphite,
  Monsoon, Lotus, Nordic and Ember, picked from swatches in the user menu. Each has its own night,
  twilight and day sky and its own accent colour, and runs the same real-sun progression as before.
  - **Text colours follow the sky.** Themes with pale days (all but Teal Sky) switch to dark text on
    white frosted glass once the sun is 6° up and back to light text for the evening: twice a day,
    never flickering. Checked by sweeping every theme through three whole days
    (`tests/tools/theme_contrast.js`): dark text at 9.8:1 or better on the light glass; light text
    keeps the original tint engine's guarantee on the dark glass.
  - The accent (navigation, toggles, selection) uses the theme's bright shade on dark glass and its
    deep shade on light glass.
- **Stars on every theme.** The linked constellation stays all day (a faint line drawing by day,
  in ink on light backgrounds) and on the Light and Dark themes too; shooting stars remain night-only.
- **Your theme follows you.** Auto/Light/Dark and the sky theme are saved on the server for your
  user (`/api/prefs`, allowlisted values only) and applied before the first paint on any browser
  you sign in from. The login page remembers the light/dark look too, so nothing flashes.

### Security
- **Password guessing was not rate limited.** The limiter exempted every request that had an
  Authorization header at all, so wrong passwords sent that way were never counted. Now only a valid
  login is exempt (checked and cached 5 min, like the dashboard's own checks).
- **Removed `/auth/check`**, the nginx single sign-on endpoint: unused since FileBrowser Quantum got
  its own login page, and it answered "right or wrong password" without any rate limit.
- **The panel now listens on 127.0.0.1 by default** (was 0.0.0.0). nginx is the way in (HTTPS, real
  client addresses for the rate limiter); before, port 8333 answered plain-HTTP logins directly and
  let anyone forge their address with X-Forwarded-For. Set `HOST` in swizzin.cfg to override.
- **Random signing secret.** Login tokens were signed with the public default secret; each install
  now generates its own (`.flask_secret`, mode 600) unless swizzin.cfg sets one.
- **Buttons can't be pressed from other sites.** App start/stop/restart, preferences and the speed
  test only accept requests from the dashboard itself (X-Requested-With, same Origin). App control
  also accepts only start/stop/restart/enable/disable and plain unit names, and on servers with the
  matching swizzin update runs through its root-owned checked wrapper instead of `sudo systemctl`.
- Netdata (every process and user on the box) is admin-only; its proxy has a timeout.
- Live stats over Socket.IO need a valid login.
- Security headers: `X-Frame-Options: SAMEORIGIN` (no clickjacking), `nosniff`,
  `Referrer-Policy: same-origin`, `no-store` on API answers.
- Plex player state is allowlisted before it reaches the page.

### Fixed
- The user menu is opaque enough to read over cards (it sits inside the glass header, where
  browsers can't blur what's behind it).

## [sheergood-8] — Nov 27 2026

### Fixed
- Speed test failed on real M-Lab servers with "bad WebSocket handshake": a typo in the fixed
  WebSocket GUID (RFC 6455) made every handshake check fail. The test server used in the tests had
  the same typo, so the tests passed anyway. Now checked against the RFC's own worked example and
  against an independent WebSocket server library, for both download and upload. The error now
  includes the server's status line to make any future failure easier to diagnose.

## [sheergood-7] — Nov 27 2026

### Added
- **Speed test in Specs (M-Lab ndt7).** A *Speed test* row under *Public bandwidth* measures the
  server's own line: download, upload and ping to the nearest M-Lab site, which M-Lab's locate service
  picks from the server's IP. Live progress while it runs; the last result (with M-Lab city and how
  long ago) is kept across restarts and included in *Copy*.
  - Runs on the server in its own process (`core/ndt7.py`, standard library only), never in your
    browser, so it measures the datacenter link and never slows the panel. About 20 s.
  - Only starts when you press the button: one test at a time, at most one every 10 minutes (a
    1 Gbit/s test moves about 2.5 GB). Admin only; the request must come from the dashboard itself
    (`X-Requested-With` header and same Origin), so another site can't trigger it with your saved login.
  - The popover says plainly that M-Lab publishes every result, including the server's IP address.
- **Per-core temperatures.** Each per-core load tile shows its core's temperature, coloured when it
  nears the CPU's own "high" mark (Intel coretemp) or 80/90 °C. Hyper-threads are matched to their
  physical core from the kernel's CPU topology (on a 4-core/8-thread Xeon, C0 and C4 share core 0),
  replacing the old guess from CPU order that paired threads with the wrong core.

### Fixed
- Specs and drive popovers: the glass rim and header no longer scroll away with the content and
  spill past the rounded corners; only the list inside scrolls.

## [sheergood-6] — Nov 27 2026

### Added
- `/auth/check`: single sign-on for apps behind nginx. nginx (auth_request) asks the panel whether the
  browser's saved swizzin login is valid — the same check the dashboard makes — so FileBrowser
  Quantum accepts you exactly when the dashboard does. Restricted to the user nginx names
  (`X-Swizzin-Require-User`); answers cached 5 min per login and htpasswd change (~2 ms per request).
- The login page returns you to where you came from (`/login?next=/fbquantum/`, same site only).

## [sheergood-5] — Nov 27 2026

### Fixed
- **Charts and text stay readable in every sky.** At golden hour and sunset the glass cards picked up
  so much orange from the sky that the orange upload bars and grey labels disappeared into them.
  - The sky engine now works out how bright the sky behind the glass can get and darkens the glass
    just enough to keep it readable, using a deep shade of the sky's own colour, so cards still glow
    rose at sunset and blue at noon instead of turning grey.
  - New text colours: secondary and muted text are lighter in Auto and Dark and darker in Light.
  - New chart colours (download blue, upload orange, teal), checked with a colour-blind-safe palette
    validator against the darkest and brightest glass each theme can produce.
  - Measured on screen (`tests/tools/contrast_audit.py`, 8 sky phases and themes): main text 8:1 or
    better, secondary 5.7:1+, muted 3.7:1+, chart colours 3.2:1+ (night, dawn, golden hour, sunset,
    dusk, noon, Light, Dark). Previously chart colours were as low as 1.0:1 at sunset.

## [sheergood-4] — Nov 27 2026

### Changed
- Header shows only the hostname and a **Specs** button; the CPU / TCP / Link / Arch chips are gone
  (all of that is in the specs popover). The user menu stays on the right.
- Popovers (specs, time, IP, weather, drives) are frosted glass again in every theme instead of
  near-opaque panels; they stay solid only where the browser can't blur (old browsers, native 8K).
- Scrollbars everywhere are slim and rounded with no arrow buttons; the specs popover no longer
  shows a horizontal scrollbar.

### Removed
- Compact layout: it left too many gaps. A saved "compact" setting is cleared automatically.

## [sheergood-3] — Nov 27 2026

### Added
- **Themes: Auto · Light · Dark.** Auto is the living sky that follows the sun. Light and Dark are
  calm, fixed themes in the style of a clean admin console (white or slate surfaces, green accent),
  still built from frosted, refracting glass. The choice is applied before first paint (no flash)
  and the login page follows it.
- **Server specs popover.** The header now reads *hostname — OS*; click it for hostname, OS, kernel,
  uptime, CPU, motherboard (with version), BIOS, RAM modules (vendor, part number, size × count,
  type and speed), drives grouped by model with their RAID arrays, Ethernet cards, public bandwidth
  and GPU/iGPU. **Copy** puts the whole list on the clipboard as text. Set `SERVER_BANDWIDTH` in
  swizzin.cfg (e.g. `"1Gbit/s unmetered and guaranteed"`) to describe your plan; RAM module details
  come from the root `swizzin-host` timer (sheergood/swizzin) and appear within an hour of `box update`.
- **Integrated graphics.** The Graphics card now recognises Intel and AMD iGPUs and only appears
  when one (or a graphics card) is enabled with a kernel driver. For Intel it shows load (from the
  GPU's idle residency, no root or intel_gpu_top needed), current/max clock, render node, PCI slot
  and driver; if Plex is installed it says whether Plex can use the iGPU for hardware transcoding and
  gives the one command to fix it if not.
- **Plex "Now playing"** card, shown only when Plex is installed: who is watching what, on which
  device, LAN or remote, progress, quality and bandwidth, and Direct play / Direct stream /
  Transcode (HW) with the reason (e.g. `video hevc → h264`, subtitles burned in). Polls the local
  Plex server every 5 s while visible; the token stays on the server. `PLEX_URL` and `PLEX_TOKEN` in
  swizzin.cfg override the defaults.
- The swizzin mark in the sidebar sits on a small drop of liquid glass tinted by the sky or theme.
- FileBrowser Quantum (`box install fbquantum`) appears in the apps list with its icon.

### Verified
- 32 unit tests (new: iGPU detection on fake sysfs for Intel i915/xe and AMD APU vs discrete, load
  maths and counter clamping, Plex session parsing and every error state, DMI junk filtering, RAM
  vendor codes, drive/RAID grouping); smoke test covers `/api/specs`, `/api/plex`, `/api/procs`.
- Layout re-checked on 30 screens (360 px phone to native 8K) in Auto, Light + Compact and Dark.

## [sheergood-2] — Nov 27 2026

### Added
- **Weekly upstream sync** (`.github/workflows/upstream-sync.yml`, Mondays 03:41 UTC): merges new
  `liaralabs/swizzin_dashboard` commits into the test branch `upstream-sync`, runs the full CI
  (Python 3.8/3.10/3.12, Python 3.6 compatibility, JS syntax, unit and smoke tests) and
  fast-forwards `master`/`custom` only if everything passes. A conflict, a failed test or an
  upstream change to a workflow file opens an issue instead and leaves every branch alone. Run it
  by hand from Actions → upstream-sync (tick *test only* to rehearse).

### Changed
- **Sign in button** is now liquid glass: a translucent, blurred pill tinted by the live sky
  (indigo at night, rose/gold at dawn and dusk, azure by day), with a specular highlight across
  the top and a soft light that follows the pointer.
- Sky menu labels are now **Auto · Light · Dark** (were Auto · Day · Night). Saved choices carry
  over unchanged.

### Fixed
- **Moon phase accuracy** — the weather icon's moon now uses Meeus' lunar formulas; the lit
  fraction is within 0.2 % of the JPL-based `ephem` library (the old approximation was off by up
  to 9 %), and waxing/waning is always right.
- Browsers without `color-mix()` (Safari < 16.2, Firefox < 113, older TV browsers) now get a plain
  sky gradient and a solid glass button instead of a blank background. Previously the whole
  `background` declaration was dropped there, because a `var()` inside an unsupported function
  does not fall back to an earlier declaration.
- The Applications list no longer depends on `replaceChildren()` (Safari < 14).

### Verified (production audit)
- **Calculations** cross-checked against `psutil` on a loaded machine: CPU total and per core,
  iowait, memory, network counters, disk read/write MB/s and busy %, per-process CPU % and the
  process count all agree. Sun elevation matches `astral` to 0.01°.
- **Unit tests** (`tests/test_core.py`, now in CI): NVMe data units (×512 000 bytes), SATA
  `Total_LBAs_Written` with 512 B and 4 KiB sectors, vendor GiB / 32 MiB counters, wear attribute
  priority, HDD attribute states; mdstat with inactive, spare, failed, `resync=PENDING`,
  `auto-read-only`, rebuilding RAID 1 and resyncing RAID 5 arrays; vnstat 1.x (KiB) and 2.x
  totals for today, yesterday, this week (from Monday), last 7 days, month, all time and the last
  24 hours; and graceful "unavailable" results for a missing vnstat, a missing or corrupt
  `/proc/mdstat`, SMART JSON or fail2ban JSON.
- **Load test**: 3 600 concurrent API requests (8 clients) with 0 errors, median 9 ms, p95 under
  100 ms; panel memory steady at ~66 MiB. Unauthenticated callers are still rate-limited (429).
- **2-hour browser soak**: DOM node count flat after the first fill, JS heap ~2.5 MiB, every
  history buffer capped, no script errors.
- Layout re-checked on 30 screens from a 360 px phone to native 8K: no clipped or off-screen
  content.

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
- **RAID aware** — the Drives card lists arrays above the disks:
  - **mdadm**: level, size, member map (`[UU]`/`[U_]`), each member (✓ active, ✕ failed, ◌ spare),
    health (Healthy / Checking / Rebuilding / Degraded / Inactive), check or rebuild progress with
    time left and speed, and live array read/write. Read from `/proc/mdstat`, no root needed.
  - **ZFS** pools (when `zpool` exists): health, used/size, capacity and fragmentation.
  - **Hardware RAID** controller volumes (PERC/MegaRAID/Smart Array/Adaptec/…) are labelled, and
    the physical disks behind them get SMART/wear/health via the root collector
    (`smartctl --scan-open`, `-d megaraid,N` etc.).
  - Each disk row shows which arrays it belongs to (a failed membership is marked ✗).
- **Processes (btop-style)** — top 20 programs by CPU (100 % = one core, like top/btop) or by
  memory, with PID, user, threads, CPU and RSS bars, the full command line on hover and a filter;
  `GET /api/procs`, polled every 3 s only while the card is on screen.
- **Per-core graphs** — every logical CPU gets its own 2-minute area graph with current load
  (amber at 60 %, red at 90 %); hover shows now / average / peak and the core temperature.
- **24-hour network graph** — the Data transferred card has 7 days | 24 hours | Table; the hourly
  chart shows upload and download per hour from vnstat's hourly data (vnstat 1.x and 2.x).
- **Login logo** sits on a frosted-white badge — the dark teal logo was invisible on the sky.
- **Living sky theme for the whole UI** (`static/js/sky.js`, shared by the dashboard and login):
  the background follows the sun's real elevation at the server's location (or this device's when
  📍 is on), re-evaluated every minute — night → dawn → golden hour → day → golden hour → dusk —
  with warmer sunsets than sunrises and smooth 6 s colour glides. At night a constellation fades in
  behind the glass; by day it fades out and stops drawing. Glass darkens and secondary text lightens
  as the sky brightens so everything stays readable. User menu → **Sky**: Auto, Day, Night.
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
- Sky menu: **Auto** could not be selected — the page root carried the same `data-sky` attribute the
  buttons used, so every click also re-applied the current phase. The root now uses
  `data-skyphase`, the buttons are scoped to the menu, and only Auto / Day / Night remain (a saved
  Dawn or Dusk falls back to Auto).
- App icons could stay blank: the grid was rebuilt every 10 s (lazy images were replaced before
  loading) and relied on inline `onerror`. Cards are now built once and updated in place, and
  every icon has the app's initial underneath that shows if the image doesn't load.
- `core/util.py`: invalid `\.` escape in the IP-mount regex (SyntaxWarning on Python 3.12+).
- `.gitignore` now excludes `__pycache__/` and the per-server `swizzin.cfg`.
