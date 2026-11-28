/* swizzin glass — living sky shared by the login page and the dashboard.
 *
 * The sky follows the real sun: its elevation is computed for a location (this device's, the server's,
 * or a guess from the browser's time zone) and mapped onto a palette that runs night → dawn → golden
 * hour → day → dusk. It is re-evaluated every minute and the colours glide between states (CSS @property).
 * A constellation (drifting stars linked to neighbours and to the pointer) is always there: bright at
 * night, a faint line drawing by day and on the Light and Dark themes.
 *
 * window.SWZ_SKY.init({ canvas, fps, interactive })   start (once per page)
 * window.SWZ_SKY.setLocation(lat, lon, source)         'device' | 'server'  (remembered per browser)
 * window.SWZ_SKY.setMode('auto' | 'day' | 'night')
 * window.SWZ_SKY.setTheme(key) / theme() / themes()   Auto's sky theme (teal, glacier, aurora, …)
 * window.SWZ_SKY.state()                               { elevation, phase, source, mode }
 */
(function () {
  "use strict";
  const root = document.documentElement;
  const store = {
    get(k, d) { try { const v = localStorage.getItem("swz." + k); return v == null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem("swz." + k, v); } catch (_) { /* storage unavailable */ } },
  };

  /* ---------------------------------------------------------------- sun */
  function sunElevation(date, lat, lon) {
    const rad = Math.PI / 180, d = date.getTime() / 86400000 - 10957.5;          // days since J2000.0
    const g = (357.529 + 0.98560028 * d) * rad, q = 280.459 + 0.98564736 * d;
    const L = (q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * rad;
    const e = (23.439 - 0.00000036 * d) * rad;
    const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)), dec = Math.asin(Math.sin(e) * Math.sin(L));
    const gmst = (((18.697374558 + 24.06570982441908 * d) % 24) + 24) % 24;
    const ha = (gmst * 15 + lon) * rad - ra;
    return Math.asin(Math.sin(lat * rad) * Math.sin(dec) + Math.cos(lat * rad) * Math.cos(dec) * Math.cos(ha)) / rad;
  }
  // rising (morning) or setting (evening): compare with the elevation 10 minutes later
  const rising = (date, lat, lon) => sunElevation(new Date(date.getTime() + 600000), lat, lon) > sunElevation(date, lat, lon);

  function location() {
    const lat = parseFloat(store.get("loc.lat", "")), lon = parseFloat(store.get("loc.lon", ""));
    if (isFinite(lat) && isFinite(lon)) return { lat, lon, source: store.get("loc.src", "server") };
    // no location yet (e.g. first visit to the login page): longitude from the time zone, mid-latitude
    return { lat: 28, lon: -new Date().getTimezoneOffset() / 4, source: "timezone" };
  }

  /* ---------------------------------------------------------------- palette
     Keyed by sun elevation (degrees). top/mid/low = sky gradient, g1..g4 = soft light glows,
     stars = constellation opacity, tint = extra darkening under glass cards so text stays readable by day. */
  const STOPS = [
    { e: -90, top: "#03040b", mid: "#060917", low: "#0a0c1b", g1: "#25307a", g2: "#4a2386", g3: "#0b4250", g4: "#2b1a45", stars: 1, tint: 0 },
    { e: -15, top: "#04060f", mid: "#0a0e22", low: "#121433", g1: "#2c3a92", g2: "#55309a", g3: "#0f4d60", g4: "#3a2152", stars: 1, tint: 0 },
    { e: -8, top: "#0a0f2c", mid: "#1b1f4e", low: "#3a2a5c", g1: "#3b4bb8", g2: "#7a3aa8", g3: "#1c5a78", g4: "#8a3a62", stars: .7, tint: .04 },
    { e: -3, top: "#1a2257", mid: "#4a3a7c", low: "#b4586a", g1: "#5a63d0", g2: "#c0558a", g3: "#38708e", g4: "#ff8a52", stars: .3, tint: .1 },
    { e: 2, top: "#2a4a8c", mid: "#9a6a92", low: "#ff9a5c", g1: "#5a88e0", g2: "#e0788c", g3: "#5a9cb0", g4: "#ffb15c", stars: .04, tint: .18 },
    { e: 9, top: "#1d55a2", mid: "#3a80c2", low: "#8fbedd", g1: "#4a96ee", g2: "#7a86ee", g3: "#3cb8bc", g4: "#ffcf7a", stars: 0, tint: .36 },
    { e: 30, top: "#1660bd", mid: "#348ed6", low: "#92cbee", g1: "#58adff", g2: "#8a9cff", g3: "#48cccc", g4: "#ffe09c", stars: 0, tint: .44 },
    { e: 90, top: "#1463c4", mid: "#3896de", low: "#9ad3f2", g1: "#5ab3ff", g2: "#8ea2ff", g3: "#4ad2d0", g4: "#ffe6a8", stars: 0, tint: .46 },
  ];
  const MODES = { night: -40, day: 40 };
  /* Light and Dark are calm, fixed themes (not skies): a soft neutral backdrop with faint pastel glows
     for the frosted glass to refract. Pages that only style for skies pass init({ themes: false }) and
     get the bright-day / night sky instead. */
  const THEMES = {
    // Light = Porcelain, Dark = Obsidian
    day: { top: "#f5f2ee", mid: "#eef0f6", low: "#e8edf6", g1: "#ffc9a8", g2: "#cfc4ff", g3: "#b4ead8", g4: "#ffe2a0", stars: 0, tint: 0 },
    night: { top: "#030305", mid: "#06060a", low: "#0a0912", g1: "#3a1c96", g2: "#0b6d78", g3: "#6a1752", g4: "#18308f", stars: 0, tint: 0 },
  };
  /* Sky themes for Auto. Each gives a night, a twilight and a day sky (top/mid/low gradient + four
     glows) and an accent for dark and for light surfaces; the eight elevation stops are built from
     them so every theme runs the same night → dawn → golden hour → day progression as the original.
     lightDay themes have pale day skies: once the sun is 6° up the page switches to dark text on
     white frosted glass (the Light styling) and back to light text on tinted glass for evening. */
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const mix = (a, b, t) => { const A = hex(a), B = hex(b); return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join(""); };
  const P7 = (top, mid, low, g1, g2, g3, g4) => ({ top, mid, low, g1, g2, g3, g4 });
  const SKIES = {
    obsidian: { name: "Obsidian & Porcelain", note: "black glass at night · white frost by day", accent: ["#b69cff", "#2563eb"], lightDay: true,
      night: P7("#030305", "#06060a", "#0a0912", "#3a1c96", "#0b6d78", "#6a1752", "#18308f"),
      twi: P7("#1b1636", "#4a3a6e", "#e8b9a6", "#6b4fc0", "#e0937a", "#3f8f9a", "#ffd2a8"),
      day: P7("#f5f2ee", "#eef0f6", "#e8edf6", "#ffc9a8", "#cfc4ff", "#b4ead8", "#ffe2a0") },
    teal: { name: "Teal Sky", note: "teal on navy · blue sky", accent: ["#5eead4", "#0f9488"], stops: STOPS },
    glacier: { name: "Glacier", note: "icy cyan on deep ocean · crisp morning sky", accent: ["#67e8f9", "#0284c7"], lightDay: true,
      night: P7("#020a14", "#04182a", "#062a3e", "#0b4f6e", "#12345e", "#0a5c66", "#1b2f55"),
      twi: P7("#0b2a4a", "#1f5a7c", "#7fc4d8", "#3a8fc0", "#6aa8d8", "#5cc6d0", "#bfe6f0"),
      day: P7("#5aa9e6", "#9fd0f2", "#e2f2fb", "#7ac8ff", "#a8d4ff", "#8fe3ee", "#f2f8ff") },
    aurora: { name: "Aurora", note: "violet night, green aurora · lilac dawn", accent: ["#a78bfa", "#6d28d9"], lightDay: true,
      night: P7("#07041a", "#120a2e", "#1c1040", "#1f8a62", "#5b2a9e", "#1f7a6a", "#3a1f6b"),
      twi: P7("#3a2a6e", "#8a6ab8", "#e8b8d8", "#9a7ae0", "#e0a0d0", "#7ad0c0", "#ffd0e0"),
      day: P7("#c4b4f2", "#ddd2f8", "#f3edfd", "#cbb8ff", "#f0c8ec", "#bfeadf", "#fff0f6") },
    sandsea: { name: "Sand & Sea", note: "warm charcoal · sunlit sand, sea-teal", accent: ["#5eead4", "#0f766e"], lightDay: true,
      night: P7("#0e0c0a", "#1a1613", "#26201b", "#3a2e22", "#2a3a3a", "#1d4a46", "#4a3020"),
      twi: P7("#3a4a5a", "#a08878", "#f0c090", "#6aa0a8", "#e0a080", "#4aa8a0", "#ffd8a0"),
      day: P7("#a9d8e2", "#f3e6cc", "#fbf3e3", "#8fd6cf", "#f6d7a8", "#6cc4bd", "#ffe9c2") },
    graphite: { name: "Graphite", note: "neutral graphite · soft fog, sky-blue", accent: ["#7cc4fa", "#0369a1"], lightDay: true,
      night: P7("#0b0c0e", "#141619", "#1c1f23", "#2a2f36", "#303540", "#23292f", "#3a3a40"),
      twi: P7("#2a2f38", "#5a6070", "#a8aebb", "#6a7488", "#8a8fa0", "#7a8a96", "#c8ccd6"),
      day: P7("#cdd5de", "#e4e8ed", "#f4f6f8", "#b8c8dc", "#cfd6e2", "#c4d6dc", "#eef0f3") },
    monsoon: { name: "Monsoon", note: "rain-cloud slate · misty sage", accent: ["#7ee0c8", "#0f766e"], lightDay: true,
      night: P7("#0a0f12", "#131b20", "#1b262c", "#24404a", "#2a3540", "#1f4a45", "#2e3a3e"),
      twi: P7("#2e3e48", "#5f7680", "#a9bfb8", "#5a8a90", "#7a9098", "#6aa898", "#c8d8c8"),
      day: P7("#b2c8c2", "#d5e2dc", "#eef4f0", "#b6d4c6", "#c8d6d2", "#a8d4c4", "#f0f2e6") },
    lotus: { name: "Lotus", note: "plum · blush-pink dawn, orchid", accent: ["#f0abfc", "#a21caf"], lightDay: true,
      night: P7("#12061a", "#1f0c2a", "#2c1238", "#5a1f5e", "#3a1a5a", "#6a2a50", "#2a1440"),
      twi: P7("#4a2a5e", "#b06a9a", "#f6b8c8", "#c070b0", "#f090a8", "#a088d0", "#ffd0d8"),
      day: P7("#f3c6d9", "#f9e1ea", "#fdf2f6", "#f4bcd6", "#e6c6f2", "#f8d2c8", "#fff0e8") },
    nordic: { name: "Nordic", note: "polar blue · icy white, periwinkle", accent: ["#a5b4fc", "#4338ca"], lightDay: true,
      night: P7("#050b18", "#0b1730", "#122444", "#1e3d7a", "#2a3a80", "#165a72", "#283868"),
      twi: P7("#2a3d70", "#7088c0", "#c8d4f0", "#6a8ae0", "#9aa8f0", "#7ab8d8", "#e0e8ff"),
      day: P7("#c9d8f2", "#e6edf9", "#f6f8fd", "#c6d2fa", "#d6dcfb", "#cfe6f4", "#f4f6ff") },
    ember: { name: "Ember", note: "campfire · warm peach, copper", accent: ["#fb923c", "#c2410c"], lightDay: true,
      night: P7("#0e0806", "#1c0f0a", "#2c160c", "#6a2a10", "#4a1a10", "#8a3a12", "#3a1a14"),
      twi: P7("#4a2418", "#b05a30", "#ffb070", "#e07a40", "#c05040", "#f0a050", "#ffd090"),
      day: P7("#f6c9a8", "#fbe2cf", "#fef3ea", "#f8c49a", "#f4b8a4", "#fad7a0", "#fff0de") },
  };
  const KEYS7 = ["top", "mid", "low", "g1", "g2", "g3", "g4"];
  const blend = (a, b, t) => { const o = {}; for (const k of KEYS7) o[k] = mix(a[k], b[k], t); return o; };
  function stopsFor(key) {
    const th = SKIES[key] || SKIES.obsidian;
    if (th.stops) return th.stops;
    const n = th.night, w = th.twi, d = th.day, deep = blend(n, P7("#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000"), .25);
    const golden = blend(w, d, .3); golden.low = mix(w.low, d.low, .15);
    const at = [deep, n, blend(n, w, .45), w, golden, blend(w, d, .75), d, d];
    return th.stops = STOPS.map((b, i) => Object.assign({}, at[i], { e: b.e, stars: b.stars, tint: b.tint }));
  }
  const themeNow = () => { const t = store.get("skytheme", "obsidian"); return SKIES[t] ? t : "obsidian"; };
  const LIGHT_INK_AT = 6;      // sun elevation (°) above which lightDay themes use dark text
  const STARS_BY_DAY = .5;     // the constellation never disappears: by day it is a faint line drawing
  let useThemes = true;
  const modeNow = () => { const m = store.get("sky", "auto"); return m === "day" || m === "night" ? m : "auto"; };
  function palette(el, evening, stops) {
    stops = stops || STOPS;
    let i = 0;
    while (i < stops.length - 2 && el > stops[i + 1].e) i++;
    const a = stops[i], b = stops[i + 1], t = Math.max(0, Math.min(1, (el - a.e) / (b.e - a.e)));
    const p = {};
    for (const k of ["top", "mid", "low", "g1", "g2", "g3", "g4"]) p[k] = mix(a[k], b[k], t);
    p.stars = a.stars + (b.stars - a.stars) * t; p.tint = a.tint + (b.tint - a.tint) * t;
    if (evening && el > -8 && el < 10) {            // sunsets run warmer and pinker than sunrises
      const w = 1 - Math.abs(el - 1) / 9;
      p.low = mix(p.low, "#ff7a5a", .35 * w); p.g2 = mix(p.g2, "#ff6a8a", .3 * w); p.g4 = mix(p.g4, "#ff7040", .3 * w);
    }
    return p;
  }
  const phaseName = (el, evening) => el < -12 ? "Night" : el < -0.8 ? (evening ? "Dusk" : "Dawn") : el < 8 ? (evening ? "Golden hour · evening" : "Golden hour · morning") : "Day";

  /* ---------------------------------------------------------------- apply */
  /* ---------------------------------------------------------------- legibility
     Glass cards are see-through, so how bright they look depends on the sky behind them: a golden-hour
     or noon sky makes them bright enough to wash out light text and coloured charts. Instead of
     hand-tuning a tint per stop, work out the brightest colour the glass can sit on (sky stops, and
     the glows screened over the horizon) and darken the glass just enough that, after the frost, its
     luminance stays under GLASS_MAX_L. That keeps white text >= 8:1, secondary text >= 5:1, muted
     text and chart colours >= 3:1 in every phase (checked with the dataviz palette validator), while letting as much sky through as it can. */
  const GLASS_MAX_L = 0.11, FROST = 0.09, GLOW_WEIGHT = 0.7;   // estimate is conservative: measured worst is ~half
  // the tint is the sky's own mid colour taken deep, so cards keep the hue of the hour (rose at
  // sunset, blue at noon, indigo at night) instead of turning grey
  const tintRgb = (p) => hex(mix(p.mid, "#05070f", 0.82));
  const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const luminance = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const screenMix = (a, b, o) => a.map((v, i) => v + ((255 - (255 - v) * (255 - b[i]) / 255) - v) * o);
  function legibleTint(p) {
    const base = [p.top, p.mid, p.low].map(hex);
    const cands = base.slice();
    for (const g of [p.g1, p.g2, p.g3, p.g4]) for (const b of base) cands.push(screenMix(b, hex(g), GLOW_WEIGHT));
    const brightest = cands.reduce((m, c) => (luminance(c) > luminance(m) ? c : m), cands[0]);
    const T = tintRgb(p);
    const lum = (a) => luminance(brightest.map((v, i) => { const t = v + (T[i] - v) * a; return t + (255 - t) * FROST; }));
    if (lum(p.tint) <= GLASS_MAX_L) return p.tint;
    let lo = p.tint, hi = 0.92;
    for (let k = 0; k < 18; k++) { const m = (lo + hi) / 2; if (lum(m) > GLASS_MAX_L) lo = m; else hi = m; }
    return hi;
  }

  let current = { elevation: 0, phase: "", stars: 1, starAlpha: 1, starRgb: "140,170,255", source: "timezone", mode: "auto", theme: "obsidian", ink: "light" };
  const rgbOf = (h) => hex(h).join(",");
  function apply() {
    const mode = modeNow(), loc = location(), now = new Date(), tkey = themeNow(), th = SKIES[tkey];
    let el, evening = false;
    if (mode === "auto") { el = sunElevation(now, loc.lat, loc.lon); evening = !rising(now, loc.lat, loc.lon); }
    else el = MODES[mode];
    const themed = mode !== "auto" && useThemes;
    const p = themed ? THEMES[mode] : palette(el, evening, mode === "auto" ? stopsFor(tkey) : STOPS);
    // dark text on white glass: the Light theme, and Auto on a pale-day theme while the sun is up
    const lightInk = themed ? mode === "day" : !!(mode === "auto" && th.lightDay && el >= LIGHT_INK_AT && useThemes);
    const s = root.style;
    s.setProperty("--sky-top", p.top); s.setProperty("--sky-mid", p.mid); s.setProperty("--sky-low", p.low);
    s.setProperty("--glow-1", p.g1); s.setProperty("--glow-2", p.g2); s.setProperty("--glow-3", p.g3); s.setProperty("--glow-4", p.g4);
    s.setProperty("--glass-tint", (themed || lightInk ? 0 : legibleTint(p)).toFixed(3)); s.setProperty("--stars", p.stars.toFixed(3));
    s.setProperty("--glass-tint-rgb", tintRgb(p).join(", "));
    // Auto carries the theme's accent (bright step on dark glass, deep step on light glass)
    if (mode === "auto") {
      const acc = lightInk ? th.accent[1] : th.accent[0];
      s.setProperty("--accent", acc); s.setProperty("--accent-ink", acc); s.setProperty("--accent-soft", "rgba(" + rgbOf(acc) + ", .16)");
      s.setProperty("--accent-rgb", rgbOf(acc));
    } else for (const k of ["--accent", "--accent-ink", "--accent-soft", "--accent-rgb"]) s.removeProperty(k);
    root.dataset.theme = lightInk ? "light" : themed ? "dark" : "sky";
    if (mode === "auto") root.dataset.auto = tkey; else delete root.dataset.auto;
    root.dataset.skyphase = themed ? (mode === "day" ? "light" : "night") : el < -6 ? "night" : el < 8 ? "twilight" : "day";
    store.set("lasttheme", root.dataset.theme);   // lets the page pick light/dark before first paint
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = p.top;
    const starRgb = lightInk ? "38,58,96" : mode === "auto" ? rgbOf(mix(th.accent[0], "#c8d6ff", .55)) : "150,178,230";
    current = { elevation: el, phase: mode === "auto" ? phaseName(el, evening) : mode === "day" ? "Light" : "Dark", stars: p.stars,
      starAlpha: Math.max(p.stars, STARS_BY_DAY), starRgb, light: lightInk, source: loc.source, mode, theme: tkey, ink: lightInk ? "dark" : "light" };
    document.dispatchEvent(new CustomEvent("swz-sky", { detail: current }));
    if (wake) wake();
    if (!painted) {   // first paint: no 6 s glide from the default colours; later changes glide
      painted = true;
      requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("sky-instant")));
    }
  }
  let painted = false;
  root.classList.add("sky-instant");

  /* ---------------------------------------------------------------- constellation */
  let wake = null;
  function constellation(cv, opts) {
    const ctx = cv.getContext("2d"), still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const minDt = 1000 / (opts.fps || 60);
    let W = 0, H = 0, dpr = 1, stars = [], link = 130, mouse = { x: -1e4, y: -1e4, on: false }, shoot = null, raf = 0, last = 0, lastDraw = 0;
    const rnd = (a, b) => a + Math.random() * (b - a);
    function resize() {
      const z = root.currentCSSZoom || 1;
      dpr = Math.min(devicePixelRatio || 1, 2); W = innerWidth / z; H = innerHeight / z;
      cv.width = Math.round(W * z * dpr); cv.height = Math.round(H * z * dpr); ctx.setTransform(dpr * z, 0, 0, dpr * z, 0, 0);
      link = Math.max(95, Math.min(150, Math.sqrt(W * H) / 7));
      const n = Math.round(Math.max(55, Math.min(opts.max || 240, (W * H) / 7500)));
      while (stars.length < n) stars.push({ x: rnd(0, W), y: rnd(0, H), vx: rnd(-.18, .18), vy: rnd(-.14, .14),
        r: Math.random() < .12 ? rnd(1.5, 2.2) : rnd(.5, 1.3), t: rnd(0, 6.28), ts: rnd(.6, 1.8), hue: Math.random() < .2 ? 268 : 215 });
      stars.length = n;
    }
    function draw(dt) {
      const k = current.starAlpha, night = current.stars, rgb = current.starRgb, light = current.light;
      ctx.clearRect(0, 0, W, H);
      if (k < .01) return;
      const speed = opts.speed || 1;
      for (const a of stars) {
        if (!still) {
          a.x += a.vx * dt * speed; a.y += a.vy * dt * speed; a.t += .02 * a.ts * dt;
          if (opts.interactive && mouse.on) { const dx = mouse.x - a.x, dy = mouse.y - a.y, d = dx * dx + dy * dy;
            if (d < 40000 && d > 400) { a.x += dx * .0009 * dt; a.y += dy * .0009 * dt; } }
          if (a.x < -20) a.x = W + 20; else if (a.x > W + 20) a.x = -20;
          if (a.y < -20) a.y = H + 20; else if (a.y > H + 20) a.y = -20;
        }
      }
      ctx.lineWidth = 1;
      for (let i = 0; i < stars.length; i++) {
        const a = stars[i];
        for (let j = i + 1; j < stars.length; j++) {
          const b = stars[j], dx = a.x - b.x, dy = a.y - b.y;
          if (dx > link || dx < -link || dy > link || dy < -link) continue;
          const d = Math.sqrt(dx * dx + dy * dy); if (d > link) continue;
          ctx.strokeStyle = "rgba(" + rgb + "," + ((1 - d / link) * (light ? .3 : .42) * k).toFixed(3) + ")";
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
        if (opts.interactive && mouse.on) { const dx = a.x - mouse.x, dy = a.y - mouse.y, d = Math.sqrt(dx * dx + dy * dy);
          if (d < link * 1.4) { ctx.strokeStyle = "rgba(" + rgb + "," + ((1 - d / (link * 1.4)) * (light ? .4 : .6) * k).toFixed(3) + ")";
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke(); } }
      }
      for (const a of stars) {
        const al = (.55 + .45 * Math.sin(a.t)) * k;
        if (light) { ctx.fillStyle = "rgba(" + rgb + "," + ((.35 + .3 * (al / Math.max(k, .01))) * k).toFixed(3) + ")";
          ctx.beginPath(); ctx.arc(a.x, a.y, a.r * 1.1, 0, 6.283); ctx.fill(); continue; }
        if (a.r > 1.4 && night > .2) { const g = ctx.createRadialGradient(a.x, a.y, 0, a.x, a.y, a.r * 5);
          g.addColorStop(0, "hsla(" + a.hue + ",90%,80%," + (.35 * al).toFixed(3) + ")"); g.addColorStop(1, "hsla(" + a.hue + ",90%,70%,0)");
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(a.x, a.y, a.r * 5, 0, 6.283); ctx.fill(); }
        ctx.fillStyle = "hsla(" + a.hue + ",80%,92%," + ((.5 + .5 * (al / Math.max(k, .01))) * k).toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, 6.283); ctx.fill();
      }
      if (!still && opts.shooting !== false && night > .5) {
        if (!shoot && Math.random() < .0025 * dt) shoot = { x: rnd(W * .1, W * .9), y: rnd(0, H * .4), vx: rnd(7, 11) * (Math.random() < .5 ? -1 : 1), vy: rnd(3, 5), life: 1 };
        if (shoot) {
          const tx = shoot.x - shoot.vx * 9, ty = shoot.y - shoot.vy * 9, sg = ctx.createLinearGradient(shoot.x, shoot.y, tx, ty);
          sg.addColorStop(0, "rgba(255,255,255," + (shoot.life * k).toFixed(2) + ")"); sg.addColorStop(1, "rgba(160,190,255,0)");
          ctx.strokeStyle = sg; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(shoot.x, shoot.y); ctx.lineTo(tx, ty); ctx.stroke();
          shoot.x += shoot.vx * dt; shoot.y += shoot.vy * dt; shoot.life -= .018 * dt;
          if (shoot.life <= 0 || shoot.y > H) shoot = null;
        }
      }
    }
    function frame(now) {
      raf = 0;
      if (document.hidden) return;
      if (now - lastDraw >= minDt - 1) {
        const dt = Math.min(50, now - (last || now)) / 16.67;
        last = now; lastDraw = now;
        if (opts.onFrame) opts.onFrame(now);
        draw(dt);
      }
      // keep animating only while stars are visible and the canvas is shown (hidden on 8K-class screens)
      if (!still && current.starAlpha >= .01 && cv.getClientRects().length) raf = requestAnimationFrame(frame);
    }
    wake = () => { if (!raf) { last = 0; raf = requestAnimationFrame(frame); } };
    addEventListener("resize", () => { resize(); if (still) draw(0); });
    if (opts.interactive) {
      addEventListener("pointermove", (e) => { const z = root.currentCSSZoom || 1; mouse.x = e.clientX / z; mouse.y = e.clientY / z; mouse.on = true; }, { passive: true });
      document.addEventListener("pointerleave", () => { mouse.on = false; });
    }
    document.addEventListener("visibilitychange", () => { if (!document.hidden) wake(); });
    resize(); if (still) draw(0); else wake();
  }

  /* ---------------------------------------------------------------- public */
  let started = false;
  window.SWZ_SKY = {
    sunElevation,
    init(opts) {
      if (started) return; started = true;
      if (opts && opts.themes === false) useThemes = false;
      if (opts && opts.canvas) constellation(opts.canvas, opts);
      apply();
      // re-evaluate every minute, aligned to the start of the minute
      setTimeout(() => { apply(); setInterval(apply, 60000); }, 60000 - (Date.now() % 60000) + 50);
    },
    setLocation(lat, lon, source) {
      if (!isFinite(lat) || !isFinite(lon)) return;
      store.set("loc.lat", (+lat).toFixed(3)); store.set("loc.lon", (+lon).toFixed(3)); store.set("loc.src", source || "server");
      if (started) apply();
    },
    setMode(m) { store.set("sky", m === "day" || m === "night" ? m : "auto"); if (started) apply(); },
    setTheme(t) { store.set("skytheme", SKIES[t] ? t : "obsidian"); if (started) apply(); },
    theme: themeNow,
    themes: () => Object.keys(SKIES).map((k) => {
      const st = stopsFor(k), n = st[1], d = st[6];
      return { key: k, name: SKIES[k].name, note: SKIES[k].note, accent: SKIES[k].accent.slice(), night: n.mid, nightLow: n.low, day: d.top, dayLow: d.low };
    }),
    mode: modeNow,
    state: () => current,
  };
})();
