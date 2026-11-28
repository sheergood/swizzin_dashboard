/* swizzin glass dashboard — vanilla JS, no dependencies */
(() => {
  "use strict";
  const S = window.SWZ;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* Country flag as a bundled SVG (static/img/flags, from flag-icons, MIT): Windows shows flag emoji
     as two plain letters. If a flag file is ever missing, the country code chip takes its place. */
  const flagHTML = (cc, name) => S.flags && /^[A-Za-z]{2}$/.test(cc || "")
    ? `<img class="flag" src="${esc(S.flags + cc.toLowerCase())}.svg" width="18" height="13" alt="${esc(name || cc.toUpperCase())}" title="${esc(name || cc.toUpperCase())}" data-cc="${esc(cc.toUpperCase())}">`
    : "";
  document.addEventListener("error", (e) => {
    const im = e.target;
    if (im && im.classList && im.classList.contains("flag")) {
      const chip = document.createElement("span");
      chip.className = "cc"; chip.textContent = im.dataset.cc || ""; chip.title = im.title;
      im.replaceWith(chip);
    }
  }, true);  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const HISTORY = 90;           // points kept (90 × 2 s = 3 min)
  const LIVE_MS = 2000;

  /* ------------------------------------------------------------ formatting */
  const UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];   // binary units, same as vnstat and qBittorrent
  function bytes(n, dp = 1) {
    if (n == null || isNaN(n)) return "–";
    let i = 0; n = Number(n);
    while (Math.abs(n) >= 1024 && i < UNITS.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(n >= 100 ? 0 : dp)) + " " + UNITS[i];
  }
  const rate = (n) => (n == null ? "–" : bytes(n) + "/s");
  const mbps = (n) => (n == null ? "–" : ((n * 8) / 1e6).toFixed(n * 8 >= 1e9 ? 0 : 1) + " Mbps");
  function dur(sec) {
    sec = Math.floor(sec); const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
  }
  const hours = (h) => (h == null ? "–" : h >= 24 * 365 ? (h / 8766).toFixed(1) + " y" : h >= 48 ? Math.round(h / 24) + " d" : h + " h");
  const pct = (v, dp = 0) => (v == null ? "–" : Number(v).toFixed(dp) + "%");
  const lvl = (v, w, c) => (v == null ? "" : v >= c ? "crit" : v >= w ? "warn" : "good");
  const LVL_TEXT = { good: "Normal", warn: "Warm", crit: "Hot" };
  const levelColor = (l) => ({ good: css("--good"), warn: css("--warn"), crit: css("--crit"), serious: css("--serious") }[l] || css("--s1"));

  /* ------------------------------------------------------------ fetch */
  async function getJSON(url) {
    const r = await fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
    if (r.status === 401) { location.reload(); throw new Error("auth"); }
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }

  /* CSS zoom applied on very large screens (see glass.css). getBoundingClientRect/clientX are in
     viewport pixels, while style.left/top are in zoomed CSS pixels, so positions are divided by it. */
  const zoom = () => document.documentElement.currentCSSZoom || parseFloat(getComputedStyle(document.documentElement).zoom) || 1;

  /* ------------------------------------------------------------ tooltip */
  const tip = $("tip");
  function showTip(html, x, y) {
    tip.innerHTML = html; tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let left = x + 16, top = y + 16;
    if (left + r.width > innerWidth - 8) left = x - r.width - 16;
    if (top + r.height > innerHeight - 8) top = y - r.height - 16;
    const z = zoom(); tip.style.left = left / z + "px"; tip.style.top = top / z + "px";
  }
  const hideTip = () => { tip.hidden = true; };

  // fail2ban jails swizzin sets up, in plain words (any other jail keeps its own name)
  const JAILS = { sshd: "SSH", "swizzin-panel": "panel login", "swizzin-nginx-auth": "app login", "swizzin-filebrowser": "file login", "swizzin-scan": "scanner" };
  const jailName = (n) => JAILS[n] || n;

  let toastTimer;
  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 2600);
  }

  /* ------------------------------------------------------------ chart */
  class LineChart {
    constructor(el, opts) {
      this.el = el; this.o = opts; this.data = opts.series.map(() => []);
      this.c = document.createElement("canvas"); el.appendChild(this.c);
      this.ctx = this.c.getContext("2d"); this.hover = null;
      new ResizeObserver(() => this.resize()).observe(el);
      el.addEventListener("pointermove", (e) => this.onMove(e));
      el.addEventListener("pointerleave", () => { this.hover = null; hideTip(); this.draw(); });
      this.resize();
    }
    push(values) {
      values.forEach((v, i) => { const d = this.data[i]; d.push(v); if (d.length > HISTORY) d.shift(); });
      this.draw();
      if (this.hover != null) this.tooltipAt(this.hover.i, this.hover.x, this.hover.y);
    }
    resize() {
      const dpr = devicePixelRatio || 1, z = zoom(), r = this.el.getBoundingClientRect();
      this.w = r.width / z; this.h = r.height / z;            // drawing units = layout px (match the page's text size)
      this.c.width = Math.round(r.width * dpr); this.c.height = Math.round(r.height * dpr);
      this.ctx.setTransform(dpr * z, 0, 0, dpr * z, 0, 0); this.draw();
    }
    max() {
      if (this.o.max) return this.o.max;
      let m = 0; this.data.forEach((d) => d.forEach((v) => { if (v > m) m = v; }));
      if (m <= 0) return 1;
      const p = Math.pow(1024, Math.floor(Math.log(m) / Math.log(1024)));   // nice ceiling in binary units
      const steps = [1, 2, 4, 8, 10, 20, 40, 80, 100, 200, 400, 800, 1000];   // divide cleanly into 4
      for (const s of steps) if (s * p >= m * 1.1) return s * p;
      return m * 1.2;
    }
    geom() { return { l: this.o.axisW || 48, r: 8, t: 10, b: 20 }; }
    draw() {
      const { ctx, w, h } = this; if (!w) return;
      const g = this.geom(), pw = w - g.l - g.r, ph = h - g.t - g.b, max = this.max();
      ctx.clearRect(0, 0, w, h);
      // recessive grid + axis labels (text in text inks)
      ctx.font = "11px " + css("--sans"); ctx.textBaseline = "middle"; ctx.textAlign = "right";
      for (let k = 0; k <= 4; k++) {
        const y = g.t + ph - (ph * k) / 4;
        ctx.strokeStyle = k === 0 ? css("--grid-strong") : css("--grid");
        ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(g.l, Math.round(y) + .5); ctx.lineTo(w - g.r, Math.round(y) + .5); ctx.stroke();
        ctx.fillStyle = css("--ink-3"); ctx.fillText(this.o.fmtAxis((max * k) / 4), g.l - 8, y);
      }
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      ctx.fillText("3 min ago", g.l, h - 4); ctx.textAlign = "right"; ctx.fillText("now", w - g.r, h - 4);
      const X = (i) => g.l + (pw * i) / (HISTORY - 1);
      const Y = (v) => g.t + ph - (Math.min(v, max) / max) * ph;
      this.data.forEach((d, si) => {
        if (d.length < 2) return;
        const off = HISTORY - d.length, color = this.o.series[si].color();
        const path = new Path2D();
        d.forEach((v, i) => (i ? path.lineTo(X(i + off), Y(v ?? 0)) : path.moveTo(X(i + off), Y(v ?? 0))));
        if (this.o.fill) {
          const area = new Path2D(path); area.lineTo(X(HISTORY - 1), g.t + ph); area.lineTo(X(off), g.t + ph); area.closePath();
          const grad = ctx.createLinearGradient(0, g.t, 0, g.t + ph);
          grad.addColorStop(0, color + (this.data.length > 1 ? "33" : "55")); grad.addColorStop(1, color + "00");
          ctx.fillStyle = grad; ctx.fill(area);
        }
        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
        ctx.shadowColor = color + "99"; ctx.shadowBlur = 8; ctx.stroke(path); ctx.shadowBlur = 0;
        // end marker with surface ring
        const lx = X(HISTORY - 1), ly = Y(d[d.length - 1] ?? 0);
        ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = "rgba(10,12,20,.9)"; ctx.stroke();
      });
      if (this.hover != null) {
        const x = X(this.hover.i);
        ctx.strokeStyle = css("--cursor"); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(Math.round(x) + .5, g.t); ctx.lineTo(Math.round(x) + .5, g.t + ph); ctx.stroke();
        this.data.forEach((d, si) => {
          const v = d[this.hover.i - (HISTORY - d.length)]; if (v == null) return;
          ctx.beginPath(); ctx.arc(x, Y(v), 4.5, 0, Math.PI * 2); ctx.fillStyle = this.o.series[si].color(); ctx.fill();
          ctx.lineWidth = 2; ctx.strokeStyle = "rgba(10,12,20,.9)"; ctx.stroke();
        });
      }
    }
    onMove(e) {
      const z = zoom(), r = this.el.getBoundingClientRect(), g = this.geom();
      const pw = r.width / z - g.l - g.r;
      let i = Math.round((((e.clientX - r.left) / z - g.l) / pw) * (HISTORY - 1));
      const len = Math.max(...this.data.map((d) => d.length));
      i = Math.max(HISTORY - len, Math.min(HISTORY - 1, i));
      if (!len) return;
      this.hover = { i, x: e.clientX, y: e.clientY }; this.draw(); this.tooltipAt(i, e.clientX, e.clientY);
    }
    tooltipAt(i, x, y) {
      const ago = (HISTORY - 1 - i) * (LIVE_MS / 1000);
      let html = `<div class="t-title">${ago ? ago + " s ago" : "now"}</div>`;
      this.data.forEach((d, si) => {
        const v = d[i - (HISTORY - d.length)], s = this.o.series[si];
        html += `<div class="t-row"><span><i class="dot" style="background:${s.color()}"></i>${s.name}</span><b>${this.o.fmtTip(v)}</b></div>`;
      });
      showTip(html, x, y);
    }
  }

  /* ------------------------------------------------------------ ring */
  const CIRC = 2 * Math.PI * 50;
  function setRing(id, value, level) {
    const el = $(id); if (!el) return;
    el.style.strokeDashoffset = CIRC * (1 - Math.max(0, Math.min(100, value || 0)) / 100);
    const c = level && level !== "good" ? levelColor(level) : css("--s1");
    el.style.stroke = c; el.style.filter = `drop-shadow(0 0 6px ${c}88)`;
  }

  /* ------------------------------------------------------------ glass sheen + prefs */
  document.addEventListener("pointermove", (e) => {
    const g = e.target.closest && e.target.closest(".glass"); if (!g) return;
    const r = g.getBoundingClientRect();
    g.style.setProperty("--mx", e.clientX - r.left + "px"); g.style.setProperty("--my", e.clientY - r.top + "px");
  }, { passive: true });

  const store = {
    get(k, d) { try { const v = localStorage.getItem("swz." + k); return v == null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem("swz." + k, v); } catch (_) { /* storage unavailable */ } },
  };
  function applyPrefs() {
    document.documentElement.dataset.refraction = store.get("refraction", "on");
    document.documentElement.dataset.motion = store.get("motion", "on");
    $("optRefraction").checked = store.get("refraction", "on") === "on";
    $("optMotion").checked = store.get("motion", "on") === "on";
  }
  $("optRefraction").addEventListener("change", (e) => { store.set("refraction", e.target.checked ? "on" : "off"); applyPrefs(); });
  $("optMotion").addEventListener("change", (e) => { store.set("motion", e.target.checked ? "on" : "off"); applyPrefs(); });
  applyPrefs();

  /* ------------------------------------------------------------ living sky */
  const SKY = window.SWZ_SKY;
  let savePrefs = () => {};
  if (SKY) {
    SKY.init({ canvas: $("sky"), fps: 24, interactive: true, speed: .6, max: 200 });
    const SRC = { device: "this device's location", server: "the server's location", timezone: "your time zone (no location yet)" };
    const paintSkyMenu = (st) => {
      $("skyPhase").textContent = st.phase + (st.mode === "auto" ? ` · sun ${st.elevation >= 0 ? "+" : ""}${Math.round(st.elevation)}°` : "");
      $("skySrc").textContent = st.mode === "auto" ? `Obsidian & Porcelain follows the sun at ${SRC[st.source] || st.source}; updates every minute.` : `Always ${st.mode === "day" ? "light" : "dark"}. Choose Auto to follow the real sun.`;
      document.querySelectorAll(".sky-seg [data-skymode]").forEach((b) => { const on = b.dataset.skymode === st.mode; b.setAttribute("aria-selected", String(on)); b.setAttribute("aria-checked", String(on)); });
    };
    // remember the choice on the server too, so every browser you sign in from looks the same
    let prefTimer = 0;
    savePrefs = function () {
      clearTimeout(prefTimer);
      prefTimer = setTimeout(() => {
        fetch(S.api.prefs, { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
          body: JSON.stringify({ sky: SKY.mode() }) }).catch(() => {});
      }, 400);
    };
    document.addEventListener("swz-sky", (e) => paintSkyMenu(e.detail));
    paintSkyMenu(SKY.state());
    document.querySelectorAll(".sky-seg [data-skymode]").forEach((b) => b.addEventListener("click", () => { SKY.setMode(b.dataset.skymode); savePrefs(); }));
  }

  const menu = $("menu"), menuBtn = $("menuBtn");
  menuBtn.addEventListener("click", (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; menuBtn.setAttribute("aria-expanded", String(!menu.hidden)); });
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) { menu.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { menu.hidden = true; hideTip(); } });

  /* ============================================================ ADMIN: system + live */
  let sys = null, threads = 1, drivesCache = [], lastLive = null;
  const cpuChart = S.admin ? new LineChart($("cpuChart"), {
    max: 100, fill: true,
    series: [{ name: "CPU", color: () => css("--s1") }],
    fmtAxis: (v) => v.toFixed(0) + "%", fmtTip: (v) => pct(v, 1),
  }) : null;
  const netChart = S.admin ? new LineChart($("netChart"), {
    fill: true, axisW: 72,
    series: [{ name: "Download", color: () => css("--s1") }, { name: "Upload", color: () => css("--s2") }],
    fmtAxis: (v) => (v ? bytes(v, 0) + "/s" : "0"), fmtTip: (v) => (v == null ? "–" : `${rate(v)} · ${mbps(v)}`),
  }) : null;

  async function loadSystem() {
    sys = await getJSON(S.api.system);
    threads = sys.cpu_threads || 1;
    $("host").textContent = sys.hostname;
    if ($("sysline")) $("sysline").textContent = `${sys.os} · ${sys.kernel}`;   // non-admin header
    document.title = `${sys.hostname} · swizzin`;
    $("cpuModel").textContent = `${sys.cpu_model} · ${sys.cpu_cores} cores / ${sys.cpu_threads} threads${sys.cpu_max_mhz ? " · up to " + (sys.cpu_max_mhz / 1000).toFixed(2) + " GHz" : ""}`;
    $("loadThreads").textContent = `${threads} threads`;
    const link = sys.link_speed && sys.link_speed > 0 ? (sys.link_speed >= 1000 ? sys.link_speed / 1000 + " Gbit" : sys.link_speed + " Mbit") : "–";
    $("netLink").textContent = `link ${link}`;
    $("netCc").textContent = [sys.tcp_cc, sys.qdisc].filter(Boolean).join(" · ") || "–";
  }

  function renderLive(d) {
    lastLive = d;
    const c = d.cpu;
    // CPU tile
    $("cpuPct").textContent = c.total == null ? "–" : Math.round(c.total);
    setRing("cpuArc", c.total, lvl(c.total, 75, 92));
    $("cpuFreq").textContent = c.freq_avg ? `${(c.freq_avg / 1000).toFixed(2)} GHz avg` : "– GHz";
    $("cpuIowait").textContent = `iowait ${pct(c.iowait, 1)}`;
    const tl = lvl(c.temp, 80, 92), tb = $("cpuTempBadge");
    tb.className = "badge " + tl; tb.textContent = c.temp != null ? `${Math.round(c.temp)}°C · ${LVL_TEXT[tl]}` : "";
    // memory tile
    const m = d.mem, mp = m.total ? (m.used / m.total) * 100 : 0;
    $("memPct").textContent = Math.round(mp); setRing("memArc", mp, lvl(mp, 85, 95));
    $("memTotal").textContent = bytes(m.total, 0);
    $("memUsed").textContent = `${bytes(m.used)} used`;
    $("swapUsed").textContent = m.swap_total ? `swap ${bytes(m.swap_used)}` : "no swap";
    // network tile
    const ifc = d.net.ifaces[d.net.iface] || Object.values(d.net.ifaces)[0] || {};
    $("netRx").textContent = rate(ifc.rx); $("netTx").textContent = rate(ifc.tx);
    $("netIface").textContent = d.net.iface || ""; $("netIface2").textContent = d.net.iface || "–";
    $("netTotals").innerHTML = [
      ["Down now", mbps(ifc.rx)], ["Up now", mbps(ifc.tx)],
      ["Received since boot", bytes(ifc.rx_total)], ["Sent since boot", bytes(ifc.tx_total)],
      ["Ratio (up / down)", ifc.rx_total ? (ifc.tx_total / ifc.rx_total).toFixed(2) : "–"], ["Uptime", dur(d.uptime)],
    ].map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join("");
    // load tile
    $("load1").textContent = d.load[0].toFixed(2); $("load5").textContent = d.load[1].toFixed(2); $("load15").textContent = d.load[2].toFixed(2);
    $("loadPct").textContent = `${Math.round((d.load[0] / threads) * 100)}% of capacity`;
    $("uptime").textContent = `up ${dur(d.uptime)}`;
    // cpu card stats + cores
    $("cpuStats").innerHTML = [
      [c.temp != null ? Math.round(c.temp) + "°C" : "–", "package"],
      [c.freq_max ? (c.freq_max / 1000).toFixed(2) + " GHz" : "–", "fastest core"],
      [pct(c.iowait, 1), "iowait"],
    ].map(([v, k]) => `<div class="stat"><b>${v}</b><small>${k}</small></div>`).join("");
    const coresEl = $("cores");
    if (coresEl.children.length !== c.cores.length) {
      coresEl.innerHTML = c.cores.map((_, i) => `<div class="core" data-i="${i}"><span class="core-id">C${i}<span class="core-t"></span></span><b class="core-v">–</b><svg class="core-g" viewBox="0 0 ${CORE_HIST - 1} 100" preserveAspectRatio="none" aria-hidden="true"><path class="core-a"/><path class="core-l"/></svg></div>`).join("");
    }
    const tt = c.thread_temps || [];   // temperature of the physical core under each logical CPU
    c.cores.forEach((v, i) => {
      const h = coreHist[i] || (coreHist[i] = []);
      h.push(v == null ? 0 : v); if (h.length > CORE_HIST) h.shift();
      const el = coresEl.children[i], off = CORE_HIST - h.length;
      const pts = h.map((x, j) => `${j + off},${(100 - Math.min(100, x)).toFixed(1)}`).join(" L");
      el.querySelector(".core-l").setAttribute("d", "M" + pts);
      el.querySelector(".core-a").setAttribute("d", `M${off},100 L${pts} L${CORE_HIST - 1},100 Z`);
      el.querySelector(".core-v").textContent = v == null ? "–" : Math.round(v) + "%";
      const t = tt[i], te = el.querySelector(".core-t");
      el.dataset.t = t ? t.c : "";
      el.dataset.v = v;
      if (te) {
        te.textContent = t ? Math.round(t.c) + "°" : "";
        // the CPU's own "high" mark when it reports one (Intel coretemp: tempN_max), else 80/90 °C
        const hi = t && t.high ? t.high : 90, warn = hi - 10;
        te.className = "core-t" + (!t ? "" : t.c >= hi ? " crit" : t.c >= warn ? " warn" : "");
        te.title = t ? `Core temperature ${t.c}°C` + (t.high ? ` (high at ${Math.round(t.high)}°C)` : "") : "";
      }
      el.classList.toggle("hot", v >= 90); el.classList.toggle("busy", v >= 60 && v < 90);
    });
    $("coreTempsLine").textContent = c.core_temps.length ? `core temps ${Math.min(...c.core_temps)}–${Math.max(...c.core_temps)}°C` : "";
    cpuChart.push([c.total]);
    netChart.push([ifc.rx, ifc.tx]);
    renderGpus(d.gpus);
    renderMem(m);
    renderSensors(d.sensors);
    renderDriveIO(d.disk_io);
  }

  const CORE_HIST = 60, coreHist = [];
  $("cores") && $("cores").addEventListener("pointermove", (e) => {
    const el = e.target.closest(".core"); if (!el) return hideTip();
    const i = +el.dataset.i, h = coreHist[i] || [], avg = h.length ? h.reduce((a, b) => a + b, 0) / h.length : null;
    showTip(`<div class="t-title">CPU ${i}</div><div class="t-row"><span>Now</span><b>${pct(el.dataset.v === "null" ? null : el.dataset.v, 1)}</b></div>
      <div class="t-row"><span>2-min avg</span><b>${pct(avg, 1)}</b></div><div class="t-row"><span>2-min peak</span><b>${pct(h.length ? Math.max(...h) : null, 0)}</b></div>` +
      (el.dataset.t ? `<div class="t-row"><span>Core temp</span><b>${el.dataset.t}°C</b></div>` : ""), e.clientX, e.clientY);
  });
  $("cores") && $("cores").addEventListener("pointerleave", hideTip);

  function barRow(name, parts, val, title) {
    const segs = parts.filter((p) => p.v > 0).map((p) => `<i style="width:${Math.max(p.v, 0.5).toFixed(2)}%;background:${p.c}" title="${esc(p.t || "")}"></i>`).join("");
    return `<div class="row" ${title ? `title="${esc(title)}"` : ""}><span class="name">${esc(name)}</span><span class="bar">${segs}</span><span class="val">${val}</span></div>`;
  }

  function renderMem(m) {
    const t = m.total || 1, apps = ((m.used) / t) * 100, cache = Math.min((m.cache / t) * 100, 100 - apps);
    let html = barRow("RAM", [{ v: apps, c: css("--s1"), t: "Used by apps" }, { v: cache, c: css("--s3"), t: "Cache / buffers" }],
      `${bytes(m.used)} / ${bytes(m.total, 0)}`);
    if (m.swap_total) {
      const sp = (m.swap_used / m.swap_total) * 100;
      html += barRow("Swap", [{ v: sp, c: levelColor(lvl(sp, 50, 80)) }], `${bytes(m.swap_used)} / ${bytes(m.swap_total, 0)}`);
    }
    html += `<div class="legend-inline"><span><i style="background:${css("--s1")}"></i>Used by apps</span><span><i style="background:${css("--s3")}"></i>Cache / buffers (reclaimable)</span></div>`;
    $("memBreak").innerHTML = html;
  }

  function renderSensors(list) {
    const el = $("sensors"); $("sensorsHead").hidden = !list.length;
    el.innerHTML = list.map((s) => { const l = lvl(s.c, 70, 85); return `<span class="badge ${l}" title="${LVL_TEXT[l]}">${esc(s.name)} · ${Math.round(s.c)}°C</span>`; }).join("");
  }

  function renderGpus(gpus) {
    const box = $("gpus");
    const igpu = gpus.length && gpus.every((g) => g.integrated);
    $("gpuTitle").textContent = igpu ? "Integrated graphics" : "Graphics";
    $("gpuNote").textContent = !gpus.length ? "No GPU detected"
      : igpu ? `${gpus.map((g) => g.vendor).join(" + ")} iGPU · ${gpus[0].driver || "kernel driver"}`
      : `${gpus.length} device${gpus.length > 1 ? "s" : ""} detected`;
    const card = $("gpuCard");
    // only shown when the CPU's iGPU (or a graphics card) is enabled and has a kernel driver
    card.hidden = !gpus.length;
    if (!gpus.length) { box.innerHTML = ""; return; }
    box.innerHTML = gpus.map((g) => {
      const vr = g.vram_total && !g.integrated ? (g.vram_used / g.vram_total) * 100 : null, tl = lvl(g.temp, 80, 90);
      const clockTxt = g.clock ? Math.round(g.clock) + (g.clock_max ? " / " + Math.round(g.clock_max) : "") + " MHz" : "–";
      const metrics = g.integrated ? [
        ["Render node", g.render_node ? g.render_node.replace("/dev/dri/", "") : "–"],
        ["PCI", g.slot ? g.slot.replace(/^0000:/, "") : "–"], ["Driver", g.driver || "–"],
      ] : [
        ["Load", pct(g.util)], ["Temp", g.temp != null ? Math.round(g.temp) + "°C" : "–"],
        ["Power", g.power != null ? g.power.toFixed(0) + (g.power_limit ? " / " + g.power_limit.toFixed(0) : "") + " W" : "–"],
        ["Clock", clockTxt], ["Fan", g.fan != null ? g.fan + "%" : g.fan_rpm ? g.fan_rpm + " rpm" : "–"],
        ["Encoders", g.encoders != null ? g.encoders : "–"],
      ];
      const hw = g.hw_transcode;
      const hwNote = !hw ? "" : hw.ok
        ? `<div class="hw-note good"><span class="dot"></span>Plex can use it for hardware transcoding${g.vendor === "Intel" ? " (Quick Sync)" : ""}</div>`
        : `<div class="hw-note warn"><span class="dot"></span>Plex isn't in the <code>${esc(hw.group)}</code> group, so it can't hardware-transcode yet:
           <code>usermod -aG ${esc(hw.group)} plex &amp;&amp; systemctl restart plexmediaserver</code></div>`;
      const loadLabel = g.integrated && g.vendor === "Intel" ? "Load (busy time)" : "Core";
      const clockPct = g.clock && g.clock_max ? (g.clock / g.clock_max) * 100 : null;
      return `<div class="gpu${g.integrated ? " igpu" : ""}">
        <div class="drive-top"><div class="drive-title"><b><span class="kind">${esc(g.vendor)}${g.integrated ? " · iGPU" : ""}</span>${esc(g.name)}</b></div>
          ${g.temp != null ? `<span class="badge ${tl}">${LVL_TEXT[tl]}</span>` : ""}</div>
        ${g.util != null || !g.integrated ? barRow(loadLabel, [{ v: g.util || 0, c: css("--s1") }], pct(g.util)) : ""}
        ${clockPct != null ? barRow("Clock", [{ v: clockPct, c: css("--s2") }], clockTxt) : ""}
        ${vr != null ? barRow("VRAM", [{ v: vr, c: css("--s3") }], `${bytes(g.vram_used)} / ${bytes(g.vram_total, 0)}`) : ""}
        <div class="metrics">${metrics.map(([k, v]) => `<div class="metric"><small>${k}</small><b>${esc(String(v))}</b></div>`).join("")}</div>
        ${hwNote}
      </div>`;
    }).join("");
  }

  /* ------------------------------------------------------------ storage */
  let raidCache = { md: [], zfs: [] };
  async function loadStorage() {
    const d = await getJSON(S.api.storage);
    drivesCache = d.drives; raidCache = d.raid || { md: [], zfs: [] };
    renderArrays();
    $("mounts").innerHTML = d.mounts.map((m) => {
      const p = (m.used / m.total) * 100, l = lvl(p, 85, 95);
      return barRow(m.mount, [{ v: p, c: l === "good" ? css("--s1") : levelColor(l) }], `${bytes(m.used)} / ${bytes(m.total, 0)} · ${pct(p)}`,
        `${bytes(m.free)} free`);
    }).join("") || `<div class="empty">No mounts reported</div>`;
    renderDrives();
  }

  function driveHealth(dr) {
    if (!dr.smart) return { cls: "", text: "No SMART data" };
    const issues = [];
    if (dr.healthy === false) issues.push("SMART failed");
    if (dr.realloc > 0) issues.push(`${dr.realloc} reallocated`);
    if (dr.pending > 0) issues.push(`${dr.pending} pending`);
    if (dr.uncorrectable > 0) issues.push(`${dr.uncorrectable} uncorrectable`);
    if (dr.media_errors > 0) issues.push(`${dr.media_errors} media errors`);
    if (dr.spare != null && dr.spare < 20) issues.push(`spare ${dr.spare}%`);
    if (dr.healthy === false || dr.pending > 0 || dr.uncorrectable > 0 || (dr.wear_used ?? 0) >= 90) return { cls: "crit", text: "Failing", issues };
    if (issues.length || (dr.wear_used ?? 0) >= 75) return { cls: "warn", text: "Degraded", issues };
    return { cls: "good", text: "Healthy", issues };
  }

  const STATE_TXT = { good: "OK", warn: "Watch", crit: "Failing" };
  const STATE_ICON = { good: "✓", warn: "!", crit: "✕" };

  function ssdWear(dr) {
    if (dr.wear_used == null) {
      return `<div class="wear"><div class="wear-top"><span class="label">Wear level</span><b class="muted">Not reported</b></div>
        <p class="muted">This drive doesn't expose a wear indicator over SMART.</p></div>`;
    }
    const used = dr.wear_used, left = Math.max(0, 100 - used), l = lvl(used, 75, 90);
    let est = "";
    if (used >= 1 && dr.power_on_hours) {
      const hrsLeft = (dr.power_on_hours * left) / used;
      est = ` · about ${hrsLeft >= 8766 ? (hrsLeft / 8766).toFixed(1) + " years" : Math.round(hrsLeft / 24) + " days"} left at this pace`;
    }
    const tbw = dr.written != null && dr.power_on_hours ? ` · ${bytes(dr.written / (dr.power_on_hours / 24), 1)}/day written on average` : "";
    return `<div class="wear">
      <div class="wear-top"><span class="label">Wear level</span>${l === "good" ? "" : `<span class="badge ${l}">${STATE_ICON[l]} ${l === "warn" ? "Wearing" : "Near end of life"}</span>`}</div>
      <div class="wear-num"><b>${used}%</b><span>used</span><em>${left}% life left</em></div>
      <span class="bar wear-bar"><i style="width:${Math.max(used, 0.8)}%;background:${l === "good" ? css("--good") : levelColor(l)}"></i></span>
      <p class="muted">${dr.written != null ? bytes(dr.written, 1) + " written" : "Writes not reported"}${est}${tbw}</p>
    </div>`;
  }

  function hddSmart(dr) {
    if (!dr.attrs || !dr.attrs.length) return `<div class="empty">No SMART attributes reported</div>`;
    const key = dr.attrs.filter((a) => [1, 5, 7, 10, 184, 187, 188, 196, 197, 198, 199, 200].includes(a.id));
    const rest = dr.attrs.filter((a) => !key.includes(a));
    const row = (a) => `<tr class="${a.state}"><td class="id">${a.id}</td><td>${esc(a.name)}</td><td class="num">${esc(a.raw)}</td>
      <td class="num muted">${a.value ?? "–"}${a.thresh ? " / " + a.thresh : ""}</td>
      <td class="st"><span class="s-${a.state}">${STATE_ICON[a.state]} ${STATE_TXT[a.state]}</span></td></tr>`;
    const st = dr.self_test;
    return `<div class="smart">
      <div class="wear-top"><span class="label">SMART attributes</span>
        ${st ? `<span class="muted">Last self-test: ${esc(st.type || "")} · ${st.passed ? "passed" : esc(st.status || "failed")}${st.hours != null ? " at " + hours(st.hours) : ""}</span>` : ""}</div>
      <table class="smart-t"><thead><tr><th>ID</th><th>Attribute</th><th class="num">Raw</th><th class="num">Norm / Thr</th><th class="st">Status</th></tr></thead>
        <tbody>${key.map(row).join("")}</tbody></table>
      ${rest.length ? `<details><summary>${rest.length} more attributes</summary><table class="smart-t"><tbody>${rest.map(row).join("")}</tbody></table></details>` : ""}
    </div>`;
  }

  /* Drives: one compact row each; the full wear / SMART detail opens in a popover on click. */
  function driveDetail(dr) {
    const h = driveHealth(dr), hot = dr.kind === "NVMe" ? [70, 80] : dr.kind === "HDD" ? [50, 60] : [60, 70];
    const tl = lvl(dr.temp, hot[0], hot[1]), isHdd = dr.kind === "HDD";
    const metrics = isHdd ? [
      ["Temp", dr.temp != null ? `${dr.temp}°C` : "–", tl && tl !== "good" ? LVL_TEXT[tl] : ""],
      ["Power-on", hours(dr.power_on_hours)], ["Start/stops", dr.power_cycles ?? "–"],
    ] : [
      ["Temp", dr.temp != null ? `${dr.temp}°C` : "–", tl && tl !== "good" ? LVL_TEXT[tl] : ""],
      ["Power-on", hours(dr.power_on_hours)],
      [dr.kind === "NVMe" ? "Spare" : "Read", dr.kind === "NVMe" ? pct(dr.spare) : dr.read != null ? bytes(dr.read) : "–"],
    ];
    const body = !dr.smart ? `<div class="empty">No SMART data for this drive yet — <code>swizzin-smart.timer</code> refreshes every 10 min.</div>`
      : isHdd ? hddSmart(dr) : ssdWear(dr);
    return `<div class="drive-top">
        <div class="drive-title"><b><span class="kind">${esc(dr.kind)}</span>${esc(dr.model)}</b>
          <small>/dev/${esc(dr.name)} · ${bytes(dr.capacity, 2)}${dr.rpm ? " · " + dr.rpm + " rpm" : ""}${dr.firmware ? " · fw " + esc(dr.firmware) : ""}</small></div>
        <span class="badge ${h.cls}">${h.text}</span>
      </div>
      ${body}
      <div class="metrics">${metrics.map(([k, v, note]) => `<div class="metric"><small>${k}${note ? " · " + note : ""}</small><b>${v}</b></div>`).join("")}</div>
      <div class="io" data-dev="${esc(dr.name)}"><span>Read <b data-io="read">–</b></span><span>Write <b data-io="write">–</b></span><span>Busy <b data-io="busy">–</b></span></div>
      ${h.issues && h.issues.length ? `<div class="warnline">⚠ ${esc(h.issues.join(" · "))}</div>` : ""}`;
  }

  function renderDrives() {
    const box = $("drives");
    const anySmart = drivesCache.some((d) => d.smart);
    $("smartNote").textContent = anySmart ? "Click a drive for wear level (SSD) or SMART attributes (HDD)"
      : "Live I/O only — enable swizzin-smart.timer for wear level and SMART";
    box.innerHTML = drivesCache.map((dr, i) => {
      const h = driveHealth(dr), hot = dr.kind === "NVMe" ? [70, 80] : dr.kind === "HDD" ? [50, 60] : [60, 70];
      const tl = lvl(dr.temp, hot[0], hot[1]);
      let key = "";
      if (dr.kind !== "HDD" && dr.wear_used != null) {
        const l = lvl(dr.wear_used, 75, 90);
        key = `<span class="dr-wear" title="${100 - dr.wear_used}% life left"><span class="bar"><i style="width:${Math.max(dr.wear_used, 1)}%;background:${l === "good" ? css("--good") : levelColor(l)}"></i></span><b>${dr.wear_used}%</b><small>wear</small></span>`;
      } else if (dr.kind === "HDD" && dr.smart) {
        const bad = (dr.realloc || 0) + (dr.pending || 0) + (dr.uncorrectable || 0);
        key = `<span class="dr-wear"><b class="${bad ? "s-warn" : "s-good"}">${bad ? bad + " bad" : "0 bad"}</b><small>sectors</small></span>`;
      } else key = `<span class="dr-wear"><small>no SMART</small></span>`;
      return `<button class="drive-row" data-pop="popDrive" data-drive="${i}" aria-haspopup="dialog" aria-expanded="false" data-dev="${esc(dr.name)}">
        <span class="kind">${esc(dr.kind)}</span>
        <span class="dr-name"><b>${esc(dr.model)}</b><small>${esc(dr.name)} · ${bytes(dr.capacity, 1)}${dr.behind_controller ? " · behind RAID controller" : ""}${dr.hw_raid ? " · RAID volume" : ""}</small>${(dr.arrays || []).length ? `<span class="dr-arrays">${dr.arrays.map((a) => `<em class="${a.includes("✗") ? "bad" : ""}">${esc(a)}</em>`).join("")}</span>` : ""}</span>
        ${key}
        <span class="dr-busy" title="IO% — share of time the disk was busy"><small>IO</small><span class="bar"><i data-busy-bar></i></span><b data-io="busy">–</b></span>
        <span class="dr-io" title="Read"><span class="dr-io-head"><i class="dot s1"></i><b data-io="read">–</b></span><span class="spark" data-spark="read"></span></span>
        <span class="dr-io" title="Write"><span class="dr-io-head"><i class="dot s2"></i><b data-io="write">–</b></span><span class="spark" data-spark="write"></span></span>
        <span class="dr-temp ${tl && tl !== "good" ? "s-" + tl : ""}">${dr.temp != null ? dr.temp + "°" : "–"}</span>
        <span class="badge ${h.cls}">${h.text}</span>
      </button>`;
    }).join("") || `<div class="empty">No physical drives found</div>`;
    if (openPop && openPop.pop.id === "popDrive") closePop();
    if (lastLive) renderDriveIO(lastLive.disk_io);
  }

  const RAID_HEALTH = { healthy: ["good", "Healthy"], checking: ["good", "Checking"], rebuilding: ["warn", "Rebuilding"],
    degraded: ["crit", "Degraded"], inactive: ["crit", "Inactive"] };
  function renderArrays() {
    const box = $("arrays"), md = raidCache.md || [], zfs = raidCache.zfs || [];
    const hwVols = drivesCache.filter((d) => d.hw_raid);
    box.hidden = !md.length && !zfs.length && !hwVols.length;
    if (box.hidden) return;
    const mdHtml = md.map((a) => {
      const [cls, label] = RAID_HEALTH[a.health] || ["", a.health];
      const prog = a.progress != null ? `<div class="array-prog"><span class="bar"><i style="width:${a.progress}%;background:${cls === "warn" ? css("--warn") : css("--s1")}"></i></span>
        <small>${esc(a.action)} ${a.progress.toFixed(1)}%${a.finish ? " · " + dur(a.finish) + " left" : ""}${a.speed ? " · " + rate(a.speed) : ""}</small></div>` : "";
      const members = a.members.map((m) => `<span class="member ${m.failed ? "bad" : m.spare ? "spare" : ""}" title="${m.failed ? "failed" : m.spare ? "spare" : "active"} · slot ${m.slot}">${m.failed ? "✕" : m.spare ? "◌" : "✓"} ${esc(m.dev)}</span>`).join("");
      return `<div class="array" data-md="${esc(a.name)}">
        <div class="array-top"><span class="kind">${esc((a.level || "").toUpperCase())}</span><b>${esc(a.name)}</b>
          <small>${a.size ? bytes(a.size, 2) : ""}${a.map ? " · [" + esc(a.map) + "]" : ""}</small>
          <span class="array-io" data-aio>–</span><span class="badge ${cls}">${label}</span></div>
        <div class="array-members">${members}</div>${prog}</div>`;
    }).join("");
    const zHtml = zfs.map((p) => {
      const ok = p.health === "ONLINE", cls = ok ? "good" : p.health === "DEGRADED" ? "warn" : "crit";
      return `<div class="array"><div class="array-top"><span class="kind">ZFS</span><b>${esc(p.name)}</b>
        <small>${bytes(p.alloc)} of ${bytes(p.size, 1)} · ${p.cap ?? "–"}% full${p.frag != null ? " · frag " + p.frag + "%" : ""}</small>
        <span class="array-io"></span><span class="badge ${cls}">${esc(p.health)}</span></div></div>`;
    }).join("");
    const hwHtml = hwVols.map((d) => `<div class="array"><div class="array-top"><span class="kind">HW RAID</span><b>${esc(d.name)}</b>
        <small>${esc(d.model)} · ${bytes(d.capacity, 1)} · controller volume${drivesCache.some((x) => x.behind_controller) ? " — member disks listed below" : " — install smartmontools for member disk health"}</small>
        <span class="array-io"></span><span class="badge ${d.healthy === false ? "crit" : ""}">${d.healthy === false ? "Failing" : "Volume"}</span></div></div>`).join("");
    box.innerHTML = `<div class="sub-head"><span>Arrays</span><span class="muted">${md.length ? md.length + " mdadm" : ""}${zfs.length ? (md.length ? " · " : "") + zfs.length + " ZFS" : ""}${hwVols.length ? ((md.length || zfs.length) ? " · " : "") + hwVols.length + " hardware" : ""}</span></div>` + mdHtml + zHtml + hwHtml;
    if (lastLive) renderDriveIO(lastLive.disk_io);
  }

  /* btop-style live disk I/O: per-disk history of read/write (30 samples = 1 min) drawn as block bars */
  const DISK_HIST = 30, diskHist = {};
  function sparkBars(vals, color) {
    const max = Math.max(256 * 1024, ...vals), n = DISK_HIST, w = 3, gap = 1;
    let out = "";
    for (let i = 0; i < n; i++) {
      const v = vals[i - (n - vals.length)];
      if (v == null) continue;
      const h = Math.max(v > 0 ? 1.5 : 0.6, (v / max) * 22);
      out += `<rect x="${i * (w + gap)}" y="${(22 - h).toFixed(1)}" width="${w}" height="${h.toFixed(1)}" rx="1" fill="${color}" opacity="${v > 0 ? 0.9 : 0.25}"/>`;
    }
    return `<svg viewBox="0 0 ${n * (w + gap)} 22" preserveAspectRatio="none" aria-hidden="true">${out}</svg>`;
  }
  function renderDriveIO(io) {
    let tr = 0, tw = 0;
    const physical = new Set(drivesCache.map((d) => d.name));
    for (const [dev, d] of Object.entries(io)) {
      if (!physical.has(dev) && !dev.startsWith("md")) continue;
      const h = diskHist[dev] || (diskHist[dev] = { r: [], w: [] });
      h.r.push(d.read || 0); h.w.push(d.write || 0);
      if (h.r.length > DISK_HIST) { h.r.shift(); h.w.shift(); }
      if (physical.has(dev)) { tr += d.read || 0; tw += d.write || 0; }   // arrays would double count
    }
    document.querySelectorAll(".array[data-md]").forEach((el) => {
      const d = io[el.dataset.md]; if (!d) return;
      el.querySelector("[data-aio]").textContent = `${rate(d.read)} read · ${rate(d.write)} write`;
    });
    const tot = $("diskTotals");
    if (tot) tot.innerHTML = `<span><i class="dot s1"></i>Read <b>${rate(tr)}</b></span><span><i class="dot s2"></i>Write <b>${rate(tw)}</b></span>`;
    const c1 = css("--s1"), c2 = css("--s2");
    document.querySelectorAll(".drive-row, #popDrive .io").forEach((el) => {
      const d = io[el.dataset.dev]; if (!d) return;
      const set = (k, v) => { const n = el.querySelector(`[data-io="${k}"]`); if (n) n.textContent = v; };
      set("read", rate(d.read)); set("write", rate(d.write)); set("busy", pct(d.busy));
      const bar = el.querySelector("[data-busy-bar]");
      if (bar) { const b = d.busy || 0, l = lvl(b, 70, 90); bar.style.width = Math.max(b, 1) + "%"; bar.style.background = l === "good" ? c1 : levelColor(l); }
      const h = diskHist[el.dataset.dev], sr = el.querySelector('[data-spark="read"]'), sw = el.querySelector('[data-spark="write"]');
      if (h && sr) sr.innerHTML = sparkBars(h.r, c1);
      if (h && sw) sw.innerHTML = sparkBars(h.w, c2);
    });
  }

  /* ============================================================ server identity */
  let clock = null;          // { skew, tz, offsetMin }
  /* weather icons: netdiag's set (static/js/wxicons.js), line style, real moon phase at night */
  const WXI = window.SWZ_WX;
  const wxSvg = (w, size) => WXI ? WXI.wxIcon(w.code, w.is_day, "line", size, w.is_day ? null : WXI.moonNow(w.lat)) : "";
  const fmtOffset = (m) => `UTC${m < 0 ? "−" : "+"}${String(Math.floor(Math.abs(m) / 60)).padStart(2, "0")}:${String(Math.abs(m) % 60).padStart(2, "0")}`;
  function zoneOk(tz) { try { new Intl.DateTimeFormat(undefined, { timeZone: tz }); return true; } catch (_) { return false; } }
  function hm(min) { const a = Math.abs(min), h = Math.floor(a / 60), m = a % 60; return (h ? h + "h" : "") + (m ? (h ? " " : "") + m + "m" : h ? "" : "0m"); }

  function tickClock() {
    if (!clock) return;
    const now = new Date(Date.now() + clock.skew);
    const zone = clock.tz && zoneOk(clock.tz) ? { timeZone: clock.tz } : null;
    const h12 = store.get("clock24", "off") !== "on";
    const tz = zone || { timeZone: "UTC" }, at = zone ? now : new Date(now.getTime() + clock.offsetMin * 60000);
    const parts = new Intl.DateTimeFormat("en-US", { ...tz, hour: h12 ? "numeric" : "2-digit", minute: "2-digit", second: "2-digit", hour12: h12 }).formatToParts(at);
    const get = (type) => (parts.find((p) => p.type === type) || {}).value || "";
    $("clockTime").textContent = `${h12 ? get("hour") : get("hour").replace(/^24/, "00")}:${get("minute")}:${get("second")}`;
    $("clockAmPm").textContent = h12 ? get("dayPeriod").toUpperCase() : "";
    $("clockDate").textContent = new Intl.DateTimeFormat(undefined, { ...tz, weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(at);
    const mine = -new Date().getTimezoneOffset(), diff = clock.offsetMin - mine;
    $("clockYou").textContent = `Your time ${new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: h12 }).format(new Date())}`;
    $("clockOffset").textContent = diff === 0 ? "same time as you" : `server is ${hm(diff)} ${diff > 0 ? "ahead" : "behind"}`;
    $("clockBtn").textContent = h12 ? "Switch to 24-hour" : "Switch to 12-hour";
  }

  async function loadPlace() {
    const p = await getJSON(S.api.place);
    const tm = p.time;
    clock = { skew: tm.epoch_ms - Date.now(), tz: tm.tz, offsetMin: tm.offset_min };
    const named = tm.tz && tm.tz.includes("/") && !tm.tz.startsWith("Etc/");
    $("tzName").textContent = `${named ? tm.tz.replace(/_/g, " ") : tm.abbr} · ${fmtOffset(tm.offset_min)}`;
    $("tzShort").textContent = named ? tm.tz.split("/").pop().replace(/_/g, " ") : tm.abbr;
    tickClock();

    const g = p.geo || {}, loc = p.local || { v4: [], v6: [] }, pub = p.public || {};
    const setIp = (id, v) => { const el = $(id); el.textContent = v || "Not available"; el.classList.toggle("none", !v); el.title = v || "";
      el.nextElementSibling.hidden = !v; };
    setIp("ip4", pub.v4); setIp("ip4b", pub.v4); setIp("ip6", pub.v6);
    const where = g.city ? `${flagHTML(g.cc, g.country)}${esc(g.city)}` : "";
    $("ipWhere").innerHTML = where; $("ipWhere2").innerHTML = g.city ? `${where}, ${esc(g.country || g.cc)}` : "";
    const rows = [];
    // only show interface addresses when they differ from the public ones (NAT / extra IPs)
    loc.v4.filter((a) => a !== pub.v4).slice(0, 1).forEach((a) => rows.push([`Local IPv4 · ${p.iface}`, a]));
    loc.v6.filter((a) => a !== pub.v6).slice(0, 1).forEach((a) => rows.push([`Other IPv6 · ${p.iface}`, a]));
    if (g.isp) rows.push(["Provider", `${g.isp}${g.asn ? " · AS" + g.asn : ""}`, true]);
    if (!p.lookups) rows.push(["Lookups", "disabled in swizzin.cfg", true]);
    $("ipRows").innerHTML = rows.map(([k, v, sans]) => `<div><span>${esc(k)}</span><b class="${sans ? "sans" : ""}" title="${esc(v)}">${esc(v)}</b></div>`).join("");

    serverWeather = p.weather; serverLookups = p.lookups;
    const sLat = (p.weather && p.weather.lat) ?? (p.geo && p.geo.lat), sLon = (p.weather && p.weather.lon) ?? (p.geo && p.geo.lon);
    serverLoc = sLat != null && sLon != null ? { lat: sLat, lon: sLon } : null;
    if (SKY && serverLoc && store.get("wxMode", "server") !== "device") SKY.setLocation(serverLoc.lat, serverLoc.lon, "server");
    if (store.get("wxMode", "server") !== "device") renderWeather(p.weather, p.lookups);
  }

  let serverWeather = null, serverLookups = true, serverLoc = null;
  function renderWeather(w, lookups) {
    if (!w) {
      $("wxIcon").innerHTML = ""; $("wxIconBig").innerHTML = ""; $("wxTemp").textContent = "–"; $("wxTempBig").textContent = "–";
      $("wxText").textContent = lookups ? "Weather unavailable" : "Lookups disabled";
      $("wxPlace").textContent = ""; $("wxPlace2").textContent = ""; $("wxFeels").textContent = "–";
      $("wxFoot").textContent = ""; return;
    }
    const T = (v) => (v == null ? "–" : Math.round(v) + w.units.temp);
    const timeOnly = (iso) => (iso ? iso.slice(11, 16) : "–");
    $("wxPlace").textContent = w.place || ""; $("wxPlace2").textContent = w.place || "";
    const longName = (WXI && WXI.WX_CODES[w.code]) || w.text, shortName = (WXI && WXI.WX_SHORT[w.code]) || w.text;
    $("wxIcon").innerHTML = wxSvg(w, 28); $("wxIconBig").innerHTML = wxSvg(w, 64);
    $("wxTemp").textContent = T(w.temp); $("wxTempBig").textContent = T(w.temp);
    $("wxText").textContent = shortName;
    $("wxFeels").textContent = `${longName} · feels like ${T(w.feels)}`;
    if (WXI) {   // moon phase (lit fraction, waxing/waning) at this location's hemisphere
      const m = WXI.moonNow(w.lat);
      $("wxMoon").innerHTML = `<span class="moon-ico">${WXI.wxIcon(0, false, "line", 40, m)}</span>
        <span><b>${WXI.moonName(m.age)}</b><small>${Math.round(m.illum * 100)}% lit · day ${Math.round(m.age * 29.53)} of 29.5</small></span>`;
    }
    $("wxFoot").innerHTML = [
      ["H/L", `${T(w.high)} / ${T(w.low)}`], ["Humidity", w.humidity != null ? w.humidity + "%" : "–"],
      ["Wind", w.wind != null ? `${Math.round(w.wind)} ${w.units.wind}` : "–"],
      ["Rain", w.rain_chance != null ? w.rain_chance + "%" : "–"],
      ["Sun", `${timeOnly(w.sunrise)}–${timeOnly(w.sunset)}`],
    ].map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join("");
  }

  async function deviceWeather(ask) {
    const btn = $("wxLocate");
    if (!navigator.geolocation || !window.isSecureContext) {
      toast("Device location needs the panel on https://"); store.set("wxMode", "server"); return false;
    }
    btn.classList.add("busy");
    try {
      const pos = await new Promise((ok, no) => navigator.geolocation.getCurrentPosition(ok, no,
        { enableHighAccuracy: false, timeout: 10000, maximumAge: ask ? 0 : 30 * 60000 }));
      const q = `?lat=${pos.coords.latitude.toFixed(3)}&lon=${pos.coords.longitude.toFixed(3)}`;
      const r = await getJSON(S.api.weather + q);
      if (!r.weather) throw new Error(r.error || "no data");
      renderWeather(r.weather, true);
      if (SKY) SKY.setLocation(pos.coords.latitude, pos.coords.longitude, "device");
      btn.setAttribute("aria-pressed", "true"); btn.title = "Showing your location — click for the server's";
      store.set("wxMode", "device"); return true;
    } catch (err) {
      toast(err && err.code === 1 ? "Location permission denied" : "Couldn't get weather for your location");
      store.set("wxMode", "server"); btn.setAttribute("aria-pressed", "false"); renderWeather(serverWeather, serverLookups);
      return false;
    } finally { btn.classList.remove("busy"); }
  }
  if (S.admin) {
    $("wxLocate").addEventListener("click", () => {
      if (store.get("wxMode", "server") === "device") {
        store.set("wxMode", "server"); $("wxLocate").setAttribute("aria-pressed", "false");
        $("wxLocate").title = "Use this device's location"; renderWeather(serverWeather, serverLookups);
        if (SKY && serverLoc) SKY.setLocation(serverLoc.lat, serverLoc.lon, "server");
      } else deviceWeather(true);
    });
    $("clockBtn").addEventListener("click", () => { store.set("clock24", store.get("clock24", "off") === "on" ? "off" : "on"); tickClock(); });
  }

  /* ------------------------------------------------------------ status-bar popovers */
  let openPop = null;
  function closePop() {
    if (!openPop) return;
    openPop.pop.hidden = true; openPop.btn.setAttribute("aria-expanded", "false"); openPop = null;
  }
  function placePop(pop, anchor) {
    const z = zoom(), r = anchor.getBoundingClientRect(), w = pop.getBoundingClientRect().width;
    const left = Math.max(12, Math.min(r.left, innerWidth - w - 12));
    pop.style.left = (left + scrollX) / z + "px"; pop.style.top = (r.bottom + scrollY + 8) / z + "px";
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-pop]");
    if (btn) {
      const pop = $(btn.dataset.pop), same = openPop && openPop.pop === pop;
      closePop();
      if (!same) {
        if (btn.dataset.drive != null) { pop.innerHTML = `<div class="pop-scroll">${driveDetail(drivesCache[+btn.dataset.drive])}</div>`; if (lastLive) renderDriveIO(lastLive.disk_io); }
        if (btn.dataset.pop === "popSpecs") { loadSpecs(); loadSpeed(); }
        pop.hidden = false; placePop(pop, btn.closest(".sb") || btn); btn.setAttribute("aria-expanded", "true"); openPop = { pop, btn };
      }
      return;
    }
    if (openPop && !openPop.pop.contains(e.target)) closePop();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  addEventListener("resize", () => openPop && placePop(openPop.pop, openPop.btn.closest(".sb") || openPop.btn));

  /* ------------------------------------------------------------ server specs popover */
  let specsText = "";
  const gbit = (mbit) => (mbit == null ? null : mbit >= 1000 ? (mbit / 1000).toLocaleString() + " Gbit/s" : mbit + " Mbit/s");
  const decSize = (n) => {   // drive and DIMM labels use the maker's decimal/marketing sizes
    if (!n) return "";
    const tb = n / 1e12, gb = n / 1e9;
    return tb >= 1 ? (tb >= 10 ? Math.round(tb) : +tb.toFixed(1)) + " TB" : Math.round(gb) + " GB";
  };
  const ramSize = (n) => (n ? (n >= 2 ** 30 ? n / 2 ** 30 + " GB" : n / 2 ** 20 + " MB") : "");
  function renderSpecs(d) {
    const rows = [];   // [label, html value, plain-text value]
    const add = (k, html, text) => { if (html) rows.push([k, html, text != null ? text : html.replace(/<[^>]+>/g, "")]); };
    add("Hostname", esc(d.hostname) + (d.fqdn ? ` <small>${esc(d.fqdn)}</small>` : ""), d.hostname + (d.fqdn ? " (" + d.fqdn + ")" : ""));
    add("Operating system", esc(d.os));
    add("Kernel", esc([d.kernel, d.arch].filter(Boolean).join(" · ")));
    if (d.uptime != null) add("Uptime", esc(dur(d.uptime)));
    const cpuBits = [d.cpu_cores && `${d.cpu_cores} cores / ${d.cpu_threads} threads`, d.cpu_max_mhz && `up to ${(d.cpu_max_mhz / 1000).toFixed(2)} GHz`].filter(Boolean);
    add("CPU", esc(d.cpu) + (cpuBits.length ? ` <small>${esc(cpuBits.join(" · "))}</small>` : ""), d.cpu);
    const b = d.board || {};
    if (b.motherboard) add("Motherboard", esc(b.motherboard) + (b.motherboard_version ? ` <small>Version ${esc(b.motherboard_version)}</small>` : ""),
      b.motherboard + (b.motherboard_version ? ` (Version - ${b.motherboard_version})` : ""));
    if (b.system) add("System", esc(b.system));
    if (b.bios) add("BIOS", esc(b.bios) + (b.bios_detail ? ` <small>${esc(b.bios_detail)}</small>` : ""), b.bios);
    const m = d.memory || {};
    if (m.groups && m.groups.length) {
      const lines = m.groups.map((g) => [g.vendor, g.part].filter(Boolean).join(" ") + ` ${ramSize(g.size)} × ${g.count}` +
        ([g.type, g.speed].filter(Boolean).length ? ` · ${[g.type, g.speed].filter(Boolean).join(" ")}` : ""));
      add("RAM", lines.map(esc).join("<br>") + ` <small>${bytes(m.total, 0)} usable</small>`, lines.join("; "));
    } else if (m.total) add("RAM", `${bytes(m.total, 0)} <small>module details appear after the panel's hourly host scan</small>`, bytes(m.total, 0));
    for (const st of d.storage || []) {
      const raid = (st.raid || []).map((r) => `${(r.level || "").replace(/^raid/i, "RAID ")} (${r.name})`).join(", ");
      const line = `${st.model} ${decSize(st.capacity)} ${st.kind === "ZFS" ? "" : st.kind}${st.count > 1 ? " × " + st.count : ""}${raid ? " · " + raid : ""}`.replace(/\s+/g, " ").trim();
      add({ HDD: "HDD", ZFS: "ZFS", RAID: "RAID disk", NVMe: "NVMe SSD" }[st.kind] || "SSD", esc(line));
    }
    const nics = (d.network || []).filter((n) => n.kind === "Ethernet" || n.kind === "Wi-Fi");
    for (const n of nics) add(n.kind === "Wi-Fi" ? "Wi-Fi" : "Ethernet card", esc(n.name) + ` <small>${esc([n.iface, gbit(n.speed), !n.up ? "down" : ""].filter(Boolean).join(" · "))}</small>`, n.name);
    const def = nics.find((n) => n.default) || nics[0];
    if (d.bandwidth_note || (def && def.speed)) add("Public bandwidth", esc(d.bandwidth_note || `${gbit(def.speed)} port`) +
      (!d.bandwidth_note ? ` <small>set SERVER_BANDWIDTH in swizzin.cfg to describe your plan</small>` : ""), d.bandwidth_note || `${gbit(def.speed)} port`);
    portMbps = def && def.speed > 0 ? def.speed : null;
    add("Speed test", `<span id="speedtest" class="st">${speedHTML()}</span>`, "@speedtest");
    for (const g of d.graphics || []) add(g.integrated ? "iGPU" : "GPU", esc(g.name) + ` <small>${esc(g.state + (g.driver ? " · " + g.driver : ""))}</small>`, `${g.name} (${g.state})`);
    $("specs").innerHTML = rows.map(([k, v]) => `<div class="spec"><span>${esc(k)}</span><b>${v}</b></div>`).join("");
    specsRows = rows.map(([k, , t]) => [k, t]);
    buildSpecsText();
  }

  /* ------------------------------------------------------------ speed test (M-Lab ndt7)
     The test runs on the server (core/ndt7.py), never in the viewer's browser, so it measures the
     server's own line to the nearest M-Lab site. It only starts when the admin presses the button. */
  let speed = null, speedTimer = null, specsRows = [], portMbps = null;
  const mbit = (v) => (v == null ? "–" : v >= 1000 ? (v / 1000).toFixed(2) + " Gbit/s" : (v >= 100 ? Math.round(v) : v.toFixed(1)) + " Mbit/s");
  const PHASE = { locate: "Finding the nearest M-Lab server…", download: "Testing download", upload: "Testing upload" };
  function speedPlain(r) {
    if (!r) return "not run yet";
    const where = r.server && [r.server.city, r.server.country].filter(Boolean).join(", ");
    return `↓ ${mbit(r.download)} / ↑ ${mbit(r.upload)}` + (r.latency_ms != null ? ` · ${r.latency_ms} ms ping` : "") +
      ` (M-Lab${where ? " " + where : ""}, ${new Date(r.at * 1000).toLocaleString()})`;
  }
  function buildSpecsText() {
    specsText = specsRows.map(([k, t]) => `${k} - ${t === "@speedtest" ? speedPlain(speed && speed.last) : t}`).join("\n");
  }
  function speedHTML() {
    const st = speed, last = st && st.last;
    const where = last && last.server && [last.server.city, last.server.country].filter(Boolean).join(", ");
    let h = "";
    if (st && st.running) {
      const live = st.phase !== "locate" && st.mbps != null ? ` <span class="st-live">${esc(mbit(st.mbps))}</span>` : "";
      const step = st.phase === "upload" ? 2 : st.phase === "download" ? 1 : 0;
      h += `<span class="st-run" role="status">${esc(PHASE[st.phase] || "Starting…")}${live}</span>
        <span class="st-steps" aria-hidden="true"><i class="${step === 1 ? "now" : step > 1 ? "on" : ""}"></i><i class="${step === 2 ? "now" : ""}"></i></span>`;
      if (st.server && st.server.city) h += `<small>${flagHTML(st.server.country)}M-Lab ${esc([st.server.city, st.server.country].filter(Boolean).join(", "))}</small>`;
    } else if (last) {
      h += `<span class="st-res"><span title="Download">↓ ${esc(mbit(last.download))}</span><span title="Upload">↑ ${esc(mbit(last.upload))}</span>` +
        (last.latency_ms != null ? `<span title="Lowest round-trip time to the M-Lab server">${esc(last.latency_ms)} ms ping</span>` : "") + `</span>
        <small>${last.server ? flagHTML(last.server.country) : ""}M-Lab${where ? " " + esc(where) : ""} · ${esc(ago(Math.max(0, Date.now() / 1000 - last.at)))}` +
        (portMbps && last.download ? ` · ${Math.round(Math.min(last.download, portMbps) / portMbps * 100)}% of the port` : "") + `</small>`;
    } else if (st) h += `<span class="muted">Not run yet</span>`;
    else h += `<span class="muted">…</span>`;
    if (st && st.error && !st.running) h += `<small class="st-err">${esc(st.error)}</small>`;
    if (st && !st.running) {
      const wait = st.cooldown > 0;
      h += `<span class="st-act"><button class="pill pop-mini" id="speedRun" ${wait ? "disabled" : ""}>${last ? "Run again" : "Run speed test"}</button>` +
        (wait ? `<small>available in ${Math.ceil(st.cooldown / 60)} min</small>` : "") + `</span>`;
      h += `<small class="st-note">Runs from the server to the nearest M-Lab site for about 20&nbsp;s and moves up to ${
        portMbps ? "about " + Math.max(1, Math.round(portMbps * 20 / 8 / 1000 * 10) / 10) + " GB" : "a few GB"}. M-Lab publishes every result, including this server's IP address.</small>`;
    }
    return h;
  }
  function paintSpeed() {
    const el = $("speedtest");
    if (el) el.innerHTML = speedHTML();
    buildSpecsText();
  }
  function pollSpeed(ms) {
    clearTimeout(speedTimer);
    speedTimer = setTimeout(loadSpeed, ms);
  }
  async function loadSpeed() {
    try { speed = await getJSON(S.api.speedtest); }
    catch (_) { if (!speed) speed = { running: false, error: "Couldn't read the speed test status.", cooldown: 0 }; }
    paintSpeed();
    if (speed.running) pollSpeed(1000);
    else if (speed.cooldown > 0 && openPop && openPop.pop.id === "popSpecs") pollSpeed(30000);
  }
  async function runSpeed(btn) {
    btn.disabled = true;
    try {
      const r = await fetch(S.api.speedtest, { method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (r.status === 401) { location.reload(); return; }
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      speed = d;
      if (!d.started_now && d.message) toast(d.message);
    } catch (e) { toast("Couldn't start the speed test" + (e.message ? ": " + e.message : "")); }
    paintSpeed();
    if (speed && speed.running) pollSpeed(800);
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("#speedRun");
    if (b) { e.stopPropagation(); runSpeed(b); }
  });
  async function loadSpecs() {
    try { renderSpecs(await getJSON(S.api.specs)); }
    catch (_) { if (!specsText) $("specs").innerHTML = `<div class="empty">Couldn't read the server's specifications.</div>`; }
  }
  if (S.admin && $("specsCopy")) $("specsCopy").addEventListener("click", async (e) => {
    e.stopPropagation();
    try { await navigator.clipboard.writeText(specsText); toast("Specifications copied"); }
    catch (_) { toast("Copy needs https — select the text instead"); }
  });

  /* ------------------------------------------------------------ fail2ban */
  function ago(sec) {
    if (sec < 60) return "just now";
    if (sec < 3600) return Math.floor(sec / 60) + " min ago";
    if (sec < 86400) return Math.floor(sec / 3600) + " h ago";
    return Math.floor(sec / 86400) + " d ago";
  }
  const REGION = (() => { try { return new Intl.DisplayNames(undefined, { type: "region" }); } catch (_) { return null; } })();
  const countryName = (cc) => { try { return (REGION && REGION.of(cc.toUpperCase())) || cc.toUpperCase(); } catch (_) { return cc.toUpperCase(); } };
  let banLookupTimer = 0;
  function renderBanCountries(c) {
    const wrap = $("f2bCcWrap");
    if (!c || (!c.top.length && !c.unknown)) { wrap.hidden = true; return; }
    wrap.hidden = false;
    $("f2bCcTitle").textContent = c.window === "7d" ? "Bans by country · 7 days" : "Recent bans by country";
    const max = Math.max(1, ...c.top.map((r) => r.bans));
    const rows = c.top.slice(0, 6).map((r) => `<div class="ccrow" title="${esc(countryName(r.cc))}: ${r.bans} bans from ${r.ips} address${r.ips === 1 ? "" : "es"}">
        <span class="ccname">${flagHTML(r.cc, countryName(r.cc))}${esc(countryName(r.cc))}</span>
        <span class="ccbar"><i style="width:${Math.max(3, (r.bans / max) * 100).toFixed(1)}%"></i></span>
        <b>${r.bans}</b></div>`).join("");
    const rest = c.top.slice(6).reduce((a, r) => a + r.bans, 0) + c.other;
    $("f2bCountries").innerHTML = rows || `<div class="empty">Looking up countries…</div>`;
    const notes = [];
    if (rest) notes.push(`${rest} more elsewhere`);
    if (c.unknown) notes.push(c.lookups ? `${c.unknown} being looked up` : `${c.unknown} unknown (lookups off)`);
    $("f2bCcNote").textContent = notes.join(" · ");
    // lookups run in the background a batch at a time: check back soon while some are pending
    clearTimeout(banLookupTimer);
    if (c.unknown && c.lookups) banLookupTimer = setTimeout(loadF2b, 15000);
  }
  async function loadF2b() {
    const d = await getJSON(S.api.fail2ban), st = $("f2bState");
    if (!d.available) {
      st.className = "badge"; st.textContent = "";
      $("f2bNote").textContent = "fail2ban";
      $("f2bStats").innerHTML = "";
      $("f2bList").innerHTML = `<div class="empty">Ban data appears once <code>swizzin-f2b.timer</code> is running (installed with the panel; run <code>box update</code>).</div>`;
      return;
    }
    const nowS = Date.now() / 1000;
    st.className = "badge " + (d.running ? "good" : "crit"); st.textContent = d.running ? "Protecting" : "Not running";
    $("f2bNote").textContent = `fail2ban · updated ${ago(d.age)}`;
    const failed = d.jails.reduce((a, j) => a + j.currently_failed, 0);
    $("f2bStats").innerHTML = [["Banned now", d.banned_now ?? 0], ["Bans 24 h", d.bans_24h], ["Bans 7 d", d.bans_7d], ["Failing now", failed]]
      .map(([k, v]) => `<div class="metric"><small>${k}</small><b>${v}</b></div>`).join("");
    $("f2bJails").textContent = d.jails.map((j) => `${jailName(j.name)} ${j.currently_banned}`).join(" · ");
    // which addresses are behind "Failing now" (collector sheergood-14 and later)
    const failing = Array.isArray(d.failing) ? d.failing : [];
    $("f2bFailWrap").hidden = !failing.length;
    $("f2bFailing").innerHTML = failing.slice(0, 25).map((f) => `<div class="ban"><code title="${esc(f.ip)}${f.cc ? " · " + esc(countryName(f.cc)) : ""}">${f.cc ? flagHTML(f.cc, countryName(f.cc)) : ""}${esc(f.ip)}</code>
        <span class="when" title="${new Date(Number(f.last) * 1000).toLocaleString()}">${ago(Math.max(0, nowS - Number(f.last)))}</span>
        <span class="meta"><span class="jail" title="${esc(f.jail)}">${esc(jailName(f.jail))}</span>${Number(f.count) || 0} of ${Number(f.maxretry) || "?"} tries before a ban</span>
        <span class="st off">Watching</span></div>`).join("");
    const fTile = $("f2bStats").querySelectorAll(".metric")[3];
    if (fTile) fTile.title = failing.length ? failing.map((f) => `${f.ip} · ${jailName(f.jail)} · ${f.count}/${f.maxretry}`).join("\n")
      : "Addresses with a failed login that haven't reached the ban limit";
    renderBanCountries(d.countries);
    $("f2bList").innerHTML = d.recent.length ? d.recent.slice(0, 25).map((b) => {
      const left = b.active && b.bantime > 0 ? b.time + b.bantime - nowS : null;
      const status = b.active ? (b.bantime < 0 ? "Banned forever" : left > 0 ? `Banned · ${dur(left)} left` : "Banned") : "Expired";
      return `<div class="ban"><code title="${esc(b.ip)}${b.cc ? " · " + esc(countryName(b.cc)) : ""}">${b.cc ? flagHTML(b.cc, countryName(b.cc)) : ""}${esc(b.ip)}</code>
        <span class="when" title="${new Date(b.time * 1000).toLocaleString()}">${ago(nowS - b.time)}</span>
        <span class="meta"><span class="jail" title="${esc(b.jail)}">${esc(jailName(b.jail))}</span>${b.failures ? b.failures + " failures" : ""}${b.count > 1 ? ` · ban #${b.count}` : ""}</span>
        <span class="st ${b.active ? "on" : "off"}">${status}</span></div>`;
    }).join("") : `<div class="empty">No bans recorded yet</div>`;
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest(".copy"); if (!b) return;
    const txt = $(b.dataset.copy).textContent;
    try { await navigator.clipboard.writeText(txt); b.classList.add("done"); b.textContent = "✓"; toast(`Copied ${txt}`); }
    catch (_) { toast("Copy not allowed on plain http — select the IP instead"); }
    setTimeout(() => { b.classList.remove("done"); b.textContent = "⧉"; }, 1500);
  });

  /* ============================================================ data transferred */
  let traffic = null;
  const DAY = new Intl.DateTimeFormat(undefined, { weekday: "short" });
  const DATE = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "short" });
  const SINCE = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
  const parseDay = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
  const ratio = (t) => (t.rx ? (t.tx / t.rx).toFixed(2) : "–");

  async function loadTraffic() {
    const t = await getJSON(S.api.traffic);
    traffic = t;
    if (!t.available) {
      $("trafficNote").textContent = t.reason || "vnstat unavailable";
      $("periods").innerHTML = `<div class="empty" style="grid-column:1/-1">No traffic history yet — vnstat collects it every 5 minutes once it's running (<code>systemctl enable --now vnstat</code>).</div>`;
      $("bars").innerHTML = ""; return;
    }
    $("trafficNote").textContent = `${t.iface} · counted by vnstat${t.since ? " since " + SINCE.format(parseDay(t.since)) : ""}`;
    const periods = [
      ["Today", t.today, true], ["Yesterday", t.yesterday], ["This week", t.week],
      ["Last 7 days", t.last7], ["This month", t.month], ["All time", t.all, true],
    ];
    $("periods").innerHTML = periods.map(([label, v, hl]) => `
      <div class="period${hl ? " hl" : ""}">
        <div class="p-label">${label}</div>
        <div class="p-up"><span class="arrow" aria-label="Uploaded">▲</span><b>${bytes(v.tx, 2)}</b></div>
        <div class="p-sub"><span>▼ <b>${bytes(v.rx, 2)}</b></span><span>ratio <b>${ratio(v)}</b></span></div>
      </div>`).join("");
    renderBars(t.days);
    renderHours(t.hours);
    renderMonth(t);
    renderWeekTable(t.days, t.last7);
  }

  function niceMax(m) {
    if (m <= 0) return 1;
    const p = Math.pow(1024, Math.floor(Math.log(m) / Math.log(1024)));
    for (const s of [1, 2, 4, 8, 10, 20, 40, 80, 100, 200, 400, 800, 1000]) if (s * p >= m * 1.08) return s * p;
    return m * 1.2;
  }

  const HOUR = new Intl.DateTimeFormat(undefined, { hour: "numeric" });
  const HOUR_LONG = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
  function renderBarChart(el, items, labelOf, isNow, labelEvery) {
    const max = niceMax(Math.max(...items.map((d) => Math.max(d.tx, d.rx))));
    const best = items.reduce((a, d, i) => (d.tx > items[a].tx ? i : a), 0);
    let html = "";
    for (let k = 0; k <= 4; k++) {
      html += `<div class="gl${k ? "" : " base"}" style="bottom:${24 + (186 * k) / 4}px"><span>${k ? bytes((max * k) / 4, 0) : "0"}</span></div>`;
    }
    html += `<div class="cols" style="grid-template-columns:repeat(${items.length},1fr)">` + items.map((d, i) => {
      const up = (d.tx / max) * 100, down = (d.rx / max) * 100, now = isNow(i);
      // direct label only on the busiest upload bar and the current one, never on every bar
      const label = (i === best || now) && d.tx > 0 ? `<span class="vl" style="bottom:calc(${up}% + 6px)">${bytes(d.tx, 1)}</span>` : "";
      const xl = i % labelEvery === 0 || now ? `<span class="xl">${labelOf(d, i)}</span>` : "";
      return `<div class="col${now ? " today" : ""}" data-i="${i}">${label}<i class="up" style="height:${up}%"></i><i class="down" style="height:${down}%"></i>${xl}</div>`;
    }).join("") + `</div>`;
    el.innerHTML = html;
  }
  function renderBars(days) {
    renderBarChart($("bars"), days, (d, i) => (i === days.length - 1 ? "Today" : DAY.format(parseDay(d.date))), (i) => i === days.length - 1, 1);
  }
  function renderHours(hours) {
    if (!hours || !hours.length) { $("hourBars").innerHTML = `<div class="empty">No hourly data from vnstat yet</div>`; return; }
    renderBarChart($("hourBars"), hours, (d, i) => (i === hours.length - 1 ? "Now" : HOUR.format(new Date(d.time))), (i) => i === hours.length - 1, 3);
  }

  const MON = new Intl.DateTimeFormat(undefined, { month: "short" });
  const MONLONG = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });
  function renderMonth(t) {
    const md = t.month_days || [], ms = t.months || [];
    if (md.length) renderBarChart($("monthBars"), md, (d) => String(+d.date.slice(8)), (i) => i === md.length - 1, md.length > 16 ? 3 : 1);
    else $("monthBars").innerHTML = `<div class="empty">No daily data for this month yet</div>`;
    if (ms.length) renderBarChart($("yearBars"), ms, (m) => MON.format(new Date(+m.month.slice(0, 4), +m.month.slice(5) - 1, 1)), (i) => i === ms.length - 1, 1);
    else $("yearBars").innerHTML = `<div class="empty">No monthly data yet</div>`;
    const best = md.reduce((a, d) => (!a || d.tx + d.rx > a.tx + a.rx ? d : a), null), p = t.month_projection;
    $("monthFoot").innerHTML = [
      `<span>${esc(MONLONG.format(new Date()))}: <b>▲ ${bytes(t.month.tx, 2)}</b> · ▼ ${bytes(t.month.rx, 2)}</span>`,
      best && best.tx + best.rx > 0 ? `<span>Busiest day: <b>${esc(DATE.format(parseDay(best.date)))}</b> · ${bytes(best.tx + best.rx, 2)}</span>` : "",
      p ? `<span title="Straight-line estimate from the days so far">On pace for <b>▲ ${bytes(p.tx, 1)}</b> · ▼ ${bytes(p.rx, 1)} this month</span>` : "",
    ].filter(Boolean).join("");
  }

  function renderWeekTable(days, sum) {
    const row = (name, d) => `<tr><td>${name}</td><td>${bytes(d.tx, 2)}</td><td>${bytes(d.rx, 2)}</td><td>${bytes(d.tx + d.rx, 2)}</td><td>${ratio(d)}</td></tr>`;
    $("weekTable").innerHTML = `<table><thead><tr><th>Day</th><th>Upload</th><th>Download</th><th>Total</th><th>Ratio</th></tr></thead>
      <tbody>${days.slice().reverse().map((d, i) => row(i === 0 ? "Today" : DATE.format(parseDay(d.date)), d)).join("")}</tbody>
      <tfoot>${row("7-day total", sum)}</tfoot></table>`;
  }

  if (S.admin) {
    $("hourBars").addEventListener("pointermove", (e) => {
      const col = e.target.closest(".col"); if (!col || !traffic || !traffic.hours) return hideTip();
      const d = traffic.hours[+col.dataset.i], end = new Date(new Date(d.time).getTime() + 3600000);
      showTip(`<div class="t-title">${HOUR_LONG.format(new Date(d.time))} – ${HOUR.format(end)}</div>
        <div class="t-row"><span><i class="dot s2"></i>Upload</span><b>${bytes(d.tx, 2)}</b></div>
        <div class="t-row"><span><i class="dot s1"></i>Download</span><b>${bytes(d.rx, 2)}</b></div>
        <div class="t-row"><span>Average up</span><b>${mbps(d.tx / 3600)}</b></div>`, e.clientX, e.clientY);
    });
    $("hourBars").addEventListener("pointerleave", hideTip);
    $("bars").addEventListener("pointermove", (e) => {
      const col = e.target.closest(".col"); if (!col || !traffic) return hideTip();
      const d = traffic.days[+col.dataset.i];
      showTip(`<div class="t-title">${DATE.format(parseDay(d.date))}</div>
        <div class="t-row"><span><i class="dot s2"></i>Upload</span><b>${bytes(d.tx, 2)}</b></div>
        <div class="t-row"><span><i class="dot s1"></i>Download</span><b>${bytes(d.rx, 2)}</b></div>
        <div class="t-row"><span>Ratio</span><b>${ratio(d)}</b></div>`, e.clientX, e.clientY);
    });
    $("bars").addEventListener("pointerleave", hideTip);
    const monthTip = (el, list, title) => {
      el.addEventListener("pointermove", (e) => {
        const col = e.target.closest(".col"); if (!col || !traffic) return hideTip();
        const d = list()[+col.dataset.i]; if (!d) return hideTip();
        showTip(`<div class="t-title">${esc(title(d))}</div>
          <div class="t-row"><span><i class="dot s2"></i>Upload</span><b>${bytes(d.tx, 2)}</b></div>
          <div class="t-row"><span><i class="dot s1"></i>Download</span><b>${bytes(d.rx, 2)}</b></div>
          <div class="t-row"><span>Ratio</span><b>${ratio(d)}</b></div>`, e.clientX, e.clientY);
      });
      el.addEventListener("pointerleave", hideTip);
    };
    monthTip($("monthBars"), () => traffic.month_days || [], (d) => DATE.format(parseDay(d.date)) + (d.known === false ? " · not recorded" : ""));
    monthTip($("yearBars"), () => traffic.months || [], (m) => MONLONG.format(new Date(+m.month.slice(0, 4), +m.month.slice(5) - 1, 1)));
    document.querySelectorAll("#trafficCard .seg button").forEach((b) => b.addEventListener("click", () => {
      document.querySelectorAll("#trafficCard .seg button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      const v = b.dataset.view;
      $("bars").hidden = v !== "chart"; $("hourBars").hidden = v !== "hours"; $("weekTable").hidden = v !== "table";
      $("monthBars").hidden = v !== "month"; $("yearBars").hidden = v !== "year"; $("monthFoot").hidden = v !== "month" && v !== "year";
      $("weekTitle").textContent = { hours: "Last 24 hours", month: "This month, day by day", year: "Last 12 months" }[v] || "Last 7 days";
    }));

  }

  /* App icons: the letter is always drawn underneath (CSS ::before); a broken image is simply removed,
     so the tile never ends up empty. One capture listener works for every <img>, no inline handlers. */
  const iconHTML = (cls, p) => `<span class="${cls}" data-letter="${esc((p.pretty_name || "?")[0])}"><img src="${S.iconBase}${encodeURIComponent(p.img)}.png" alt="" decoding="async"></span>`;
  document.addEventListener("load", (e) => {
    const t = e.target;
    if (t && t.tagName === "IMG" && t.parentElement && t.parentElement.dataset.letter != null) t.parentElement.classList.add("has-img");
  }, true);
  document.addEventListener("error", (e) => {
    const t = e.target;
    if (t && t.tagName === "IMG" && t.parentElement && t.parentElement.dataset.letter != null) t.remove();
  }, true);

  /* ============================================================ sidebar */
  function applySide() {
    const mini = store.get("side", "full") === "mini";
    document.documentElement.dataset.side = mini ? "mini" : "full";
    const t = $("sideToggle"); t.setAttribute("aria-expanded", String(!mini));
    t.title = mini ? "Expand sidebar" : "Collapse sidebar"; t.setAttribute("aria-label", t.title);
  }
  function setDrawer(open) {
    document.documentElement.dataset.drawer = open ? "open" : "closed"; $("sideBackdrop").hidden = !open;
  }
  (function buildSide() {
    const list = $("sideList");
    S.pages.filter((p) => p.url).sort((a, b) => a.pretty_name.localeCompare(b.pretty_name)).forEach((p) => {
      const a = document.createElement("a");
      a.className = "side-item"; a.href = p.url; a.target = "_blank"; a.rel = "noopener"; a.title = p.pretty_name; a.dataset.app = p.name;
      a.innerHTML = iconHTML("side-ico", p).replace("</span>", '<i class="side-dot"></i></span>') + `<span class="side-label">${esc(p.pretty_name)}</span>`;
      list.appendChild(a);
    });
    applySide();
    $("sideToggle").addEventListener("click", () => { store.set("side", store.get("side", "full") === "mini" ? "full" : "mini"); applySide(); });
    $("sideOpen").addEventListener("click", () => setDrawer(true));
    $("sideBackdrop").addEventListener("click", () => setDrawer(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setDrawer(false); });
  })();
  function sideDots() {
    document.querySelectorAll(".side-item[data-app]").forEach((a) => {
      const st = statusMap[a.dataset.app], dot = a.querySelector(".side-dot");
      dot.className = "side-dot" + (st ? (st.active ? " on" : " off") : "");
      a.title = a.querySelector(".side-label").textContent + (st ? (st.active ? " · running" : " · stopped") : "");
    });
  }

  /* ============================================================ processes (btop-style) */
  let procSort = "cpu", procData = null, procVisible = false;
  function renderProcs() {
    if (!procData) return;
    const q = ($("procSearch").value || "").toLowerCase();
    let list = procData.procs.filter((p) => !q || p.name.toLowerCase().includes(q) || (p.cmd || "").toLowerCase().includes(q) || p.user.includes(q) || String(p.pid) === q);
    list.sort((a, b) => procSort === "cpu" ? (b.cpu - a.cpu) || (b.rss - a.rss) : b.rss - a.rss);
    list = list.slice(0, 20);
    const maxCpu = Math.max(100, ...list.map((p) => p.cpu)), memTotal = (lastLive && lastLive.mem.total) || 1;
    $("procs").innerHTML = list.map((p) => {
      const cl = lvl(p.cpu, 80, 200);
      return `<div class="proc-row" role="row" title="${esc(p.cmd || p.name)}">
        <span class="num mono">${p.pid}</span>
        <span class="pname"><b>${esc(p.name)}</b><small>${esc((p.cmd || "").replace(/^\S*\//, ""))}</small></span>
        <span class="puser">${esc(p.user)}</span>
        <span class="num">${p.threads}</span>
        <span class="pbar"><span class="bar"><i style="width:${Math.min(100, (p.cpu / maxCpu) * 100)}%;background:${cl === "good" ? css("--s1") : levelColor(cl)}"></i></span><b>${p.cpu.toFixed(1)}%</b></span>
        <span class="pbar"><span class="bar"><i style="width:${Math.min(100, (p.rss / memTotal) * 100 * 4)}%;background:${css("--s3")}"></i></span><b>${bytes(p.rss)}</b></span>
      </div>`;
    }).join("") || `<div class="empty">No matching processes</div>`;
    $("procNote").textContent = `${procData.count} processes · top ${list.length} by ${procSort === "cpu" ? "CPU (100 % = one core)" : "memory"}${procData.warming ? " · measuring…" : ""}`;
  }
  async function loadProcs() {
    if (!procVisible || document.hidden) return;
    procData = await getJSON(S.api.procs); renderProcs();
  }
  if (S.admin) {
    new IntersectionObserver((ents) => { procVisible = ents.some((e) => e.isIntersecting); if (procVisible) loadProcs().catch(() => {}); }).observe($("procCard"));
    document.querySelectorAll("[data-psort]").forEach((b) => b.addEventListener("click", () => {
      procSort = b.dataset.psort; document.querySelectorAll("[data-psort]").forEach((x) => x.setAttribute("aria-selected", String(x === b))); renderProcs();
    }));
    $("procSearch").addEventListener("input", renderProcs);
  }

  /* ============================================================ Plex: now playing */
  let plexVisible = true, plexInstalled = null;
  const kbps = (k) => (k == null ? "–" : k >= 1000 ? (k / 1000).toFixed(k >= 10000 ? 0 : 1) + " Mbps" : Math.round(k) + " kbps");
  const mediaTime = (ms) => { if (ms == null) return "–"; const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };
  const PLEX_MSG = {
    no_token: "Plex is installed, but the panel doesn't have its local token yet. The panel's host scan copies it hourly; run <code>systemctl start swizzin-host</code> to do it now, or set <code>PLEX_TOKEN</code> in swizzin.cfg.",
    unreachable: "Plex isn't answering on this server right now (is <code>plexmediaserver</code> running?).",
    unauthorized: "Plex refused the panel's token. It refreshes hourly; run <code>systemctl start swizzin-host</code> to refresh it now.",
    error: "Plex returned an error. Try again in a moment.",
  };
  function renderPlex(d) {
    const card = $("plexCard");
    plexInstalled = !!d.installed; card.hidden = !d.installed;
    if (!d.installed) return;
    if (d.status !== "ok") {
      $("plexNote").textContent = "Plex Media Server"; $("plexStats").innerHTML = "";
      $("plexList").innerHTML = `<div class="empty">${PLEX_MSG[d.status] || PLEX_MSG.error}</div>`; return;
    }
    $("plexNote").textContent = d.count ? `${d.count} stream${d.count > 1 ? "s" : ""} · refreshes every 5 s` : "Nothing playing right now";
    $("plexStats").innerHTML = [["Streams", d.count], ["Transcoding", d.transcodes + (d.hw_transcodes ? ` <small>(${d.hw_transcodes} HW)</small>` : "")], ["Bandwidth", kbps(d.bandwidth)]]
      .map(([k, v]) => `<div class="metric"><small>${k}</small><b>${v}</b></div>`).join("");
    $("plexList").innerHTML = d.sessions.length ? d.sessions.map((x) => {
      const dec = x.decision === "transcode" ? "warn" : x.decision === "direct stream" ? "info" : "good";
      const decText = x.decision === "transcode" ? `Transcode${x.hw ? " (HW)" : ""}` : x.decision === "direct stream" ? "Direct stream" : "Direct play";
      const why = x.reasons && x.reasons.length ? x.reasons.join(" · ") : "";
      const icon = x.type === "track" ? "♪" : x.type === "episode" ? "▤" : "▶";
      return `<div class="np ${x.state}">
        <div class="np-ico" aria-hidden="true">${icon}</div>
        <div class="np-main">
          <div class="np-title"><b title="${esc(x.title)}">${esc(x.title)}</b>${x.subtitle ? `<span>${esc(x.subtitle)}</span>` : ""}</div>
          <div class="np-who"><span class="np-user">${esc(x.user)}</span> on ${esc(x.device || x.product || "a device")}${x.product && x.device && x.product !== x.device ? ` <small>${esc(x.product)}</small>` : ""}
            <span class="np-loc ${x.location === "LAN" ? "lan" : "wan"}">${x.location}${x.relayed ? " · relay" : ""}</span>
            ${x.state !== "playing" ? `<span class="np-state">${esc(x.state)}</span>` : ""}</div>
          <div class="np-prog"><span class="bar"><i style="width:${x.progress || 0}%"></i></span><small>${mediaTime(x.offset)} / ${mediaTime(x.duration)}</small></div>
        </div>
        <div class="np-side">
          <span class="np-dec ${dec}" title="${esc(why)}">${decText}</span>
          <small>${esc([x.quality && (x.out_quality && x.out_quality !== x.quality ? `${x.quality} → ${x.out_quality}` : x.quality), kbps(x.bandwidth)].filter(Boolean).join(" · "))}</small>
          ${why ? `<small class="np-why">${esc(why)}${x.throttled ? " · throttled" : x.speed ? ` · ${x.speed.toFixed(1)}×` : ""}</small>` : ""}
        </div>
      </div>`;
    }).join("") : `<div class="empty">Nobody is watching right now.</div>`;
  }
  async function loadPlex() {
    if (plexInstalled === false && !loadPlex.recheck) return;      // not installed: re-checked once a minute
    if (plexInstalled && (!plexVisible || document.hidden)) return;
    loadPlex.recheck = false;
    renderPlex(await getJSON(S.api.plex));
  }
  if (S.admin) {
    new IntersectionObserver((ents) => { plexVisible = ents.some((e) => e.isIntersecting); }).observe($("plexCard"));
    setInterval(() => { loadPlex.recheck = true; }, 60000);
  }

  /* ============================================================ apps */
  let statusMap = {}, updReady = false;
  const apps = S.pages.slice().sort((a, b) => a.pretty_name.localeCompare(b.pretty_name));

  const cardCache = new Map();
  function appCard(a) {
    let card = cardCache.get(a.name);
    if (card) return card;
    card = document.createElement("div");
    card.className = "app"; card.dataset.app = a.name;
    const name = a.url ? `<a class="app-name" href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.pretty_name)}</a>` : `<span class="app-name">${esc(a.pretty_name)}</span>`;
    card.innerHTML = `${iconHTML("app-icon", a)}<div style="min-width:0">${name}<span class="app-state"></span></div><div class="app-actions"></div>`;
    cardCache.set(a.name, card);
    return card;
  }
  function paintApp(card, a) {
    const st = statusMap[a.name], ctrl = a.systemd && st;
    const stEl = card.querySelector(".app-state"), act = card.querySelector(".app-actions");
    stEl.className = "app-state " + (!st ? "" : st.active ? "on" : "off");
    stEl.textContent = !st ? (a.systemd ? "Checking…" : "Web app") : st.active ? "Running" : "Stopped";
    const sig = ctrl ? `c${st.enabled ? 1 : 0}` : "-";
    if (act.dataset.sig === sig || act.contains(document.activeElement)) return;   // unchanged, or user is interacting
    act.dataset.sig = sig;
    act.innerHTML = ctrl ? `
      <button class="icon-btn" data-act="restart" title="Restart ${esc(a.pretty_name)}" aria-label="Restart ${esc(a.pretty_name)}">↻</button>
      <label class="switch" title="${st.enabled ? "Enabled at boot — click to stop & disable" : "Disabled — click to enable & start"}">
        <input type="checkbox" data-act="toggle" ${st.enabled ? "checked" : ""} aria-label="Enable ${esc(a.pretty_name)}"><i></i></label>` : "";
  }
  function renderApps() {
    const q = ($("svcSearch").value || "").toLowerCase();
    const list = apps.filter((a) => !q || a.pretty_name.toLowerCase().includes(q) || a.name.includes(q));
    const box = $("apps"), want = list.map(appCard);
    // only touch the DOM order when the visible set changed (filtering); otherwise just repaint states
    if (box.children.length !== want.length || want.some((c, i) => box.children[i] !== c)) {
      if (box.replaceChildren) box.replaceChildren(...want);
      else { box.textContent = ""; want.forEach((c) => box.appendChild(c)); }   // Safari < 14, old TV browsers
      if (!want.length) box.innerHTML = `<div class="empty">No matching applications</div>`;
    }
    list.forEach((a) => paintApp(appCard(a), a));
    const tracked = Object.values(statusMap);
    const up = tracked.filter((s) => s.active).length;
    $("svcSummary").textContent = (tracked.length ? `${up} of ${tracked.length} services running · ${apps.length} installed` : `${apps.length} installed`)
      + ($("svcSummary").dataset.upd || "");
    if (S.admin && updReady) paintUpdates();
  }

  async function loadStatus() {
    try {
      const list = await getJSON(S.api.status);
      statusMap = {}; list.forEach((s) => (statusMap[s.name] = s));
    } catch (_) { /* keep previous */ }
    renderApps();
    sideDots();
  }

  async function service(app, fn) {
    const r = await fetch(S.api.service, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ application: app, function: fn, user: S.user }),
    });
    const txt = (await r.text()).trim();
    if (!r.ok || (txt !== "0" && txt !== "")) throw new Error(txt || r.status);
  }

  $("apps").addEventListener("click", async (e) => {
    const btn = e.target.closest('[data-act="restart"]'); if (!btn) return;
    const app = btn.closest(".app").dataset.app; btn.classList.add("spin"); btn.disabled = true;
    try { await service(app, "restart"); toast(`Restarted ${app}`); } catch (err) { toast(`Restart failed: ${err.message}`); }
    setTimeout(loadStatus, 1500);
  });
  $("apps").addEventListener("change", async (e) => {
    const inp = e.target.closest('[data-act="toggle"]'); if (!inp) return;
    const app = inp.closest(".app").dataset.app, fn = inp.checked ? "enable" : "disable";
    try { await service(app, fn); toast(`${fn === "enable" ? "Enabled & started" : "Stopped & disabled"} ${app}`); }
    catch (err) { inp.checked = !inp.checked; toast(`Failed: ${err.message}`); }
    setTimeout(loadStatus, 1500);
  });
  $("svcSearch").addEventListener("input", renderApps);

  /* ============================================================ app updates
     The server checks every 6 hours (and on "Check now"); this shows the result on each app card.
     autobrr / Filebrowser / Plex are upgraded by swizzin as root, Sonarr / Radarr / Prowlarr by
     their own updater, qBittorrent only says a newer version exists. One upgrade at a time, always
     after a confirmation; "Update all" works down the list one by one. */
  const upd = { data: null, timer: null, queue: [], dlg: null, prevJobs: {} };
  updReady = true;
  const UPD_HOW = {
    autobrr: "The server runs swizzin's autobrr upgrade: it downloads the official release and restarts autobrr for each user (a few seconds). If autobrr doesn't come back up, the previous version is put back.",
    filebrowser: "The server runs swizzin's Filebrowser upgrade: it downloads the official release and restarts Filebrowser. If it doesn't come back up, the previous version is put back.",
    fbquantum: "The server runs swizzin's FileBrowser Quantum upgrade, which checks the new release, keeps the old one and rolls back if it doesn't start.",
    plex: "The server upgrades only the Plex Media Server package from Plex's own repository. Plex restarts, so anyone watching is interrupted for about a minute.",
    sonarr: "Sonarr installs the update with its own updater and restarts itself, usually within a minute.",
    radarr: "Radarr installs the update with its own updater and restarts itself, usually within a minute.",
    prowlarr: "Prowlarr installs the update with its own updater and restarts itself, usually within a minute.",
    lidarr: "Lidarr installs the update with its own updater and restarts itself, usually within a minute.",
    navidrome: "The server runs swizzin's Navidrome upgrade: it downloads the official release and restarts Navidrome. If it doesn't come back up, the previous version is put back.",
    netronome: "The server runs swizzin's Netronome upgrade: it downloads the official release and restarts Netronome for each user. If it doesn't come back up, the previous version is put back.",
    emby: "The server installs the new Emby Server package from Emby's official releases. Emby restarts, so anyone watching is interrupted for about a minute.",
    lounge: "The server updates The Lounge with swizzin's upgrade (yarn) and restarts it; IRC connections reconnect.",
    jellyfin: "The server upgrades only the Jellyfin packages from Jellyfin's own repository. Jellyfin restarts, so anyone watching is interrupted for about a minute.",
    ombi: "The server upgrades only the Ombi package from Ombi's own repository and restarts Ombi.",
  };
  const vtxt = (v) => (v ? (/^\d/.test(v) ? "v" + v : v) : "?");
  // 1.41.5.9522 → 1.41.6.9606 reads as v1.41.5 → v1.41.6 (build numbers only when they're the difference)
  const vpair = (a, b) => {
    const pa = String(a || "").split("."), pb = String(b || "").split(".");
    const cut = pa.length > 3 && pb.length > 3 && pa.slice(0, 3).join(".") !== pb.slice(0, 3).join(".");
    return cut ? [pa.slice(0, 3).join("."), pb.slice(0, 3).join(".")] : [a, b];
  };
  // release notes: plain text made a little friendlier (headings bold, bullets, `code`); escaped first
  const vshort = (v) => { const p = String(v || "").split("."); return p.length > 3 ? p.slice(0, 3).join(".") : v; };
  const notesHTML = (t) => esc(String(t || "").replace(/\n{3,}/g, "\n\n")).split("\n").map((l) => {
    const h = l.match(/^\s*#{1,6}\s+(.*)$/);
    if (h) return `<b class="nh">${h[1]}</b>`;
    return l.replace(/^(\s*)[-*]\s+/, "$1• ").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  }).join("\n");
  const prettyApp2 = (n) => (apps.find((a) => a.name === n) || {}).pretty_name || n;
  const upgradable = (a) => a && a.available && a.action === "upgrade" && !(a.job && a.job.state === "running");
  const inUpdateAll = (a) => upgradable(a) && !a.major;     // major versions are confirmed one by one
  function updRow(name) {
    const a = upd.data && upd.data.apps[name];
    if (!a || (!a.installed && !a.latest && !a.job)) return null;
    const busy = upd.data.busy, j = a.job, P = esc(prettyApp2(name));
    const [vi, vl] = vpair(a.installed, a.latest);
    const vers = `<span class="vi">${esc(vtxt(vi))}<span class="arrow">→</span></span><b>${esc(vtxt(vl))}</b>`;
    const notesBtn = (inner, title) => `<button class="uv" type="button" data-upd="notes" title="${esc(title)}">${inner}</button>`;
    if (j && j.state === "running")
      return { cls: "run", html: `<span class="spin" aria-hidden="true"></span><span class="uv" role="status" title="${esc(j.message || "")}">Updating to <b>${esc(vtxt(vpair(a.installed, a.latest)[1]))}</b>…</span><button class="ghost" type="button" data-upd="log">Log</button><span class="bar" aria-hidden="true"><i></i></span>` };
    if (j && j.state === "failed")
      return { cls: "fail", html: `<span class="dot"></span><span class="uv" title="${esc(j.message || "")}">Update failed</span><button class="ghost" type="button" data-upd="log">Log</button>${upgradable(a) ? `<button class="go" type="button" data-upd="go" ${busy ? "disabled" : ""}>Retry</button>` : ""}` };
    if (j && j.state === "done")
      return { cls: "done", html: `<span class="dot"></span><span class="uv" title="${esc(vtxt(a.installed))}${j.finished ? " · " + esc(agoTs(j.finished)) : ""}">Updated to <b>${esc(vtxt(vshort(a.installed)))}</b></span><button class="ghost" type="button" data-upd="log">Log</button>` };
    if (!a.installed && a.error) return { cls: "err", html: `<span class="dot"></span><span class="uv" title="${esc(a.error)}">Couldn't check for updates</span>` };
    if (!a.available) return { cls: "ok", html: `<span class="dot"></span><span class="uv"${a.error ? ` title="Last check failed: ${esc(a.error)}"` : ""}>Up to date · <b title="${esc(vtxt(a.installed))}">${esc(vtxt(vshort(a.installed)))}</b></span>` };
    if (a.action === "notify")
      return { cls: "avail", html: `<span class="dot"></span>${notesBtn(`${vers} available`, `Torrent apps are updated by you, not from the panel`)}` };
    if (a.action === "needs-update")
      return { cls: "avail", html: `<span class="dot"></span>${notesBtn(vers, "Run box update on the server once to enable upgrades from the panel")}` };
    if (a.action === "blocked")
      return { cls: "avail", html: `<span class="dot"></span>${notesBtn(vers, `Install this update from ${P} itself`)}` };
    return { cls: "avail" + (a.major ? " major" : ""), html: `<span class="dot"></span>${notesBtn(vers + `<span class="vn"> available</span>`, `${vtxt(a.installed)} → ${vtxt(a.latest)}${a.major ? " · new major version" : ""} · release notes`)}<button class="go" type="button" data-upd="go" ${busy ? `disabled title="${esc(prettyApp2(busy))} is updating"` : ""} aria-label="Update ${P} to ${esc(vtxt(a.latest))}">Update</button>` };
  }
  // narrow cards: "v1.41.5 → v1.41.6" becomes "v1.41.6 available" rather than being cut off
  function fitUpdRows() {
    document.querySelectorAll("#apps .upd .uv").forEach((uv) => {
      const row = uv.closest(".upd");
      row.classList.remove("tight");
      if (!uv.querySelector(".vi")) return;
      const r = document.createRange(); r.selectNodeContents(uv);         // buttons don't report scrollWidth
      if (r.getBoundingClientRect().width > uv.getBoundingClientRect().width + 1) row.classList.add("tight");
    });
  }
  if (window.ResizeObserver) {
    let fitT;
    new ResizeObserver(() => { clearTimeout(fitT); fitT = setTimeout(fitUpdRows, 80); }).observe($("apps"));
  }
  // "x min ago" from a unix time (the other ago() above takes an age in seconds)
  function agoTs(t) {
    const m = Math.max(0, Math.round((Date.now() / 1000 - t) / 60));
    return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
  }
  function paintUpdates() {
    if (!upd.data) return;
    apps.forEach((a) => {
      const card = cardCache.get(a.name); if (!card) return;
      const r = updRow(a.name);
      let el = card.querySelector(".upd");
      if (!r) { if (el) el.remove(); card.classList.remove("has-upd"); return; }
      if (!el) { el = document.createElement("div"); card.appendChild(el); card.classList.add("has-upd"); }
      const sig = r.cls + "|" + r.html;
      if (el.dataset.sig === sig) return;
      const had = el.contains(document.activeElement);
      el.dataset.sig = sig; el.className = "upd " + r.cls; el.innerHTML = r.html;
      if (had) { const f = el.querySelector("button"); if (f) f.focus({ preventScroll: true }); }   // keyboard users keep their place
    });
    fitUpdRows();
    const d = upd.data, list = Object.entries(d.apps);
    const avail = list.filter(([, a]) => a.available).length;
    const ready = list.filter(([, a]) => inUpdateAll(a)).map(([n]) => n);
    $("updHead").hidden = !list.length && !d.supported;
    $("updPill").hidden = !avail; $("updPill").textContent = `${avail} update${avail === 1 ? "" : "s"}`;
    const chk = $("updCheck");
    chk.disabled = d.checking || !d.supported; chk.textContent = d.checking ? "Checking…" : "Check now";
    chk.title = !d.supported ? "Run box update on the server to enable update checks" : d.cooldown && !d.checking ? `Checked recently; available again in ${Math.ceil(d.cooldown / 60)} min` : "Check every app for updates now";
    const all = $("updAll");
    all.hidden = ready.length < 2 && !upd.queue.length;
    all.disabled = !!d.busy || !!upd.queue.length;
    all.textContent = upd.queue.length ? `Updating… (${upd.queue.length} left)` : `Update all (${ready.length})`;
    const sum = $("svcSummary"), extra = d.checked ? ` · updates checked ${agoTs(d.checked)}` : "";
    sum.dataset.upd = extra;
    sum.textContent = sum.textContent.replace(/ · updates checked .*$/, "") + extra;
  }
  function noteJobChanges() {
    Object.entries(upd.data.apps).forEach(([n, a]) => {
      const st = a.job && a.job.state, was = upd.prevJobs[n];
      if (was === "running" && st === "done") toast(`${prettyApp2(n)} updated to ${vtxt(a.installed)}`);
      if (was === "running" && st === "failed") toast(`${prettyApp2(n)} update failed`);
      upd.prevJobs[n] = st;
    });
  }
  async function loadUpdates() {
    clearTimeout(upd.timer);
    try {
      upd.data = await getJSON(S.api.updates);
      noteJobChanges(); paintUpdates();
      if (!upd.data.busy && upd.queue.length) nextInQueue();
    } catch (e) { console.warn(e); }
    const fast = upd.data && (upd.data.busy || upd.data.checking || upd.queue.length);
    upd.timer = setTimeout(loadUpdates, fast ? 3000 : document.hidden ? 300000 : 60000);
  }
  async function updPost(body) {
    const r = await fetch(S.api.updates, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" }, body: JSON.stringify(body),
    });
    if (r.status === 401) { location.reload(); throw new Error("auth"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || r.status);
    return d;
  }
  async function startUpgrade(name, major) {
    try {
      const d = await updPost({ action: "upgrade", app: name, major: !!major });
      upd.data = d; if (d.ok) upd.prevJobs[name] = "running";
      toast(d.ok ? `Updating ${prettyApp2(name)}…` : d.message);
      if (!d.ok) upd.queue = [];
      paintUpdates();
    } catch (e) { toast(`Couldn't start the update: ${e.message}`); upd.queue = []; }
    loadUpdates();
  }
  function nextInQueue() {
    while (upd.queue.length) {
      const n = upd.queue.shift(), a = upd.data.apps[n];
      if (inUpdateAll(a)) { startUpgrade(n); return; }
    }
    paintUpdates();
  }
  function openUpd(mode, name) {
    const a = upd.data && upd.data.apps[name], dlg = $("updDlg"), P = prettyApp2(name);
    if (!a && mode !== "all") return;
    const go = $("updGo"), link = $("updLink"), notes = $("updNotes");
    upd.dlg = { mode, name, back: document.activeElement };
    notes.classList.toggle("log", mode === "log"); notes.textContent = "";
    link.hidden = true; go.hidden = false; go.disabled = !!(upd.data && upd.data.busy);
    $("updCancel").textContent = "Cancel";
    if (mode === "all") {
      const ready = Object.entries(upd.data.apps).filter(([, x]) => inUpdateAll(x));
      $("updTitle").textContent = `Update ${ready.length} apps?`;
      $("updWhat").textContent = "They're updated one after another, each with its own backup and check. Keep this page open until the list is done; an update that's already running always finishes.";
      notes.textContent = ready.map(([n, x]) => `${prettyApp2(n)}  ${vtxt(x.installed)} → ${vtxt(x.latest)}`).join("\n");
      go.textContent = "Update all";
    } else if (mode === "log") {
      $("updTitle").textContent = `${P} · update log`;
      $("updWhat").textContent = a.job && a.job.message ? a.job.message : "";
      notes.textContent = "Loading…"; go.hidden = true; $("updCancel").textContent = "Close";
      getJSON(S.api.updates + "?log=" + encodeURIComponent(name))
        .then((d) => { if (upd.dlg && upd.dlg.name === name) { notes.textContent = d.log || "No log yet."; notes.scrollTop = notes.scrollHeight; } })
        .catch(() => { notes.textContent = "Couldn't load the log."; });
    } else {
      const can = upgradable(a);
      $("updTitle").textContent = can ? `Update ${P} to ${vtxt(a.latest)}?` : `${P} ${vtxt(a.latest)}`;
      $("updWhat").textContent = can ? (UPD_HOW[name] || "") : a.action === "notify"
        ? `You're on ${vtxt(a.installed)}. Torrent apps are updated by you (for example with box upgrade over SSH, at a quiet moment), not from the panel.`
        : a.action === "needs-update" ? "Run box update on the server once to enable upgrades from the panel."
        : a.action === "blocked" ? `${P} says its built-in updater can't install this one; update it from ${P} itself.` : "";
      if (a.notes) notes.innerHTML = notesHTML(a.notes);
      else notes.textContent = a.url ? "" : "No release notes were published with this version.";
      go.hidden = !can; go.textContent = "Update now"; $("updCancel").textContent = can ? "Cancel" : "Close";
      if (can && a.major) {
        const from = String(a.installed || "").split(".")[0], to = String(a.latest || "").split(".")[0];
        $("updTitle").textContent = `Install ${P} ${vtxt(a.latest)}? (major version)`;
        $("updWhat").textContent = `This moves ${P} from version ${from} to version ${to}. Major versions can change settings, `
          + `drop older systems or need other apps updated too, so ${P} won't install it unless you confirm. `
          + `Read the notes first; ${P} keeps its own backup and restarts itself.`;
        go.textContent = "Install major update";
        upd.dlg.major = true;
      }
    }
    if (mode !== "all" && a && a.url) { link.href = a.url; link.hidden = false; }
    dlg.hidden = false;
    (go.hidden ? $("updCancel") : go).focus();
  }
  function closeUpd() {
    const dlg = $("updDlg"); if (dlg.hidden) return;
    dlg.hidden = true;
    const back = upd.dlg && upd.dlg.back; upd.dlg = null;
    if (back && back.isConnected) back.focus();
  }
  if (S.admin && S.api.updates) {
    $("apps").addEventListener("click", (e) => {
      const b = e.target.closest("[data-upd]"); if (!b) return;
      const name = b.closest(".app").dataset.app, act = b.dataset.upd;
      openUpd(act === "log" ? "log" : "notes", name);
    });
    $("updCheck").addEventListener("click", async (e) => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "Checking…";
      try { const d = await updPost({ action: "check" }); upd.data = d; if (!d.ok) toast(d.message); paintUpdates(); }
      catch (err) { toast(`Couldn't start the check: ${err.message}`); }
      loadUpdates();
    });
    $("updAll").addEventListener("click", () => openUpd("all"));
    $("updGo").addEventListener("click", () => {
      const st = upd.dlg; if (!st) return;
      closeUpd();
      if (st.mode === "all") {
        upd.queue = Object.entries(upd.data.apps).filter(([, x]) => inUpdateAll(x)).map(([n]) => n);
        nextInQueue();
      } else startUpgrade(st.name, st.major);
    });
    $("updCancel").addEventListener("click", closeUpd);
    $("updDlg").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeUpd(); });
    $("updDlg").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeUpd(); return; }
      if (e.key !== "Tab") return;                       // keep focus inside the dialog
      const f = [...$("updDlg").querySelectorAll("button:not([hidden]):not(:disabled), a:not([hidden]), pre")];
      if (!f.length) return;
      const i = f.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadUpdates(); });
  }

  /* ============================================================ command palette (Ctrl+K)
     Everything the page can do, by typing: open an app, jump to a card, start / stop / restart a
     service, switch theme, run the speed test, copy the IP. Stopping or restarting asks for a
     second Enter, so a slip of the keyboard never takes Plex down mid-stream. */
  const cmdk = { el: $("cmdk"), input: $("cmdkInput"), list: $("cmdkList"), foot: $("cmdkFoot"), items: [], shown: [], sel: 0, arm: null, back: null };
  if (/Mac|iPhone|iPad/.test(navigator.platform || "")) $("cmdKbd").textContent = "⌘K";
  function cmdItems() {
    const out = [];
    const add = (group, label, run, extra) => out.push(Object.assign({ group, label, run, keys: label.toLowerCase() }, extra || {}));
    apps.filter((a) => a.url).forEach((a) => add("Apps", `Open ${a.pretty_name}`, () => window.open(a.url, "_blank", "noopener"), { hint: a.url }));
    apps.filter((a) => a.systemd && statusMap[a.name]).forEach((a) => {
      const st = statusMap[a.name], doIt = (fn, done) => async () => {
        try { await service(a.name, fn); toast(`${done} ${a.pretty_name}`); } catch (err) { toast(`${a.pretty_name}: ${err.message}`); }
        setTimeout(loadStatus, 1500);
      };
      add("Services", `Restart ${a.pretty_name}`, doIt("restart", "Restarted"), { confirm: true, hint: st.active ? "running" : "stopped" });
      if (st.active) add("Services", `Stop ${a.pretty_name}`, doIt("disable", "Stopped and disabled"), { confirm: true, hint: "stops and disables at boot" });
      else add("Services", `Start ${a.pretty_name}`, doIt("enable", "Started and enabled"), { hint: "starts and enables at boot" });
    });
    document.querySelectorAll("main section.card:not([hidden]) h2").forEach((h) => {
      const sec = h.closest("section"), name = (h.childNodes[0] && h.childNodes[0].textContent || h.textContent).trim();
      if (name) add("Go to", name, () => { sec.scrollIntoView({ behavior: "smooth", block: "start" }); sec.classList.remove("flash"); void sec.offsetWidth; sec.classList.add("flash"); });
    });
    if (S.admin) {
      add("Server", "Show server specifications", () => document.querySelector('[data-pop="popSpecs"]').click());
      add("Server", "Run speed test", () => { document.querySelector('[data-pop="popSpecs"]').click(); setTimeout(() => { const b = $("speedRun"); if (b && !b.disabled) runSpeed(b); }, 400); }, { hint: "M-Lab, about 20 s" });
      add("Server", "Copy IPv4 address", () => { const b = document.querySelector('[data-copy="ip4"]'); if (b && !b.hidden) b.click(); });
    }
    [["auto", "Auto (Obsidian & Porcelain, follows the sun)"], ["day", "Light (Porcelain)"], ["night", "Dark (Obsidian)"]].forEach(([m, n]) => add("Theme", `Theme: ${n}`, () => { SKY.setMode(m); savePrefs(); }));
    return out;
  }
  function score(q, it) {
    if (!q) return 1;
    const k = it.keys, words = q.split(/\s+/).filter(Boolean);
    let total = 0;
    for (const w of words) {
      const i = k.indexOf(w);
      if (i >= 0) { total += 10 + (i === 0 || k[i - 1] === " " ? 6 : 0) - i * 0.01; continue; }
      let j = 0, gaps = 0, last = -1;                       // subsequence fallback ("rsplx" -> "restart plex")
      for (const ch of w) { const f = k.indexOf(ch, j); if (f < 0) return 0; if (last >= 0 && f > last + 1) gaps++; last = f; j = f + 1; }
      total += Math.max(1, 6 - gaps);
    }
    return total + (it.group === "Apps" ? 0.5 : 0);
  }
  function cmdRender() {
    const q = cmdk.input.value.trim().toLowerCase();
    cmdk.shown = cmdk.items.map((it) => [score(q, it), it]).filter(([sc]) => sc > 0)
      .sort((a, b) => (q ? b[0] - a[0] : 0)).map(([, it]) => it).slice(0, 60);
    cmdk.sel = Math.min(cmdk.sel, Math.max(0, cmdk.shown.length - 1));
    let last = "";
    cmdk.list.innerHTML = cmdk.shown.length ? cmdk.shown.map((it, i) => {
      const head = !q && it.group !== last ? `<li class="cmdk-group" role="presentation">${esc(it.group)}</li>` : "";
      last = it.group;
      const armed = cmdk.arm === it;
      return `${head}<li role="option" id="cmdk-${i}" data-i="${i}" aria-selected="${i === cmdk.sel}" class="${armed ? "armed" : ""}">
        <span class="cmdk-label">${esc(armed ? `Press Enter again to ${it.label[0].toLowerCase() + it.label.slice(1)}` : it.label)}</span>
        <span class="cmdk-hint">${esc(q && !armed ? it.group : it.hint || "")}</span></li>`;
    }).join("") : `<li class="cmdk-empty" role="presentation">Nothing matches “${esc(cmdk.input.value)}”</li>`;
    cmdk.input.setAttribute("aria-activedescendant", cmdk.shown.length ? `cmdk-${cmdk.sel}` : "");
    const cur = $("cmdk-" + cmdk.sel); if (cur) cur.scrollIntoView({ block: "nearest" });
  }
  function cmdOpen() {
    if (!cmdk.el.hidden) return;
    cmdk.back = document.activeElement;
    cmdk.items = cmdItems(); cmdk.sel = 0; cmdk.arm = null; cmdk.input.value = "";
    cmdk.el.hidden = false; cmdRender(); cmdk.input.focus();
  }
  function cmdClose() {
    if (cmdk.el.hidden) return;
    cmdk.el.hidden = true; cmdk.arm = null;
    if (cmdk.back && cmdk.back.focus) cmdk.back.focus();
  }
  function cmdRun(i) {
    const it = cmdk.shown[i]; if (!it) return;
    if (it.confirm && cmdk.arm !== it) { cmdk.arm = it; cmdk.sel = i; cmdRender(); cmdk.foot.dataset.msg = "confirm"; return; }
    cmdClose(); it.run();
  }
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); cmdk.el.hidden ? cmdOpen() : cmdClose(); return; }
    if (cmdk.el.hidden) return;
    if (e.key === "Escape") { e.preventDefault(); cmdClose(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); const n = cmdk.shown.length; if (!n) return;
      cmdk.sel = (cmdk.sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; cmdk.arm = null; cmdRender();
    } else if (e.key === "Enter") { e.preventDefault(); cmdRun(cmdk.sel); }
    else if (e.key === "Tab") { e.preventDefault(); cmdk.input.focus(); }
  });
  cmdk.input.addEventListener("input", () => { cmdk.sel = 0; cmdk.arm = null; cmdRender(); });
  cmdk.list.addEventListener("click", (e) => { const li = e.target.closest("[data-i]"); if (li) cmdRun(+li.dataset.i); });
  cmdk.list.addEventListener("mousemove", (e) => { const li = e.target.closest("[data-i]"); if (li && +li.dataset.i !== cmdk.sel && !cmdk.arm) { cmdk.sel = +li.dataset.i; cmdRender(); } });
  cmdk.el.addEventListener("mousedown", (e) => { if (e.target === cmdk.el) cmdClose(); });
  $("cmdOpen").addEventListener("click", cmdOpen);

  /* ============================================================ quota */
  async function loadQuota() {
    try {
      const d = (await getJSON(S.api.quota)).quota, p = Number(d.perutil), l = lvl(p, 75, 90);
      $("quota").innerHTML = barRow("Home", [{ v: p, c: l === "good" ? css("--s1") : levelColor(l) }], `${d.diskused} / ${d.disktotal}`);
      $("quotaLine").textContent = `${d.diskfree} free`;
    } catch (_) { /* quota not installed */ }
  }

  /* ============================================================ media queue (Sonarr / Radarr) */
  const APPNAME = { sonarr: "Sonarr", radarr: "Radarr" };
  const WEEKDAY = new Intl.DateTimeFormat(undefined, { weekday: "long" });
  const HM = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
  function dayLabel(d) {
    const a = new Date(); a.setHours(0, 0, 0, 0);
    const b = new Date(d); b.setHours(0, 0, 0, 0);
    const n = Math.round((b - a) / 86400000);
    return n <= 0 ? "Today" : n === 1 ? "Tomorrow" : WEEKDAY.format(d);
  }
  function etaText(q) {
    if (!q.eta) return "";
    const s = (new Date(q.eta) - Date.now()) / 1000;
    return s > 0 ? `${dur(s)} left` : "";
  }
  async function loadMedia() {
    const d = await getJSON(S.api.media), card = $("mediaCard");
    if (!d.available) { card.hidden = true; return; }
    card.hidden = false;
    const bad = d.apps.filter((a) => !a.ok);
    $("mediaNote").textContent = d.apps.map((a) => APPNAME[a.name] || a.name).join(" and ") +
      (bad.length ? ` · ${bad.map((a) => `${APPNAME[a.name]} ${a.error}`).join(", ")}` : " · updates every minute");
    $("mediaLinks").innerHTML = d.apps.map((a) => {
      const p = (S.pages || []).find((x) => x.name === a.name);
      return p && p.url ? `<a class="pill pop-mini" href="${esc(p.url)}" target="_blank" rel="noopener">${esc(APPNAME[a.name])}</a>` : "";
    }).join("");
    $("mqCount").textContent = d.queue.length ? String(d.queue.length) : "";
    $("mediaQueue").innerHTML = d.queue.length ? d.queue.slice(0, 8).map((q) => `
      <div class="mq-row${q.warn ? " warn" : ""}" title="${esc(q.client ? "Download client: " + q.client : "")}">
        <div class="mq-top"><span class="mq-title">${esc(q.title)}${q.sub ? ` <small>${esc(q.sub)}</small>` : ""}</span>
          <span class="mq-pct">${q.pct == null ? esc(q.state || "queued") : q.pct.toFixed(0) + "%"}</span></div>
        <div class="mq-bar"><i style="width:${q.pct == null ? 0 : Math.max(1, q.pct)}%"></i></div>
        <div class="mq-meta"><span>${esc(APPNAME[q.app])}${q.state && q.pct != null ? " · " + esc(q.state) : ""}${q.warn ? " · needs attention" : ""}</span>
          <span>${q.left > 0 ? esc(bytes(q.left, 1)) + " to go" : ""}${etaText(q) ? " · " + esc(etaText(q)) : ""}</span></div>
      </div>`).join("") : `<div class="empty">Nothing downloading</div>`;
    $("mcTitle").textContent = `Coming up · next ${d.days} days`;
    $("mcCount").textContent = d.upcoming.length ? String(d.upcoming.length) : "";
    let last = "";
    $("mediaCal").innerHTML = d.upcoming.length ? d.upcoming.slice(0, 12).map((u) => {
      const t = new Date(u.when), lab = dayLabel(t), head = lab !== last ? `<div class="mc-day">${esc(lab)}</div>` : "";
      last = lab;
      return `${head}<div class="mc-row"><span class="mc-time">${u.app === "sonarr" ? esc(HM.format(t)) : ""}</span>
        <span class="mc-title">${esc(u.title)}${u.sub ? ` <small>${esc(u.sub)}</small>` : ""}</span>
        <span class="mc-st ${u.has_file ? "got" : ""}">${u.has_file ? "Downloaded" : esc(APPNAME[u.app])}</span></div>`;
    }).join("") : `<div class="empty">Nothing scheduled this week</div>`;
  }

  /* ============================================================ app uptime (24 h) */
  let uptimeData = null;
  const HR = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric" });
  const prettyApp = (n) => ((S.pages || []).find((p) => p.name === n) || {}).pretty_name || n;
  function cellClass(c) {
    if (!c || !c.expected) return "off";
    const r = c.up / c.expected;
    return r >= 0.999 ? "ok" : r >= 0.5 ? "part" : "down";
  }
  async function loadUptime() {
    const d = await getJSON(S.api.uptime);
    uptimeData = d;
    const card = $("uptimeCard");
    if (!d.apps || !d.apps.length) { card.hidden = true; return; }
    card.hidden = false;
    const sampled = d.panel_samples.reduce((a, b) => a + b, 0);
    $("uptimeNote").textContent = sampled < 60 ? "Collecting — the strips fill in over the next 24 hours" : "Last 24 hours · checked every minute";
    $("uptimeGrid").innerHTML = d.apps.map((a, ai) => {
      const pct = a.pct == null ? "–" : a.pct >= 99.995 ? "100%" : a.pct.toFixed(a.pct >= 99 ? 2 : 1) + "%";
      const nowCls = a.now === "up" ? "on" : a.now === "down" ? "off" : "";
      return `<div class="up-row" data-a="${ai}">
        <span class="up-name"><i class="side-dot ${nowCls}" aria-hidden="true"></i>${esc(prettyApp(a.name))}</span>
        <span class="up-strip" role="img" aria-label="${esc(prettyApp(a.name))}: ${esc(pct)} up in the last 24 hours">${a.hours.map((c, i) =>
          `<i class="uc ${cellClass(c)}" data-h="${i}"></i>`).join("")}</span>
        <b class="up-pct${a.pct != null && a.pct < 99 ? " warn" : ""}">${pct}</b></div>`;
    }).join("");
  }
  if (S.admin && $("uptimeGrid")) {
    $("uptimeGrid").addEventListener("pointermove", (e) => {
      const cell = e.target.closest(".uc"), row = e.target.closest(".up-row");
      if (!cell || !row || !uptimeData) return hideTip();
      const a = uptimeData.apps[+row.dataset.a], i = +cell.dataset.h, c = a.hours[i];
      const t0 = new Date((uptimeData.start + i * 3600) * 1000), t1 = new Date(t0.getTime() + 3600000);
      const what = !c ? "No data (panel wasn't running)" : !c.expected ? "Stopped and disabled on purpose"
        : `Running ${c.up} of ${c.expected} minutes checked`;
      showTip(`<div class="t-title">${esc(prettyApp(a.name))} · ${HR.format(t0)} – ${HOUR.format(t1)}</div><div class="t-row"><span>${esc(what)}</span></div>`, e.clientX, e.clientY);
    });
    $("uptimeGrid").addEventListener("pointerleave", hideTip);
  }

  /* ============================================================ loops */
  function every(ms, fn) {
    let t;
    const run = async () => { try { await fn(); } catch (e) { console.warn(e); } t = setTimeout(run, document.hidden ? ms * 5 : ms); };
    run(); return () => clearTimeout(t);
  }
  renderApps();
  every(10000, loadStatus);
  if (S.quota) every(60000, loadQuota);
  if (S.admin) {
    loadSystem().catch(console.warn);
    every(LIVE_MS, async () => renderLive(await getJSON(S.api.live)));
    every(60000, loadStorage);
    every(300000, loadTraffic);
    every(3000, loadProcs);
    every(300000, loadPlace);
    if (store.get("wxMode", "server") === "device") setTimeout(() => deviceWeather(false), 400);
    every(600000, () => store.get("wxMode", "server") === "device" ? deviceWeather(false) : null);
    every(60000, loadF2b);
    every(60000, loadUptime);
    every(60000, loadMedia);
    if (S.api.updates) loadUpdates();
    loadPlex.recheck = true;
    every(5000, loadPlex);
    setInterval(tickClock, 1000);
  }
})();
