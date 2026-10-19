// Shared mechanics for the design prototypes (a/b/c.html): demo data, the plane with trails, the world map, the time
// slider and the "uphill" pill navbar. Each prototype only differs in layout and look; the behaviour is the same, so
// the choice is about design. Data: demo-trends.json (SIMULATED movements, see make_demo_data.py).
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import * as topojson from "https://cdn.jsdelivr.net/npm/topojson-client@3/+esm";

export { d3 };

const PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#86bcb6"];

// ---------------------------------------------------------------- data
// regionColors: a country takes its region's colour (identity comes from labels and the table, not from one hue per
// country — more than eight categories cannot each get a distinguishable hue). extraColor: in a country view the
// reference outlets take the palette in order and the extra outlets share this muted ink (they are drawn hollow).
export async function loadData(url = "demo-trends.json", palette = PALETTE, { regionColors = null, extraColor = null } = {}) {
  const raw = await (await fetch(url, { cache: "no-store" })).json();       // no-store: the pilot's file grows while it runs
  const months = raw.months;
  const T = months.length;
  const regions = raw.regions || [];
  const countries = raw.countries.map((c, i) => ({ ...c, regionName: regions[c.region],
    color: regionColors && c.region !== undefined ? regionColors[c.region] : palette[i % palette.length] }));
  const byCountry = Object.fromEntries(countries.map((c) => [c.id, c]));
  const outlets = raw.outlets;
  const refs = outlets.filter((o) => o.tier === "reference");
  const mean = (arr) => arr.reduce((a, b) => a + b, 0) / arr.length;

  // world view: a country = mean of its reference outlets; origin = mean of all reference outlets (relative scale)
  const world = {};
  for (const c of countries) world[c.id] = [];
  for (let t = 0; t < T; t++) {
    const ox = mean(refs.map((o) => o.s[t][0])), oy = mean(refs.map((o) => o.s[t][1]));
    for (const c of countries) {
      if (c.s) {                    // the pilot's own country position and uncertainty (A74), only re-centred here
        world[c.id].push([c.s[t][0] - ox, c.s[t][1] - oy, c.s[t][2], c.s[t][3]]);
        continue;
      }
      const r = refs.filter((o) => o.country === c.id);
      const sx = Math.sqrt(mean(r.map((o) => o.s[t][2] ** 2)) / r.length);
      const sy = Math.sqrt(mean(r.map((o) => o.s[t][3] ** 2)) / r.length);
      world[c.id].push([mean(r.map((o) => o.s[t][0])) - ox, mean(r.map((o) => o.s[t][1])) - oy, sx, sy]);
    }
  }
  // country view: every outlet of the country, origin = mean of that country's reference outlets
  const national = {};
  for (const c of countries) {
    const all = outlets.filter((o) => o.country === c.id);
    const r = all.filter((o) => o.tier === "reference");
    let k = 0;
    const centre = (t) => c.s ? [c.s[t][0], c.s[t][1]] : [mean(r.map((q) => q.s[t][0])), mean(r.map((q) => q.s[t][1]))];
    national[c.id] = all.map((o, i) => ({
      id: o.id, label: o.name, tier: o.tier,
      color: extraColor ? (o.tier === "reference" ? palette[k++ % palette.length] : extraColor) : palette[i % palette.length],
      s: o.s.map((p, t) => { const [cx, cy] = centre(t); return [p[0] - cx, p[1] - cy, p[2], p[3]]; }),
    }));
  }
  return { months, regions, countries, byCountry, world, national, demo: raw.meta?.demo, meta: raw.meta || {} };
}

export function monthLabel(m) {                       // "2026-10" → "Oct 2026"; a day ("2019-01-06") → "6 Jan 2019"
  const [y, mo, d] = m.split("-").map(Number);
  const opts = { month: "short", year: "numeric", timeZone: "UTC", ...(d ? { day: "numeric" } : {}) };
  return new Date(Date.UTC(y, mo - 1, d || 1)).toLocaleString("en-GB", opts);
}

