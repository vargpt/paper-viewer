"""Film the generated webpage: phone-width screenshots of the header, every section, every visual,
and flipbooks of the sliders being dragged, plus the page text. This is all the director sees,
and the raw footage the video is cut from.
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

WIDTH, HEIGHT, SCALE = 430, 932, 2.5   # an iPhone-ish viewport; 430 * 2.5 ≈ 1080 px, the video's width
MAX_VISUALS, MAX_SWEEPS, SWEEP_FRAMES = 24, 4, 10

# Visual elements worth filming, outermost first; nested matches are dropped.
VISUALS_JS = r"""
() => {
  const sel = 'figure, .widget, .figure, .chart, .plotly, canvas, svg, img, table, .card, .callout, .key-numbers, .stats';
  const ok = (el) => {
    if (el.closest('details:not([open])')) return false;
    const r = el.getBoundingClientRect();
    return r.width >= 160 && r.height >= 110 && r.height < 2600;
  };
  const els = [...document.querySelectorAll(sel)].filter(ok);
  const keep = els.filter(el => !els.some(o => o !== el && o.contains(el) && o.getBoundingClientRect().height < 1400));
  const heading = (el) => {
    const s = el.closest('section, header');
    const h = s && s.querySelector('h1, h2, h3');
    return h ? h.textContent.trim() : '';
  };
  return keep.map((el, i) => {
    el.setAttribute('data-tt', 'v' + i);
    const cap = el.querySelector('figcaption, .caption, h3, h4');
    return {sel: '[data-tt="v' + i + '"]', kind: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''),
            section: heading(el), caption: ((cap && cap.textContent) || el.getAttribute('alt') || el.getAttribute('aria-label') || '').trim().slice(0, 200),
            text: (el.innerText || '').trim().slice(0, 300)};
  });
}
"""

SECTIONS_JS = r"""
() => [...document.querySelectorAll('header, section')].filter(s => !s.parentElement.closest('section, header'))
  .filter(s => s.getBoundingClientRect().height > 120).map((s, i) => {
    s.setAttribute('data-ts', 's' + i);
    const h = s.querySelector('h1, h2');
    return {sel: '[data-ts="s' + i + '"]', title: h ? h.textContent.trim() : (s.id || s.tagName.toLowerCase()),
            text: (s.innerText || '').trim().slice(0, 1500)};
  })
"""

SLIDERS_JS = r"""
() => [...document.querySelectorAll('input[type=range]')].filter(r => !r.closest('details:not([open])')).map((r, i) => {
  r.setAttribute('data-tr', 'r' + i);
  let box = r.closest('.widget, figure, .card') || r.parentElement;
  for (let p = box; p && p !== document.body; p = p.parentElement) {   // grow until it holds a visual
    if (p.querySelector('canvas, svg, img, .chart')) { box = p; break; }
    if (p.getBoundingClientRect().height > 1400) break;
  }
  box.setAttribute('data-tb', 'b' + i);
  const label = (r.labels && r.labels[0] && r.labels[0].textContent) || r.getAttribute('aria-label') || r.id || '';
  return {input: '[data-tr="r' + i + '"]', box: '[data-tb="b' + i + '"]', label: label.trim().slice(0, 120),
          min: +r.min || 0, max: r.max === '' ? 100 : +r.max, value: +r.value};
})
"""

RECT_JS = r"""
(sel) => { const r = document.querySelector(sel).getBoundingClientRect();
  return [r.left + scrollX, r.top + scrollY, r.width, r.height]; }
"""

SET_SLIDER_JS = r"""
([sel, v]) => { const r = document.querySelector(sel); r.value = v;
  r.dispatchEvent(new Event('input', {bubbles: true})); r.dispatchEvent(new Event('change', {bubbles: true})); }
