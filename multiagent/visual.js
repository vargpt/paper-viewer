// Building blocks for explorable visuals, added to PV on every page (see the style guide in prompts.py).
//
//   PV.loadImages({t: 'figures/a.jpg', ...}, width)  -> Promise of {t: {w, h, lin, disp}, ...}
//   PV.imageLab(el, opts)    canvases computed pixel by pixel from the paper's images, driven by controls
//   PV.stepper(el, opts)     step through a process (pills + Back/Next); calls onStep(i)
//   PV.compare(el, opts)     wipe between two canvases or images with a draggable handle
//   PV.svg(tag, attrs), PV.drag(el, onMove, svg)   inline-SVG diagrams with draggable points
//   PV.toLinear / PV.toDisplay   sRGB <-> linear light, per channel in 0..1;  PV.h(tag, attrs, ...children)
(() => {
  const PV = window.PV;
  const $ = (el) => (typeof el === 'string' ? document.getElementById(el) : el);
  const h = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
    }
    kids.flat().forEach((k) => k != null && e.append(k));
    return e;
  };

  const LUT = new Float32Array(256).map((_, i) => { const c = i / 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const toDisplay = (l) => { l = l < 0 ? 0 : l > 1 ? 1 : l; return l <= 0.0031308 ? 12.92 * l : 1.055 * l ** (1 / 2.4) - 0.055; };

  // Load images, scale them to one width, and keep linear-light RGB floats (3 per pixel).
  async function loadImages(srcs, width = 480) {
    const out = {};
    await Promise.all(Object.entries(srcs).map(([k, src]) => new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => { try {
        const w = width, hh = Math.round((img.naturalHeight * w) / img.naturalWidth);
        const c = h('canvas'); c.width = w; c.height = hh;
        const g = c.getContext('2d', { willReadFrequently: true }); g.drawImage(img, 0, 0, w, hh);
        const px = g.getImageData(0, 0, w, hh).data, lin = new Float32Array(w * hh * 3), disp = new Float32Array(w * hh * 3);
        for (let i = 0, j = 0; i < px.length; i += 4, j += 3) {
          for (let ch = 0; ch < 3; ch++) { lin[j + ch] = LUT[px[i + ch]]; disp[j + ch] = px[i + ch] / 255; }
        }
        out[k] = { w, h: hh, lin, disp };
        resolve();
      } catch (e) { reject(new Error(src.slice(0, 60) + ' (' + e.message + ')')); } };
      img.onerror = () => reject(new Error('could not load ' + src));
      img.src = src;
    })));
    return out;
  }

  // Controls: [{id, label, min, max, step, value, format}] -> range inputs; [{id, label, options: [[value, text]]}] -> toggle.
  function controls(host, specs, onChange) {
    const values = {};
    const row = h('div', { class: 'controls' });
    for (const s of specs) {
      if (s.options) {
        values[s.id] = s.value ?? s.options[0][0];
        const group = h('div', { class: 'pv-toggle', role: 'group', 'aria-label': s.label });
        const buttons = s.options.map(([v, text]) => h('button', {
          type: 'button', class: 'btn', 'aria-pressed': String(v === values[s.id]), text,
          onclick: () => { values[s.id] = v; buttons.forEach((b, i) => b.setAttribute('aria-pressed', String(s.options[i][0] === v))); onChange(values); },
        }));
        group.append(h('span', { class: 'pv-label', text: s.label }), ...buttons);
        row.append(group);
      } else {
        values[s.id] = s.value;
        const fmt = s.format || ((v) => String(v));
        const out = h('output', { text: fmt(s.value) });
        const input = h('input', { type: 'range', min: s.min, max: s.max, step: s.step || 'any', value: s.value, 'aria-label': s.label,
          oninput: (e) => { values[s.id] = Number(e.target.value); out.textContent = fmt(values[s.id]); onChange(values); } });
        row.append(h('label', {}, s.label + ' ', input, out));
      }
    }
    host.append(row);
    return values;
  }

  // imageLab: one or more panels, each a function of the source images and the control values.
  //   images: {name: src}; width; controls: [...]
  //   panels: [{title, pixel(src, p, i) -> [r, g, b] in linear light (or display units if display: true)}]
  //     src.name.lin / src.name.disp are the Float32Arrays; i is the index of the pixel's red channel
  //     inset: {x, y, size, zoom}: also draw a magnified, pixelated close-up of the square around (x, y)
