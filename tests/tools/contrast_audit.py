"""Measure real on-screen contrast of text and chart colours against the glass cards, across sky phases and themes."""
import asyncio, datetime as dt, io, json, sys
from playwright.async_api import async_playwright
from PIL import Image
from astral import Observer
from astral.sun import elevation
H = {"Authorization": "Basic cHJhdGVlazp0ZXN0"}
LAT, LON = 52.37, 4.90
obs = Observer(LAT, LON)

def find_time(target, evening):
    t = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)
    step = dt.timedelta(minutes=1) * (1 if evening else -1)
    best = None
    for i in range(12 * 60):
        x = t + step * i
        e = elevation(obs, x)
        if best is None or abs(e - target) < abs(best[1] - target):
            best = (x, e)
    return best[0]

SCEN = [("night", "auto", find_time(-30, True)), ("dawn", "auto", find_time(-4, False)), ("golden-am", "auto", find_time(3, False)),
        ("golden-pm", "auto", find_time(1.5, True)), ("dusk", "auto", find_time(-4, True)), ("noon", "auto", find_time(14, True)),
        ("light", "day", find_time(14, True)), ("dark", "night", find_time(14, True))]

def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
def cr(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
def parse(c):
    c = c.strip()
    if c.startswith("#"):
        c = c[1:]; return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    nums = c[c.index("(") + 1:c.index(")")].replace("/", " ").replace(",", " ").split()
    return tuple(int(float(x)) for x in nums[:3])

PROBE = """() => {
  const cs = getComputedStyle(document.documentElement), v = (n) => cs.getPropertyValue(n).trim();
  const boxes = {};
  for (const id of ['trafficCard', 'cpuCard', 'netCard', 'memCard', 'f2bCard', 'procCard', 'plexCard']) {
    const e = document.getElementById(id); if (!e || e.hidden) continue;
    const r = e.getBoundingClientRect(); boxes[id] = [r.left, r.top, r.width, r.height];
  }
  const plot = document.querySelector('#trafficBars, .bars'); const pr = plot && plot.getBoundingClientRect();
  return { ink: v('--ink'), ink2: v('--ink-2'), ink3: v('--ink-3'), s1: v('--s1'), s2: v('--s2'), s3: v('--s3'),
           phase: document.documentElement.dataset.skyphase, boxes, plot: pr ? [pr.left, pr.top, pr.width, pr.height] : null };
}"""

async def main():
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        for name, mode, when in [x for x in SCEN if not sys.argv[2:] or x[0] in sys.argv[2:]]:
            ctx = await b.new_context(viewport={"width": 1440, "height": 900}, extra_http_headers=H)
            await ctx.add_init_script(f"localStorage.setItem('swz.sky','{mode}');localStorage.setItem('swz.loc.lat','{LAT}');localStorage.setItem('swz.loc.lon','{LON}');localStorage.setItem('swz.loc.src','device')")
            pg = await ctx.new_page()
            await pg.clock.set_fixed_time(when)
            await pg.goto("http://localhost:8333/"); await pg.wait_for_timeout(3500)
            # hide text and marks so we sample only the glass the text sits on
            info = await pg.evaluate(PROBE)
            await pg.add_style_tag(content="#grid section > *, #grid .statusbar > * { visibility: hidden !important; } .glass::after { opacity: 0 !important; }")
            await pg.wait_for_timeout(400)
            samples = []
            # the sky is fixed to the window and cards scroll over it, so sample each card at the top,
            # middle and bottom of the window (the warmest/brightest parts of the sky)
            for bid in info["boxes"]:
                for where in ("start", "center", "end"):
                    await pg.evaluate(f"document.getElementById('{bid}').scrollIntoView({{block: '{where}'}})")
                    await pg.wait_for_timeout(120)
                    x, y, w, h = await pg.evaluate(f"(() => {{ const r = document.getElementById('{bid}').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }})()")
                    img = Image.open(io.BytesIO(await pg.screenshot())).convert("RGB")
                    hdr = await pg.evaluate("document.querySelector('.topbar').getBoundingClientRect().bottom")
                    for fx in (.06, .3, .5, .7, .94):
                        for fy in (.08, .3, .5, .7, .92):
                            px, py = int(x + w * fx), int(y + h * fy)
                            if 0 <= px < img.width and hdr + 6 <= py < img.height:     # not under the sticky header
                                samples.append(img.getpixel((px, py)) + ((bid, where, fx, fy),))
            if sys.argv[1:] and sys.argv[1] == name: img.save(f"/tmp/contrast_{name}.png")
            res = {k: round(min(cr(parse(info[k]), s) for s in samples), 2) for k in ("ink", "ink2", "ink3", "s1", "s2", "s3")}
            ranked = sorted(samples, key=lambda s: lum(s), reverse=info["phase"] != "light")
            print("   brightest/darkest:", [(s[:3], s[3]) for s in ranked[:4]])
            import statistics
            print("   median L:", round(statistics.median(lum(s) for s in samples), 3), " p90:", round(sorted(lum(s) for s in samples)[int(len(samples) * .9)], 3))
            worst = max(samples, key=lambda s: lum(s)) if info["phase"] not in ("light",) else min(samples, key=lambda s: lum(s))
            res["s1_vs_s2"] = round(cr(parse(info["s1"]), parse(info["s2"])), 2)
            out[name] = res
            print(f"{name:10} el={elevation(obs, when):6.1f} phase={info['phase']:8} " + "  ".join(f"{k}={v}" for k, v in res.items()) + f"  bg-extreme={worst}")
            await ctx.close()
        await b.close()
if __name__ == '__main__':
    asyncio.run(main())
