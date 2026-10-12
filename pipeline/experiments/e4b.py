"""E4b: same pairs, new prompt — does v4 fix what E4 found? (A46)

    uv run python -m pipeline.experiments.e4b [--judge simulated] [--budget-usd 0.70]

Re-asks every E4 US/UK call (338) with prompt v4 — same article pair, same order, same question direction — so the
only change is the prompt. Stored as real v4 measurements (purpose "measure", prompt "v4"). Then compares v3 and v4 on
exactly these calls: AllSides Spearman (social), the six US/UK contrasts, how far NYT, Sky, BBC and Fox move, verdict
agreement, "na" rates and the first-shown lean. Writes reports/e4b_v3_vs_v4.md and .png.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

from pipeline import store  # noqa: E402
from pipeline.contracts import ComparisonRequest  # noqa: E402
from pipeline.experiments.e4 import A, PREREG, centred, contrast_p  # noqa: E402
from pipeline.judge.client import OpenAIJudge, SimulatedJudge  # noqa: E402
from pipeline.measure.run import record  # noqa: E402
from pipeline.rate.model import BradleyTerryLaplace  # noqa: E402
from pipeline.settings import load_settings  # noqa: E402
from pipeline.sources.registry import load_outlets  # noqa: E402

US_UK = {"United States", "United Kingdom"}
WATCH = ("nyt", "skynews", "bbc", "foxnews")


def fit(rows, outlets, axis):
    rs = [r for r in rows if r["axis"] == axis and r["w"] is not None]
    return BradleyTerryLaplace(outlets, axis).fit([(r["f"], r["s"]) for r in rs], [bool(r["w"]) for r in rs],
                                                  [A[r["k"]] for r in rs])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", choices=["openai", "simulated"], default="openai")
    ap.add_argument("--budget-usd", type=float, default=0.70)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    conn = store.connect()
    reg = {o.id: o for o in load_outlets()}
    since = json.loads(Path("reports/e4_run.json").read_text())["e4_start"]
    e4 = [c for c in conn.execute("""SELECT * FROM judge_calls WHERE purpose = 'measure' AND prompt_version = 'v3'
                                     AND at >= ? ORDER BY at, rowid""", (since,))
          if reg[c["first_outlet"]].country in US_UK and reg[c["second_outlet"]].country in US_UK]
    art = {r["id"]: r for r in conn.execute("SELECT id, outlet_id, title, summary, full_text, published FROM articles")}
    hist = store.headline_history(conn, [i for c in e4 for i in (c["first_article"], c["second_article"])])
    from pipeline.measure.run import rendered
    done = {(r["first_article"], r["second_article"]) for r in conn.execute(
        "SELECT first_article, second_article FROM judge_calls WHERE purpose = 'measure' AND prompt_version = 'v4'")}
    todo = [c for c in e4 if (c["first_article"], c["second_article"]) not in done
            and c["first_article"] in art and c["second_article"] in art]
    outlet_ids = sorted({c["first_outlet"] for c in e4} | {c["second_outlet"] for c in e4})
    judge = OpenAIJudge(load_settings()) if args.judge == "openai" else SimulatedJudge(outlet_ids, seed=1)
    spent, est = 0.0, 0.0015 * len(todo)
    print(f"{len(e4)} E4 US/UK calls; {len(todo)} to re-ask with v4 (≈ ${est:.2f}); judge {args.judge}")

    def ask(c):
        a, b = art[c["first_article"]], art[c["second_article"]]
        if args.judge == "openai":
            return judge.compare(rendered(a, hist, c["text_kind"]), rendered(b, hist, c["text_kind"]), c["direction"], "v4")
        return judge.compare_outlets(c["first_outlet"], c["second_outlet"], A[c["text_kind"]], c["direction"])

    for k in range(0, len(todo), 30):
        batch = todo[k:k + 30]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            verdicts = list(ex.map(ask, batch))
        for c, v in zip(batch, verdicts):
            req = ComparisonRequest(c["event_id"], "both", c["first_outlet"], c["second_outlet"], c["first_article"], c["second_article"])
            record(conn, "measure" if args.judge == "openai" else "simulation", req, c["text_kind"], v, c["direction"],
                   c["event_id"], "v4")
            spent += v.cost_usd
        conn.commit()
        if spent >= args.budget_usd:
            print(f"budget reached at ${spent:.2f}"); break
    print(f"re-asked {len(todo)} calls, spent ${spent:.3f}")

    # ---- compare v3 and v4 on the same calls
    purpose = "measure" if args.judge == "openai" else "simulation"
    q = """SELECT c.call_id, c.first_article fa, c.second_article sa, c.first_outlet f, c.second_outlet s, c.text_kind k,
                  j.axis, j.verdict v, j.first_wins w FROM judge_calls c JOIN judgments j ON j.call_id = c.call_id
           WHERE c.purpose = ? AND c.prompt_version = ?"""
    keys = {(c["first_article"], c["second_article"]) for c in e4}
    v3 = [r for r in conn.execute(q + " AND c.at >= ?", ("measure", "v3", since)) if (r["fa"], r["sa"]) in keys]
    v4 = [r for r in conn.execute(q, (purpose, "v4")) if (r["fa"], r["sa"]) in keys]
    pre = json.loads(PREREG.read_text())
    rated = {o: x for o, x in pre["allsides"]["ratings"].items() if o in outlet_ids}
    out = {}
    for name, rows in (("v3", v3), ("v4", v4)):
        posts = {ax: fit(rows, outlet_ids, ax) for ax in ("econ", "social")}
        pos = {ax: {o: centred(posts[ax], outlet_ids.index(o)) for o in outlet_ids} for ax in posts}
        rho = spearmanr([rated[o] for o in rated], [pos["social"][o] for o in rated]).statistic
        cons = [(a, b, contrast_p(posts[ax], a, b)) for cc, ax, a, b in pre["contrasts"]
                if cc in ("US", "UK") and a in outlet_ids and b in outlet_ids]
        dec = [rows_ for rows_ in rows if rows_["v"] in ("A", "B")]
        out[name] = {"rho": rho, "pos": pos, "contrasts": cons,
                     "na": {ax: np.mean([r["v"] == "na" for r in rows if r["axis"] == ax]) for ax in ("econ", "social")},
                     "first": np.mean([r["v"] == "A" for r in dec]) if dec else float("nan"), "n_calls": len({r["call_id"] for r in rows})}
    by3 = {(r["fa"], r["sa"], r["axis"]): r["v"] for r in v3}
    agree = {ax: np.mean([by3.get((r["fa"], r["sa"], ax)) == r["v"] for r in v4 if r["axis"] == ax]) for ax in ("econ", "social")}
    L = ["# E4b — same 338 US/UK pairs, prompt v3 vs v4", "",
         f"Only the prompt changed (same pairs, order and question direction). v4 calls: {out['v4']['n_calls']}, "
         f"${spent:.2f}. Judge: {args.judge}.", "",
         "| | v3 | v4 |", "|---|---|---|",
         f"| AllSides vs social (Spearman, {len(rated)} outlets) | {out['v3']['rho']:.2f} | {out['v4']['rho']:.2f} |",
         f"| US/UK contrasts confirmed (P > 0.8) | {sum(p > 0.8 for *_, p in out['v3']['contrasts'])}/{len(out['v3']['contrasts'])} | "
         f"{sum(p > 0.8 for *_, p in out['v4']['contrasts'])}/{len(out['v4']['contrasts'])} |",
         f"| 'na' econ / social | {out['v3']['na']['econ']:.0%} / {out['v3']['na']['social']:.0%} | "
         f"{out['v4']['na']['econ']:.0%} / {out['v4']['na']['social']:.0%} |",
         f"| picks the first-shown (decisive) | {out['v3']['first']:.2f} | {out['v4']['first']:.2f} |",
         f"| same verdict as v3 on the same call | – | econ {agree['econ']:.0%} / social {agree['social']:.0%} |", "",
         "| outlet (AllSides) | social v3 | social v4 | econ v3 | econ v4 |", "|---|---:|---:|---:|---:|"]
    for o in sorted(rated, key=lambda o: out["v4"]["pos"]["social"][o]):
        mark = " ←" if o in WATCH else ""
        L.append(f"| {reg[o].name} ({rated[o]:+d}){mark} | {out['v3']['pos']['social'][o]:+.2f} | {out['v4']['pos']['social'][o]:+.2f} | "
                 f"{out['v3']['pos']['econ'][o]:+.2f} | {out['v4']['pos']['econ'][o]:+.2f} |")
    L += ["", "![v3 vs v4](e4b_v3_vs_v4.png)", ""]
    suffix = "" if args.judge == "openai" else "_simulated"
    Path(f"reports/e4b_v3_vs_v4{suffix}.md").write_text("\n".join(L))
    fig, ax = plt.subplots(figsize=(8, 6))
    for o in rated:
        y3, y4 = out["v3"]["pos"]["social"][o], out["v4"]["pos"]["social"][o]
        x = rated[o] + (hash(o) % 7 - 3) * 0.04
        ax.annotate("", (x, y4), (x, y3), arrowprops=dict(arrowstyle="->", color="#999"))
        ax.plot(x, y4, "o", color="#c0392b" if reg[o].country == "United States" else "#1f4e8c")
        ax.annotate(reg[o].name, (x, y4), fontsize=7.5, xytext=(5, 2), textcoords="offset points")
    ax.set_xticks([-2, -1, 0, 1, 2]); ax.set_xticklabels(["Left", "Lean Left", "Center", "Lean Right", "Right"])
    ax.set_xlabel("AllSides rating"); ax.set_ylabel("our social position (+ = more conservative)")
    ax.set_title(f"v3 → v4 on the same pairs · Spearman {out['v3']['rho']:.2f} → {out['v4']['rho']:.2f}", loc="left")
    ax.axhline(0, color="#ddd", lw=0.8); fig.tight_layout()
    fig.savefig(f"reports/e4b_v3_vs_v4{suffix}.png", dpi=150, facecolor="white"); plt.close(fig)
    print("\n".join(L))


if __name__ == "__main__":
    main()