// ---------------------------------------------------------------- the plane
// Three layers: a back SVG (frame, grid, centre lines, axis names), a canvas (ovals and trails: thousands of short
// segments) and a front SVG (dots, labels, hover/click). The view is fitted to what is on the board at time t, per
// axis, and eases towards it, so the board follows the content instead of staying centred on the reference point.
export class Plane {
  constructor(el, data, opts = {}) {
    this.data = data;
    this.o = { ovals: true, labels: true, axisColor: "#999", textColor: "#333", frameColor: "#ddd", gridColor: null,
               dotRadius: 5, trailWidth: 2.4, font: "inherit", bg: "#fff",
               smooth: 2,          // display smoothing of the monthly series, in months (Gaussian sigma)
               fade: 1.5,          // how fast a trail fades with age (1 = linear)
               faint: 0.06,        // opacity of the oldest part of a trail
               maxStretch: 3,      // one axis may be zoomed at most this much more than the other
               ovalSize: 40,       // ovals larger than this (px radius) fade with their area
               ...opts };
    this.mode = "world";
    this.country = null;
    this.handlers = { select: () => {}, hover: () => {} };
    this.el = el;
    const layer = (node) => node.style("position", "absolute").style("inset", "0").style("width", "100%").style("height", "100%");
    this.back = layer(d3.select(el).append("svg")).style("pointer-events", "none").style("font-family", this.o.font);
    this.canvas = layer(d3.select(el).append("canvas")).style("pointer-events", "none").node();
    this.front = layer(d3.select(el).append("svg")).style("font-family", this.o.font);
    this.cache = new Map();
    new ResizeObserver(() => this.layout()).observe(el);
    this.layout();
    const tick = () => { this.animate(); requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }
  on(name, fn) { this.handlers[name] = fn; return this; }

  series() {
    const key = this.mode === "world" ? "world" : this.country;
    if (!this.cache.has(key)) {
      const raw = this.mode === "world"
        ? this.data.countries.map((c) => ({ id: c.id, label: c.name, color: c.color, tier: "reference", s: this.data.world[c.id] }))
        : this.data.national[this.country];
      this.cache.set(key, raw.map((d) => ({ ...d, sm: smooth(d.s, this.o.smooth) })));
    }
    const all = this.cache.get(key);
    if (this.mode !== "world" || !this.filter) return all;
    if (this.filtered?.key !== this.filterKey) this.filtered = { key: this.filterKey, list: all.filter((d) => this.filter.has(d.id)) };
    return this.filtered.list;
  }

  // show only some countries in the world view (e.g. one region); colours and the centre stay as they are
  setFilter(ids) {
    this.filter = ids ? new Set(ids) : null;
    this.filterKey = ids ? ids.join("|") : null;
    if (this.t !== undefined) this.render(this.t);
  }

  setMode(mode, country = null) {
    this.mode = mode;
    this.country = country;
    this.jump = true;                                    // a new board: no easing from the old view
    this.render(this.t ?? this.data.months.length - 1);
  }

  layout() {
    const r = this.el.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    this.w = Math.max(200, r.width);
    this.h = Math.max(200, r.height);
    this.back.attr("viewBox", `0 0 ${this.w} ${this.h}`);
    this.front.attr("viewBox", `0 0 ${this.w} ${this.h}`);
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(this.h * dpr);
    this.ctx = this.canvas.getContext("2d");
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.dirty = true;
  }

  // the box that holds every dot and its whole trail up to time t, padded, with a cap on how unequal the axes get
  fit(t) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const K = Math.floor(t);
    for (const d of this.series()) {
      for (let k = 0; k <= K; k++) {
        const [x, y] = d.sm[k];
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
      const [x, y] = at(d.sm, t);
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    let sx = Math.max(x1 - x0, 0.2), sy = Math.max(y1 - y0, 0.2);
    sx = Math.max(sx, sy / this.o.maxStretch);
    sy = Math.max(sy, sx / this.o.maxStretch);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, p = 1.12;
    return [cx - (sx * p) / 2, cx + (sx * p) / 2, cy - (sy * p) / 2, cy + (sy * p) / 2];
  }

  render(t) {
    this.t = t;
    this.goal = this.fit(t);
    if (this.jump || !this.view) { this.view = [...this.goal]; this.jump = false; }
    this.dirty = true;
  }

  animate() {
    if (!this.goal) return;
    let moving = false;
    for (let i = 0; i < 4; i++) {                        // the camera eases towards the fitted box
      const d = this.goal[i] - this.view[i];
      if (Math.abs(d) > 1e-4) { this.view[i] += d * 0.12; moving = true; } else this.view[i] = this.goal[i];
    }
    if (moving || this.dirty) this.draw();
    this.dirty = false;
  }

  draw() {
    const t = this.t, o = this.o, S = this.series(), T = this.data.months.length;
    const m = { l: 18, r: 78, t: 34, b: 38 };            // room for axis names and for labels right of the dots
    const fx0 = 6, fy0 = 22, fx1 = this.w - 6, fy1 = this.h - 24;
    const [vx0, vx1, vy0, vy1] = this.view;
    const X = (v) => m.l + ((v - vx0) / (vx1 - vx0)) * (this.w - m.l - m.r);
    const Y = (v) => m.t + ((vy1 - v) / (vy1 - vy0)) * (this.h - m.t - m.b);
    const Kx = (this.w - m.l - m.r) / (vx1 - vx0), Ky = (this.h - m.t - m.b) / (vy1 - vy0);
    const H = this.hlSet || new Set(), dim = (d) => (H.size && !H.has(d.id) ? 0.3 : 1);
    const crowd = S.length > 14, cw = crowd ? 0.6 : 1, ca = crowd ? 0.6 : 1;   // many dots: thinner, quieter marks

    // back: frame, a light grid that makes the board's own movement visible, centre lines, axis names
    const b = this.back;
    b.selectAll("*").remove();
    b.append("rect").attr("x", fx0).attr("y", fy0).attr("width", fx1 - fx0).attr("height", fy1 - fy0)
      .attr("fill", "none").attr("stroke", o.frameColor);
    const grid = o.gridColor || o.frameColor;
    for (const v of d3.ticks(vx0, vx1, 8)) if (X(v) > fx0 && X(v) < fx1)
      b.append("line").attr("x1", X(v)).attr("x2", X(v)).attr("y1", fy0).attr("y2", fy1).attr("stroke", grid).attr("stroke-opacity", 0.55);
    for (const v of d3.ticks(vy0, vy1, 6)) if (Y(v) > fy0 && Y(v) < fy1)
      b.append("line").attr("y1", Y(v)).attr("y2", Y(v)).attr("x1", fx0).attr("x2", fx1).attr("stroke", grid).attr("stroke-opacity", 0.55);
    if (X(0) > fx0 && X(0) < fx1)
      b.append("line").attr("x1", X(0)).attr("x2", X(0)).attr("y1", fy0).attr("y2", fy1).attr("stroke", o.axisColor).attr("stroke-dasharray", "3 4");
    if (Y(0) > fy0 && Y(0) < fy1)
      b.append("line").attr("y1", Y(0)).attr("y2", Y(0)).attr("x1", fx0).attr("x2", fx1).attr("stroke", o.axisColor).attr("stroke-dasharray", "3 4");
    if (X(0) > fx0 && X(0) < fx1 && Y(0) > fy0 && Y(0) < fy1)
      b.append("text").text("centre").attr("x", X(0) + 5).attr("y", Y(0) - 5).attr("fill", o.axisColor).attr("font-size", 10.5);
    const lab = [["↑ conservative", this.w / 2, 15, "middle"], ["↓ liberal", this.w / 2, this.h - 7, "middle"],
                 ["← economic left", fx0 + 2, this.h - 7, "start"], ["economic right →", fx1 - 2, this.h - 7, "end"]];
    for (const [s, x, y, a] of lab)
      b.append("text").text(s).attr("x", x).attr("y", y).attr("text-anchor", a).attr("fill", o.axisColor).attr("font-size", 11);

    // canvas: ovals, then the trails from the first month to t, older = fainter and thinner
    const c = this.ctx;
    c.clearRect(0, 0, this.w, this.h);
    c.save();
    c.beginPath(); c.rect(fx0, fy0, fx1 - fx0, fy1 - fy0); c.clip();
    if (o.ovals) for (const d of S) {
      const p = at(d.sm, t), rx = Math.max(3, p[2] * Kx), ry = Math.max(3, p[3] * Ky);
      const vague = Math.min(1, Math.sqrt(o.ovalSize ** 2 / (rx * ry)));   // the less sure, the fainter the oval
      c.beginPath();
      c.ellipse(X(p[0]), Y(p[1]), rx, ry, 0, 0, 2 * Math.PI);
      c.globalAlpha = (d.tier === "reference" ? 0.10 : 0.05) * vague * ca * dim(d);
      c.fillStyle = d.color; c.fill();
      c.globalAlpha = 0.35 * vague * ca * dim(d);
      c.setLineDash(d.tier === "reference" ? [] : [3, 3]);
      c.strokeStyle = d.color; c.lineWidth = 1; c.stroke();
    }
    c.setLineDash([]);
    c.lineCap = "round"; c.lineJoin = "round";
    const SUB = 4;                                       // points per month along the spline
    for (const d of S) {
      let prev = at(d.sm, 0);
      for (let q = 1; q <= Math.ceil(t * SUB); q++) {
        const u = Math.min(t, q / SUB), p = at(d.sm, u);
        const age = (t - u) / (T - 1);
        const on = H.has(d.id);
        c.globalAlpha = (o.faint + (0.9 - o.faint) * Math.pow(1 - age, o.fade)) * (on ? 1 : ca) * dim(d);
        c.lineWidth = o.trailWidth * (on ? 1.2 : cw) * (0.45 + 0.55 * (1 - age));
        c.strokeStyle = d.color;
        c.beginPath(); c.moveTo(X(prev[0]), Y(prev[1])); c.lineTo(X(p[0]), Y(p[1])); c.stroke();
        prev = p;
      }
    }
    c.restore();

    // front: dots and labels
    const g = this.front.selectAll("g.pt").data(S, (d) => d.id).join((enter) => {
      const e = enter.append("g").attr("class", "pt").style("cursor", "pointer");
      e.append("circle");
      e.append("text");
      e.on("click", (_, d) => this.handlers.select(d)).on("mouseenter", (_, d) => this.handlers.hover(d))
        .on("mouseleave", () => this.handlers.hover(null));
      return e;
    });
    const pos = new Map(S.map((d) => { const p = at(d.sm, t); return [d.id, [X(p[0]), Y(p[1])]]; }));
    const spots = this.placeLabels(S, pos, [fx0, fy0, fx1, fy1], crowd ? o.dotRadius - 1 : o.dotRadius);
    g.attr("transform", (d) => `translate(${pos.get(d.id)[0]},${pos.get(d.id)[1]})`);
    g.select("circle").attr("r", (d) => (H.has(d.id) ? o.dotRadius + 2.5 : crowd ? o.dotRadius - 1 : o.dotRadius))
      .attr("fill", (d) => (d.tier === "reference" ? d.color : o.bg))
      .attr("stroke", (d) => d.color).attr("stroke-width", (d) => (d.tier === "reference" ? 1.5 : 2));
    g.select("text").text((d) => (o.labels ? d.label : ""))
      .attr("x", (d) => spots.get(d.id)?.[0] ?? 0).attr("y", (d) => spots.get(d.id)?.[1] ?? 0)
      .attr("text-anchor", (d) => spots.get(d.id)?.[2] ?? "start").style("display", (d) => (spots.has(d.id) ? null : "none"))
      .attr("font-size", 11.5)
      .attr("fill", o.textColor).attr("font-weight", (d) => (H.has(d.id) ? 700 : 500))
      .attr("paint-order", "stroke").attr("stroke", o.bg).attr("stroke-width", 3);
    g.style("opacity", (d) => (H.size && !H.has(d.id) ? 0.35 : 1));
  }

  highlight(ids) {                                     // one id, a list of ids, or null
    const list = ids == null ? [] : Array.isArray(ids) ? ids : [ids];
    this.hl = list[0] ?? null;
    this.hlSet = new Set(list);
    this.dirty = true;
  }

  // labels never overlap each other or another dot: the highlighted dot first, then references, each trying four
  // corners (its previous corner first, so labels do not hop while time plays); a label that fits nowhere is hidden
  placeLabels(S, pos, [fx0, fy0, fx1, fy1], r) {
    const c = this.ctx, family = getComputedStyle(this.el).fontFamily;
    this.lastCorner ||= new Map();
    const boxes = S.map((d) => { const [x, y] = pos.get(d.id); return [x - r - 1, y - r - 1, x + r + 1, y + r + 1, d.id]; });
    const hit = (a, id) => boxes.some((b) => b[4] !== id && a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]);
    const corners = [[8, -8, "start"], [8, 15, "start"], [-8, -8, "end"], [-8, 15, "end"]];
    const H = this.hlSet || new Set();
    const order = [...S].sort((a, b) => H.has(b.id) - H.has(a.id) || (b.tier === "reference") - (a.tier === "reference"));
    const out = new Map();
    for (const d of order) {
      c.font = `${H.has(d.id) ? 700 : 500} 11.5px ${family}`;
      const w = c.measureText(d.label).width + 4, [x, y] = pos.get(d.id);
      const prev = this.lastCorner.get(d.id) ?? 0;
      const tries = [prev, ...[0, 1, 2, 3].filter((k) => k !== prev)];
      let pick = null;
      for (const k of tries) {
        const [dx, dy, anchor] = corners[k];
        const x0 = anchor === "start" ? x + dx - 2 : x + dx - w + 2;
        const box = [x0, y + dy - 11, x0 + w, y + dy + 3, d.id];
        if (box[0] < fx0 || box[2] > fx1 || box[1] < fy0 || box[3] > fy1) continue;
        if (!hit(box, d.id)) { pick = k; boxes.push(box); break; }
      }
      if (pick === null && H.has(d.id)) pick = prev;
      if (pick !== null) { this.lastCorner.set(d.id, pick); out.set(d.id, corners[pick]); }
    }
    return out;
  }
}

// Gaussian smoothing over months, renormalised at the ends (display only: the series stays as measured)
function smooth(s, sigma) {
  if (!sigma) return s;
  const R = Math.ceil(3 * sigma), T = s.length;
  const w = Array.from({ length: 2 * R + 1 }, (_, j) => Math.exp(-0.5 * ((j - R) / sigma) ** 2));
  return s.map((_, k) => {
    const acc = [0, 0, 0, 0];
    let W = 0;
    for (let j = -R; j <= R; j++) {
      const i = k + j;
      if (i < 0 || i >= T) continue;
      W += w[j + R];
      for (let n = 0; n < 4; n++) acc[n] += w[j + R] * s[i][n];
    }
    return acc.map((v) => v / W);
  });
}

// position at a fractional month: Catmull-Rom between months (the trail is a curve, not a polyline)
export function at(s, t) {
  const n = s.length - 1, k = Math.max(0, Math.min(n, Math.floor(t))), f = Math.max(0, Math.min(1, t - k));
  if (f === 0 || k === n) return s[k];
  const p0 = s[Math.max(0, k - 1)], p1 = s[k], p2 = s[k + 1], p3 = s[Math.min(n, k + 2)];
  const cr = (a, b, c, d) => 0.5 * (2 * b + (-a + c) * f + (2 * a - 5 * b + 4 * c - d) * f * f + (-a + 3 * b - 3 * c + d) * f * f * f);
  return [cr(p0[0], p1[0], p2[0], p3[0]), cr(p0[1], p1[1], p2[1], p3[1]), p1[2] + (p2[2] - p1[2]) * f, p1[3] + (p2[3] - p1[3]) * f];
}

// ---------------------------------------------------------------- the world map
const ATLAS = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";
const FOCUS = { type: "MultiPoint", coordinates: [[-126, 22], [36, 72], [-126, 72], [36, 22]] };   // the US + Europe
const BOXES = { "840": { type: "MultiPoint", coordinates: [[-125, 24], [-66, 50]] } };              // lower 48 for zooming

export class WorldMap {
  constructor(el, data, opts = {}) {
    this.el = el;
    this.data = data;
    this.o = { land: "#e6e6e6", border: "#fff", water: "transparent", focus: false, zoom: false, ...opts };
    this.handlers = { select: () => {}, hover: () => {} };
    this.byIso = Object.fromEntries(data.countries.map((c) => [c.iso, c]));
    this.svg = d3.select(el).append("svg").style("width", "100%").style("height", "100%");
    this.g = this.svg.append("g");
  }
  on(name, fn) { this.handlers[name] = fn; return this; }

