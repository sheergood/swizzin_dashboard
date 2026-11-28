# Layout sweep: every card at phone..4K in Auto/Light/Dark; script errors, sideways scroll, things outside
# their card, small elements pinned to a whole card. Needs the dev panel on :8333 and tests/tools sample data.
import asyncio, sys, json
sys.argv = ["x"]
import newcards as nc
from playwright.async_api import async_playwright
VIEWS = [("phone", 390, 844), ("tablet", 820, 1180), ("laptop", 1440, 900), ("qhd", 2560, 1440), ("4k", 3840, 2160)]
MODES = ["auto", "day", "night"]
CHECK = r"""() => {
  const out = {hscroll: document.scrollingElement.scrollWidth - innerWidth, escaped: [], pinned: []};
  const vis = (e) => { const s = getComputedStyle(e); return s.display !== 'none' && s.visibility !== 'hidden' && e.getClientRects().length; };
  for (const card of document.querySelectorAll('main section.glass:not([hidden])')) {
    if (!vis(card)) continue;
    const c = card.getBoundingClientRect();
    for (const el of card.querySelectorAll('*')) {
      if (!vis(el) || el.closest('.pop, .dropdown, svg, canvas, [hidden]')) continue;
      // rows scrolled out of view inside a scroll box are clipped by it, not outside the card
      let clipped = false;
      for (let a = el.parentElement; a && a !== card; a = a.parentElement) { const oy = getComputedStyle(a).overflowY, ox = getComputedStyle(a).overflowX;
        if (/(auto|scroll|hidden)/.test(oy + ox)) { const ar = a.getBoundingClientRect(), er = el.getBoundingClientRect();
          if (ar.top >= c.top - 1 && ar.bottom <= c.bottom + 1) { clipped = true; break; } } }
      if (clipped) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      // anything outside its card that the card would otherwise clip or show oddly
      const o = Math.max(c.left - r.left, r.right - c.right, c.top - r.top, r.bottom - c.bottom);
      if (o > 1.5 && getComputedStyle(card).overflow !== 'hidden') out.escaped.push(`${card.id || card.className.split(' ').slice(-1)} > ${el.tagName.toLowerCase()}.${[...el.classList].join('.')} (${Math.round(o)}px)`);
      // absolutely positioned, but its positioning box is the whole card rather than a nearby parent
      const st = getComputedStyle(el);
      if ((st.position === 'absolute') && el.offsetParent === card && el.parentElement !== card && r.width < 40 && r.height < 40)
        out.pinned.push(`${card.id} > ${el.tagName.toLowerCase()}.${[...el.classList].join('.')}`);
    }
  }
  out.escaped = [...new Set(out.escaped)].slice(0, 12); out.pinned = [...new Set(out.pinned)];
  return out;
}"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        problems = 0
        for vname, w, h in VIEWS:
            for mode in MODES:
                ctx = await b.new_context(viewport={"width": w, "height": h}, extra_http_headers=nc.H)
                await ctx.add_init_script(f"localStorage.setItem('swz.sky','{mode}')")
                pg = await ctx.new_page(); errs = []
                pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:160]))
                pg.on("console", lambda m: errs.append("console: " + m.text[:160]) if m.type == "error" else None)
                await pg.route("**/api/uptime*", lambda r: r.fulfill(json=nc.UPTIME))
                await pg.route("**/api/fail2ban*", lambda r: r.fulfill(json=nc.F2B))
                await pg.goto("http://localhost:8333/"); await pg.wait_for_timeout(3500)
                res = await pg.evaluate(CHECK)
                # interactions: specs popover, user menu, palette, traffic tabs
                try:
                    if w >= 720:
                        await pg.click(".brand-btn"); await pg.wait_for_timeout(500); await pg.keyboard.press("Escape")
                        await pg.click("#menuBtn"); await pg.wait_for_timeout(300); await pg.keyboard.press("Escape")
                    await pg.keyboard.press("Control+k"); await pg.wait_for_timeout(200); await pg.keyboard.type("plex"); await pg.keyboard.press("Escape")
                    for v in ("hours", "month", "year", "table", "chart"):
                        btn = await pg.query_selector(f'#trafficCard [data-view="{v}"]')
                        if btn and await btn.is_visible(): await btn.click(); await pg.wait_for_timeout(150)
                except Exception as e:
                    errs.append("interaction: " + str(e)[:160])
                await pg.wait_for_timeout(500)
                bad = res["hscroll"] > 0 or res["escaped"] or res["pinned"] or errs
                problems += bool(bad)
                print(f"{vname:6} {mode:5} hscroll={res['hscroll']:>3} escaped={res['escaped']} pinned={res['pinned']} errors={errs}" if bad else f"{vname:6} {mode:5} ok")
                await ctx.close()
        print("combinations with problems:", problems)
        await b.close()
asyncio.run(main())
