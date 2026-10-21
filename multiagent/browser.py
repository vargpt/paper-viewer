"""A headless browser that renders a page the way readers see it, and reports what broke.

Optional: without Playwright (or without a Chromium/Chrome to drive) the pipeline still runs and
pages are only checked statically. Install with `pip install playwright && playwright install chromium`;
an installed Google Chrome also works.
"""
import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

DESKTOP = 1100
PHONE = 390

# Each check returns [section id, problem] pairs; "page" when the problem isn't inside a section.
CHECKS_JS = r"""
() => {
  const where = (el) => { const s = el.closest('section[id]'); return s ? s.id : 'page'; };
  const name = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '');
  const out = [];
  for (const img of document.images)
    if (!img.complete || img.naturalWidth === 0) out.push([where(img), 'Image failed to load: ' + (img.getAttribute('src') || '').slice(0, 80)]);
  for (const e of document.querySelectorAll('.katex-error'))
    out.push([where(e), 'Math failed to render: ' + (e.getAttribute('title') || e.textContent).slice(0, 160)]);
  for (const el of document.querySelectorAll('.chart, .widget canvas, .widget svg')) {
    if (el.closest('details:not([open])')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 20) out.push([where(el), 'Visual is empty or collapsed (' + Math.round(r.width) + 'x' + Math.round(r.height) + 'px): ' + name(el)]);
  }
  for (const el of document.querySelectorAll('.chart'))
    if (!el.closest('details:not([open])') && window.Plotly && !el.querySelector('.main-svg') && !el.querySelector('canvas, svg'))
      out.push([where(el), 'Chart container has nothing drawn in it: ' + name(el)]);
  return out;
}
"""

OVERFLOW_JS = r"""
() => {
  const vw = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth <= vw + 1) return [];
  const scrolls = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement)
      if (getComputedStyle(p).overflowX !== 'visible') return true;
    return false;
  };
  const out = [];
  for (const el of document.querySelectorAll('main *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.right <= vw + 1 || scrolls(el)) continue;
    const parent = el.parentElement;
    if (parent && parent.closest('main') && parent.getBoundingClientRect().right > vw + 1) continue;
    const s = el.closest('section[id]');
    out.push([s ? s.id : 'page', el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.classList.length ? '.' + [...el.classList].join('.') : '')
      + ' sticks out ' + Math.round(r.right - vw) + 'px past the edge of a ' + vw + 'px phone screen']);
    if (out.length >= 8) break;
  }
  return out;
}
"""


# Operate one control of a widget, the way a reader would try it first. Returns what was done, or null.
PLAY_JS = r"""
(w) => {
  const visible = (el) => el.offsetParent !== null && !el.disabled && !el.closest('details:not([open])');
  const range = [...w.querySelectorAll('input[type=range]')].find(visible);
  if (range) {
    const min = Number(range.min || 0), max = Number(range.max || 100), v = Number(range.value);
    const to = Math.abs(v - max) > Math.abs(v - min) ? max : min;
    range.value = to; range.dispatchEvent(new Event('input', { bubbles: true })); range.dispatchEvent(new Event('change', { bubbles: true }));
    const label = range.getAttribute('aria-label') || (range.closest('label') || {}).textContent || 'slider';
    return 'moved the slider "' + label.trim().slice(0, 40) + '" to ' + to;
  }
  const select = [...w.querySelectorAll('select')].find(visible);
  if (select && select.options.length > 1) {
    select.selectedIndex = (select.selectedIndex + 1) % select.options.length; select.dispatchEvent(new Event('change', { bubbles: true }));
    return 'picked "' + select.options[select.selectedIndex].text.slice(0, 40) + '" in a menu';
  }
  const buttons = [...w.querySelectorAll('button, [role=button], input[type=checkbox], input[type=radio]')].filter(visible)
    .filter((b) => b.getAttribute('aria-pressed') !== 'true' && !b.checked && !/back|prev|reset|smaller/i.test(b.textContent));
  const b = buttons.find((x) => /next|play|step|run|show|try/i.test(x.textContent)) || buttons[buttons.length - 1];
  if (b) { b.click(); return 'clicked "' + (b.textContent || b.getAttribute('aria-label') || b.value || 'a control').trim().slice(0, 40) + '"'; }
  return null;
}
"""


@dataclass
class Render:
    problems: dict[str, list[str]] = field(default_factory=dict)  # section id (or "page") -> problems
    shots: list[Path] = field(default_factory=list)
    plays: list[tuple[Path, str]] = field(default_factory=list)  # (screenshot after operating a widget, what was done)
    height: int = 0

    def add(self, where: str, problem: str) -> None:
        if problem not in self.problems.setdefault(where, []):
            self.problems[where].append(problem)


def _section_of(text: str) -> str:
    m = re.search(r"section-([\w-]+)\.js", text or "")
    return m.group(1) if m else "page"


