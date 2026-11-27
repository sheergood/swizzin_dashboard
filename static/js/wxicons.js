/* Weather icons from sheergood/netdiag (public/index.html, generated there from tools/wxicons.js):
   every Open-Meteo WMO weather code, day + night, with the real moon phase at night, animated with CSS.
   Used here in netdiag's "line" style. wxIcon(code, isDay, set, size, moon) → SVG string. */
(function () {
"use strict";
const WX_CODES = {
  0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast', 45: 'Fog', 48: 'Depositing rime fog',
  51: 'Light drizzle', 53: 'Moderate drizzle', 55: 'Dense drizzle', 56: 'Light freezing drizzle', 57: 'Dense freezing drizzle',
  61: 'Slight rain', 63: 'Moderate rain', 65: 'Heavy rain', 66: 'Light freezing rain', 67: 'Heavy freezing rain',
  71: 'Slight snowfall', 73: 'Moderate snowfall', 75: 'Heavy snowfall', 77: 'Snow grains',
  80: 'Slight rain showers', 81: 'Moderate rain showers', 82: 'Violent rain showers', 85: 'Slight snow showers', 86: 'Heavy snow showers',
  95: 'Thunderstorm', 96: 'Thunderstorm with slight hail', 97: 'Heavy thunderstorm', 99: 'Thunderstorm with heavy hail',
};
const WX_SHORT = {
  0: 'Clear', 1: 'Mostly clear', 2: 'Partly cloudy', 3: 'Overcast', 45: 'Fog', 48: 'Freezing fog', 51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
  56: 'Freezing drizzle', 57: 'Freezing drizzle', 61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 66: 'Freezing rain', 67: 'Freezing rain',
  71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains', 80: 'Showers', 81: 'Rain showers', 82: 'Violent showers',
  85: 'Snow showers', 86: 'Heavy snow showers', 95: 'Thunderstorm', 96: 'Storm, hail', 97: 'Heavy thunderstorm', 99: 'Storm, heavy hail',
};

const WX_STYLE = { // the page uses 'line'; glass and duo are kept for switching later
  glass: {
    sun: 'url(#wx-sun)', sunRay: '#ffbe45', moon: 'url(#wx-moon)', star: '#e8edff',
    cloud: 'url(#wx-cloud)', cloudBack: 'url(#wx-cloud-b)', cloudDark: 'url(#wx-cloud-d)', cloudStroke: 'rgba(40,52,90,.28)', sw: 0.9,
    rain: '#5c9cff', drizzle: '#7fb2ff', snow: '#eef6ff', snowStroke: '#79b4f5', ice: '#8fd3ff', bolt: '#ffd34d', boltStroke: '#f59e0b', hail: '#eef6ff', fog: '#a9b4d2',
  },
  line: {
    sun: 'none', sunStroke: '#f5a524', sunRay: '#f5a524', moon: 'none', moonStroke: '#a5b4fc', star: '#a5b4fc',
    cloud: 'none', cloudBack: 'none', cloudDark: 'none', cloudStroke: 'currentColor', sw: 1.8, lineOnly: true,
    rain: '#5b9cf5', drizzle: '#7aaef7', snow: 'none', snowStroke: '#7dd3fc', ice: '#7dd3fc', bolt: 'none', boltStroke: '#f59e0b', hail: 'none', hailStroke: '#93c5fd', fog: 'currentColor',
  },
  duo: {
    sun: '#ffc145', sunRay: '#ffc145', moon: '#c7d2fe', star: '#c7d2fe',
    cloud: '#d6dcee', cloudBack: '#aab4d0', cloudDark: '#848fb0', cloudStroke: 'none', sw: 0,
    rain: '#5b9cf5', drizzle: '#7aaef7', snow: '#8cc4f7', snowStroke: 'none', ice: '#5fc4f5', bolt: '#ffc145', boltStroke: 'none', hail: '#a6d2f7', fog: '#97a1c0',
  },
};

const WX_CSS = `
.wxi{display:inline-block;overflow:visible;color:var(--mut,#8890aa)}
.wxi *{transform-box:fill-box}
.wxi .rays{transform-origin:center;animation:wx-spin 30s linear infinite}
.wxi .halo{animation:wx-breathe 4s ease-in-out infinite}
.wxi .drift{animation:wx-drift 7s ease-in-out infinite}
.wxi .drift2{animation:wx-drift 9s ease-in-out -3s infinite reverse}
.wxi .tw{animation:wx-tw 2.6s ease-in-out infinite}
.wxi .drop{animation:wx-fall 1.05s linear infinite}
.wxi.slow .drop{animation-duration:1.7s}
.wxi.fast .drop{animation-duration:.7s}
.wxi .flake{transform-origin:center;animation:wx-snow 2.8s linear infinite}
.wxi .grain{animation:wx-grain 1.9s linear infinite}
.wxi .hail{animation:wx-hail 1.2s cubic-bezier(.5,0,.8,.4) infinite}
.wxi .bolt{animation:wx-flash 3.4s linear infinite}
.wxi .bolt.b2{animation-delay:-1.6s}
.wxi .fogl{animation:wx-fog 5s ease-in-out infinite}
.wxi .fogl.r{animation-direction:reverse}
.wxi .spark{transform-origin:center;animation:wx-spark 2.2s ease-in-out infinite}
@keyframes wx-spin{to{transform:rotate(360deg)}}
@keyframes wx-breathe{0%,100%{opacity:.35}50%{opacity:.7}}
@keyframes wx-drift{0%,100%{transform:translateX(-1.6px)}50%{transform:translateX(1.6px)}}
@keyframes wx-tw{0%,100%{opacity:.25}50%{opacity:1}}
@keyframes wx-fall{0%{transform:translate(1px,-4px);opacity:.25}15%{opacity:1}80%{opacity:1}100%{transform:translate(-2px,8px);opacity:.15}}
@keyframes wx-snow{0%{transform:translate(0,-4px) rotate(0);opacity:.3}15%{opacity:1}80%{opacity:1}100%{transform:translate(2px,9px) rotate(120deg);opacity:.2}}
@keyframes wx-grain{0%{transform:translateY(-4px);opacity:.3}20%{opacity:1}100%{transform:translateY(8px);opacity:.2}}
@keyframes wx-hail{0%{transform:translateY(-6px);opacity:.3}15%{opacity:1}70%{transform:translateY(9px)}82%{transform:translateY(6.5px)}100%{transform:translateY(9px);opacity:0}}
@keyframes wx-flash{0%,84%,100%{opacity:.8;filter:none}86%,90%{opacity:1;filter:drop-shadow(0 0 3px #ffe58a)}88%{opacity:.45}93%{opacity:1;filter:drop-shadow(0 0 4px #ffe58a)}96%{opacity:.8}}
@keyframes wx-fog{0%,100%{transform:translateX(-2.5px)}50%{transform:translateX(2.5px)}}
@keyframes wx-spark{0%,100%{opacity:.2;transform:scale(.6)}50%{opacity:1;transform:scale(1)}}
@media (prefers-reduced-motion:reduce){.wxi *{animation:none!important}}
`;

let WX_UID = 0;
/* moon (optional) = { age 0–1 (0 new, .5 full), illum 0–1, up: above the horizon, south: southern hemisphere }:
   at night the icon then shows the real phase — or, with no moon in the sky, a soft glow with stars. */
function wxIcon(code, isDay = true, set = 'glass', size = 48, moon = null) {
  const S = WX_STYLE[set] || WX_STYLE.glass, L = !!S.lineOnly, uid = ++WX_UID;
  const st = (fill, stroke, w) => `fill="${fill}"${stroke && stroke !== 'none' ? ` stroke="${stroke}" stroke-width="${w}" stroke-linejoin="round" stroke-linecap="round"` : ''}`;
  // pieces (64×64 canvas)
  const sun = (cx, cy, r, rays = true) => `<g>${set === 'glass' ? `<circle class="halo" cx="${cx}" cy="${cy}" r="${r * 1.75}" fill="#ffd36e" opacity=".45"/>` : ''}
    ${rays ? `<g class="rays">${Array.from({ length: 8 }, (_, i) => { const a = (i * Math.PI) / 4, r1 = r * 1.38, r2 = r * 1.78; return `<line x1="${(cx + Math.cos(a) * r1).toFixed(1)}" y1="${(cy + Math.sin(a) * r1).toFixed(1)}" x2="${(cx + Math.cos(a) * r2).toFixed(1)}" y2="${(cy + Math.sin(a) * r2).toFixed(1)}" stroke="${S.sunRay}" stroke-width="${set === 'line' ? 1.8 : 2.4}" stroke-linecap="round"/>`; }).join('')}</g>` : ''}
    <circle cx="${cx}" cy="${cy}" r="${r}" ${st(S.sun, S.sunStroke, 1.8)}/></g>`;
  const moonCres = (cx, cy, r) => `<g>${set === 'glass' ? `<circle class="halo" cx="${cx}" cy="${cy}" r="${r * 1.7}" fill="#c9d4ff" opacity=".35"/>` : ''}
    <path d="M${cx + r * 0.35},${cy - r} A${r},${r} 0 1 0 ${cx + r},${cy + r * 0.38} A${r * 0.82},${r * 0.82} 0 0 1 ${cx + r * 0.35},${cy - r} Z" ${st(S.moon, S.moonStroke, 1.8)}/></g>`;
  const noMoon = moon && (!moon.up || moon.illum < 0.03);
  const phaseMoon = (cx, cy, r) => {
    const age = moon.age ?? 0.5, waxing = age < 0.5, k = Math.cos(2 * Math.PI * age), rx = (Math.abs(k) * r).toFixed(2);
    const lit = `M${cx},${cy - r} A${r},${r} 0 0 ${waxing ? 1 : 0} ${cx},${cy + r} A${rx},${r} 0 0 ${waxing ? (k > 0 ? 0 : 1) : (k > 0 ? 1 : 0)} ${cx},${cy - r} Z`;
    const litFill = L ? 'rgba(214,222,255,.62)' : set === 'duo' ? S.moon : 'url(#wx-moon)', ring = L ? S.moonStroke : 'rgba(200,210,240,.5)';
    return `<g${moon.south ? ` transform="translate(${2 * cx} 0) scale(-1 1)"` : ''}>${set === 'glass' ? `<circle class="halo" cx="${cx}" cy="${cy}" r="${r * 1.6}" fill="#c9d4ff" opacity=".3"/>` : ''}
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="${L ? 'rgba(165,180,252,.08)' : 'rgba(60,70,110,.35)'}" stroke="${ring}" stroke-width="${L ? 1.3 : 0.8}" stroke-opacity="${L ? 0.6 : 1}" stroke-dasharray="${L ? '2 2.4' : 'none'}"/>
      ${moon.illum > 0.015 ? `<path d="${lit}" fill="${litFill}" stroke="${L ? S.moonStroke : 'none'}" stroke-width="1.8" stroke-linejoin="round"/>` : ''}</g>`;
  };
  const glow = (cx, cy, r) => `<defs><radialGradient id="wxg${uid}"><stop offset="0" stop-color="#b8c4ff" stop-opacity=".8"/><stop offset=".5" stop-color="#a5b4fc" stop-opacity=".3"/><stop offset="1" stop-color="#a5b4fc" stop-opacity="0"/></radialGradient></defs>
    <circle class="halo" cx="${cx}" cy="${cy}" r="${r * 1.9}" fill="url(#wxg${uid})"/><circle cx="${cx}" cy="${cy}" r="${r * 0.5}" fill="none" stroke="${L ? S.moonStroke : '#c7d2fe'}" stroke-width="1.4" stroke-opacity=".75" stroke-dasharray="2 2.2"/>`;
  const nightBody = (cx, cy, r) => (!moon ? null : noMoon ? glow(cx, cy, r) : phaseMoon(cx, cy, r));
  const star = (x, y, s, d) => `<path class="tw" style="animation-delay:${d}s" d="M${x},${y - s} L${x + s * 0.28},${y - s * 0.28} L${x + s},${y} L${x + s * 0.28},${y + s * 0.28} L${x},${y + s} L${x - s * 0.28},${y + s * 0.28} L${x - s},${y} L${x - s * 0.28},${y - s * 0.28} Z" fill="${S.star}"/>`;
  const CL = 'M14,46 h32 a10,10 0 0 0 1.2,-19.9 A14,14 0 0 0 20.4,24.2 A9,9 0 0 0 14,46 Z';
  const cloud = (dx, dy, k, fill, cls = 'drift') => `<g class="${cls}"><path transform="translate(${dx} ${dy}) scale(${k})" d="${CL}" ${st(L ? 'none' : fill, S.cloudStroke, S.sw / k)}/></g>`;
  const drops = (n, kind, y0 = 46) => {
    const xs = [22, 30, 38, 46, 26, 42].slice(0, n), col = kind === 'drizzle' ? S.drizzle : S.rain, len = kind === 'drizzle' ? 3.6 : kind === 'heavy' ? 8 : 6.5;
    return xs.map((x, i) => `<line class="drop" style="animation-delay:${(-i * 0.37).toFixed(2)}s" x1="${x}" y1="${y0 + 3 + (i % 2) * 2}" x2="${x - 2}" y2="${y0 + 3 + (i % 2) * 2 + len}" stroke="${col}" stroke-width="${kind === 'heavy' ? 2.6 : kind === 'drizzle' ? 2.4 : 2.2}" stroke-linecap="round"/>`).join('');
  };
  const iceDrops = (n, y0 = 46) => [24, 34, 44].slice(0, n).map((x, i) => `<path class="drop" style="animation-delay:${(-i * 0.45).toFixed(2)}s" d="M${x},${y0 + 3} l2.6,4 -2.6,4 -2.6,-4 Z" ${st(L ? 'none' : S.ice, L ? S.ice : 'none', 1.5)}/>`).join('');
  const flake = (x, y, r, d) => `<g class="flake" style="animation-delay:${d}s"><g stroke="${L ? S.snowStroke : set === 'glass' ? S.snowStroke : S.snow}" stroke-width="${set === 'glass' ? 1.8 : 1.9}" stroke-linecap="round">${[0, 60, 120].map((a) => { const t = (a * Math.PI) / 180; return `<line x1="${(x - Math.cos(t) * r).toFixed(1)}" y1="${(y - Math.sin(t) * r).toFixed(1)}" x2="${(x + Math.cos(t) * r).toFixed(1)}" y2="${(y + Math.sin(t) * r).toFixed(1)}"/>`; }).join('')}</g>${set === 'glass' ? `<circle cx="${x}" cy="${y}" r="${r * 0.35}" fill="#ffffff" stroke="${S.snowStroke}" stroke-width=".5"/>` : ''}</g>`;
  const flakes = (n, y0 = 50) => [[24, y0], [36, y0 + 3], [46, y0], [30, y0 + 7], [42, y0 + 8]].slice(0, n).map(([x, y], i) => flake(x, y, 3.2, (-i * 0.6).toFixed(2))).join('');
  const grains = (y0 = 49) => [22, 28, 34, 40, 46].map((x, i) => `<circle class="grain" style="animation-delay:${(-i * 0.38).toFixed(2)}s" cx="${x}" cy="${y0 + (i % 2) * 3}" r="1.6" ${st(L ? 'none' : S.snow, L ? S.snowStroke : set === 'glass' ? S.snowStroke : 'none', 1.2)}/>`).join('');
  const hailBalls = (n, y0 = 48) => [24, 34, 44, 29, 39].slice(0, n).map((x, i) => `<circle class="hail" style="animation-delay:${(-i * 0.3).toFixed(2)}s" cx="${x}" cy="${y0 + (i > 2 ? 4 : 0)}" r="${n > 3 ? 2.4 : 2.1}" ${st(L ? 'none' : S.hail, L ? S.hailStroke : set === 'glass' ? 'rgba(80,110,170,.6)' : 'none', 1.3)}/>`).join('');
  const bolt = (x, y, k = 1, cls = '') => `<path class="bolt ${cls}" transform="translate(${x} ${y}) scale(${k})" d="M6,0 L0,11 H5 L2,21 L12,7 H7 L10,0 Z" ${st(L ? 'none' : S.bolt, S.boltStroke, set === 'line' ? 1.6 : 0.8)}/>`;
  const fog = (rows) => rows.map(([y, x1, x2], i) => `<line class="fogl${i % 2 ? ' r' : ''}" x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="${S.fog}" stroke-width="${set === 'line' ? 2 : 3}" stroke-linecap="round" opacity="${set === 'glass' ? 0.9 : 1}"/>`).join('');
  const spark = (x, y, d) => `<g class="spark" style="animation-delay:${d}s"><path d="M${x},${y - 3} V${y + 3} M${x - 3},${y} H${x + 3}" stroke="${S.ice}" stroke-width="1.5" stroke-linecap="round"/></g>`;
  const body = (big) => (isDay ? sun(big ? 32 : 22, big ? 32 : 20, big ? 11 : 8.5) : nightBody(big ? 30 : 21, big ? 30 : 19, big ? 13 : 9.5) || moonCres(big ? 30 : 21, big ? 30 : 19, big ? 13 : 9.5));
  const stars = () => (isDay ? '' : star(47, 15, 3.2, 0) + star(53, 30, 2.2, -1.1) + star(12, 42, 2.4, -1.9));

  let g = '', cls = '';
  const c = +code;
  if (c === 0) g = isDay ? sun(32, 32, 11.5) : (nightBody(31, 31, 13.5) || moonCres(30, 31, 14)) + stars();
  else if (c === 1) g = body(false) + (isDay ? '' : star(52, 14, 2.6, -0.6)) + cloud(14, 14, 0.62, S.cloud);
  else if (c === 2) g = body(false) + cloud(6, 6, 0.85, S.cloud);
  else if (c === 3) g = cloud(10, -6, 0.72, S.cloudBack, 'drift2') + cloud(0, 4, 0.92, S.cloud);
  else if (c === 45) g = cloud(4, -4, 0.85, S.cloudBack, 'drift2') + fog([[42, 12, 52], [49, 8, 46], [56, 16, 54]]);
  else if (c === 48) g = cloud(4, -4, 0.85, S.cloudBack, 'drift2') + fog([[42, 12, 52], [49, 8, 46], [56, 16, 54]]) + spark(54, 14, 0) + spark(10, 30, -1.1);
  else if ([51, 53, 55].includes(c)) { cls = 'slow'; g = cloud(0, -4, 0.92, S.cloud) + drops({ 51: 2, 53: 3, 55: 5 }[c], 'drizzle', 42); }
  else if ([56, 57].includes(c)) { cls = 'slow'; g = cloud(0, -4, 0.92, S.cloud) + drops(c === 56 ? 2 : 4, 'drizzle', 42) + spark(53, 52, 0) + spark(13, 55, -1); }
  else if ([61, 63, 65].includes(c)) { cls = c === 65 ? 'fast' : ''; g = (c === 65 ? cloud(10, -10, 0.72, S.cloudDark, 'drift2') : '') + cloud(0, -4, 0.92, c === 65 ? S.cloudBack : S.cloud) + drops({ 61: 2, 63: 3, 65: 5 }[c], c === 65 ? 'heavy' : 'rain', 42); }
  else if ([66, 67].includes(c)) g = cloud(0, -4, 0.92, S.cloud) + drops(c === 66 ? 1 : 2, 'rain', 42) + iceDrops(c === 66 ? 2 : 3, 42);
  else if ([71, 73, 75].includes(c)) g = cloud(0, -6, 0.92, c === 75 ? S.cloudBack : S.cloud) + flakes({ 71: 2, 73: 3, 75: 5 }[c], 48);
  else if (c === 77) g = cloud(0, -6, 0.92, S.cloud) + grains(46);
  else if ([80, 81, 82].includes(c)) { cls = c === 82 ? 'fast' : ''; g = body(false) + cloud(6, 2, 0.84, c === 82 ? S.cloudBack : S.cloud) + drops({ 80: 2, 81: 3, 82: 5 }[c], c === 82 ? 'heavy' : 'rain', 44); }
  else if ([85, 86].includes(c)) g = body(false) + cloud(6, 0, 0.84, S.cloud) + flakes(c === 85 ? 2 : 4, 50);
  else if ([95, 96, 97, 99].includes(c)) {
    cls = c === 97 ? 'fast' : '';
    g = cloud(10, -10, 0.72, S.cloudDark, 'drift2') + cloud(0, -4, 0.92, S.cloudBack)
      + (c === 97 ? bolt(16, 38, 0.95) + bolt(36, 40, 0.8, 'b2') + drops(3, 'heavy', 42) : bolt(26, 38, 1))
      + (c === 95 ? drops(2, 'rain', 42).replace(/x1="22"/, 'x1="18"') : '')
      + (c === 96 ? hailBalls(3, 46) : c === 99 ? hailBalls(5, 46) : '');
  } else g = cloud(0, 4, 0.92, S.cloud);
  if (!g.includes('<defs>')) WX_UID--; // ids are only needed for the glow gradient
  return `<svg class="wxi ${cls}" width="${size}" height="${size}" viewBox="4 3 56 56" role="img" aria-label="${WX_CODES[c] || 'Weather'}">${g}</svg>`;
}


document.head.insertAdjacentHTML("beforeend", `<style>${WX_CSS}</style>`);

/* Moon phase for night icons: age 0–1 (0 new, .5 full) from a known new moon; illum = lit fraction. */
function moonNow(lat) {
  const SYN = 29.530588853, ref = Date.UTC(2000, 0, 6, 18, 14) / 86400000;
  const age = ((((Date.now() / 86400000 - ref) / SYN) % 1) + 1) % 1;
  return { age, illum: (1 - Math.cos(2 * Math.PI * age)) / 2, up: true, south: lat != null && lat < 0 };
}
window.SWZ_WX = { wxIcon, WX_CODES, WX_SHORT, moonNow };
})();