//   probes: [{x, y, label}] (fractions of the image), drawn on every panel
  //   readout(panelIndex, probe, rgbDisplay, p) -> short text under the panel for that probe
  //   onUpdate(p) after every redraw (update captions etc.)
  // Returns {set(values), values, canvases, ready: Promise}.
  function imageLab(el, opts) {
    el = $(el);
    const wrap = h('div', { class: 'pv-lab' });
    const grid = h('div', { class: 'pv-panels', style: `--n:${opts.panels.length}` });
    const canvases = [], readouts = [];
    opts.panels.forEach((p) => {
      const c = h('canvas', { role: 'img', 'aria-label': p.alt || p.title });
      const r = h('div', { class: 'pv-readout' });
      canvases.push(c); readouts.push(r);
      grid.append(h('figure', { class: 'pv-panel' }, h('figcaption', { text: p.title }), h('div', { class: 'pv-canvas' }, c, ...(opts.probes || []).map((pr) =>
        h('span', { class: 'pv-probe', style: `left:${pr.x * 100}%;top:${pr.y * 100}%`, text: pr.label }))), r));
    });
    let values = {}, src = null, queued = false;
    const draw = () => {
      queued = false;
      if (!src) return;
      const first = src[Object.keys(src)[0]], { w, h: hh } = first;
      opts.panels.forEach((panel, k) => {
        const c = canvases[k]; c.width = w; c.height = hh;
        const g = c.getContext('2d'), out = g.createImageData(w, hh), d = out.data;
        for (let i = 0, j = 0; j < w * hh * 3; i += 4, j += 3) {
          const rgb = panel.pixel(src, values, j);
          for (let ch = 0; ch < 3; ch++) d[i + ch] = 255 * (panel.display ? Math.min(1, Math.max(0, rgb[ch])) : toDisplay(rgb[ch]));
          d[i + 3] = 255;
        }
        g.putImageData(out, 0, 0);
        const ins = typeof panel.inset === 'function' ? panel.inset(values) : panel.inset;
        if (ins) {
          const size = ins.size || 24, zoom = ins.zoom || 6, sx = Math.round(ins.x * w - size / 2), sy = Math.round(ins.y * hh - size / 2);
          const S = size * zoom, dx = w - S - 8, dy = 8;
          g.imageSmoothingEnabled = false;
          g.drawImage(c, sx, sy, size, size, dx, dy, S, S);
          g.strokeStyle = '#fff'; g.lineWidth = 2; g.strokeRect(dx, dy, S, S); g.strokeRect(sx, sy, size, size);
          g.beginPath(); g.moveTo(sx + size, sy); g.lineTo(dx, dy + S); g.stroke();
        }
        if (opts.readout && opts.probes) {
          readouts[k].replaceChildren(...opts.probes.map((pr) => {
            const x = Math.round(pr.x * (w - 1)), y = Math.round(pr.y * (hh - 1)), o = (y * w + x) * 4;
            return h('div', {}, h('b', { text: pr.label + ' ' }), opts.readout(k, pr, [d[o] / 255, d[o + 1] / 255, d[o + 2] / 255], values));
          }));
        }
      });
      if (opts.onUpdate) opts.onUpdate(values);
    };
    const redraw = () => { if (!queued) { queued = true; requestAnimationFrame(draw); } };
    if (opts.controls && opts.controls.length) values = controls(wrap, opts.controls, redraw);
    wrap.append(grid);
    el.append(wrap);
    const ready = loadImages(opts.images, opts.width || 480).then((s) => { src = s; draw(); })
      .catch((e) => { grid.replaceChildren(h('p', { class: 'pv-error', text: 'Image could not be loaded: ' + e.message })); console.error('PV.imageLab: ' + e.message); });
    return { set(v) { Object.assign(values, v); redraw(); }, get values() { return values; }, canvases, ready };
  }

  // stepper: [{label, title, text, mark}] -> clickable pills, Back / Next, and the current step's text.
  function stepper(el, opts) {
    el = $(el);
    let i = opts.start || 0;
    const pills = opts.steps.map((s, k) => h('button', { type: 'button', class: 'pv-pill', onclick: () => go(k) },
      h('span', { class: 'pv-n', text: String(s.n ?? k + 1) }), ' ' + s.label, s.mark ? h('span', { class: 'pv-mark', text: s.mark }) : null));
    const back = h('button', { type: 'button', class: 'btn', text: '← Back', onclick: () => go(i - 1) });
    const next = h('button', { type: 'button', class: 'btn', text: 'Next →', onclick: () => go(i + 1) });
    const count = h('span', { class: 'pv-count' });
    const title = h('h4', { class: 'pv-step-title' }), text = h('p', { class: 'pv-step-text' });
    el.append(h('div', { class: 'pv-pills' }, pills), h('div', { class: 'controls' }, back, next, count),
      h('div', { class: 'pv-step', 'aria-live': 'polite' }, title, text));
    function go(k) {
      i = Math.max(0, Math.min(opts.steps.length - 1, k));
      pills.forEach((p, n) => { p.classList.toggle('on', n === i); p.classList.toggle('done', n < i); p.setAttribute('aria-current', n === i ? 'step' : 'false'); });
      back.disabled = i === 0; next.disabled = i === opts.steps.length - 1;
      count.textContent = opts.steps[i].count ?? `Step ${i + 1} of ${opts.steps.length}`;
      title.textContent = opts.steps[i].title || opts.steps[i].label;
      text.innerHTML = opts.steps[i].text || '';
      if (opts.onStep) opts.onStep(i, opts.steps[i]);
    }
    go(i);
    return { go, get index() { return i; } };
  }

  // compare: wipe between two images or canvases. left/right: src string or canvas; labels: [left, right]
  function compare(el, opts) {
    el = $(el);
    const make = (x) => (typeof x === 'string' ? h('img', { src: x, alt: '' }) : x);
    const a = make(opts.left), b = make(opts.right);
    const top = h('div', { class: 'pv-wipe-top' }, b);
    const handle = h('div', { class: 'pv-wipe-handle', 'aria-hidden': 'true' });
    const box = h('div', { class: 'pv-wipe', role: 'img', 'aria-label': opts.alt || `${opts.labels[0]} versus ${opts.labels[1]}` },
      a, top, handle, h('span', { class: 'pv-wipe-label l', text: opts.labels[0] }), h('span', { class: 'pv-wipe-label r', text: opts.labels[1] }));
    const input = h('input', { type: 'range', min: 0, max: 100, step: 0.5, value: (opts.value ?? 0.5) * 100, 'aria-label': opts.sliderLabel || 'Wipe position',
      oninput: (e) => set(e.target.value / 100) });
    function set(f) {
      f = Math.max(0, Math.min(1, f));
      top.style.clipPath = `inset(0 0 0 ${f * 100}%)`; handle.style.left = f * 100 + '%'; input.value = f * 100;
      if (opts.onChange) opts.onChange(f);
    }
    const drag = (e) => { const r = box.getBoundingClientRect(); set((e.clientX - r.left) / r.width); };
    box.addEventListener('pointerdown', (e) => { box.setPointerCapture(e.pointerId); drag(e); });
    box.addEventListener('pointermove', (e) => { if (e.buttons) drag(e); });
    el.append(box, h('div', { class: 'controls' }, h('label', {}, (opts.sliderLabel || 'Wipe') + ' ', input)));
    set(opts.value ?? 0.5);
    return { set };
  }

  // svg: create an SVG element. drag: make el draggable; onMove(x, y) gets SVG user coordinates when svg is given
  // (otherwise pixels relative to el's parent). Works with mouse, touch and pen.
  const NS = 'http://www.w3.org/2000/svg';
  function svg(tag, attrs = {}) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) { if (k === 'text') e.textContent = v; else e.setAttribute(k, v); }
    return e;
  }
  function drag(el, onMove, root) {
    el = $(el);
    el.style.cursor = 'grab'; el.style.touchAction = 'none';
    const point = (e) => {
      if (root) { const m = root.getScreenCTM().inverse(), p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m); return [p.x, p.y]; }
      const r = el.parentElement.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top];
    };
    el.addEventListener('pointerdown', (e) => { el.setPointerCapture(e.pointerId); el.style.cursor = 'grabbing'; onMove(...point(e), e); e.preventDefault(); });
    el.addEventListener('pointermove', (e) => { if (el.hasPointerCapture(e.pointerId)) onMove(...point(e), e); });
    el.addEventListener('pointerup', () => { el.style.cursor = 'grab'; });
  }

  Object.assign(PV, { toLinear, toDisplay, loadImages, imageLab, stepper, compare, svg, drag, h });
})();
