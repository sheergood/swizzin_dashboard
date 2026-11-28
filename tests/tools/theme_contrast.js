// usage: node tests/tools/theme_contrast.js static/js/sky.js
// Load sky.js with a fake browser, step every theme through the day, and check text contrast.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const store = {}, props = {}, dataset = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
global.document = { documentElement: { style: { setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; } }, dataset, classList: { add() {}, remove() {} } },
  querySelector: () => null, dispatchEvent: () => {}, addEventListener() {}, hidden: false };
global.window = global; global.CustomEvent = function (n, o) { this.detail = o.detail; };
global.requestAnimationFrame = () => 0; global.matchMedia = () => ({ matches: true });
global.setTimeout = () => 0; global.setInterval = () => 0;
eval(src);
const SKY = window.SWZ_SKY;
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
const L = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
const cr = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
const over = (c, top, a) => c.map((v, i) => v + (top[i] - v) * a);
SKY.init({});
let worst = {};
const DARK = { ink: "#f3f5fa", ink2: "#c4ccdd", ink3: "#9aa5bd" }, LIGHT = { ink: "#0e1525", ink2: "#344055", ink3: "#55627a" };
for (const t of SKY.themes()) {
  SKY.setTheme(t.key);
  for (let el = -30; el <= 70; el += 1) for (const evening of [false, true]) {
    // drive apply() through a fixed elevation by faking the sun
    SKY.sunElevation = null;
  }
}
// apply() computes elevation from the clock; instead sweep the clock over a day at a fixed place
const lat = 52.37, lon = 4.9;
store["swz.loc.lat"] = String(lat); store["swz.loc.lon"] = String(lon); store["swz.loc.src"] = "device";
const realDate = Date;
for (const t of SKY.themes()) {
  SKY.setTheme(t.key);
  let w = { ink: 99, ink2: 99, ink3: 99 }, wl = { ink: 99, ink2: 99, ink3: 99 }, flips = 0, lastLight = null;
  for (const day of ["2026-06-21", "2026-12-21", "2026-03-20"]) for (let m = 0; m < 1440; m += 10) {
    const T = new realDate(day + "T00:00:00Z").getTime() + m * 60000;
    global.Date = class extends realDate { constructor(...a) { super(...(a.length ? a : [T])); } static now() { return T; } };
    SKY.setMode("auto");
    const st = SKY.state();
    const light = dataset.theme === "light";
    if (lastLight !== null && light !== lastLight) flips++; lastLight = light;
    const sky = [props["--sky-top"], props["--sky-mid"], props["--sky-low"]].map(hex);
    const glows = [1, 2, 3, 4].map((i) => hex(props["--glow-" + i]));
    // brightest and darkest backdrop the glass can sit on (glows screened at the engine's 0.7)
    const cands = sky.slice(); for (const g of glows) for (const b of sky) cands.push(b.map((v, i) => v + ((255 - (255 - v) * (255 - g[i]) / 255) - v) * 0.7));
    let glass;
    const inks = light ? LIGHT : DARK;
    for (const c of cands) {
      if (light) glass = over(c, [255, 255, 255], .56);                     // Porcelain frosted glass (weakest stop)
      else { const tint = parseFloat(props["--glass-tint"]), trgb = props["--glass-tint-rgb"].split(",").map(Number);
        glass = over(over(c, trgb, tint), [255, 255, 255], .09); }
      for (const k of ["ink", "ink2", "ink3"]) { const W = light ? wl : w; W[k] = Math.min(W[k], cr(hex(inks[k]), glass)); }
    }
  }
  global.Date = realDate;
  console.log(`${t.key.padEnd(9)} dark-glass ink ${w.ink.toFixed(1)}/${w.ink2.toFixed(1)}/${w.ink3.toFixed(1)}   light-glass ink ${wl.ink === 99 ? "—" : wl.ink.toFixed(1) + "/" + wl.ink2.toFixed(1) + "/" + wl.ink3.toFixed(1)}   switches: ${flips}`);
}
