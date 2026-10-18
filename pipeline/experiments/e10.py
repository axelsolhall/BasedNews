"""E10: prompt v4 out of sample on fresh US/UK stories (H7) — docs/experiments/e10_prereg.json. E6 Q2's rule and code
on the E10 calls only: hierarchical fit, social axis, Spearman with the 14 AllSides ratings, target ≥ 0.60.

    BASEDNEWS_DB=data/experiments/e10.sqlite PYTHONPATH=. uv run python -m pipeline.experiments.e10

Writes reports/e10.md. Not a claim (pipeline phase).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from scipy.stats import spearmanr

from pipeline import store
from pipeline.aggregate.plane import _contrast_stats
from pipeline.experiments.e4 import PREREG as E4_PREREG
from pipeline.experiments.e6 import hfit, social_rows

PREREG = Path("docs/experiments/e10_prereg.json")
PURPOSE = "e10"


def q(conn, purpose: str = PURPOSE) -> dict:
    rated_all = json.loads(E4_PREREG.read_text())["allsides"]["ratings"]
    cs = conn.execute("SELECT * FROM judge_calls WHERE purpose = ? AND prompt_version = 'v4'", (purpose,)).fetchall()
    rows = social_rows(conn, {c["call_id"] for c in cs})
    out = {"calls": len(cs), "usd": round(sum(c["cost_usd"] or 0 for c in cs), 4)}
    na = conn.execute("""SELECT j.axis, avg(j.verdict = 'na') FROM judgments j JOIN judge_calls c USING(call_id)
                         WHERE c.purpose = ? GROUP BY 1""", (purpose,)).fetchall()
    out["na_share"] = {a: round(v, 3) for a, v in na}
    if not rows:
        return {**out, "result": "undecided: no decisive social verdicts", "ok": None}
    outs, h, post = hfit(rows)
    n_dec = Counter(o for r in rows for o in (r["f"], r["s"]))
    rated = {o: v for o, v in rated_all.items() if o in outs and n_dec[o] >= 5}
    out.update(decisive_social=len(rows), icc=round(h.icc, 3), sigma_a=round(h.sigma_a, 3), tau=round(h.tau, 3),
               beta=round(float(h.mean[len(outs)]), 3), rated_outlets=len(rated),
               usd_per_decisive=round(out["usd"] / max(len(rows), 1), 5))
    if len(rated) < 10:
        return {**out, "result": f"undecided: {len(rated)} rated outlets with ≥ 5 decisive verdicts", "ok": None}
    pos = {o: _contrast_stats(post, {o: 1.0})[0] for o in rated}
    rho = float(spearmanr([rated[o] for o in rated], [pos[o] for o in rated]).statistic)
    out.update(rho=round(rho, 3), positions={o: round(float(pos[o]), 3) for o in rated}, allsides=rated)
    return {**out, "result": f"Spearman {rho:+.2f} on {len(rated)} outlets", "ok": rho >= 0.6}


def main():
    conn = store.connect()
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    r = q(conn)
    verdict = {True: "met → v4 is locked for E11", False: "not met → no training verdicts; the prompt is revisited",
               None: "undecided → v4 kept, shortfall reported"}[r["ok"]]
    L = ["# E10 — prompt v4 on fresh US/UK stories (H7)", "",
         f"Pre-registration `{PREREG}` (sha256 `{sha[:16]}…`). {r['calls']} calls, ${r['usd']:.2f}. Not a claim.", "",
         f"**AllSides vs social, E10 calls only:** {r['result']} (target ≥ 0.60) — **{verdict}**", "",
         f"'na' share: {r['na_share']} · decisive social verdicts {r.get('decisive_social', 0)} · ICC {r.get('icc')} · "
         f"β {r.get('beta')} · ${r.get('usd_per_decisive', 0):.4f} per decisive verdict", ""]
    if "positions" in r:
        L += ["| outlet | AllSides | position (social) |", "|---|---:|---:|"]
        L += [f"| {o} | {r['allsides'][o]:+d} | {p:+.2f} |" for o, p in sorted(r["positions"].items(), key=lambda kv: kv[1])]
    Path("reports").mkdir(exist_ok=True)
    Path("reports/e10.md").write_text("\n".join(L) + "\n")
    Path("reports/e10.json").write_text(json.dumps(r, indent=2))
    print("\n".join(L[:5]))


if __name__ == "__main__":
    main()
