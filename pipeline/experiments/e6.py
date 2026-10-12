"""E6 analysis: the pre-registered questions of docs/experiments/e6_prereg.json.

    uv run python -m pipeline.experiments.e6

Q1 planner article spread (E6 vs E4's flat planner), Q2 v4 out of sample vs AllSides, Q3 contrasts, Q4 split halves —
all with the hierarchical model (A49). Writes reports/e6_results.md. Not a claim (pipeline phase).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.stats import norm, spearmanr

from pipeline import store
from pipeline.aggregate.plane import _contrast_stats
from pipeline.experiments.e4 import A, PREREG as E4_PREREG
from pipeline.rate.hierarchical import HierarchicalBT
from pipeline.sources.registry import load_outlets

PREREG = Path("docs/experiments/e6_prereg.json")
US_UK = {"United States", "United Kingdom"}
E4_FLAT_PLANNER_SINCE = "2026-10-11T14:08:14Z"     # E4 all-countries part (flat planner, prompt v3)


def calls(conn, prompt, since, until=None):
    q = "SELECT * FROM judge_calls WHERE purpose = 'measure' AND prompt_version = ? AND at >= ?"
    args = [prompt, since]
    if until:
        q += " AND at < ?"; args.append(until)
    return conn.execute(q + " ORDER BY at, rowid", args).fetchall()


def spread(cs):
    use = Counter(a for c in cs for a in (c["first_article"], c["second_article"]))
    seen, reuse = set(), 0
    for c in cs:
        reuse += c["first_article"] in seen or c["second_article"] in seen
        seen.update((c["first_article"], c["second_article"]))
    v = np.array(list(use.values())) if use else np.array([0])
    return {"calls": len(cs), "articles": len(use), "mean_uses": float(v.mean()), "max_uses": int(v.max()),
            "reuse_share": reuse / max(len(cs), 1), "articles_per_100_calls": 100 * len(use) / max(len(cs), 1)}


def social_rows(conn, call_ids):
    if not call_ids:
        return []
    ids = list(call_ids)
    out = []
    for k in range(0, len(ids), 900):
        chunk = ids[k:k + 900]
        out += conn.execute("""SELECT c.call_id, c.first_outlet f, c.second_outlet s, c.first_article fa, c.second_article sa,
                                      c.text_kind k, j.first_wins w FROM judge_calls c JOIN judgments j USING(call_id)
                               WHERE j.axis = 'social' AND j.first_wins IS NOT NULL AND c.call_id IN (%s)""" % ",".join("?" * len(chunk)),
                            chunk).fetchall()
    return out


def hfit(rows):
    outs = sorted({r["f"] for r in rows} | {r["s"] for r in rows})
    h = HierarchicalBT(outs, "social").fit([(r["f"], r["s"], r["fa"], r["sa"], A[r["k"]]) for r in rows], [bool(r["w"]) for r in rows])
    return outs, h, h.outlet_posterior()


def main():
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    rated_all = json.loads(E4_PREREG.read_text())["allsides"]["ratings"]
    contrasts = json.loads(E4_PREREG.read_text())["contrasts"]
    start = json.loads(Path("reports/e6_run.json").read_text())["e6_start"]
    conn = store.connect()
    reg = {o.id: o for o in load_outlets()}
    e6 = calls(conn, "v4", start)
    e4 = calls(conn, "v3", E4_FLAT_PLANNER_SINCE, start)
    allv4 = calls(conn, "v4", "2000")
    spent = sum(c["cost_usd"] or 0 for c in e6)
    L = ["# E6 — fresh stories, prompt v4, article-aware planner + hierarchical model", "",
         f"Pre-registration `docs/experiments/e6_prereg.json` (sha256 `{sha[:16]}…`). E6 calls: **{len(e6)}**, **${spent:.2f}**.", ""]

    # Q1
    s6, s4 = spread(e6), spread(e4)
    L += ["## Q1 — does the new planner spread verdicts over more articles?", "",
          "| | E4 (flat planner, v3) | E6 (article-aware, v4) |", "|---|---:|---:|",
          f"| calls | {s4['calls']} | {s6['calls']} |",
          f"| distinct articles | {s4['articles']} | {s6['articles']} |",
          f"| distinct articles per 100 calls | {s4['articles_per_100_calls']:.0f} | {s6['articles_per_100_calls']:.0f} |",
          f"| mean uses per article | {s4['mean_uses']:.2f} | {s6['mean_uses']:.2f} |",
          f"| max uses of one article | {s4['max_uses']} | {s6['max_uses']} |",
          f"| calls that reuse an already-judged article | {s4['reuse_share']:.0%} | {s6['reuse_share']:.0%} |", "",
          "Descriptive: prompt and stories differ between the runs.", ""]

    results = []
    # Q2: E6 only (out of sample) and all v4
    for label, cs in (("E6 only (fresh)", e6), ("all v4 (E4b + E6)", allv4)):
        rows = social_rows(conn, {c["call_id"] for c in cs})
        outs, h, post = hfit(rows)
        n_dec = Counter(o for r in rows for o in (r["f"], r["s"]))
        rated = {o: v for o, v in rated_all.items() if o in outs and n_dec[o] >= 5}
        if len(rated) >= 10:
            pos = {o: _contrast_stats(post, {o: 1.0})[0] for o in rated}
            rho = spearmanr([rated[o] for o in rated], [pos[o] for o in rated]).statistic
            res = f"Spearman {rho:+.2f} on {len(rated)} outlets"
            ok = rho >= 0.6
        else:
            res, ok = f"undecided: {len(rated)} rated outlets with ≥ 5 decisive verdicts", None
        results.append((f"Q2 AllSides vs social — {label}", f"{res} · ICC {h.icc:.2f} (σ_a {h.sigma_a:.2f}, τ {h.tau:.2f})",
                        "≥ 0.60", ok))
        if label.startswith("all"):
            all_outs, all_h, all_post = outs, h, post
            all_rows = rows

    # Q3: contrasts on all v4
    cres = []
    for country, axis, a, b in contrasts:
        if axis != "social" or a not in all_outs or b not in all_outs:
            cres.append((country, axis, a, b, float("nan"), "no data" if axis == "social" else "econ: not refitted"))
            continue
        i, j = all_outs.index(a), all_outs.index(b)
        d = all_post.mean[i] - all_post.mean[j]
        v = all_post.cov[i, i] + all_post.cov[j, j] - 2 * all_post.cov[i, j]
        p = float(norm.cdf(d / np.sqrt(v)))
        cres.append((country, axis, a, b, p, "confirmed" if p > 0.8 else "contradicted" if p < 0.2 else "undecided"))
    dec = [c for c in cres if c[5] in ("confirmed", "contradicted")]
    conf = sum(c[5] == "confirmed" for c in dec)
    results.append(("Q3 contrasts (social, all v4)", f"{conf} of {len(dec)} decided confirmed", "≥ 80 %",
                    (conf / len(dec) >= 0.8) if dec else None))

    # Q4: split halves, US/UK, all v4
    u = [r for r in all_rows if reg[r["f"]].country in US_UK and reg[r["s"]].country in US_UK]
    ids = sorted({r["call_id"] for r in u})
    vals = []
    for seed in range(30):
        half = set(np.random.default_rng(seed).permutation(ids)[: len(ids) // 2])
        parts = [hfit([r for r in u if r["call_id"] in p]) for p in (half, set(ids) - half)]
        common = sorted(set(parts[0][0]) & set(parts[1][0]))
        vals.append(spearmanr([_contrast_stats(parts[0][2], {o: 1.0})[0] for o in common],
                              [_contrast_stats(parts[1][2], {o: 1.0})[0] for o in common]).statistic)
    results.append(("Q4 split halves (US/UK, all v4)", f"mean {np.mean(vals):.2f} over 30 splits (range {min(vals):.2f}–{max(vals):.2f})",
                    "≥ 0.50 (exploratory)", np.mean(vals) >= 0.5))

    L += ["## Q2–Q4", "", "| question | result | target | |", "|---|---|---|---|"]
    L += [f"| {a} | {b} | {c} | {'✅' if d else ('–' if d is None else '❌')} |" for a, b, c, d in results]
    L += ["", "| country | expected (social): more conservative than | P | outcome |", "|---|---|---|---|"]
    L += [f"| {c} | {reg[a].name} > {reg[b].name} | {p:.2f} | {o} |" for c, ax, a, b, p, o in cres if ax == "social"]
    verdicts = Counter((r["axis"], r["verdict"]) for r in conn.execute(
        "SELECT j.axis, j.verdict FROM judgments j JOIN judge_calls c USING(call_id) WHERE c.call_id IN (%s)"
        % ",".join("?" * len(e6)), [c["call_id"] for c in e6])) if e6 else Counter()
    dec6 = sum(v for (ax, vd), v in verdicts.items() if vd in ("A", "B"))
    na = {ax: verdicts[(ax, "na")] / max(1, sum(v for (a, _), v in verdicts.items() if a == ax)) for ax in ("econ", "social")}
    L += ["", "## Also", "",
          f"- 'na': econ {na['econ']:.0%}, social {na['social']:.0%} · cost per decisive verdict ${spent / max(dec6, 1):.4f}",
          f"- judge order bias β (social, all v4): {all_h.mean[all_h.n]:+.2f}", ""]
    Path("reports/e6_results.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
