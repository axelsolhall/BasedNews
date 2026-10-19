// Shared page wiring: masthead, plane, map, table (or country index), slider, pill. start() returns the pieces so a
// page can add its own layer (a story's chapters) on top.
import { loadData, Plane, WorldMap, PillNav, HeaderMorph, connect } from "./common.js";
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
  // the pilot's positions (make_pilot_data.py) unless ?data= names another file (?data=demo-trends.json: the demo)
  const url = new URL(new URLSearchParams(location.search).get("data") || "pilot-trends.json", import.meta.url);
  const load = () => regions ? loadData(url, REGION, { regionColors: REGION, extraColor: EXTRA }) : loadData(url, INK);
  const data = await load();
  const plane = new Plane(document.getElementById("plane"), data, { textColor:"#1b1a17", axisColor:"#9a9284", frameColor:"#e2dacb", bg:"#f6f2ea", trailWidth: 2.5 });
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
  if (data.meta.pilot)                 // while the pilot runs: new days join without a reload
    setInterval(async () => {
      let fresh;
      try { fresh = await load(); } catch { return; }
      if (fresh.meta.updated === data.meta.updated) return;
      Object.assign(data, fresh);      // every part holds this one object
      plane.cache.clear(); plane.filtered = null;
      ui.slider.setMonths(data.months);
      dateline(data);
    }, 30_000);
  return { data, plane, map, ui, morph, pill, slider: ui.slider, table: tbl };
}

// the masthead's date and data label, and the page's data note, for the pilot's file
function dateline(data) {
  if (!data.meta.pilot) return;
  const d = new Date(`${data.meta.updated}T00:00:00Z`), f = (o) => d.toLocaleDateString("en-GB", { ...o, timeZone: "UTC" });
  const day = `${f({ weekday: "long" })} ${f({ day: "numeric" })} ${f({ month: "long" })} ${f({ year: "numeric" })}`;
  const line = document.querySelector('[data-part="dateline"]');
  if (line) line.innerHTML = `${day}<b>PILOT DATA</b>`;
  const note = document.querySelector(".demo");
  if (note) note.textContent = "Pilot · 2019 · positions as the pilot computed them, a point a week, updated while it runs";
}
