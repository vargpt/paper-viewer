"""The page every expert writes into: one shared design system, the hero and glossary, and assembly.

Experts only write <section> fragments (sections/<id>.html). This module wraps them in the shared
shell (CSS components, Plotly, KaTeX and the small PV helper library), checks fragments for
mechanical problems, and turns a page into plain text for the reader agents.
"""
import base64
import html
import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
PLOTLY = "https://cdn.plot.ly/plotly-2.35.2.min.js"
KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist"

CSS = r"""
:root {
  color-scheme: light;
  --fs: 18px;
  --bg: #f9f9f7; --surface: #fcfcfb; --panel: #ffffff;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #6b6a65;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11, 11, 11, .10);
  --link: #1c5cab; --accent: #2a78d6;
  --c1: #2a78d6; --c2: #eb6834; --c3: #1baf7a; --c4: #eda100;
  --c5: #e87ba4; --c6: #008300; --c7: #6250d6; --c8: #e34948;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b; --good-ink: #006300;
  --kid-bg: #fff6dc; --kid-edge: #eda100;
  --deep-bg: #eef4fc; --deep-edge: #2a78d6;
  --toy-bg: #ece9fb; --toy-ink: #4a3aa7;
  --paper-bg: #e3f4ec; --paper-ink: #0b6b47;
  --caveat-bg: #fdf0ea; --caveat-edge: #ec835a;
  --result-bg: #e8f6ef; --result-edge: #1baf7a;
  --shadow: 0 1px 2px rgba(0, 0, 0, .05), 0 6px 24px rgba(0, 0, 0, .05);
  --radius: 14px;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #0d0d0d; --surface: #1a1a19; --panel: #1f1f1d;
    --ink: #f0efec; --ink-2: #c3c2b7; --muted: #a3a29b;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255, 255, 255, .10);
    --link: #86b6ef; --accent: #3987e5;
    --c1: #3987e5; --c2: #d95926; --c3: #199e70; --c4: #c98500;
    --c5: #d55181; --c6: #008300; --c7: #9085e9; --c8: #e66767;
    --good-ink: #0ca30c;
    --kid-bg: #2a2412; --kid-edge: #c98500;
    --deep-bg: #142033; --deep-edge: #3987e5;
    --toy-bg: #231e3d; --toy-ink: #c3bcf5;
    --paper-bg: #10261d; --paper-ink: #7ad8ac;
    --caveat-bg: #2b1a13; --caveat-edge: #d95926;
    --result-bg: #102219; --result-edge: #199e70;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
html { font-size: var(--fs); scroll-behavior: smooth; scroll-padding-top: 64px; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--font); line-height: 1.65;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
a { color: var(--link); text-underline-offset: 2px; }
img, canvas, video { max-width: 100%; height: auto; }
svg { max-width: 100%; }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.skip { position: absolute; left: -999px; }
.skip:focus { left: 12px; top: 12px; z-index: 100; background: var(--panel); padding: 8px 12px; border-radius: 8px; }
.progress { position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 50; pointer-events: none; }
.progress span { display: block; height: 100%; width: 0; background: var(--accent); }

/* hero */
.hero { background: linear-gradient(180deg, var(--surface), var(--bg)); border-bottom: 1px solid var(--grid); }
.hero-inner { max-width: 1040px; margin: 0 auto; padding: 64px 20px 40px; }
.kicker { text-transform: uppercase; letter-spacing: .08em; font-size: .75rem; font-weight: 700; color: var(--accent); margin: 0 0 12px; }
.hero h1 { font-size: clamp(2rem, 4.6vw, 3.1rem); line-height: 1.12; letter-spacing: -.02em; margin: 0 0 14px; }
.subtitle { font-size: 1.2rem; color: var(--ink-2); margin: 0 0 16px; max-width: 62ch; }
.paper-ref { font-size: .85rem; color: var(--muted); margin: 0 0 28px; max-width: 80ch; }
.levels { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 0 0 18px; }
.level { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); }
.level h2 { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; margin: 0 0 8px; color: var(--ink-2); }
.level p { margin: 0; }
.level.kid { background: var(--kid-bg); }
.level.kid p { font-size: 1.06rem; }
.why { background: var(--panel); border: 1px solid var(--border); border-left: 5px solid var(--accent); border-radius: var(--radius);
  padding: 14px 18px; margin: 0 0 18px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.stat { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.stat .v { display: block; font-size: 1.9rem; font-weight: 700; line-height: 1.1; letter-spacing: -.01em; }
.stat .l { display: block; font-size: .92rem; color: var(--ink-2); margin-top: 4px; }
.stat .s { display: block; font-size: .74rem; color: var(--muted); margin-top: 6px; }
@media (max-width: 860px) { .levels { grid-template-columns: 1fr; } }

/* top bar */
.topbar { position: sticky; top: 0; z-index: 40; background: color-mix(in srgb, var(--bg) 92%, transparent);
  backdrop-filter: saturate(1.4) blur(8px); -webkit-backdrop-filter: saturate(1.4) blur(8px); border-bottom: 1px solid var(--grid); }
.topbar-inner { max-width: 1040px; margin: 0 auto; display: flex; align-items: center; gap: 8px; padding: 6px 12px; }
.toc { display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; flex: 1; min-width: 0; }
.toc::-webkit-scrollbar { display: none; }
.toc a { white-space: nowrap; text-decoration: none; color: var(--ink-2); font-size: .8rem; padding: 6px 10px; border-radius: 999px; }
.toc a:hover { background: var(--grid); color: var(--ink); }
.toc a.active { background: var(--ink); color: var(--bg); }
.tools { display: flex; gap: 4px; flex: none; }
.tools button { font: inherit; font-size: .78rem; background: var(--panel); color: var(--ink); border: 1px solid var(--border);
  border-radius: 8px; padding: 5px 9px; cursor: pointer; }
.tools button:hover { border-color: var(--accent); }

/* chapters */
main { max-width: 860px; margin: 0 auto; padding: 0 20px 64px; counter-reset: chapter; }
.chapter { padding: 64px 0 20px; border-bottom: 1px solid var(--grid); }
.chapter > h2 { font-size: clamp(1.6rem, 3.2vw, 2.1rem); line-height: 1.2; letter-spacing: -.01em; margin: 0 0 12px; counter-increment: chapter; }
.chapter > h2::before { content: counter(chapter, decimal-leading-zero); display: block; font-size: .78rem; font-weight: 700;
  letter-spacing: .12em; color: var(--accent); margin-bottom: 6px; }
.chapter h3 { font-size: 1.22rem; line-height: 1.3; margin: 34px 0 8px; }
.chapter p, .chapter li { max-width: 68ch; }
.tldr { font-size: 1.12rem; color: var(--ink-2); border-left: 4px solid var(--accent); padding: 2px 0 2px 14px; margin: 0 0 22px; }

/* layered explanations */
.eli5 { background: var(--kid-bg); border-radius: var(--radius); padding: 16px 20px; margin: 22px 0;
  border: 1px solid color-mix(in srgb, var(--kid-edge) 35%, transparent); }
.eli5 > :first-child { margin-top: 0; } .eli5 > :last-child { margin-bottom: 0; }
.eli5 .label { font-weight: 700; font-size: .82rem; letter-spacing: .03em; color: var(--ink-2); margin: 0 0 6px; }
.eli5 p { font-size: 1.05rem; }
details.deeper { background: var(--deep-bg); border-radius: var(--radius); margin: 24px 0;
  border: 1px solid color-mix(in srgb, var(--deep-edge) 25%, transparent); }
details.deeper > summary { cursor: pointer; font-weight: 650; padding: 14px 18px; list-style: none; }
details.deeper > summary::-webkit-details-marker { display: none; }
details.deeper > summary::before { content: "\25B8"; display: inline-block; margin-right: 10px; transition: transform .15s; }
details.deeper[open] > summary::before { transform: rotate(90deg); }
details.deeper > :not(summary) { margin-left: 18px; margin-right: 18px; }
details.deeper[open] { padding-bottom: 6px; }
.term { border-bottom: 2px dotted var(--accent); cursor: help; }
.tip { position: absolute; z-index: 60; max-width: 320px; background: var(--ink); color: var(--bg); font-size: .85rem; line-height: 1.45;
  padding: 8px 12px; border-radius: 8px; box-shadow: 0 8px 24px rgba(0, 0, 0, .2); pointer-events: none; opacity: 0; transition: opacity .12s; }
.tip.show { opacity: 1; }
.src { font-size: .7rem; color: var(--muted); border: 1px solid var(--grid); border-radius: 999px; padding: 1px 7px;
  white-space: nowrap; vertical-align: 2px; margin-left: 2px; }

/* visuals */
.widget { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 20px; margin: 30px 0; }
.widget h3 { margin: 6px 0 4px; font-size: 1.15rem; }
.widget .how { color: var(--ink-2); font-size: .92rem; margin: 0 0 14px; }
.widget .notice { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; font-size: .95rem; margin: 16px 0 0; max-width: none; }
.controls { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center; margin: 8px 0 16px; }
.controls label { display: flex; align-items: center; gap: 10px; font-size: .92rem; color: var(--ink-2); flex-wrap: wrap; }
input[type=range] { accent-color: var(--accent); width: 220px; max-width: 100%; }
output, .readout { font-variant-numeric: tabular-nums; font-weight: 650; color: var(--ink); }
.btn { font: inherit; font-size: .9rem; background: var(--surface); color: var(--ink); border: 1px solid var(--border);
  border-radius: 10px; padding: 7px 14px; cursor: pointer; }
.btn:hover { border-color: var(--accent); }
.btn.on, .btn[aria-pressed="true"] { background: var(--accent); color: #fff; border-color: var(--accent); }
.chart { width: 100%; min-height: 320px; }
.badge { display: inline-block; font-size: .7rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; padding: 3px 9px; border-radius: 999px; }
.badge.toy { background: var(--toy-bg); color: var(--toy-ink); }
.badge.paper { background: var(--paper-bg); color: var(--paper-ink); }
figure { margin: 30px 0; }
.paper-figure img { display: block; width: 100%; background: #fff; border-radius: 10px; border: 1px solid var(--border); }
figcaption { font-size: .9rem; color: var(--ink-2); margin-top: 10px; }
figcaption .badge { margin-right: 6px; }
.callout { border-radius: var(--radius); padding: 14px 18px; margin: 24px 0; border-left: 5px solid var(--accent); background: var(--deep-bg); }
.callout.result { background: var(--result-bg); border-left-color: var(--result-edge); }
.callout.caveat { background: var(--caveat-bg); border-left-color: var(--caveat-edge); }
.callout > :first-child { margin-top: 0; } .callout > :last-child { margin-bottom: 0; }
.equation { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px 16px; margin: 18px 0; overflow-x: auto; }
.equation .symbols { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; font-size: .9rem; margin: 10px 0 0; }
.equation .symbols dt { font-weight: 600; } .equation .symbols dd { margin: 0; color: var(--ink-2); }
.eq-plain { font-size: .95rem; color: var(--ink-2); }
.katex-display { overflow-x: auto; overflow-y: hidden; padding: 4px 0; }
.steps { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; counter-reset: step; margin: 24px 0; }
.steps > li { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px; max-width: none; }
.steps > li::before { counter-increment: step; content: counter(step); display: grid; place-items: center; width: 26px; height: 26px;
  border-radius: 50%; background: var(--accent); color: #fff; font-weight: 700; font-size: .8rem; margin-bottom: 8px; }
.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 24px 0; }
.compare > * { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.compare > * > :first-child { margin-top: 0; }
.quiz { border: 2px dashed var(--grid); border-radius: var(--radius); padding: 14px 18px; margin: 26px 0; }
.quiz .q { font-weight: 650; margin: 0 0 8px; }
.quiz summary { cursor: pointer; color: var(--link); font-weight: 600; }
.table-wrap { overflow-x: auto; }
table.data { border-collapse: collapse; width: 100%; font-size: .92rem; font-variant-numeric: tabular-nums; margin: 16px 0; }
table.data caption { text-align: left; font-size: .85rem; color: var(--ink-2); padding-bottom: 6px; }
table.data th, table.data td { padding: 8px 10px; border-bottom: 1px solid var(--grid); text-align: left; }
table.data th { font-weight: 650; color: var(--ink-2); }
table.data tr.highlight td { background: var(--result-bg); font-weight: 650; }
@media (max-width: 640px) { .compare { grid-template-columns: 1fr; } .widget { padding: 16px; } }

/* glossary and footer */
.glossary { padding: 64px 0 20px; }
.glossary h2 { font-size: 1.6rem; margin: 0 0 16px; }
.glossary dl { display: grid; grid-template-columns: minmax(140px, max-content) 1fr; gap: 10px 22px; }
.glossary dt { font-weight: 650; }
.glossary dd { margin: 0; color: var(--ink-2); }
@media (max-width: 640px) { .glossary dl { grid-template-columns: 1fr; gap: 2px; } .glossary dd { margin-bottom: 10px; } }
footer.site { max-width: 860px; margin: 0 auto; padding: 28px 20px 80px; font-size: .88rem; color: var(--muted); }
footer.site h2 { font-size: 1rem; color: var(--ink-2); margin: 0 0 8px; }
footer.site p { max-width: 75ch; }
"""

