import csv, sqlite3, collections, math
v = sqlite3.connect("reports/outliers/v.sqlite")
h = sqlite3.connect("file:/Users/axelsolhall/dev/BasedNews/data/history/history.sqlite?mode=ro", uri=True, timeout=60)
arts = dict(h.execute("SELECT outlet_id, count(*) FROM articles WHERE kind IS NULL GROUP BY 1"))
ref = {r["outlet"]: (r["country"], float(r["cross_platform"])) for r in csv.DictReader(open("/Users/axelsolhall/dev/BasedNews/docs/reference/dnr2017_audience_leaning.csv"))}
ids = list(ref); q = ",".join("?" * len(ids))
res = v.execute(f"SELECT a, b, count(econ), sum(econ), count(social), sum(social) FROM v WHERE a IN ({q}) AND b IN ({q}) GROUP BY 1, 2", ids + ids).fetchall()
pair = collections.defaultdict(lambda: [0, 0, 0, 0])          # (right, left) by DNR: [econ n, econ right-wins, social n, social right-wins]
for a, b, en, ew, sn, sw in res:
    ca, xa = ref[a]; cb, xb = ref[b]
    if ca != cb or xa == xb: continue
    hi, lo, flip = (a, b, False) if xa > xb else (b, a, True)
    p = pair[(hi, lo)]
    p[0] += en; p[1] += (en - (ew or 0)) if flip else (ew or 0)
    p[2] += sn; p[3] += (sn - (sw or 0)) if flip else (sw or 0)
def show(title, keys):
    for ax, (ni, wi) in (("econ", (0, 1)), ("social", (2, 3))):
        n = sum(pair[k][ni] for k in keys); w = sum(pair[k][wi] for k in keys)
        if n: print(f"  {title:<34} {ax:<6} DNR-right outlet judged more right {w/n:.3f} ± {1.96*math.sqrt(.25/n):.3f}  (n {n:,}, {len(keys)} pairs)")
thin = {o for o in ref if arts.get(o, 0) < 5000}
keys = list(pair)
print("Direct same-country verdicts vs DNR order (0.5 = no relation):")
show("all pairs", keys)
show("no thin outlet", [k for k in keys if not (set(k) & thin)])
gap = lambda k: ref[k[0]][1] - ref[k[1]][1]
for lo_, hi_ in ((0, .05), (.05, .10), (.10, .20), (.20, 1)):
    show(f"DNR gap {lo_:.2f}–{hi_:.2f}, no thin", [k for k in keys if lo_ < gap(k) <= hi_ and not (set(k) & thin)])
print("By country (no thin):")
for c in sorted({ref[k[0]][0] for k in keys}):
    show(c, [k for k in keys if ref[k[0]][0] == c and not (set(k) & thin)])