  async init() {
    const topo = await (await fetch(ATLAS)).json();
    this.features = topojson.feature(topo, topo.objects.countries).features.filter((f) => f.id !== "010");   // no Antarctica
    new ResizeObserver(() => this.draw()).observe(this.el);
    this.draw();
    return this;
  }

  draw() {
    const r = this.el.getBoundingClientRect();
    const w = Math.max(200, r.width), h = Math.max(150, r.height);
    this.svg.attr("viewBox", `0 0 ${w} ${h}`);
    this.w = w; this.h = h;
    if (this.o.layout === "europe") return this.drawEurope(w, h);
    this.projection = d3.geoNaturalEarth1();
    if (this.o.focus) this.projection.fitExtent([[8, 8], [w - 8, h - 8]], FOCUS);
    else this.projection.fitExtent([[8, 8], [w - 8, h - 8]], { type: "FeatureCollection", features: this.features });
    this.path = d3.geoPath(this.projection);
    this.paths(this.g, this.features, this.path);
    this.restyle();
    if (this.o.zoom && this.selected) this.zoomTo(this.selected, 0);      // the size changed: zoom again
  }

  // Europe fills the box; North America sits in a framed inset in the bottom-left corner (the Atlantic is empty)
  drawEurope(w, h) {
    const west = (f) => d3.geoCentroid(f)[0] < -30;
    const iw = Math.min(w * 0.36, 230), ih = Math.min(h * 0.42, 170), pad = 8;
    // fit a fixed box (Iceland to Ukraine, Crete to North Cape), not the shapes: France's shape includes French Guiana
    const EUROPE = { type: "MultiPoint", coordinates: [[-24, 35], [40, 35], [-24, 70], [40, 70], [8, 71], [8, 34]] };
    const main = d3.geoAzimuthalEqualArea().rotate([-10, -53]).fitExtent([[pad + iw * 0.3, pad], [w - pad, h - pad]], EUROPE);
    const naFeatures = this.features.filter(west);
    const inset = d3.geoConicEqualArea().parallels([20, 60]).rotate([100, 0]).fitExtent([[pad + 6, h - ih - pad + 18], [pad + iw - 6, h - pad - 6]],
      { type: "FeatureCollection", features: naFeatures.filter((f) => this.byIso[f.id]) });
    this.path = d3.geoPath(main);
    if (!this.layers) {
      const defs = this.svg.insert("defs", ":first-child");
      defs.append("clipPath").attr("id", "clip-main").append("rect");
      defs.append("clipPath").attr("id", "clip-inset").append("rect");
      this.layers = { main: this.g.append("g").attr("clip-path", "url(#clip-main)"),
                      frame: this.g.append("rect"), label: this.g.append("text"),
                      inset: this.g.append("g").attr("clip-path", "url(#clip-inset)") };
    }
    this.svg.select("#clip-main rect").attr("x", 0).attr("y", 0).attr("width", w).attr("height", h);
    this.svg.select("#clip-inset rect").attr("x", pad).attr("y", h - ih - pad).attr("width", iw).attr("height", ih);
    this.paths(this.layers.main, this.features.filter((f) => !west(f)), d3.geoPath(main));
    this.layers.frame.attr("x", pad).attr("y", h - ih - pad).attr("width", iw).attr("height", ih)
      .attr("fill", this.o.insetBg || "#fbf8f2").attr("stroke", this.o.insetLine || "#cfc6b6");
    this.layers.label.text("North America").attr("x", pad + 8).attr("y", h - ih - pad + 14)
      .attr("font-size", 10.5).attr("letter-spacing", ".08em").attr("fill", this.o.insetText || "#8a8273");
    this.layers.inset.raise();
    this.paths(this.layers.inset, naFeatures, d3.geoPath(inset));
    this.restyle();
  }