class Browser:
    def __init__(self):
        self._pw = self._browser = None
        self._lock = asyncio.Lock()
        self.available = async_playwright is not None
        self.note = "" if self.available else "Playwright is not installed"

    async def start(self) -> bool:
        async with self._lock:
            if self._browser or not self.available:
                return self._browser is not None
            try:
                self._pw = await async_playwright().start()
            except Exception as e:
                self.available, self.note = False, f"Playwright failed to start: {e}"
                return False
            errors = []
            # drafts load figures/ from disk; this lets widgets read their pixels (PV.imageLab), as on the final
            # page, where images are inlined
            args = ["--allow-file-access-from-files"]
            for options in ({}, {"channel": "chrome"}, {"channel": "msedge"}):  # bundled Chromium, else an installed browser
                try:
                    self._browser = await self._pw.chromium.launch(args=args, **options)
                    return True
                except Exception as e:
                    errors.append(str(e).splitlines()[0])
            await self._pw.stop()
            self.available, self.note = False, "no browser to drive (run `playwright install chromium`): " + errors[0]
            return False

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = self._pw = None

    async def render(self, page_path: Path, *, shots: Path | None = None, prefix: str = "page", selector: str | None = None,
                     max_shots: int = 16, slice_px: int = 1500, open_details: bool = False, max_plays: int = 0) -> Render | None:
        """Load the page at desktop width (screenshots, errors), then at phone width (overflow). With max_plays,
        also operate up to that many widgets (a slider, a button) and screenshot them afterwards."""
        if not await self.start():
            return None
        out = Render()
        context = await self._browser.new_context(viewport={"width": DESKTOP, "height": 900}, device_scale_factor=1)
        try:
            page = await context.new_page()

            def on_console(msg):  # failed loads are reported by on_failed and the image check instead
                if msg.type == "error" and not msg.text.startswith("Failed to load resource"):
                    out.add(_section_of(str(msg.location.get("url", ""))), "Console error: " + msg.text[:300])

            def on_error(err):
                out.add(_section_of(str(getattr(err, "stack", "") or "")), f"JavaScript error: {str(err)[:300]}")

            def on_failed(request):  # local files are covered by the image check
                if not request.url.startswith(("file:", "data:")):
                    out.add("page", f"Failed to load {request.url[:120]} (offline, or a CDN is blocked?)")

            page.on("console", on_console)
            page.on("pageerror", on_error)
            page.on("requestfailed", on_failed)
            try:
                await page.goto(page_path.resolve().as_uri(), wait_until="networkidle", timeout=45_000)
            except Exception as e:  # a slow CDN shouldn't sink the check; report it and look at what loaded
                out.add("page", f"Page did not finish loading: {str(e).splitlines()[0][:200]}")
            await page.wait_for_timeout(700)
            if open_details:
                await page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
                await page.wait_for_timeout(500)
            for where, problem in await page.evaluate(CHECKS_JS):
                out.add(where, problem)

            top, height = 0, await page.evaluate("document.documentElement.scrollHeight")
            if selector:
                box = await page.evaluate(
                    "(s) => { const e = document.querySelector(s); if (!e) return null;"
                    " const r = e.getBoundingClientRect(); return [r.top + scrollY, r.height]; }", selector)
                if box:
                    top, height = int(box[0]), int(box[1])
            out.height = height
            if shots:
                shots.mkdir(parents=True, exist_ok=True)
                y = top
                while y < top + height and len(out.shots) < max_shots:
                    h = min(slice_px, top + height - y)
                    path = shots / f"{prefix}-{len(out.shots) + 1:02d}.jpg"
                    await page.screenshot(path=str(path), full_page=True, type="jpeg", quality=70,
                                          clip={"x": 0, "y": y, "width": DESKTOP, "height": h})
                    out.shots.append(path)
                    y += h

            if max_plays and shots:
                await self._play(page, out, shots, prefix, selector, max_plays)

            await page.set_viewport_size({"width": PHONE, "height": 800})
            await page.wait_for_timeout(500)
            for where, problem in await page.evaluate(OVERFLOW_JS):
                out.add(where, problem)
        finally:
            await context.close()
        return out

    async def _play(self, page, out: Render, shots: Path, prefix: str, selector: str | None, max_plays: int) -> None:
        """Operate each widget's first control; report widgets that don't visibly change."""
        widgets = await page.query_selector_all(f"{selector or 'main'} .widget")
        for w in widgets:
            if len(out.plays) >= max_plays:
                break
            where = await w.evaluate("(w) => { const s = w.closest('section[id]'); return s ? s.id : 'page'; }")
            name = await w.evaluate("(w) => (w.querySelector('h3') || {}).textContent || w.id || 'a widget'")
            try:
                await w.scroll_into_view_if_needed(timeout=3000)
                before = await w.screenshot(type="jpeg", quality=70)
                action = await w.evaluate(PLAY_JS)
                if not action:
                    continue
                await page.wait_for_timeout(600)
                path = shots / f"{prefix}-play-{len(out.plays) + 1:02d}.jpg"
                after = await w.screenshot(path=str(path), type="jpeg", quality=70)
            except Exception as e:
                out.add(where, f"Could not operate the widget \"{name.strip()[:60]}\": {str(e).splitlines()[0][:160]}")
                continue
            if after == before:
                out.add(where, f"Widget \"{name.strip()[:60]}\": {action}, and nothing on screen changed. "
                               "Every control must visibly change what the widget shows.")
            out.plays.append((path, f"{where}: \"{name.strip()[:60]}\" after the reader {action}"))
