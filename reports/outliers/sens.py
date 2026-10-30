import csv, json, sqlite3, sys
import numpy as np
sys.path.insert(0, "/Users/axelsolhall/dev/BasedNews")
from pipeline.history import external_check as X
conn = sqlite3.connect(f"file:{X.DB}?mode=ro", uri=True, timeout=60)
arts = dict(conn.execute("SELECT outlet_id, count(*) FROM articles WHERE kind IS NULL GROUP BY 1"))
start, last = X.latest(conn)
ours = X.averaged(conn, "history_positions", "outlet", start, last)
rows = [r for r in csv.DictReader((X.REF / "dnr2017_audience_leaning.csv").open()) if r["outlet"] in ours]
for cut in (0, 5000):
    keep = [r for r in rows if arts.get(r["outlet"], 0) >= cut]
    by_c = {}
    for r in keep: by_c.setdefault(r["country"], []).append(r)
    out = []
    for ax in ("econ", "social", "sum"):
        groups = []
        for rs in by_c.values():
            if len(rs) < 2: continue
            a = np.array([float(r["cross_platform"]) for r in rs]); b = np.array([ours[r["outlet"]][ax] for r in rs])
            groups.append(list(zip(a - a.mean(), b - b.mean())))
        rho, lo, hi = X.boot_ci(groups); out.append(f"{ax} {rho:+.2f} [{lo:+.2f},{hi:+.2f}]")
    print(f"DNR  min articles {cut}: n {sum(len(g) for g in by_c.values())} · " + " · ".join(out))
    # per-country, social
    print("     " + ", ".join(f"{c} {X.spearman([float(r['cross_platform']) for r in rs], [ours[r['outlet']]['social'] for r in rs]):+.2f}" for c, rs in sorted(by_c.items()) if len(rs) > 2))
rated = json.loads(open("/Users/axelsolhall/dev/BasedNews/docs/experiments/e4_prereg.json").read())["allsides"]["ratings"]
for cut in (0, 5000):
    rs = [(o, x) for o, x in rated.items() if o in ours and arts.get(o, 0) >= cut]
    out = []
    for ax in ("econ", "social", "sum"):
        rho, lo, hi = X.boot_ci([[(x, ours[o][ax])] for o, x in rs]); out.append(f"{ax} {rho:+.2f} [{lo:+.2f},{hi:+.2f}]")
    print(f"AllSides min articles {cut}: n {len(rs)} · " + " · ".join(out))
print("thin (<5000 articles) in DNR/AllSides sets:", sorted({r['outlet'] for r in rows if arts.get(r['outlet'],0) < 5000} | {o for o in rated if o in ours and arts.get(o,0) < 5000}))