PV_JS = r"""
window.PV = (() => {
  const v = (n) => getComputedStyle(document.documentElement).getPropertyValue('--' + n).trim();
  const isObj = (x) => x && typeof x === 'object' && !Array.isArray(x);
  const merge = (a, b) => { for (const k of Object.keys(b || {})) a[k] = isObj(b[k]) && isObj(a[k]) ? merge({ ...a[k] }, b[k]) : b[k]; return a; };
  const ready = (fn) => (document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn());
  const mathOptions = { delimiters: [{ left: '\\[', right: '\\]', display: true }, { left: '\\(', right: '\\)', display: false }], throwOnError: false };
  const palette = () => [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v('c' + i));
  const layout = (extra = {}) => {
    const axis = { gridcolor: v('grid'), linecolor: v('axis'), zerolinecolor: v('axis'), tickfont: { color: v('ink-2') },
      title: { font: { color: v('ink-2') } }, automargin: true };
    return merge({ paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', colorway: palette(),
      font: { family: v('font'), size: 15, color: v('ink') }, margin: { l: 56, r: 16, t: 24, b: 48 },
      xaxis: { ...axis }, yaxis: { ...axis }, legend: { orientation: 'h', y: -0.22 }, hoverlabel: { font: { family: v('font') } } }, extra);
  };
  const config = { displayModeBar: false, responsive: true };
  return {
    color: v, palette, layout, config, ready, mathOptions,
    plot(el, traces, lay = {}, conf = {}) {
      el = typeof el === 'string' ? document.getElementById(el) : el;
      if (!el || !window.Plotly) return null;
      return Plotly.newPlot(el, traces, layout(lay), { ...config, ...conf });
    },
    math(el) { if (window.renderMathInElement && el) window.renderMathInElement(el, mathOptions); },
  };
})();
"""