"""


def capture(page_html: Path, out: Path) -> dict:
    """Screenshots into out/shots/, and out/footage.json describing them."""
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=SCALE,
                                color_scheme="light")
        page.goto(page_html.resolve().as_uri(), wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(2500)   # Plotly, KaTeX and entrance animations
        page.add_style_tag(content="*, *::before, *::after { animation-play-state: paused !important; caret-color: transparent !important; }")
        title = page.title()

        page.screenshot(path=str(shots / "screen.jpg"), quality=88, type="jpeg")
        items.append({"id": "screen", "type": "image", "file": "shots/screen.jpg", "about": "the top of the page as a phone shows it"})

        sections = page.evaluate(SECTIONS_JS)
        for i, s in enumerate(sections):
            f = shots / f"section{i}.jpg"
            if _shoot(page, s["sel"], f):
                items.append({"id": f"section{i}", "type": "image", "file": f"shots/{f.name}",
                              "about": f"whole section: {s['title']}", "text": s["text"]})

        for i, v in enumerate(page.evaluate(VISUALS_JS)[:MAX_VISUALS]):
            f = shots / f"visual{i}.jpg"
            if _shoot(page, v["sel"], f):
                items.append({"id": f"visual{i}", "type": "image", "file": f"shots/{f.name}",
                              "about": f"{v['kind']} in '{v['section']}'" + (f": {v['caption']}" if v["caption"] else ""),
                              "text": v["text"]})

        for i, s in enumerate(page.evaluate(SLIDERS_JS)[:MAX_SWEEPS]):
            frames = _sweep(page, s, shots, f"sweep{i}")
            if frames:
                items.append({"id": f"sweep{i}", "type": "sweep", "file": frames[len(frames) // 2], "frames": frames,
                              "about": f"slider '{s['label']}' dragged from {s['min']:g} to {s['max']:g} (plays as a flipbook)"})

        text = page.evaluate("() => document.body.innerText")
        browser.close()

    for it in items:
        it["size"] = _size(out / it["file"])
    footage = {"page": str(page_html), "title": title, "text": text[:20000], "items": items}
    (out / "footage.json").write_text(json.dumps(footage, indent=2))
    return footage


def _sweep(page, s: dict, shots: Path, name: str) -> list[str]:
    """Drag a slider through its range, filming the same region of the page at every step.
    The region is fixed (the widget's largest extent over the sweep) so the frames line up."""
    values = [s["min"] + (s["max"] - s["min"]) * k / (SWEEP_FRAMES - 1) for k in range(SWEEP_FRAMES)]
    try:
        page.locator(s["box"]).first.scroll_into_view_if_needed(timeout=3000)
        rects = []
        for v in values:
            page.evaluate(SET_SLIDER_JS, [s["input"], v])
            page.wait_for_timeout(120)
            rects.append(page.evaluate(RECT_JS, s["box"]))
        x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
        clip = {"x": x0, "y": y0, "width": max(r[0] + r[2] for r in rects) - x0, "height": max(r[1] + r[3] for r in rects) - y0}
        frames = []
        for k, v in enumerate(values):
            page.evaluate(SET_SLIDER_JS, [s["input"], v])
            page.wait_for_timeout(250)
            f = shots / f"{name}_{k:02d}.jpg"
            page.screenshot(path=str(f), type="jpeg", quality=88, clip=clip, full_page=True)
            frames.append(f"shots/{f.name}")
        page.evaluate(SET_SLIDER_JS, [s["input"], s["value"]])
        return frames
    except Exception as e:
        print(f"    could not film slider {s['input']}: {str(e).splitlines()[0][:120]}")
        return []


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception:
        return p.chromium.launch(channel="chrome")   # an installed Google Chrome


def _shoot(page, sel: str, f: Path) -> bool:
    try:
        el = page.locator(sel).first
        el.scroll_into_view_if_needed(timeout=3000)
        page.wait_for_timeout(120)
        el.screenshot(path=str(f), type="jpeg", quality=88, timeout=15_000)
        return True
    except Exception as e:
        print(f"    could not film {sel}: {str(e).splitlines()[0][:120]}")
        return False


def _size(f: Path) -> list[int]:
    import struct
    data = f.read_bytes()
    i = 2
    while i < len(data):   # walk the JPEG markers to the frame header
        marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return [w, h]
        i += 2 + length
    return [0, 0]
