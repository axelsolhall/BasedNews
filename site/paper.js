// Shared page wiring: masthead, plane, map, table (or country index), slider, pill. start() returns the pieces so a
// page can add its own layer (a story's chapters) on top.
import { loadFrames, Plane, WorldMap, PillNav, HeaderMorph, connect } from "./common.js";
import { CountryTable } from "./table.js";

export const INK = ["#a3271f", "#1d4e89", "#2f7d4f", "#c47f17", "#6b3fa0", "#0f7c80", "#b6487f", "#5b5b5b", "#8a5a2b", "#3c6e9f"];
// one hue per region, fixed order (validated on the paper colour with the dataviz palette checker: all checks pass,
// one adjacent pair in the 6–8 colour-blind band, so every country also carries a label and a table row)
export const REGION = ["#2b62a8", "#b5741a", "#a3271f", "#2f7d4f", "#b6487f", "#6b3fa0"];
export const EXTRA = "#8a8273";                    // extra outlets: muted ink, drawn hollow
export const BAR = 62;

export async function start({ mapZoom = false, index = true, table = false, regions = false, mapLayout = "world",
                              onMode = () => {}, onTime = () => {}, stops = () => [] } = {}) {
  const morph = new HeaderMorph(document.getElementById("mast"), { bar: BAR });
  const data = regions ? await loadFrames(undefined, REGION, { regionColors: REGION, extraColor: EXTRA }) : await loadFrames(undefined, INK);
  // display smoothing of about two months, as the prototypes had at a frame a month (weekly frames: ≈ 8.7 frames)
  const plane = new Plane(document.getElementById("plane"), data, { textColor:"#1b1a17", axisColor:"#9a9284", frameColor:"#e2dacb",
    bg:"#f6f2ea", trailWidth: 2.5, smooth: 61 / data.meta.stepDays });
  const map = await new WorldMap(document.getElementById("map"), data, { land:"#e3dccf", border:"#f6f2ea", focus:true,
    zoom: mapZoom, layout: mapLayout }).init();

  const list = index && document.getElementById("index");
  if (list)
    for (const c of data.countries) {
      const b = document.createElement("button");
      b.dataset.id = c.id;
      b.innerHTML = `<i style="background:${c.color}"></i>${c.name}<small>${data.national[c.id].length}</small>`;
      list.append(b);
    }
  let tbl = null;
  const ui = connect({ plane, map, slider: document.getElementById("slider"), onMode: (c) => {
    document.getElementById("back").hidden = !c;
    document.getElementById("plane-title").textContent = c ? `${c.name}: its national media` : "Countries, relative to each other";
    if (list) for (const b of list.children) b.classList.toggle("on", b.dataset.id === c?.id);
    tbl?.onMode(c);
    onMode(c);
  }, onTime: (t) => { tbl?.update(t); onTime(t); } });
  if (table) {
    tbl = new CountryTable(document.getElementById("table"), { plane, map, data, open: ui.open, regionColors: REGION });
    const ph = plane.handlers.hover, mh = map.handlers.hover;      // hovering a dot or a country marks its row
    plane.on("hover", (d) => { ph(d); tbl.mark(d?.id ?? null); });
    map.on("hover", (c) => { mh(c); if (plane.mode === "world") tbl.mark(c?.id ?? null); });
  }
  if (list)
    for (const b of list.children) {
      b.onclick = () => ui.open(b.dataset.id);
      b.onmouseenter = () => { if (plane.mode === "world") plane.highlight(b.dataset.id); map.highlight(b.dataset.id); };
      b.onmouseleave = () => { plane.highlight(null); map.highlight(null); };
    }
  document.getElementById("back").onclick = () => ui.open(null);

  // the page never rests on a page border, nor with the masthead half folded
  const pill = new PillNav(document.getElementById("nav"), { color:"#1b1a17", hillColor:"#cfc6b6", onColor:"#f6f2ea", offColor:"#1b1a17",
    offset: BAR, stops: () => [[0, morph.span], ...stops()] });
  dateline(data);
  benchmarks(data, document.getElementById("bench"));
  morph.measure();                                     // the date's text arrives after the first measure: place it again
  return { data, plane, map, ui, morph, pill, slider: ui.slider, table: tbl };
}

// the masthead's date (the latest day judged) and the page's data note
function dateline(data) {
  const d = new Date(`${data.meta.updated}T00:00:00Z`), f = (o) => d.toLocaleDateString("en-GB", { ...o, timeZone: "UTC" });
  const line = document.querySelector('[data-part="dateline"]');
  if (line) line.innerHTML = `${f({ weekday: "long" })} ${f({ day: "numeric" })} ${f({ month: "long" })} ${f({ year: "numeric" })}<b>LIVE</b>`;
  const note = document.querySelector("[data-note]");
  if (note) note.textContent = `${data.months[0].slice(0, 4)} → ${f({ day: "numeric", month: "short", year: "numeric" })} · ` +
    `a point a week · a country or outlet appears once it has ${data.meta.threshold.toLocaleString("en")} comparisons`;
}

// agreement with outside ratings, on the outlets drawn (manifest.benchmarks: pipeline.history.threshold.sweep)
function benchmarks(data, root) {
  const b = data.meta.benchmarks;
  if (!root || !b) return;
  const at = b.rows.find((r) => r.min === data.meta.threshold), all = b.rows.find((r) => r.min === 0);
  if (!at) return;
  const n = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`;
  const tile = (r, name, what, who) => r.rho ? `<div class="bm"><b>${n(r.rho[0])}</b><span class="bm-name">${name}</span>
    <span class="bm-what">${what}</span><span class="bm-ci">95 % interval ${n(r.rho[1])} to ${n(r.rho[2])} · ${who}</span></div>` : "";
  root.innerHTML = `<h3>Checked against outside ratings</h3>
    <div class="bm-row">
      ${tile(at.allsides, "AllSides", "US and UK outlets' bias, rated by experts and readers", `${at.allsides.n} outlets`)}
      ${tile(at.dnr, "Reuters Institute Digital News Report", "each outlet's audience, left to right, compared within its country",
             `${at.dnr.n} outlets in ${at.dnr.countries} countries`)}
    </div>
    <p class="bm-note">Spearman rank correlation of each outlet's position (economic + social) on ${b.day} with the outside
      rating: +1 is the same order, 0 no relation. On the ${at.kept} outlets drawn; with all ${at.of}, it is
      ${n(all.allsides.rho[0])} and ${n(all.dnr.rho[0])}. BasedNews measures how the same event is framed, not which
      stories are chosen nor who reads them, so agreement has a ceiling below 1.</p>`;
}