# explorable-visual building blocks (PV.imageLab, PV.stepper, PV.compare, PV.svg, PV.drag)
VISUAL_JS = (HERE / "visual.js").read_text()
VISUAL_CSS = (HERE / "visual.css").read_text()

SHELL_JS = r"""
(() => {
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  PV.ready(() => PV.math(document.body));

  // glossary terms: tooltip on hover, focus or tap
  const tip = document.createElement('div');
  tip.className = 'tip'; tip.setAttribute('role', 'tooltip'); document.body.appendChild(tip);
  let current = null;
  const show = (el) => {
    current = el; tip.textContent = el.dataset.def; tip.classList.add('show');
    const r = el.getBoundingClientRect();
    const x = Math.min(r.left + scrollX, scrollX + innerWidth - tip.offsetWidth - 12);
    tip.style.left = Math.max(scrollX + 12, x) + 'px'; tip.style.top = (r.bottom + scrollY + 8) + 'px';
  };
  const hide = () => { current = null; tip.classList.remove('show'); };
  $$('.term[data-def]').forEach((t) => { if (!t.hasAttribute('tabindex')) t.tabIndex = 0; });
  document.addEventListener('mouseover', (e) => { const t = e.target.closest('.term[data-def]'); if (t) show(t); else if (current) hide(); });
  document.addEventListener('focusin', (e) => { const t = e.target.closest('.term[data-def]'); if (t) show(t); });
  document.addEventListener('focusout', hide);
  document.addEventListener('click', (e) => { const t = e.target.closest('.term[data-def]'); if (!t) hide(); else if (current === t) hide(); else show(t); });

  // reading progress and the active chapter in the top bar
  const bar = document.querySelector('.progress span');
  addEventListener('scroll', () => {
    const h = document.documentElement.scrollHeight - innerHeight;
    if (bar) bar.style.width = (h > 0 ? (100 * scrollY) / h : 0) + '%';
  }, { passive: true });
  const nav = document.querySelector('.toc');
  const links = new Map($$('.toc a').map((a) => [a.getAttribute('href').slice(1), a]));
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (!e.isIntersecting) return;
      links.forEach((a) => a.classList.remove('active'));
      const a = links.get(e.target.id);
      if (a) { a.classList.add('active'); nav.scrollTo({ left: a.offsetLeft - 40, behavior: 'smooth' }); }
    }), { rootMargin: '-40% 0px -55% 0px' });
    $$('main > section[id]').forEach((s) => io.observe(s));
  }

  // charts hidden in a closed <details> need a resize when it opens
  document.addEventListener('toggle', (e) => {
    if (e.target.open && window.Plotly) e.target.querySelectorAll('.js-plotly-plot').forEach((p) => Plotly.Plots.resize(p));
  }, true);

  // text size and "expand all details"
  const root = document.documentElement;
  let size = 18;
  try { size = Number(localStorage.getItem('pv-fs')) || 18; } catch (e) {}
  const setSize = (s) => {
    size = Math.max(15, Math.min(26, s)); root.style.setProperty('--fs', size + 'px');
    try { localStorage.setItem('pv-fs', size); } catch (e) {}
    if (window.Plotly) $$('.js-plotly-plot').forEach((p) => Plotly.Plots.resize(p));
  };
  if (size !== 18) setSize(size);
  const smaller = document.getElementById('pv-smaller'), bigger = document.getElementById('pv-bigger'), expand = document.getElementById('pv-expand');
  if (smaller) smaller.onclick = () => setSize(size - 1);
  if (bigger) bigger.onclick = () => setSize(size + 1);
  if (expand) expand.onclick = () => {
    const open = expand.getAttribute('aria-pressed') !== 'true';
    $$('details.deeper').forEach((d) => { d.open = open; });
    expand.setAttribute('aria-pressed', String(open));
    expand.textContent = open ? 'Hide details' : 'Show all details';
  };
})();
"""

SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<meta name="description" content="{{description}}">
<link rel="stylesheet" href="{{katex}}/katex.min.css">
<script defer src="{{katex}}/katex.min.js"></script>
<script defer src="{{katex}}/contrib/auto-render.min.js"></script>
<script src="{{plotly}}"></script>
<style>{{css}}</style>
<script>{{pv_js}}</script>
</head>
<body>
<!--chrome--><a class="skip" href="#main">Skip to the explanation</a>
<div class="progress" aria-hidden="true"><span></span></div><!--/chrome-->
{{hero}}
<!--chrome--><div class="topbar">
  <div class="topbar-inner">
    <nav class="toc" aria-label="Sections">{{toc}}</nav>
    <div class="tools">
      <button id="pv-smaller" type="button" aria-label="Smaller text">A&minus;</button>
      <button id="pv-bigger" type="button" aria-label="Bigger text">A+</button>
      <button id="pv-expand" type="button" aria-pressed="false">Show all details</button>
    </div>
  </div>
</div><!--/chrome-->
<main id="main">
{{sections}}
{{glossary}}
</main>
{{footer}}
<script>{{shell_js}}</script>
</body>
</html>
"""


def fill(template: str, **values: str) -> str:
    """Replace {{name}} placeholders; braces elsewhere (CSS, JS, JSON) are left alone."""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def render_hero(plan: dict) -> str:
    paper, hero = plan["paper"], plan["hero"]
    stats = "".join(
        f'<div class="stat"><span class="v">{esc(n["value"])}</span> <span class="l">{esc(n["label"])}</span> '
        f'<span class="s">(source: {esc(n["source"])})</span></div>\n'
        for n in hero.get("key_numbers", []))
    ref = esc(paper.get("title"))
    if paper.get("authors"):
        ref += f" &middot; {esc(paper['authors'])}"
    if paper.get("venue") or paper.get("year"):
        ref += f" &middot; {esc(' '.join(x for x in (paper.get('venue'), paper.get('year')) if x))}"
    return f"""<header class="hero" id="top">
  <div class="hero-inner">
    <p class="kicker">A research paper, explained for everyone</p>
    <h1>{esc(hero["headline"])}</h1>
    <p class="subtitle">{esc(hero["subtitle"])}</p>
    <p class="paper-ref">Based on the paper: <cite>{ref}</cite></p>
    <div class="levels">
      <div class="level kid"><h2>&#x1F9D2; In kid words</h2><p>{esc(hero["kid"])}</p></div>
      <div class="level"><h2>&#x1F4D6; The short version</h2><p>{esc(hero["general"])}</p></div>
      <div class="level"><h2>&#x1F52C; For experts</h2><p>{esc(hero["expert"])}</p></div>
    </div>
    <p class="why"><strong>Why it matters:</strong> {esc(hero["why_it_matters"])}</p>
    {f'<div class="stats">{stats}</div>' if stats else ''}
  </div>
