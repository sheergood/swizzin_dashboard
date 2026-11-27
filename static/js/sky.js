/* swizzin glass — living sky shared by the login page and the dashboard.
 *
 * The sky follows the real sun: its elevation is computed for a location (this device's, the server's,
 * or a guess from the browser's time zone) and mapped onto a palette that runs night → dawn → golden
 * hour → day → dusk. It is re-evaluated every minute and the colours glide between states (CSS @property).
 * At night a constellation (drifting stars linked to neighbours and to the pointer) fades in; by day it
 * fades out and stops drawing entirely.
 *
 * window.SWZ_SKY.init({ canvas, fps, interactive })   start (once per page)
 * window.SWZ_SKY.setLocation(lat, lon, source)         'device' | 'server'  (remembered per browser)
 * window.SWZ_SKY.setMode('auto' | 'day' | 'night')
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
  const modeNow = () => { const m = store.get("sky", "auto"); return m === "day" || m === "night" ? m : "auto"; };
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const mix = (a, b, t) => { const A = hex(a), B = hex(b); return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join(""); };
  function palette(el, evening) {
    let i = 0;
    while (i < STOPS.length - 2 && el > STOPS[i + 1].e) i++;
    const a = STOPS[i], b = STOPS[i + 1], t = Math.max(0, Math.min(1, (el - a.e) / (b.e - a.e)));
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
  let current = { elevation: 0, phase: "", stars: 1, source: "timezone", mode: "auto" };
  function apply() {
    const mode = modeNow(), loc = location(), now = new Date();
    let el, evening = false;
    if (mode === "auto") { el = sunElevation(now, loc.lat, loc.lon); evening = !rising(now, loc.lat, loc.lon); }
    else el = MODES[mode];
    const p = palette(el, evening);
    const s = root.style;
    s.setProperty("--sky-top", p.top); s.setProperty("--sky-mid", p.mid); s.setProperty("--sky-low", p.low);
    s.setProperty("--glow-1", p.g1); s.setProperty("--glow-2", p.g2); s.setProperty("--glow-3", p.g3); s.setProperty("--glow-4", p.g4);
    s.setProperty("--glass-tint", p.tint.toFixed(3)); s.setProperty("--stars", p.stars.toFixed(3));
    root.dataset.skyphase = el < -6 ? "night" : el < 8 ? "twilight" : "day";
    current = { elevation: el, phase: mode === "auto" ? phaseName(el, evening) : mode === "day" ? "Light" : "Dark", stars: p.stars, source: loc.source, mode };
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
      const k = current.stars;
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
          ctx.strokeStyle = "rgba(140,170,255," + ((1 - d / link) * .42 * k).toFixed(3) + ")";
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
        if (opts.interactive && mouse.on) { const dx = a.x - mouse.x, dy = a.y - mouse.y, d = Math.sqrt(dx * dx + dy * dy);
          if (d < link * 1.4) { ctx.strokeStyle = "rgba(170,195,255," + ((1 - d / (link * 1.4)) * .6 * k).toFixed(3) + ")";
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke(); } }
      }
      for (const a of stars) {
        const al = (.55 + .45 * Math.sin(a.t)) * k;
        if (a.r > 1.4) { const g = ctx.createRadialGradient(a.x, a.y, 0, a.x, a.y, a.r * 5);
          g.addColorStop(0, "hsla(" + a.hue + ",90%,80%," + (.35 * al).toFixed(3) + ")"); g.addColorStop(1, "hsla(" + a.hue + ",90%,70%,0)");
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(a.x, a.y, a.r * 5, 0, 6.283); ctx.fill(); }
        ctx.fillStyle = "hsla(" + a.hue + ",80%,92%," + ((.5 + .5 * (al / Math.max(k, .01))) * k).toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, 6.283); ctx.fill();
      }
      if (!still && opts.shooting !== false && k > .5) {
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
      if (!still && current.stars >= .01 && cv.getClientRects().length) raf = requestAnimationFrame(frame);
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
    mode: modeNow,
    state: () => current,
  };
})();
