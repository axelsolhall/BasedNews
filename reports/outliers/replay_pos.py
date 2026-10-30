"""Rebuild the independent-model positions in memory from the extracted verdicts, day by day (verdicts applied on
their judged-for day), under variants: as recorded / old-order calls dropped / β-corrected. Analysis only."""
import sqlite3, sys, datetime as dt, json
import numpy as np
sys.path.insert(0, "/Users/axelsolhall/dev/BasedNews")
from pipeline.history import positions as HP
from pipeline.history.replay import SIGMA_A_START
from pipeline.rate.model import BTConfig
from pipeline.measure.run import A_FULL, A_SUMMARY
from pipeline.sources.registry import load_outlets
from scipy.special import expit as sigmoid
v = sqlite3.connect("reports/outliers/v.sqlite")
h = sqlite3.connect("file:/Users/axelsolhall/dev/BasedNews/data/history/history.sqlite?mode=ro", uri=True, timeout=60)
at = dict(h.execute("SELECT call_id, at FROM judge_calls WHERE purpose='history'"))
rows = v.execute("SELECT call_id, day, a, b, kind, dir, econ, social FROM v ORDER BY day").fetchall()
outlets = sorted(o.id for o in load_outlets())
OLD = "2026-10-25"                                   # calls made before this had non-random order

def run(variant, beta=None):
    states = {ax: HP.start(outlets, ax, BTConfig().prior_sd, SIGMA_A_START) for ax in ("econ", "social")}
    oidx = {o: k for k, o in enumerate(outlets)}
    last = None
    by_day = {}
    for r in rows:
        by_day.setdefault(r[1], []).append(r)
    for d in sorted(by_day):
        day = dt.date.fromisoformat(d)
        for ax, st in states.items():
            HP.grow(st, 1 if last is None else (day - last).days)
        for cid, _, a, b, kind, direc, econ, social in by_day[d]:
            old = at[cid] < OLD
            if variant == "drop_old" and old:
                continue
            for ax, fw in (("econ", econ), ("social", social)):
                if fw is None: continue
                s = states[ax]; i, j = oidx[a], oidx[b]
                aa = A_FULL if kind == "full" else A_SUMMARY
                off = beta[(ax, direc)] if (beta and (variant == "beta_all" or old)) else 0.0
                vi, vj = s.cov[i, i], s.cov[j, j]
                p = float(sigmoid(aa * (s.mean[i] - s.mean[j]) + off)); w = p * (1 - p)
                den = 1.0 + aa * aa * w * (vi + vj); g = aa * (float(fw) - p) / den
                s.mean[i] += vi * g; s.mean[j] -= vj * g
                s.cov[i, i] = vi - vi * vi * aa * aa * w / den; s.cov[j, j] = vj - vj * vj * aa * aa * w / den
        last = day
    return {ax: {o: float(states[ax].mean[k]) for k, o in enumerate(outlets)} for ax in states}

# β per axis × framing, on the logit scale, from the random-order calls: logit(P(first wins)) / a_summary
beta = {}
for ax in ("econ", "social"):
    for direc in ("positive", "negative"):
        n, w = v.execute(f"SELECT count({ax}), sum({ax}) FROM v WHERE dir=? AND call_id IN (SELECT call_id FROM v)", (direc,)).fetchone()
        rand = [r for r in rows if r[5] == direc and at[r[0]] >= OLD and r[6 if ax == "econ" else 7] is not None]
        p = sum(r[6 if ax == "econ" else 7] for r in rand) / len(rand)
        beta[(ax, direc)] = float(np.log(p / (1 - p)) / A_SUMMARY)
print("beta", {f"{k[0]}/{k[1]}": round(x, 3) for k, x in beta.items()})
out = {"recorded": run("recorded"), "drop_old": run("drop_old"), "beta_old": run("beta_old", beta), "beta_all": run("beta_all", beta)}
json.dump(out, open("reports/outliers/variants.json", "w"))
cur = {(o, ax): m for o, ax, m in h.execute("SELECT outlet, axis, mean FROM history_positions WHERE day=(SELECT max(day) FROM history_positions)")}
x = np.array([out["recorded"][ax][o] for o, ax in cur]); y = np.array([cur[k] for k in cur])
print("reproduction vs stored latest positions: r", np.corrcoef(x, y)[0, 1].round(4), "max abs diff", np.abs(x - y).max().round(3))
for o in ("breitbart", "nypost", "foxnews", "eldiario", "rtbf-info-bel", "guardian", "bbc", "nyt"):
    print(o, "  ".join(f"{k}: {out[k]['econ'][o]:+.2f}/{out[k]['social'][o]:+.2f}" for k in out))
