"""E4 analysis: the pre-registered checks of the first real measurement (docs/experiments/e4_prereg.json).

    uv run python -m pipeline.experiments.e4 --since <UTC start of the E4 measurement>

Writes reports/e4_validation.md and reports/e4_validation.png. Validates the instrument; it is not a claim (pipeline phase).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Ellipse  # noqa: E402
from scipy.stats import binomtest, norm, spearmanr  # noqa: E402

from pipeline import store  # noqa: E402
from pipeline.rate.model import BradleyTerryLaplace, outlet_sds  # noqa: E402
from pipeline.sources.registry import load_outlets  # noqa: E402

PREREG = Path("docs/experiments/e4_prereg.json")
A = {"full": 1.0, "summary": 0.6}
US_UK = {"United States", "United Kingdom"}


def judgments(conn, since, purpose="measure", prompt="v3"):
    return conn.execute("""SELECT c.call_id, c.first_outlet f, c.second_outlet s, c.text_kind k, c.cost_usd, j.axis, j.verdict,
                                  j.first_wins w FROM judge_calls c JOIN judgments j ON j.call_id = c.call_id
                           WHERE c.purpose = ? AND c.prompt_version = ? AND c.at >= ?""", (purpose, prompt, since)).fetchall()


def fit(rows, outlets, axis):
    rs = [r for r in rows if r["axis"] == axis and r["w"] is not None]
    return BradleyTerryLaplace(outlets, axis).fit([(r["f"], r["s"]) for r in rs], [bool(r["w"]) for r in rs],
                                                  [A[r["k"]] for r in rs])


def contrast_p(post, a, b):
    i, j = post.outlets.index(a), post.outlets.index(b)
    d = post.mean[i] - post.mean[j]
    v = post.cov[i, i] + post.cov[j, j] - 2 * post.cov[i, j]
    return float(norm.cdf(d / np.sqrt(max(v, 1e-12))))


def centred(post, k):
    """Position relative to the mean outlet (A26): the plane's coordinates."""
    n = len(post.outlets)
    return float(post.mean[k] - post.mean[:n].mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--purpose", default="measure", help="'simulation' to dry-test the analysis on simulated verdicts")
    ap.add_argument("--prompt", default="v3")
    args = ap.parse_args()
    pre = json.loads(PREREG.read_text())
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    conn = store.connect()
    reg = {o.id: o for o in load_outlets()}
    rows = judgments(conn, args.since, args.purpose, args.prompt)
    calls = {r["call_id"]: r for r in rows}
    outlets = sorted({r["f"] for r in rows} | {r["s"] for r in rows})
    posts = {ax: fit(rows, outlets, ax) for ax in ("econ", "social")}
    n_cmp = Counter()
    for c in calls.values():
        n_cmp[c["f"]] += 1; n_cmp[c["s"]] += 1
    spent = sum(c["cost_usd"] for c in calls.values())
    verdicts = Counter((r["axis"], r["verdict"]) for r in rows)
    decisive = sum(v for (ax, vd), v in verdicts.items() if vd in ("A", "B"))
    L = ["# E4 — first real measurement: instrument check", "",
         f"Pre-registration `docs/experiments/e4_prereg.json` (sha256 `{sha[:16]}…`), written before any measurement call. "
         f"Judge prompt v3, gpt-5.4-mini. Measurement calls since {args.since}: **{len(calls):,}**, **${spent:.2f}**, "
         f"{decisive:,} decisive verdicts (${spent / max(decisive, 1):.4f} per decisive verdict). Validates the instrument; "
         "not a claim (pipeline phase).", ""]
    res = []

    # H_allsides
    rated = {o: v for o, v in pre["allsides"]["ratings"].items() if o in outlets}
    ours = {ax: {o: centred(posts[ax], outlets.index(o)) for o in rated} for ax in posts}
    both = {o: (ours["econ"][o] + ours["social"][o]) / 2 for o in rated}
    rho = {ax: spearmanr([rated[o] for o in rated], [ours[ax][o] for o in rated]).statistic for ax in posts}
    rho_both = spearmanr([rated[o] for o in rated], [both[o] for o in rated]).statistic
    rng = np.random.default_rng(0)
    xs, ys = np.array([rated[o] for o in rated]), np.array([ours["social"][o] for o in rated])
    perm = np.array([spearmanr(xs, rng.permutation(ys)).statistic for _ in range(2000)])
    p_perm = float(np.mean(perm >= rho["social"]))
    res.append(("AllSides vs our social position (primary)", f"Spearman {rho['social']:.2f} on {len(rated)} outlets "
                f"(permutation p = {p_perm:.3f}); econ {rho['econ']:.2f}, mean of both {rho_both:.2f}", "≥ 0.60",
                rho["social"] >= 0.6))

    # H_contrasts
    cres = []
    for country, axis, a, b in pre["contrasts"]:
        if a in outlets and b in outlets:
            p = contrast_p(posts[axis], a, b)
            cres.append((country, axis, a, b, p, "confirmed" if p > 0.8 else "contradicted" if p < 0.2 else "undecided"))
        else:
            cres.append((country, axis, a, b, float("nan"), "no data"))
    decided = [c for c in cres if c[5] in ("confirmed", "contradicted")]
    conf = sum(c[5] == "confirmed" for c in decided)
    sign_p = binomtest(conf, len(decided), 0.5, alternative="greater").pvalue if decided else float("nan")
    res.append(("Pre-written contrasts", f"{conf} of {len(decided)} decided confirmed ({len(cres) - len(decided)} undecided "
                f"or no data); sign test p = {sign_p:.3f}", "≥ 80 % of decided",
                bool(decided) and conf / len(decided) >= 0.8))

    # H_split_half (US/UK calls)
    usuk = [c for c in calls.values() if reg[c["f"]].country in US_UK and reg[c["s"]].country in US_UK]
    ids = [c["call_id"] for c in usuk]
    half = set(np.random.default_rng(0).permutation(ids)[: len(ids) // 2])
    usuk_out = sorted({c["f"] for c in usuk} | {c["s"] for c in usuk})
    h1 = fit([r for r in rows if r["call_id"] in half], usuk_out, "social")
    h2 = fit([r for r in rows if r["call_id"] in set(ids) - half], usuk_out, "social")
    n = len(usuk_out)
    rho_split = spearmanr(h1.mean[:n], h2.mean[:n]).statistic if n > 2 else float("nan")
    res.append(("Split halves (US/UK social)", f"Spearman {rho_split:.2f} between two random halves "
                f"({len(ids)} calls, {n} outlets)", "≥ 0.70", rho_split >= 0.7))

    # H_nano
    nano = json.loads(Path("reports/e4_nano.json").read_text()) if Path("reports/e4_nano.json").exists() else None
    if nano:
        ok = all(round(nano["agreement_with_reference"][ax] - nano["reference_retest_agreement"][ax] + 0.10, 6) >= 0 for ax in ("econ", "social"))   # rounded: 0.80 - 0.10 is 0.7000000000000001 in floats
        res.append(("gpt-5.4-nano vs mini", f"agrees with mini {nano['agreement_with_reference']['econ']:.2f} / "
                    f"{nano['agreement_with_reference']['social']:.2f} (econ / social) vs mini's own retest "
                    f"{nano['reference_retest_agreement']['econ']:.2f} / {nano['reference_retest_agreement']['social']:.2f}; "
                    f"{nano['pairs']} pairs, ${nano['cost_usd']:.3f}", "≥ retest − 0.10", ok))

    L += ["## Pre-registered checks", "", "| check | result | target | pass |", "|---|---|---|---|"]
    L += [f"| {a} | {b} | {c} | {'✅' if d else '❌'} |" for a, b, c, d in res]
    L += ["", "## Contrasts", "", "| country | axis | expected: more + than | P(first further +) | outcome |", "|---|---|---|---|---|"]
    L += [f"| {c} | {ax} | {reg[a].name} > {reg[b].name} | {p:.2f} | {o} |" for c, ax, a, b, p, o in cres]
    L += ["", "## AllSides vs our position", "", "| outlet | AllSides | social (ours) | econ (ours) | comparisons |", "|---|---:|---:|---:|---:|"]
    L += [f"| {reg[o].name} | {rated[o]:+d} | {ours['social'][o]:+.2f} | {ours['econ'][o]:+.2f} | {n_cmp[o]} |"
          for o in sorted(rated, key=lambda o: ours['social'][o])]
    na = {ax: verdicts[(ax, "na")] / max(1, sum(v for (a, _), v in verdicts.items() if a == ax)) for ax in ("econ", "social")}
    sd = {ax: outlet_sds(posts[ax]) for ax in posts}
    L += ["", "## Also measured", "",
          f"- 'na' (no angle): econ {na['econ']:.0%}, social {na['social']:.0%}",
          f"- judge order bias β: econ {posts['econ'].mean[-1]:+.2f}, social {posts['social'].mean[-1]:+.2f} (randomised order lets the model remove it)",
          f"- outlets measured: {len(outlets)}; comparisons per outlet median {int(np.median([n_cmp[o] for o in outlets]))}; "
          f"outlet sd median econ {np.median(sd['econ']):.2f}, social {np.median(sd['social']):.2f} (prior sd 1)",
          "", "![E4](e4_validation.png)", ""]
    Path("reports/e4_validation.md").write_text("\n".join(L))

    # figure: AllSides scatter + US/UK plane
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 6))
    for o in rated:
        k = outlets.index(o)
        a1.errorbar(rated[o] + np.random.default_rng(hash(o) % 2**32).uniform(-0.12, 0.12), ours["social"][o],
                    yerr=sd["social"][k], fmt="o", color="#c0392b" if reg[o].country == "United States" else "#1f4e8c",
                    ecolor="#bbb", capsize=2)
        a1.annotate(reg[o].name, (rated[o], ours["social"][o]), fontsize=7.5, xytext=(5, 2), textcoords="offset points")
    a1.set_xticks([-2, -1, 0, 1, 2]); a1.set_xticklabels(["Left", "Lean Left", "Center", "Lean Right", "Right"])
    a1.set_xlabel("AllSides rating"); a1.set_ylabel("our social position (relative; + = more conservative)")
    a1.set_title(f"AllSides vs measured (Spearman {rho['social']:.2f}) · red US, blue UK", loc="left", fontsize=10)
    a1.axhline(0, color="#ddd", lw=0.8)
    for o in usuk_out:
        k = outlets.index(o)
        x, y = centred(posts["econ"], k), centred(posts["social"], k)
        col = "#c0392b" if reg[o].country == "United States" else "#1f4e8c"
        a2.add_patch(Ellipse((x, y), 2 * sd["econ"][k], 2 * sd["social"][k], fc=col, alpha=0.08, ec=col, lw=0.8))
        a2.plot(x, y, "o", color=col); a2.annotate(reg[o].name, (x, y), fontsize=7.5, xytext=(4, 3), textcoords="offset points")
    a2.axhline(0, color="#ddd", lw=0.8); a2.axvline(0, color="#ddd", lw=0.8)
    a2.set_xlabel("economic: ← left · right →"); a2.set_ylabel("social: ← liberal · conservative →")
    a2.set_title("US and UK outlets, ±1 sd ovals (relative to the average outlet)", loc="left", fontsize=10)
    a2.autoscale()
    fig.suptitle("E4 · first real measurement (judge v3, gpt-5.4-mini) — instrument check, not a claim", x=0.01, ha="left",
                 fontweight="bold")
    fig.tight_layout(); fig.savefig("reports/e4_validation.png", dpi=150, facecolor="white"); plt.close(fig)
    print("\n".join(L))


if __name__ == "__main__":
    main()