  paths(g, features, path) {
    g.selectAll("path").data(features, (f) => f.id + (f.properties?.name || "")).join("path")
      .attr("d", path)
      .attr("fill", (f) => (this.byIso[f.id] ? this.byIso[f.id].color : this.o.land))
      .attr("stroke", this.o.border).attr("stroke-width", 0.6)
      .style("cursor", (f) => (this.byIso[f.id] ? "pointer" : "default"))
      .on("click", (_, f) => this.byIso[f.id] && this.handlers.select(this.byIso[f.id]))
      .on("mouseenter", (_, f) => this.byIso[f.id] && this.handlers.hover(this.byIso[f.id]))
      .on("mouseleave", () => this.handlers.hover(null));
  }

  restyle() {
    const H = this.hlSet || new Set();
    this.g.selectAll("path")
      .attr("fill-opacity", (f) => {
        const c = this.byIso[f.id];
        if (!c) return 1;
        if (this.selected) return c.id === this.selected ? 0.95 : 0.25;
        if (H.size) return H.has(c.id) ? 1 : 0.35;
        if (this.filter) return this.filter.has(c.id) ? 0.9 : 0.18;
        return 0.85;
      })
      .attr("stroke-width", (f) => (this.byIso[f.id] && (H.has(this.byIso[f.id].id) || this.byIso[f.id].id === this.selected) ? 1.6 : 0.6));
  }