</header>"""


def render_glossary(plan: dict) -> str:
    items = "".join(f"<dt>{esc(g['term'])}</dt><dd>{esc(g['definition'])}</dd>"
                    for g in sorted(plan.get("glossary", []), key=lambda g: g["term"].lower()))
    return f'<section class="glossary" id="glossary"><h2>Glossary</h2><dl>{items}</dl></section>' if items else ""


def render_footer(plan: dict, readers: list[str], rounds: int) -> str:
    owners = {}
    for s in plan["sections"]:
        owners.setdefault(s["owner"], []).append(s["title"])
    team = "; ".join(f"{esc(e['name'])} ({esc(', '.join(owners.get(e['id'], [])))})" for e in plan["experts"])
    paper = plan["paper"]
    return f"""<footer class="site">
  <h2>How this page was made</h2>
  <p>This explainer was written by a team of AI agents. A lead editor read the paper and planned the page; experts wrote
  the sections: {team}. The page was then revised over {rounds} round{'s' if rounds != 1 else ''} using feedback from
  simulated readers ({esc(', '.join(readers))}), reviews by the experts of each other's sections, and a fact-checker that
  compared every section with the paper.</p>
  <p>It can still contain mistakes: the original paper is the authoritative source. Anything marked
  <span class="badge toy">Toy example</span> is a simplified teaching illustration with made-up numbers, not a result
  from the paper. Figures marked <span class="badge paper">From the paper</span> are reproduced from it.</p>
  <p>Paper: <cite>{esc(paper.get('title'))}</cite>{(' &middot; ' + esc(paper['authors'])) if paper.get('authors') else ''}</p>
