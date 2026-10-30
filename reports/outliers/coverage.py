import sqlite3, csv, sys
sys.path.insert(0, "/Users/axelsolhall/dev/BasedNews")
from pipeline.sources.registry import load_outlets
O = {o.id: o for o in load_outlets()}
v = sqlite3.connect("reports/outliers/v.sqlite")
h = sqlite3.connect("file:/Users/axelsolhall/dev/BasedNews/data/history/history.sqlite?mode=ro", uri=True, timeout=60)
arts = dict(h.execute("SELECT outlet_id, count(*) FROM articles WHERE kind IS NULL GROUP BY 1"))
last = h.execute("SELECT max(day) FROM history_positions").fetchone()[0]
pos = {}
for o, ax, m, sd in h.execute("SELECT outlet, axis, mean, sd FROM history_positions WHERE day=?", (last,)):
    pos.setdefault(o, {})[ax] = (m, sd)
rows = []
for oid, o in O.items():
    r = {"outlet": oid, "country": o.country, "tier": o.tier, "articles": arts.get(oid, 0)}
    calls = v.execute("SELECT count(*), count(DISTINCT CASE WHEN a=? THEN fa ELSE fb END) FROM v WHERE a=? OR b=?", (oid, oid, oid)).fetchone()
    r["calls"], r["articles_judged"] = calls
    for ax in ("econ", "social"):
        dec, win = v.execute(f"SELECT count({ax}), sum(CASE WHEN a=? THEN {ax} ELSE 1-{ax} END) FROM v WHERE (a=? OR b=?)", (oid, oid, oid)).fetchone()
        r[f"{ax}_decided"] = dec
        r[f"{ax}_decided_share"] = round(dec / calls[0], 3) if calls[0] else None
        r[f"{ax}_win"] = round(win / dec, 3) if dec else None
        m = pos.get(oid, {}).get(ax)
        r[f"{ax}_pos"], r[f"{ax}_sd"] = (round(m[0], 3), round(m[1], 3)) if m else (None, None)
    rows.append(r)
with open("reports/outliers/coverage.csv", "w") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(last, len(rows))
