"""H5: trajectories from the history's verdicts (A22, A67) — every outlet's and country's position per week since
2015-11, with its uncertainty, relative to the average reference outlet of that week (A26).

    PYTHONPATH=. uv run python -m pipeline.history.trajectories [--db data/history/history.sqlite] \\
        [--start 2015-11-01] [--out data/history/trajectories.json]

1. the static hierarchical fit over all history verdicts gives the article spread σ_a, the outlet spread τ and the
   judge's order bias β (A49; sparse at this size, A66);
2. the dynamic model (pipeline.rate.dynamic) puts every outlet on a random walk over weekly bins, its drift q chosen
   by the evidence;
3. outlets are read relative to the week's average reference outlet, countries as the reach-weighted mean of their
   reference outlets (A08), each with an sd from the week's joint covariance.
Only aggregates leave: no text, no titles (A10). Weeks before an outlet's first verdict or after its last are
dropped from its line (the walk there is only the prior).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np

from pipeline.judge import prompt as P
from pipeline.measure.run import A_FULL, A_SUMMARY
from pipeline.rate.dynamic import BIN_DAYS, DynamicBT
from pipeline.rate.hierarchical import HierarchicalBT

DB = Path("data/history/history.sqlite")
START = dt.date(2015, 11, 1)


def verdicts(conn, purpose: str = "history") -> list:
    return conn.execute("""SELECT c.first_outlet, c.second_outlet, c.first_article, c.second_article, c.text_kind, j.axis,
                                  j.first_wins, a.published pa, b.published pb FROM judgments j
                           JOIN judge_calls c ON c.call_id = j.call_id
                           JOIN articles a ON a.id = c.first_article JOIN articles b ON b.id = c.second_article
                           WHERE c.purpose = ? AND c.prompt_version = ? AND j.first_wins IS NOT NULL""",
                        (purpose, P.PROMPT_VERSION)).fetchall()


def bin_of(published: str, start: dt.date, bin_days: int = BIN_DAYS) -> int:
    return (dt.date.fromisoformat(published[:10]) - start).days // bin_days


def build(rows, outlets: list[str], start: dt.date, bin_days: int = BIN_DAYS, q: float | None = None) -> dict:
    """{axis: DynamicPosterior} from verdict rows (the static fit's σ_a, τ, β held)."""
    posts = {}
    for ax in P.AXES:
        rs = [r for r in rows if r["axis"] == ax]
        if not rs:
            continue
        disc = [A_FULL if r["text_kind"] == "full" else A_SUMMARY for r in rs]
        wins = [bool(r["first_wins"]) for r in rs]
        static = HierarchicalBT(outlets, ax).fit([(r["first_outlet"], r["second_outlet"], r["first_article"],
                                                    r["second_article"], d) for r, d in zip(rs, disc)], wins)
        comps = [(r["first_outlet"], r["second_outlet"], r["first_article"], r["second_article"], d,
                  bin_of(r["pa"], start, bin_days), bin_of(r["pb"], start, bin_days)) for r, d in zip(rs, disc)]
        n_bins = max(max(c[5], c[6]) for c in comps) + 1
        posts[ax] = DynamicBT(outlets, n_bins, ax, sigma_a=static.sigma_a, tau=static.tau,
                              beta=float(static.mean[len(outlets)])).fit(comps, wins, q=q)
    return posts


def to_json(posts: dict, groups: dict[str, dict[str, float]], start: dt.date, bin_days: int = BIN_DAYS) -> dict:
    """Outlet and country lines relative to the week's average reference outlet; [week, mean, sd] per point."""
    ref = {o for g in groups.values() for o in g}
    out = {"start": start.isoformat(), "bin_days": bin_days, "relative_to": "the average reference outlet of each week",
           "axes": {}}
    for ax, post in posts.items():
        present = [o for o in post.outlets if o in ref]
        rel, sd = post.relative({o: 1.0 for o in present})
        seen = post.seen
        lines = {o: [[b, round(float(rel[b, k]), 4), round(float(sd[b, k]), 4)] for b in range(post.n_bins)
                     if o in seen and seen[o][0] <= b <= seen[o][1]] for k, o in enumerate(post.outlets) if o in seen}
        w_ref = np.array([1.0 if o in ref else 0.0 for o in post.outlets]); w_ref /= w_ref.sum()
        countries = {}
        for c, g in groups.items():
            w = np.array([g.get(o, 0.0) for o in post.outlets])
            if w.sum() == 0 or not any(o in seen for o in g):
                continue
            v = w / w.sum() - w_ref                                  # country − reference average
            m = post.mean @ v
            s = np.sqrt(np.maximum(np.einsum("i,bij,j->b", v, post.cov, v), 0.0))
            lo = min(seen[o][0] for o in g if o in seen); hi = max(seen[o][1] for o in g if o in seen)
            countries[c] = [[b, round(float(m[b]), 4), round(float(s[b]), 4)] for b in range(lo, hi + 1)]
        out["axes"][ax] = {"q": post.q, "sigma_a": post.sigma_a, "tau": post.tau, "beta": round(post.beta, 4),
                           "comparisons": post.n_obs, "evidence": {str(k): round(v, 2) for k, v in post.evidence.items()},
                           "outlets": lines, "countries": countries}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--start", default=START.isoformat())
    ap.add_argument("--bin-days", type=int, default=BIN_DAYS)
    ap.add_argument("--out", default="data/history/trajectories.json")
    args = ap.parse_args()
    from pipeline import store
    from pipeline.sources.registry import load_outlets, reference_groups
    conn = store.connect(args.db)
    rows = verdicts(conn)
    if not rows:
        raise SystemExit("no history verdicts yet (pipeline.history.replay --judge local)")
    outlets = sorted({r["first_outlet"] for r in rows} | {r["second_outlet"] for r in rows})
    start = dt.date.fromisoformat(args.start)
    posts = build(rows, outlets, start, args.bin_days)
    groups = {c: {o: w for o, w in g.items() if o in outlets} for c, g in reference_groups(load_outlets()).items()}
    Path(args.out).write_text(json.dumps(to_json(posts, groups, start, args.bin_days)))
    for ax, p in posts.items():
        print(f"{ax}: {p.n_obs:,} verdicts, {p.n_bins} weeks, drift q = {p.q} per week (evidence over {len(p.evidence)}), "
              f"median sd {np.median(p.sd()):.2f}")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