</footer>"""


def tag_scripts(fragment: str, sid: str) -> str:
    """Name each inline script after its section, so browser errors say which section broke."""
    def tag(m: re.Match) -> str:
        attrs = m.group(1)
        kind = re.search(r'\btype\s*=\s*["\']?([^"\'\s>]+)', attrs)
        if re.search(r"\bsrc\s*=", attrs) or (kind and kind.group(1).lower() not in ("text/javascript", "module")):
            return m.group(0)
        return f"<script{attrs}>{m.group(2)}\n//# sourceURL=section-{sid}.js\n</script>"
    return re.sub(r"<script([^>]*)>(.*?)</script>", tag, fragment, flags=re.S | re.I)


def assemble(ws: Path, plan: dict, *, readers: list[str], rounds: int, only: str | None = None) -> str:
    """The whole page (or one section of it, for previews), with images referenced from figures/."""
    parts, links = [], []
    for s in plan["sections"]:
        if only and s["id"] != only:
            continue
        f = ws / "sections" / f"{s['id']}.html"
        fragment = f.read_text() if f.exists() else (
            f'<section class="chapter" id="{esc(s["id"])}"><h2>{esc(s["title"])}</h2><p><em>This section is still being written.</em></p></section>')
        parts.append(tag_scripts(fragment, s["id"]))
        links.append(f'<a href="#{esc(s["id"])}">{esc(s["title"])}</a>')
    if not only and plan.get("glossary"):
        links.append('<a href="#glossary">Glossary</a>')
    return fill(
        SHELL,
        title=esc(plan["hero"]["headline"]),
        description=esc(plan["hero"]["general"]),
        katex=KATEX, plotly=PLOTLY, css=CSS + VISUAL_CSS, pv_js=PV_JS + VISUAL_JS, shell_js=SHELL_JS,
        hero="" if only else render_hero(plan),
        toc="".join(links),
        sections="\n".join(parts),
        glossary="" if only else render_glossary(plan),
        footer="" if only else render_footer(plan, readers, rounds),
    )


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")

# Widgets often build image paths at run time ('figures/' + name + '.jpg'), which no regex can inline. The
# final page carries those images in a map, and image loading is patched to take them from it.
FIGURE_MAP_JS = """<script>(() => {
  const F = %s;
  const fix = (v) => (typeof v === 'string' && F[v.replace(/^\\.\\//, '')]) || v;
  const d = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  Object.defineProperty(HTMLImageElement.prototype, 'src', { get: d.get, set(v) { d.set.call(this, fix(v)); }, configurable: true });
  const set = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (n, v) { return set.call(this, n, n === 'src' ? fix(v) : v); };
})();</script>"""


def _data_uri(path: Path) -> str:
    data, mime = path.read_bytes(), "image/png"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif path.suffix.lower() in (".webp", ".gif", ".svg"):
        mime = {"webp": "image/webp", "gif": "image/gif", "svg": "image/svg+xml"}[path.suffix.lower()[1:]]
    elif len(data) > 400_000:  # big screenshots-of-photos compress far better as JPEG
        pix = pymupdf.Pixmap(str(path))
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        data, mime = pix.tobytes("jpg", jpg_quality=85), "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def inline_images(page: str, ws: Path) -> str:
    """Embed figures/ images as data URIs so the final page is a single self-contained file."""
    figures = (ws / "figures").resolve()

    def data_uri(m: re.Match) -> str:
        path = (ws / m.group(2)).resolve()
        if not path.is_file() or not path.is_relative_to(figures):
            return m.group(0)
        return f"{m.group(1)}{_data_uri(path)}{m.group(3)}"

    # src="figures/..." and quoted paths in scripts ('figures/x.jpg' for PV.imageLab): a canvas can only read
    # the pixels of a same-origin image, which a data: URI is
    page = re.sub(r'''(\bsrc\s*=\s*["']|["'])(figures/[\w./-]+\.(?:png|jpe?g|webp|gif|svg))(["'])''', data_uri, page)

    # paths built at run time: a literal prefix ('figures/' or 'figures/one-step-') plus a name that appears in the page
    prefixes = set(re.findall(r"""["'`]figures/([\w.-]*)(?:["'`]|\$\{)""", page))
    dynamic = {}
    if prefixes and figures.is_dir():
        for f in sorted(figures.iterdir()):
            if f.suffix.lower() not in IMAGE_EXT:
                continue
            for pre in prefixes:
                rest = f.stem[len(pre):] if f.stem.startswith(pre) else ""
                if rest and re.search(rf"(?<![\w-]){re.escape(rest)}(?![\w-])", page):
                    dynamic[f"figures/{f.name}"] = _data_uri(f)
                    break
    if dynamic:
        page = page.replace("</head>", FIGURE_MAP_JS % json.dumps(dynamic) + "\n</head>", 1)
    return page


# ---------------------------------------------------------------- checks

def static_check(fragment: str, sid: str, ws: Path) -> list[str]:
    """Mechanical problems in a section file that are cheaper to catch without a browser."""
    problems = []
    if not re.search(rf'<section\b[^>]*\bid\s*=\s*["\']{re.escape(sid)}["\']', fragment):
        problems.append(f'The file must be one <section class="chapter" id="{sid}"> element.')
    lower = fragment.lower()
    for tag in ("<!doctype", "<html", "<head", "<body"):
        if tag in lower:
            problems.append(f"Remove {tag}...>: a section is a fragment, the page already has it.")
    if re.search(r"<script\b[^>]*\bsrc\s*=", fragment, re.I):
        problems.append("Don't load external scripts: Plotly, KaTeX and PV are already on the page.")
    if re.search(r"<link\b", fragment, re.I) or "@import" in fragment:
        problems.append("Don't load external stylesheets or fonts; put scoped CSS in a <style> inside the section.")
    ids = re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', re.sub(r"<script.*?</script>", "", fragment, flags=re.S | re.I))
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"Duplicate ids: {', '.join(duplicates[:10])}.")
    unprefixed = [i for i in ids if i != sid and not i.startswith(sid + "-")]
    if unprefixed:
        problems.append(f"Prefix every id with '{sid}-' (found: {', '.join(unprefixed[:10])}).")
    for src in re.findall(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)', fragment, re.I):
        if not src.startswith("data:") and not (src.startswith("figures/") and (ws / src).is_file()):
            problems.append(f"Image not found: {src} (use a file from figures/).")
    for img in re.findall(r"<img\b[^>]*>", fragment, re.I):
        if not re.search(r'\balt\s*=\s*["\'][^"\']+', img):
            problems.append(f"Image without alt text: {img[:80]}")
    text = re.sub(r"<(script|style)\b.*?</\1>", "", fragment, flags=re.S | re.I)
    if re.search(r"\$\$|\$[^$\n]*[\\^_][^$\n]*\$", text):
        problems.append(r"Math must use \( ... \) inline and \[ ... \] for display; $...$ is not rendered.")
    problems += _js_syntax(fragment)
    if len(fragment) > 70_000:
        problems.append(f"The section is {len(fragment) // 1000} KB; keep it under ~50 KB (use the PV building blocks, "
                        "cut repetition, move depth into details).")
    return problems


def concept_check(fragment: str, section: dict) -> list[str]:
    """Every concept the plan gives this section needs its visual, marked data-concept="<concept id>"."""
    problems = []
    for c in section.get("concepts") or []:
        if not re.search(rf'''\bdata-concept\s*=\s*["\']{re.escape(c["id"])}["\']''', fragment):
            problems.append(f'No visual for the concept "{c["id"]}" ({c.get("concept", "")[:80]}): build its '
                            f'{c.get("visual", "visual")} and mark its container data-concept="{c["id"]}".')
    return problems


def _js_syntax(fragment: str) -> list[str]:
    node = shutil.which("node")
    if not node:
        return []
    problems = []
    scripts = [s for attrs, s in re.findall(r"<script([^>]*)>(.*?)</script>", fragment, flags=re.S | re.I)
               if "src=" not in attrs and "json" not in attrs.lower()]
    with tempfile.TemporaryDirectory() as tmp:
        for i, code in enumerate(scripts, 1):
            path = Path(tmp) / f"script{i}.js"
            path.write_text(code)
            out = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=30)
            if out.returncode:
                detail = "\n".join(line for line in out.stderr.splitlines() if line.strip() and not line.startswith("    at "))
                detail = re.sub(r"\S*script(\d+)\.js", r"script\1.js", detail)
                problems.append(f"JavaScript syntax error in <script> #{i}:\n{detail[:600]}")
    return problems


# ---------------------------------------------------------------- text for readers and fact-checkers

class _PageText(HTMLParser):
    HEADINGS = {f"h{i}": i for i in range(1, 7)}
    BLOCKS = {"p", "div", "section", "header", "footer", "figure", "figcaption", "table", "tr", "details", "aside",
              "dl", "dt", "dd", "blockquote", "pre", "ul", "ol", "nav", "label"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.open = [], 0, []  # open: (tag, glossary definition or None)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "template", "title"):
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "section" and a.get("id"):
            self.out.append(f"\n\n=== SECTION {a['id']} ===\n")
        elif tag in self.HEADINGS:
            self.out.append("\n\n" + "#" * self.HEADINGS[tag] + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "summary":
            self.out.append("\n[Expandable] ")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in self.BLOCKS:
            self.out.append("\n")
        if tag == "img":
            self.out.append(f" [Image: {a.get('alt') or 'no alt text'}] ")
        elif tag == "input":
            kind = a.get("type", "text")
            label = a.get("aria-label") or a.get("title") or a.get("name") or ""
            self.out.append(f" [{'Slider' if kind == 'range' else kind.capitalize() + ' input'}{': ' + label if label else ''}] ")
        elif tag == "select":
            self.out.append(f" [Dropdown{': ' + a['aria-label'] if a.get('aria-label') else ''}] ")
        elif tag == "button":
            self.out.append(" [Button: ")
        elif tag == "canvas":
            self.out.append(f" [Interactive drawing{': ' + a['aria-label'] if a.get('aria-label') else ''}] ")
        elif tag == "svg":
            self.out.append(f" [Diagram{': ' + a['aria-label'] if a.get('aria-label') else ''}] ")
        elif a.get("role") == "img" and a.get("aria-label"):
            self.out.append(f" [Chart: {a['aria-label']}] ")
        if tag not in self.VOID:
            is_term = "term" in (a.get("class") or "").split()
            self.open.append((tag, a.get("data-def") if is_term else None))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template", "title"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "button":
            self.out.append("] ")
        if any(t == tag for t, _ in self.open):  # pop up to the matching tag (closes unclosed <p>, <li>, ...)
            while self.open:
                t, definition = self.open.pop()
                if definition:
                    self.out.append(f" (definition on hover: {definition})")
                if t == tag:
                    break
        if tag in self.BLOCKS or tag in self.HEADINGS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def main_words(fragment: str) -> int:
    """Words of a section's main text: what a reader meets without opening any <details>."""
    text = re.sub(r"<(details|script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return len(re.findall(r"\w+", text))


def page_text(page_html: str) -> str:
    parser = _PageText()
    parser.feed(re.sub(r"<!--chrome-->.*?<!--/chrome-->", "", page_html, flags=re.S))
    text = "".join(parser.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def section_texts(page_html: str) -> dict[str, str]:
    """Plain text of each section, keyed by section id."""
    chunks = re.split(r"\n*=== SECTION ([\w-]+) ===\n", page_text(page_html))
    return {chunks[i]: chunks[i + 1].strip() for i in range(1, len(chunks) - 1, 2)}
