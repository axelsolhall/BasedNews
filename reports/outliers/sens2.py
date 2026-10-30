import csv, json, sqlite3, sys
import numpy as np
sys.path.insert(0, "/Users/axelsolhall/dev/BasedNews")
from pipeline.history import external_check as X
conn = sqlite3.connect(f"file:{X.DB}?mode=ro", uri=True, timeout=60)
arts = dict(conn.execute("SELECT outlet_id, count(*) FROM articles WHERE kind IS NULL GROUP BY 1"))
judged = X.judged(conn, "9999")
V = json.load(open("reports/outliers/variants.json"))
dnr = list(csv.DictReader((X.REF / "dnr2017_audience_leaning.csv").open()))
rated = json.loads(open("/Users/axelsolhall/dev/BasedNews/docs/experiments/e4_prereg.json").read())["allsides"]["ratings"]
def ours_of(var):
    return {o: {"econ": V[var]["econ"][o], "social": V[var]["social"][o], "sum": V[var]["econ"][o] + V[var]["social"][o]}
            for o in V[var]["econ"] if judged.get(o)}
for var in V:
    ours = ours_of(var)
    for cut in (0, 5000):
        keep = [r for r in dnr if r["outlet"] in ours and arts.get(r["outlet"], 0) >= cut]
        by_c = {}
        for r in keep: by_c.setdefault(r["country"], []).append(r)
        cells = []
        for ax in ("econ", "social", "sum"):
            groups = []
            for rs in by_c.values():
                if len(rs) < 2: continue
                a = np.array([float(r["cross_platform"]) for r in rs]); b = np.array([ours[r["outlet"]][ax] for r in rs])
                groups.append(list(zip(a - a.mean(), b - b.mean())))
            rho, lo, hi = X.boot_ci(groups); cells.append(f"{ax} {rho:+.2f} [{lo:+.2f},{hi:+.2f}]")
        rs = [(o, x) for o, x in rated.items() if o in ours and arts.get(o, 0) >= cut]
        rho, lo, hi = X.boot_ci([[(x, ours[o]["sum"])] for o, x in rs])
        print(f"{var:<9} min art {cut:>4}: DNR n {len(keep)} " + " · ".join(cells) + f" | AllSides(sum) n {len(rs)} {rho:+.2f} [{lo:+.2f},{hi:+.2f}]")