  highlight(ids) {                                     // one id, a list of ids, or null
    this.hlSet = new Set(ids == null ? [] : Array.isArray(ids) ? ids : [ids]);
    this.hl = [...this.hlSet][0] ?? null;
    this.restyle();
  }
  setFilter(ids) { this.filter = ids ? new Set(ids) : null; this.restyle(); }

  select(id) {
    this.selected = id;
    this.restyle();
    if (this.o.zoom) this.zoomTo(id, 750);
  }

  zoomTo(id, ms) {
    let k = 1, tx = 0, ty = 0;
    if (id) {
      const c = this.data.byCountry[id];
      const f = BOXES[c.iso] || this.features.find((x) => x.id === c.iso);
      const [[a, b], [cc, dd]] = this.path.bounds(f);
      k = Math.min(8, 0.75 / Math.max((cc - a) / this.w, (dd - b) / this.h));
      tx = this.w / 2 - k * (a + cc) / 2;
      ty = this.h / 2 - k * (b + dd) / 2;
    }
    this.g.transition().duration(ms).attr("transform", `translate(${tx},${ty}) scale(${k})`)
      .selectAll("path").attr("stroke-width", 0.6 / k);
  }
}

// ---------------------------------------------------------------- time slider
// Continuous time: dragging and playing move through fractional months, so dots glide instead of hopping.
// `[data-ticks]` inside the root gets one label per year — per month when the span is under two years (the pilot).
// setMonths(months) takes a longer series (a run that is still going); a slider at the end stays at the end.
export function timeSlider(root, months, onChange, { speed = 14 } = {}) {
  const input = root.querySelector("input[type=range]");
  const label = root.querySelector("[data-month]");
  const play = root.querySelector("[data-play]");
  const ticks = root.querySelector("[data-ticks]");
  let n = months.length - 1;
  input.min = 0; input.max = n; input.step = "any"; input.value = n;
  const drawTicks = () => {
    if (!ticks) return;
    ticks.replaceChildren();
    const short = new Set(months.map((m) => m.slice(0, 7))).size < 24;
    months.forEach((m, k) => {
      const prev = months[k - 1];
      let label = null;
      if (!short) { if (prev?.slice(0, 4) !== m.slice(0, 4)) label = m.slice(0, 4); }       // the first point of a year
      else if (prev?.slice(0, 7) !== m.slice(0, 7))                                          // ... of a month
        label = !prev || m.slice(5, 7) === "01" ? m.slice(0, 4) : monthLabel(m.slice(0, 7)).split(" ")[0];
      if (!label) return;
      const s = document.createElement("span");
      s.textContent = label;
      s.style.left = `${(100 * k) / Math.max(1, n)}%`;
      ticks.append(s);
    });
  };
  drawTicks();
  let raf = null, cur = n;                     // the time lives here, not in the input: some browsers round its value
  const set = (t) => {
    t = cur = Math.max(0, Math.min(n, +t));
    input.value = t;
    input.style.setProperty("--fill", `${(100 * t) / n}%`);
    if (label) label.textContent = monthLabel(months[Math.round(t)]);
    onChange(t);
  };
  const stop = () => { cancelAnimationFrame(raf); raf = null; if (play) play.dataset.state = "paused"; };
  input.addEventListener("input", () => { stop(); set(input.value); });
  if (play)
    play.addEventListener("click", () => {
      if (raf) return stop();
      if (cur >= n) set(0);
      play.dataset.state = "playing";
      let last = performance.now();
      const frame = (now) => {
        const t = cur + ((now - last) / 1000) * speed;
        last = now;
        set(t);
        if (t >= n) return stop();
        raf = requestAnimationFrame(frame);
      };
      raf = requestAnimationFrame(frame);
    });
  set(n);
  const setMonths = (next) => {
    const atEnd = cur >= n;
    months = next;
    n = months.length - 1;
    input.max = n;
    drawTicks();
    set(atEnd ? n : cur);
  };
  return { set, stop, setMonths, at: () => cur, last: () => n };
}

// ---------------------------------------------------------------- the pill navbar
// Each nav item owns a page. Inside a page, scrolling leaves the pill alone. While a page boundary is on screen the
// pill climbs a "hill" between the two items: it lags on the way up (u^2.2) and drops quickly after the peak. When
// scrolling stops with a boundary on screen, the page itself settles — to the end of the previous page or the start
// of the next one, whichever side of the peak it is on — so you can never rest on a border. Colour fades towards
// `hillColor` near the top and is full on an item.
export class PillNav {
  constructor(nav, { orientation = "horizontal", color = "#1f3864", hillColor = "#9fb3d1", onColor = "#fff",
                     offColor = "#555", lift = 3, bar = nav.closest("header, .navwrap, [data-navbar]"),
                     offset = null, stops = () => [], idle = 170, snapMs = 480 } = {}) {
    this.nav = nav;
    this.o = { orientation, color, hillColor, onColor, offColor, lift, idle, snapMs, offset, stops };
    this.bar = orientation === "horizontal" ? bar : null;          // a sticky bar covers the top of every page
    this.items = [...nav.querySelectorAll("[data-target]")];
    this.sections = this.items.map((i) => document.getElementById(i.dataset.target));
    this.pill = nav.querySelector(".pill");
    this.x = 0; this.v = 0;
    this.lastScroll = performance.now();
    this.held = false;                                             // finger or mouse down: the user is holding the page
    this.items.forEach((it, k) =>
      it.addEventListener("click", (e) => {
        e.preventDefault();
        const a = it.dataset.anchor && document.getElementById(it.dataset.anchor);   // e.g. the map, not the intro
        this.glide(a ? Math.max(0, a.getBoundingClientRect().top + scrollY - this.offset()) : this.pageTop(k), 650);
      }));
    addEventListener("scroll", () => { this.lastScroll = performance.now(); }, { passive: true });
    for (const ev of ["wheel", "touchstart", "keydown"]) addEventListener(ev, () => this.cancel(), { passive: true });
    addEventListener("pointerdown", () => { this.held = true; this.cancel(); });
    addEventListener("pointerup", () => { this.held = false; this.lastScroll = performance.now(); });
    addEventListener("touchend", () => { this.held = false; this.lastScroll = performance.now(); });
    const setOffset = () => document.documentElement.style.setProperty("--navh", `${this.offset()}px`);
    addEventListener("resize", setOffset);
    setOffset();
    const q = new URLSearchParams(location.search);                // ?scroll=900&hold=1 (for screenshots)
    if (q.get("scroll")) setTimeout(() => scrollTo(0, +q.get("scroll")), 50);
    this.hold = q.get("hold") === "1";                             // freeze "still scrolling" to show the hill
    const tick = () => { this.step(); requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    if (q.has("selftest")) setTimeout(() => this.selftest(), 300);
  }

  // ?selftest: park the page at points on and off the borders, let it settle, and write what happened into
  // <body data-selftest> (read it with headless Chrome --dump-dom). A border must always end at the bar or below the screen.
  async selftest() {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const vh = innerHeight, H = this.offset(), out = [];
    const top = (k) => this.sections[k].getBoundingClientRect().top + scrollY;
    const cases = [];
    for (let k = 1; k < this.sections.length; k++) for (const u of [0.1, 0.45, 0.55, 0.9]) cases.push([k, u]);
    for (const [k, u] of cases) {
      scrollTo(0, top(k) - vh + u * (vh - H));
      this.lastScroll = performance.now();
      await wait(1400);
      const p = this.sections[k].getBoundingClientRect().top, want = u >= 0.5 ? H : vh;
      out.push({ border: k, u, end: Math.round(p), want: Math.round(want), pill: +this.x.toFixed(3), wantPill: u >= 0.5 ? k : k - 1,
                 ok: Math.abs(p - want) < 2 && Math.abs(this.x - (u >= 0.5 ? k : k - 1)) < 0.01 });
    }
    for (const [a, b] of this.o.stops()) for (const u of [0.3, 0.7]) {
      scrollTo(0, a + u * (b - a)); this.lastScroll = performance.now();
      await wait(1400);
      const want = u < 0.5 ? a : b;
      out.push({ stop: [Math.round(a), Math.round(b)], u, end: Math.round(scrollY), want: Math.round(want), ok: Math.abs(scrollY - want) < 2 });
    }
    const free = Math.max(0, ...this.o.stops().map(([, b]) => b));  // a spot inside page 0, clear of any stop: nothing may move
    const inside = (free + top(1) - vh) / 2;
    if (top(1) - vh - free > 20) {
      scrollTo(0, inside); this.lastScroll = performance.now();
      await wait(1400);
      out.push({ inside: true, start: Math.round(inside), end: Math.round(scrollY), pill: +this.x.toFixed(3), ok: Math.abs(scrollY - inside) < 2 && this.x === 0 });
    }
    document.body.dataset.selftest = JSON.stringify({ pass: out.every((r) => r.ok), vh, bar: H, cases: out });
  }

  offset() { return this.o.offset ?? (this.bar ? this.bar.offsetHeight : 0); }
  pageTop(k) { return k === 0 ? 0 : this.sections[k].getBoundingClientRect().top + scrollY - this.offset(); }

  // where the page is: an item index, or i + hill(u) while the boundary between page i and i+1 is on screen
  where() {
    const vh = innerHeight, H = this.offset();
    for (let k = 1; k < this.sections.length; k++) {
      const p = this.sections[k].getBoundingClientRect().top;
      if (p > H + 1 && p < vh - 1) {
        const u = (vh - p) / (vh - H);
        return { pos: k - 1 + hill(u), u, back: scrollY + p - vh, fwd: scrollY + p - H };
      }
    }
    let i = 0;
    for (let k = 1; k < this.sections.length; k++) if (this.sections[k].getBoundingClientRect().top <= H + 1) i = k;
    return { pos: i };
  }

  glide(y, ms) {
    this.cancel();
    const y0 = scrollY, t0 = performance.now(), ease = (u) => 1 - Math.pow(1 - u, 3);
    const frame = (now) => {
      const u = Math.min(1, (now - t0) / ms);
      scrollTo(0, y0 + (y - y0) * ease(u));
      this.anim = u < 1 ? requestAnimationFrame(frame) : null;
    };
    this.anim = requestAnimationFrame(frame);
  }
  cancel() { if (this.anim) cancelAnimationFrame(this.anim); this.anim = null; }

  step() {
    const w = this.where();
    const idle = !this.hold && !this.held && !this.anim && performance.now() - this.lastScroll > this.o.idle;
    if (idle && w.u !== undefined) {                               // never rest on a border
      const y = Math.max(0, Math.min(document.documentElement.scrollHeight - innerHeight, w.u >= 0.5 ? w.fwd : w.back));
      if (Math.abs(y - scrollY) > 1) this.glide(y, this.o.snapMs);
    } else if (idle) {                                             // other places the page may not rest (a half-folded masthead)
      for (const [a, b] of this.o.stops())
        if (scrollY > a + 1 && scrollY < b - 1) { this.glide(scrollY < (a + b) / 2 ? a : b, this.o.snapMs); break; }
    }
    const target = idle ? Math.round(w.pos) : w.pos;
    this.v = (this.v + (target - this.x) * 0.16) * 0.72;          // spring
    this.x += this.v;
    if (Math.abs(target - this.x) < 0.0005 && Math.abs(this.v) < 0.0005) this.x = target;
    this.paint();
  }

  paint() {
    const n = this.items.length, x = Math.max(0, Math.min(n - 1, this.x));
    const i = Math.min(n - 2, Math.floor(x)), f = n > 1 ? x - i : 0;
    const a = this.items[i].getBoundingClientRect(), b = this.items[Math.min(n - 1, i + 1)].getBoundingClientRect();
    const box = this.nav.getBoundingClientRect();
    const lerp = (p, q) => p + (q - p) * f;
    const frac = x - Math.floor(x);
    const up = frac < 1e-4 ? 0 : 1 - Math.abs(2 * frac - 1);                // 0 on an item, 1 at the peak
    const s = this.pill.style;
    if (this.o.orientation === "horizontal") {
      s.left = `${lerp(a.left, b.left) - box.left}px`; s.width = `${lerp(a.width, b.width)}px`;
      s.top = `${a.top - box.top}px`; s.height = `${a.height}px`;
      s.transform = `translateY(${-this.o.lift * up}px) scale(${1 - 0.05 * up})`;
    } else {
      s.top = `${lerp(a.top, b.top) - box.top}px`; s.height = `${lerp(a.height, b.height)}px`;
      s.left = `${a.left - box.left}px`; s.width = `${a.width}px`;
      s.transform = `translateX(${this.o.lift * up}px) scale(${1 - 0.05 * up})`;
    }
    s.background = mix(this.o.color, this.o.hillColor, up);
    s.boxShadow = `0 ${2 + 6 * up}px ${8 + 10 * up}px rgba(0,0,0,${0.10 + 0.12 * up})`;
    const on = Math.round(x);
    this.items.forEach((it, k) => { it.style.color = k === on && up < 0.5 ? this.o.onColor : this.o.offColor; });
  }
}

const hill = (u) => (u < 0.5 ? 0.5 * Math.pow(u / 0.5, 2.2) : 0.5 + 0.5 * (1 - Math.pow(1 - (u - 0.5) / 0.5, 3)));

// ---------------------------------------------------------------- the masthead that the navbar slides over
// At the top: a broadsheet masthead (big centred name, tagline, double rule, nav). Scrolling, nothing jumps: the big
// name stays where it is and fades, the nav rides up with the page and covers it, and the bar appears around the nav —
// a small name fades in on the left, the date on the right, a rule underneath. The morph follows the scroll 1:1 over
// `span` px (the page never rests half-way: PillNav `stops`). Every [data-part] is placed absolutely by mastheadLayout.
export class HeaderMorph {
  constructor(header, { bar = 62, ink = "#1b1a17", paper = "#f6f2ea", track = "#ebe4d6" } = {}) {
    this.h = header;
    this.o = { bar, ink, paper, track };
    this.parts = Object.fromEntries([...header.querySelectorAll("[data-part]")].map((el) => [el.dataset.part, el]));
    this.measure();
    addEventListener("resize", () => this.measure());
    document.fonts?.ready.then(() => this.measure());
    const tick = () => { this.step(); requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }

  measure() {
    const S = {};
    for (const [k, el] of Object.entries(this.parts)) S[k] = [el.offsetWidth, el.offsetHeight];
    this.g = mastheadLayout(innerWidth, S, this.o);
    this.span = this.g.from.height - this.o.bar;
    document.documentElement.style.setProperty("--mast", `${this.g.from.height}px`);
    this.drawn = null;
  }

  step() {
    const clamp = (v) => Math.max(0, Math.min(1, v));
    const e = clamp(scrollY / this.span);
    if (e === this.drawn) return;
    this.drawn = e;
    const { from, to, fade } = this.g, L = (a, b) => a + (b - a) * e;
    this.h.style.height = `${L(from.height, to.height)}px`;
    for (const [k, el] of Object.entries(this.parts)) {
      const a = from[k], b = to[k];
      if (!a) continue;
      const [f0, f1] = fade[k] || [0, 1], f = clamp((e - f0) / (f1 - f0));
      el.style.opacity = a.o + (b.o - a.o) * f;
      el.style.visibility = +el.style.opacity < 0.01 ? "hidden" : "visible";
      el.style.transform = `translate(${L(a.x, b.x)}px, ${L(a.y, b.y)}px) scale(${a.s ?? 1})`;
      if (a.w !== undefined) el.style.width = `${a.w}px`;
    }
    const nav = this.parts.nav;
    nav.style.borderColor = mix(this.o.ink, this.o.paper, e);
    nav.style.background = mix(this.o.paper, this.o.track, e);
  }
}

// where every part sits at the top (from) and in the bar (to); only the nav (and a few px of the fades) moves
function mastheadLayout(vw, S, { bar }) {
  const mobile = vw < 760, side = mobile ? 16 : 40, s = mobile ? 0.7 : 1;
  const cx = (k, k2 = 1) => (vw - S[k][0] * k2) / 2;
  const logo = { x: cx("logo", s), y: 28, s, o: 1 };
  let y = 28 + S.logo[1] * s + 8;
  const tagline = { x: cx("tagline"), y, o: 1 };
  y += S.tagline[1] + 16;
  const rule = { x: side, y, w: vw - 2 * side, o: 1 };
  y += S.rule[1] + 14;
  const nav = { x: cx("nav"), y, o: 1 };
  const height = y + S.nav[1] + 16;
  const brand = { x: 28, y: (bar - S.brand[1]) / 2, o: mobile ? 0 : 1 };
  const dateline = { x: vw - 28 - S.dateline[0], y: (bar - S.dateline[1]) / 2, o: mobile ? 0 : 1 };
  const barline = { x: 0, y: bar - 1, w: vw, o: 1 };
  return {
    from: { height, logo, tagline, rule, nav, brand: { ...brand, y: brand.y + 6, o: 0 },
            dateline: { ...dateline, y: dateline.y + 6, o: 0 }, barline: { ...barline, o: 0 } },
    to: { height: bar, logo: { ...logo, o: 0 }, tagline: { ...tagline, o: 0 }, rule: { ...rule, o: 0 },
          nav: { ...nav, y: (bar - S.nav[1]) / 2 }, brand, dateline, barline },
    fade: { rule: [0, 0.35], tagline: [0.1, 0.45], logo: [0.45, 0.9], brand: [0.6, 1], dateline: [0.6, 1], barline: [0.85, 1] },
  };
}

function mix(c1, c2, t) {
  const p = (c) => [1, 3, 5].map((k) => parseInt(c.slice(k, k + 2), 16));
  const [a, b] = [p(c1), p(c2)];
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * t)).join(",")})`;
}

// ---------------------------------------------------------------- wiring: plane ⇄ map ⇄ slider ⇄ back button
export function connect({ plane, map, slider, onMode, onTime }) {
  let t = plane.data.months.length - 1;
  const open = (countryId) => {
    plane.setMode(countryId ? "country" : "world", countryId);
    map.select(countryId);
    plane.highlight(null);
    onMode?.(countryId ? plane.data.byCountry[countryId] : null);
  };
  plane.on("select", (d) => { if (plane.mode === "world") open(d.id); });
  plane.on("hover", (d) => { if (plane.mode === "world") map.highlight(d?.id ?? null); plane.highlight(d?.id ?? null); });
  map.on("select", (c) => open(c.id));
  map.on("hover", (c) => { if (plane.mode === "world") plane.highlight(c?.id ?? null); map.highlight(c?.id ?? null); });
  const s = timeSlider(slider, plane.data.months, (k) => { t = k; plane.render(k); onTime?.(k); });
  const q = new URLSearchParams(location.search);                    // ?country=Sweden&t=60 (for screenshots)
  if (q.get("t")) s.set(+q.get("t"));
  if (q.get("country")) open(q.get("country"));
  return { open, slider: s };
}
