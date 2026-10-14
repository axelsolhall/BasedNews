// The table under the map: one row per dot on the plane — where it sits on each axis (within the board's current
// range, with the centre, the dot and its uncertainty) and the shape of its path since the first month. World view:
// countries grouped by region, with region chips that also filter the plane and the map. Country view: its outlets,
// references first, extras hollow. It is a table of the plane, so its headers sit on the table, never on the map.
import { at } from "./common.js";

const SHORT = { "Nordics": "Nordics", "Western Europe": "Western", "Southern Europe": "Southern",
                "Central Europe & Baltics": "Central & Baltic", "Eastern Europe & Balkans": "Eastern & Balkans",
                "North America": "N. America" };

export class CountryTable {
  constructor(root, { plane, map, data, open, regionColors }) {
    Object.assign(this, { root, plane, map, data, open, regionColors });
    root.classList.add("table");
    root.innerHTML = `
      <div class="tb-head"><div class="kicker">The table <b data-title></b></div><small data-sub></small></div>
      <div class="chips" data-chips></div>
      <div class="tb-cols"><span data-name>Country</span><span>Economic<em>← left · right →</em></span>
        <span>Social<em>← liberal · conservative →</em></span><span>Path<em>since 2016</em></span></div>
      <div class="tb-rows" data-rows></div>`;
    this.rows = root.querySelector("[data-rows]");
    this.chips = root.querySelector("[data-chips]");
    this.region = null;
    this.t = data.months.length - 1;
    const all = document.createElement("button");
    all.textContent = "All"; all.dataset.r = "";
    this.chips.append(all);
    data.regions.forEach((r, k) => {
      const b = document.createElement("button");
      b.innerHTML = `<i style="background:${regionColors[k]}"></i>${SHORT[r] || r}`;
      b.dataset.r = k;
      this.chips.append(b);
    });
    this.chips.onclick = (e) => { const b = e.target.closest("button"); if (b) this.setRegion(b.dataset.r === "" ? null : +b.dataset.r); };
    this.onMode(null);
  }

  // show one region (index) or all; the plane and the map follow, colours stay with their countries
  setRegion(r) {
    this.region = r;
    const ids = r === null ? null : this.data.countries.filter((c) => c.region === r).map((c) => c.id);
    this.plane.setFilter(ids);
    this.map.setFilter(ids);
    for (const b of this.chips.children) b.classList.toggle("on", b.dataset.r === (r === null ? "" : String(r)));
    this.build();
  }

  onMode(c) {
    const world = !c;
    this.root.querySelector("[data-title]").textContent = world ? "Countries side by side" : `${c.name}, outlet by outlet`;
    const n = c ? this.data.national[c.id] : null;
    this.root.querySelector("[data-sub]").textContent = world
      ? "Each country is the average of its five largest news brands."
      : `${n.filter((o) => o.tier === "reference").length} largest brands (filled) set the centre; ${n.filter((o) => o.tier !== "reference").length} more outlets (hollow).`;
    this.root.querySelector("[data-name]").textContent = world ? "Country" : "Outlet";
    this.chips.hidden = !world;
    if (world) this.setRegion(this.region); else this.build();
  }

  build() {
    this.rows.innerHTML = "";
    const S = this.plane.series();
    const add = (d) => {
      const r = document.createElement("div");
      r.className = "tb-row"; r.dataset.id = d.id;
      const hollow = d.tier !== "reference", world = this.plane.mode === "world";
      const sw = world ? `<i class="sq" style="background:${d.color}"></i>`
                       : `<i style="border-color:${d.color};background:${hollow ? "transparent" : d.color}"></i>`;
      const extra = world ? `<small>${this.data.national[d.id].length}</small>` : hollow ? "<small>extra</small>" : "";
      r.innerHTML = `<div class="nm">${sw}<span>${d.label}</span>${extra}</div><div></div><div></div><div class="glyph"></div>`;
      r.onmouseenter = () => { this.plane.highlight(d.id); if (world) this.map.highlight(d.id); this.mark(d.id); };
      r.onmouseleave = () => { this.plane.highlight(null); this.map.highlight(null); this.mark(null); };
      r.onclick = () => { if (this.plane.mode === "world") this.open(d.id); };
      this.rows.append(r);
    };
    if (this.plane.mode === "world") {
      this.data.regions.forEach((name, k) => {
        const list = S.filter((d) => this.data.byCountry[d.id].region === k);
        if (!list.length) return;
        const h = document.createElement("div");
        h.className = "tb-group";
        h.innerHTML = `<i style="background:${this.regionColors[k]}"></i>${name}`;
        this.rows.append(h);
        list.forEach(add);
      });
    } else S.forEach(add);
    this.paint();
  }

  mark(ids) {
    const set = new Set(ids == null ? [] : Array.isArray(ids) ? ids : [ids]);
    for (const r of this.rows.querySelectorAll(".tb-row")) r.classList.toggle("hl", set.has(r.dataset.id));
  }

  update(t) {                                          // at most ~12 repaints a second while time plays
    this.t = t;
    if (this.pending) return;
    this.pending = setTimeout(() => { this.pending = null; this.paint(); }, 80);
  }

  paint() {
    if (!this.plane.goal) return;
    const [x0, x1, y0, y1] = this.plane.goal, t = this.t;
    for (const d of this.plane.series()) {
      const r = this.rows.querySelector(`[data-id="${CSS.escape(d.id)}"]`);
      if (!r) continue;
      const p = at(d.sm, t), hollow = d.tier !== "reference";
      r.children[1].innerHTML = strip(p[0], p[2], x0, x1, d.color, hollow);
      r.children[2].innerHTML = strip(p[1], p[3], y0, y1, d.color, hollow);
      r.children[3].innerHTML = glyph(d.sm, t, d.color);
    }
  }
}

// one axis: the board's current range, the centre, the dot and its uncertainty
function strip(v, sd, lo, hi, color, hollow) {
  const X = (u) => `${(100 * (u - lo)) / (hi - lo)}%`, c = (u) => Math.max(lo, Math.min(hi, u));
  return `<svg><line x1="0" x2="100%" y1="11" y2="11" stroke="#d9d1c3"/>` +
    (lo < 0 && hi > 0 ? `<line x1="${X(0)}" x2="${X(0)}" y1="4" y2="18" stroke="#9a9284" stroke-dasharray="2 2"/>` : "") +
    `<line x1="${X(c(v - sd))}" x2="${X(c(v + sd))}" y1="11" y2="11" stroke="${color}" stroke-width="5" stroke-opacity=".22" stroke-linecap="round"/>` +
    `<circle cx="${X(c(v))}" cy="11" r="4.5" fill="${hollow ? "#f6f2ea" : color}" stroke="${color}" stroke-width="2"/></svg>`;
}

// the shape of a dot's path from the first month to t, in its own box
function glyph(sm, t, color) {
  const pts = [];
  for (let u = 0; u <= t; u += 1) pts.push(at(sm, u));
  pts.push(at(sm, t));
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys), k = Math.min(52 / (Math.max(...xs) - x0 || 1), 26 / (Math.max(...ys) - y0 || 1));
  const P = (p) => [3 + (p[0] - x0) * k, 29 - (p[1] - y0) * k];
  const d = pts.map((p, i) => (i ? "L" : "M") + P(p).map((v) => v.toFixed(1)).join(",")).join("");
  const [ex, ey] = P(pts[pts.length - 1]);
  return `<svg viewBox="0 0 58 32"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" stroke-opacity=".75" stroke-linejoin="round"/>` +
    `<circle cx="${ex}" cy="${ey}" r="2.8" fill="${color}"/></svg>`;
}
