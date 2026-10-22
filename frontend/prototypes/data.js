// The data section: week by week since 2015-11, the pairs the judge can compare and the articles we hold — first a
// line per country (its region's colour, like the plane), then, opened, a line per outlet of that country (its five
// largest brands in the plane's colours, the others muted and dashed). Log scale: a week with none is a gap.
// Data: weekly.json (make_weekly_data.py, A77). Independent of the plane: it only shares the colours.
import { d3 } from "./common.js";
import { REGION, EXTRA } from "./paper.js";

const SHORT = { "Nordics": "Nordics", "Western Europe": "Western", "Southern Europe": "Southern",
                "Central Europe & Baltics": "Central & Baltic", "Eastern Europe & Balkans": "Eastern & Balkans",
                "North America": "N. America" };
const METRIC = { pairs: ["Pairs", "pairs that can be compared"], articles: ["Articles", "articles"] };
const MODE = { weekly: "Weekly", cum: "Cumulative" };              // cumulative: each week's line is the total so far
const fmt = d3.format(",");
const dayFmt = (d) => d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export async function weeklyChart(root, url = new URL("weekly.json", import.meta.url)) {
  let raw;
  try { raw = await (await fetch(url, { cache: "no-store" })).json(); } catch { root.innerHTML = `<p class="wk-note">No data yet.</p>`; return; }
  const first = new Date(`${raw.meta.first_week}T00:00:00Z`);
  const dates = d3.range(raw.meta.weeks).map((w) => new Date(+first + w * 7 * 864e5));
  const countries = raw.countries.filter((c) => c.outlets.length);
  const state = { metric: "pairs", mode: "weekly", country: null, region: null, hover: null };

  root.classList.add("wk");
  root.innerHTML = `
    <div class="wk-head">
      <div class="kicker">The record <b data-title></b></div>
      <div class="wk-tools"><button class="wk-back" data-back hidden>← All countries</button><div class="chips" data-metric></div><div class="chips" data-mode></div></div>
    </div>
    <div class="chips" data-regions></div>
    <div class="wk-plot"><svg data-svg></svg><div class="wk-tip" data-tip hidden></div></div>
    <div class="wk-legend" data-legend></div>
    <p class="wk-note" data-note></p>`;
  const $ = (s) => root.querySelector(s);
  const svg = d3.select($("[data-svg]")), tip = $("[data-tip]");

  for (const [k, [label]] of Object.entries(METRIC)) {
    const b = document.createElement("button");
    b.textContent = label; b.dataset.m = k;
    $("[data-metric]").append(b);
  }
  $("[data-metric]").onclick = (e) => { const b = e.target.closest("button"); if (b) { state.metric = b.dataset.m; draw(); } };
  for (const [k, label] of Object.entries(MODE)) {
    const b = document.createElement("button");
    b.textContent = label; b.dataset.mode = k;
    $("[data-mode]").append(b);
  }
  $("[data-mode]").onclick = (e) => { const b = e.target.closest("button"); if (b) { state.mode = b.dataset.mode; draw(); } };
  const shape = (v) => (state.mode === "cum" ? Array.from(d3.cumsum(v)) : v);
  const all = document.createElement("button");
  all.textContent = "All"; all.dataset.r = "";
  $("[data-regions]").append(all);
  raw.meta.regions.forEach((r, k) => {
    const b = document.createElement("button");
    b.innerHTML = `<i style="background:${REGION[k]}"></i>${SHORT[r] || r}`;
    b.dataset.r = k;
    $("[data-regions]").append(b);
  });
  $("[data-regions]").onclick = (e) => {
    const b = e.target.closest("button");
    if (b) { state.region = b.dataset.r === "" ? null : +b.dataset.r; draw(); }
  };
  $("[data-back]").onclick = () => { state.country = null; draw(); };

  // the lines on show: countries (by region) or one country's outlets, references first in the plane's colours
  const lines = () => {
    if (!state.country) return countries.filter((c) => state.region === null || c.region === state.region)
      .map((c) => ({ id: c.id, name: c.id, color: REGION[c.region], v: shape(c[state.metric]), week: c[state.metric], extra: false, country: c }));
    const c = countries.find((x) => x.id === state.country);
    let k = 0;
    const refs = c.outlets.filter((o) => o.tier === "reference"), rest = c.outlets.filter((o) => o.tier !== "reference");
    return [...refs, ...rest].map((o) => ({ id: o.id, name: o.name, extra: o.tier !== "reference",
      color: o.tier === "reference" ? REGION[k++ % REGION.length] : EXTRA, v: shape(o[state.metric]), week: o[state.metric] }));
  };

  function draw() {
    const L = lines();
    const W = Math.max(320, $(".wk-plot").clientWidth), H = Math.round(Math.max(240, Math.min(420, W * 0.5, innerHeight * 0.48)));   // fits a laptop screen
    const m = { t: 14, r: 14, b: 26, l: 52 };
    svg.attr("viewBox", `0 0 ${W} ${H}`).attr("width", W).attr("height", H).selectAll("*").remove();
    const max = d3.max(L, (l) => d3.max(l.v)) || 10;
    const x = d3.scaleUtc([dates[0], dates[dates.length - 1]], [m.l, W - m.r]);
    const y = d3.scaleLog([1, Math.max(10, max)], [H - m.b, m.t]).nice().clamp(true);

    // where pairs are not counted yet (the history's dry run so far, then live): a tinted band, not a zero
    if (state.metric === "pairs") {
      const [a, b] = raw.meta.history_pairs_counted || [];
      const live = raw.meta.live_from ? new Date(`${raw.meta.live_from}T00:00:00Z`) : dates[dates.length - 1];
      const gaps = a ? [[dates[0], new Date(`${a}T00:00:00Z`)], [new Date(`${b}T00:00:00Z`), live]] : [[dates[0], live]];
      for (const [g0, g1] of gaps) if (g1 > g0)
        svg.append("rect").attr("class", "wk-uncounted").attr("x", x(g0)).attr("width", Math.max(0, x(g1) - x(g0)))
          .attr("y", m.t).attr("height", H - m.b - m.t);
    }
    const ticks = y.ticks().filter((t) => Math.log10(t) % 1 === 0);
    svg.append("g").attr("class", "wk-grid").selectAll("line").data(ticks).join("line")
      .attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
    svg.append("g").attr("class", "wk-axis").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).tickValues(ticks).tickFormat(d3.format("~s")).tickSize(0).tickPadding(8)).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "wk-axis").attr("transform", `translate(0,${H - m.b})`)
      .call(d3.axisBottom(x).ticks(d3.utcYear.every(W < 560 ? 2 : 1)).tickSizeOuter(0));
    if (raw.meta.live_from) {
      const lx = x(new Date(`${raw.meta.live_from}T00:00:00Z`));
      svg.append("line").attr("class", "wk-live").attr("x1", lx).attr("x2", lx).attr("y1", m.t).attr("y2", H - m.b);
      svg.append("text").attr("class", "wk-live-label").attr("x", lx - 6).attr("y", m.t + 12).attr("text-anchor", "end").text("Live");
    }
    const line = d3.line().defined((v) => v > 0).x((_, i) => x(dates[i])).y((v) => y(v));
    const g = svg.append("g");
    const paths = g.selectAll("path").data(L, (l) => l.id).join("path").attr("class", (l) => `wk-line${l.extra ? " extra" : ""}`)
      .attr("stroke", (l) => l.color).attr("d", (l) => line(l.v));
    const cross = svg.append("line").attr("class", "wk-cross").attr("y1", m.t).attr("y2", H - m.b).style("opacity", 0);
    const dot = svg.append("circle").attr("class", "wk-dot").attr("r", 4.5).style("opacity", 0);

    const focus = (id) => {
      state.hover = id;
      paths.classed("dim", (l) => id !== null && l.id !== id).classed("on", (l) => l.id === id);
      if (id !== null) paths.filter((l) => l.id === id).raise();
      for (const b of $("[data-legend]").children) b.classList.toggle("on", b.dataset.id === id);
    };
    // hover: the week under the cursor, and the line nearest to it there (in log space)
    svg.on("pointermove", (e) => {
      const [px, py] = d3.pointer(e);
      const i = Math.max(0, Math.min(dates.length - 1, Math.round((x.invert(px) - first) / (7 * 864e5))));
      const near = d3.least(L.filter((l) => l.v[i] > 0), (l) => Math.abs(y(l.v[i]) - py));
      if (!near || px < m.l || px > W - m.r) return leave();
      focus(near.id);
      cross.attr("x1", x(dates[i])).attr("x2", x(dates[i])).style("opacity", 1);
      dot.attr("cx", x(dates[i])).attr("cy", y(near.v[i])).attr("fill", near.color).style("opacity", 1);
      tip.hidden = false;
      tip.innerHTML = `<b>${near.name}</b>Week of ${dayFmt(dates[i])}<span>${fmt(near.v[i])} ${METRIC[state.metric][1]}${state.mode === "cum" ? " so far" : ""}</span>`;
      const left = x(dates[i]) + 14, flip = left + tip.offsetWidth > W;
      tip.style.left = `${flip ? x(dates[i]) - 14 - tip.offsetWidth : left}px`;
      tip.style.top = `${Math.max(0, y(near.v[i]) - tip.offsetHeight / 2)}px`;
    });
    const leave = () => { focus(null); cross.style("opacity", 0); dot.style("opacity", 0); tip.hidden = true; };
    svg.on("pointerleave", leave);
    svg.on("click", () => { if (!state.country && state.hover) { state.country = state.hover; draw(); } });
    svg.classed("clickable", !state.country);

    // legend: every line, with its total; a country's button opens it
    const legend = $("[data-legend]");
    legend.innerHTML = "";
    const total = (l) => d3.sum(l.week);
    for (const l of [...L].sort((a, b) => (state.country ? 0 : total(b) - total(a)))) {
      const b = document.createElement("button");
      b.dataset.id = l.id;
      b.innerHTML = `<i class="${l.extra ? "extra" : ""}" style="--c:${l.color}"></i><span>${l.name}</span><small>${fmt(total(l))}</small>`;
      b.onmouseenter = () => focus(l.id);
      b.onmouseleave = () => focus(null);
      if (!state.country) b.onclick = () => { state.country = l.id; draw(); };
      legend.append(b);
    }
    legend.classList.toggle("outlets", !!state.country);

    const c = state.country && countries.find((x) => x.id === state.country);
    $("[data-title]").textContent = (c ? `${c.id}, outlet by outlet` : "Countries, week by week") + (state.mode === "cum" ? ", added up" : "");
    $("[data-back]").hidden = !c;
    $("[data-regions]").hidden = !!c;
    for (const b of $("[data-metric]").children) b.classList.toggle("on", b.dataset.m === state.metric);
    for (const b of $("[data-mode]").children) b.classList.toggle("on", b.dataset.mode === state.mode);
    for (const b of $("[data-regions]").children) b.classList.toggle("on", b.dataset.r === (state.region === null ? "" : String(state.region)));
    const counted = raw.meta.history_pairs_counted?.[0];
    $("[data-note]").textContent = (state.metric === "pairs"
      ? `A pair is two articles from different outlets about the same event — what the judge compares. A country's line counts
         every pair one of its outlets is in, once. ${counted ? "Shaded weeks are not counted yet." : "Pairs are being counted."}`
      : "Headline and description of every article we hold, by the week it was published; article text is never kept.")
      + (c ? " Coloured: the five largest brands, which set the centre; dashed: the other outlets." : " Pick a country to see its outlets.")
      + (raw.meta.live_from ? ` From ${dayFmt(new Date(`${raw.meta.live_from}T00:00:00Z`))}: the live pipeline.` : "");
  }
  draw();
  let rt;
  addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(draw, 120); });
}
