"""E12: the LoRA judge (E11's C4) read from its probabilities, with a cut for "na" (A71). Free, on the gaming PC.

    uv run python -m pipeline.experiments.e12 probe     # C4's probabilities on E11's validation days → data/distil/
    uv run python -m pipeline.experiments.e12 tune      # the cut per axis → docs/experiments/e12_cuts.json, figure
    uv run python -m pipeline.experiments.e9 run --candidate C4-T && uv run python -m pipeline.experiments.e9 analyse
    uv run python -m pipeline.experiments.e12 report    # on E9's test calls: the check of the reading, the cut's curve

Why: on E9's 943 test calls C4 disagrees with gpt-5.4-mini mostly by abstaining where gpt-5.4-mini decides (24 % of
calls on social), rarely by choosing the other side (7 %); the learning curve (E11) is flat after the first third of
the data, while validation loss still falls — the probabilities improve where the greedy answer does not change.

Method, fixed before the cut sees any test call:
- the reading: at each verdict, P(A), P(B), P(na) from the next-token probabilities, renormalised over the three;
  econ first, social after the econ answer chosen (pipeline.judge.client.ThresholdJudge)
- the rule: "na" iff P(na) ≥ cut, else the likelier of A and B; one cut per axis
- tuned on E11's validation days only (322 pairs, never trained on; they did pick the checkpoint), for the highest
  agreement with gpt-5.4-mini (E9's J1), econ first, then social after the econ cut's answers; a grid of 0.01; ties →
  the middle of the tied range
- then E9's test calls are asked once with the cuts, as candidate C4-T, and analysed with E9's own code. The cut's
  curve on the test calls is reported, never used to choose.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.judge import prompt as P
from pipeline.judge.client import OPTIONS, ThresholdJudge, decide, local_client

VAL = Path("data/distil/sft/val.jsonl")
PROBS = Path("data/distil/e12_val_probs.jsonl")
CUTS = Path("docs/experiments/e12_cuts.json")
GRID = np.round(np.arange(0.01, 1.0001, 0.01), 2)
PARALLEL = 16


def judge(client=None) -> ThresholdJudge:
    return ThresholdJudge({"econ": 0.5, "social": 0.5}, client or local_client(), served="c4")


def probe(out: Path = PROBS, client=None) -> int:
    rows = [json.loads(x) for x in VAL.read_text().splitlines() if x.strip()]
    j = judge(client)

    def one(r):
        probs, n_in = j.probabilities(r["messages"][:2])
        return {"call_id": r["call_id"], "truth": json.loads(r["messages"][2]["content"]), "probs": probs, "tokens": n_in}

    out.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(PARALLEL) as pool, out.open("w") as f:
        for k, rec in enumerate(pool.map(one, rows), 1):
            f.write(json.dumps(rec) + "\n")
            if k % 50 == 0:
                print(f"  {k}/{len(rows)}", flush=True)
    return len(rows)


def argmax(p: dict) -> str:
    return max(OPTIONS, key=lambda o: p[o])


def answers(rows: list[dict], cut: dict | None) -> list[dict]:
    """Each row's answer with these cuts (None: the greedy answer, the likeliest option on each axis)."""
    out = []
    for r in rows:
        if cut is None:
            e = argmax(r["probs"]["econ"])
            out.append({"econ": e, "social": argmax(r["probs"]["social"][e])})
        else:
            out.append(decide(r["probs"], cut))
    return out


def kappa(a: list, b: list) -> float:
    from pipeline.experiments.e9 import kappa as k
    return k(a, b)


def summary(truth: list[dict], mine: list[dict]) -> dict:
    from pipeline.experiments.e9 import errors
    out = {}
    for ax in P.AXES:
        t, m = [x[ax] for x in truth], [x[ax] for x in mine]
        out[ax] = {"agree": round(float(np.mean([a == b for a, b in zip(t, m)])), 4), "kappa": round(kappa(t, m), 3),
                   "decides": round(float(np.mean([x != "na" for x in m])), 3), **errors(t, m)}
    return out


def curve(rows: list[dict], ax: str, econ_cut: float | None = None) -> list[float]:
    truth = [r["truth"][ax] for r in rows]
    res = []
    for c in GRID:
        cut = {"econ": c if ax == "econ" else econ_cut, "social": c}
        res.append(float(np.mean([a[ax] == t for a, t in zip(answers(rows, cut), truth)])))
    return res


def best(values: list[float]) -> float:
    v = np.array(values)
    tied = np.flatnonzero(v >= v.max() - 1e-12)
    return float(GRID[tied[len(tied) // 2]])


def tune(rows: list[dict] | None = None, out: Path = CUTS, fig: Path | None = Path("reports/e12_cut.png")) -> dict:
    from pipeline.ingest.feeds import utc_now_iso
    rows = rows or [json.loads(x) for x in PROBS.read_text().splitlines() if x.strip()]
    ce = curve(rows, "econ")
    econ = best(ce)
    cs = curve(rows, "social", econ)
    cut = {"econ": econ, "social": best(cs)}
    truth = [r["truth"] for r in rows]
    res = {"written_at": utc_now_iso(), "na_cut": cut, "validation_pairs": len(rows),
           "rule": "na iff P(na) >= cut, else the likelier of A and B; econ first, social after the econ answer",
           "objective": "highest agreement with gpt-5.4-mini on E11's validation days; ties: middle of the tied range",
           "greedy": summary(truth, answers(rows, None)), "with_cut": summary(truth, answers(rows, cut)),
           "curve": {"grid": GRID.tolist(), "econ": ce, "social": cs}}
    out.write_text(json.dumps(res, indent=1))
    if fig:
        figure(rows, res, fig)
    return res


def figure(rows: list[dict], res: dict, out: Path, test: dict | None = None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    color = {"econ": "#0072B2", "social": "#D55E00"}
    name = {"econ": "economic", "social": "social"}
    ink, muted, grid = "#1f2328", "#59636e", "#d8dee4"
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3))
    a = axes[0]
    for ax in P.AXES:
        a.plot(res["curve"]["grid"], res["curve"][ax], color=color[ax], linewidth=2, label=f"{name[ax]}, validation days")
        if test:
            a.plot(test["grid"], test[ax], color=color[ax], linewidth=1.2, linestyle="--", label=f"{name[ax]}, E9 test calls")
        a.axvline(res["na_cut"][ax], color=color[ax], linewidth=1, linestyle=":")
        a.axhline(res["greedy"][ax]["agree"], color=color[ax], linewidth=0.8, alpha=0.5)
    a.set_title('Agreement with gpt-5.4-mini against the "na" cut', loc="left", color=ink, fontsize=11)
    a.set_xlabel('cut: answer "na" only when P(na) ≥ cut  (dotted: the cut chosen; thin: greedy)', color=muted, fontsize=8)
    a.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0)); a.legend(frameon=False, fontsize=8)
    for k, ax in enumerate(P.AXES, 1):
        a = axes[k]
        e_chosen = answers(rows, res["na_cut"])
        if ax == "econ":
            p_na = [r["probs"]["econ"]["na"] for r in rows]
        else:
            p_na = [r["probs"]["social"][e["econ"]]["na"] for r, e in zip(rows, e_chosen)]
        dec = np.array([r["truth"][ax] != "na" for r in rows])
        bins = np.linspace(0, 1, 21)
        p = np.array(p_na)
        a.hist(p[dec], bins=bins, color=color[ax], alpha=0.85, label="gpt-5.4-mini decides (A/B)")
        a.hist(p[~dec], bins=bins, color=muted, alpha=0.45, label='gpt-5.4-mini says "na"')
        a.axvline(res["na_cut"][ax], color=ink, linewidth=1, linestyle=":")
        a.set_title(f"{name[ax].capitalize()}: the LoRA judge's P(na)", loc="left", color=ink, fontsize=11)
        a.set_xlabel("P(na) at the verdict, validation days", color=muted); a.legend(frameon=False, fontsize=8)
    for a in axes:
        a.grid(axis="y", color=grid, linewidth=0.8); a.set_axisbelow(True)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        a.tick_params(colors=muted)
    fig.tight_layout(); fig.savefig(out, dpi=160); plt.close(fig)


def test_rows(conn) -> list[dict]:
    """E9's test calls as C4-T asked them: probabilities from the call log, gpt-5.4-mini's verdict, C4's greedy one."""
    from pipeline.experiments.e9 import purpose_of, verdicts
    ref = verdicts(conn, "c.model = 'gpt-5.4-mini' AND c.prompt_version = 'v4' AND c.purpose IN ('measure', 'eval')")
    c4 = {json.loads(r["context"])["ref_call"]: r["call_id"] for r in conn.execute(
        "SELECT l.call_id, l.context FROM judge_call_log l JOIN judge_calls c ON c.call_id = l.call_id WHERE c.purpose = ?",
        (purpose_of("C4"),))}
    greedy = verdicts(conn, "c.purpose = ?", (purpose_of("C4"),))
    out = []
    for r in conn.execute("""SELECT l.response_json, l.context FROM judge_call_log l JOIN judge_calls c
                             ON c.call_id = l.call_id WHERE c.purpose = ?""", (purpose_of("C4-T"),)):
        ref_call = json.loads(r["context"])["ref_call"]
        if ref_call in ref:
            out.append({"truth": {ax: ref[ref_call].get(ax, "na") for ax in P.AXES},
                        "probs": json.loads(r["response_json"])["probabilities"],
                        "greedy": greedy.get(c4.get(ref_call), {})})
    return out


def report(conn=None, out: Path = Path("reports/e12.md")) -> dict:
    conn = conn or store.connect()
    res = json.loads(CUTS.read_text())
    rows = test_rows(conn)
    mine = answers(rows, None)
    same = {ax: float(np.mean([m[ax] == r["greedy"].get(ax) for m, r in zip(mine, rows) if r["greedy"]])) for ax in P.AXES}
    test = {"grid": GRID.tolist(), "econ": curve(rows, "econ"), "social": curve(rows, "social", res["na_cut"]["econ"])}
    truth = [r["truth"] for r in rows]
    s_greedy, s_cut = summary(truth, mine), summary(truth, answers(rows, res["na_cut"]))
    val = [json.loads(x) for x in PROBS.read_text().splitlines() if x.strip()]
    figure(val, res, Path("reports/e12_cut.png"), test)
    lines = ["# E12 — the LoRA judge read from its probabilities, with a cut for \"na\"", "",
             f"Cuts tuned on E11's {res['validation_pairs']} validation pairs ({res['written_at']}), before any test "
             f"call: **econ {res['na_cut']['econ']:.2f}, social {res['na_cut']['social']:.2f}** — \"na\" only when "
             f"P(na) ≥ the cut. E9's {len(rows)} test calls asked once with them (C4-T). Not a claim (pipeline phase).",
             "", f"Check of the reading: the likeliest option from the probabilities equals C4's greedy answer on "
             f"{same['econ']:.1%} (econ) and {same['social']:.1%} (social) of the test calls.", "",
             "| | set | agreement | κ | decides | flip (of both decided) | miss | invent |", "|---|---|---|---|---|---|---|---|"]
    for label, s, where in (("greedy", res["greedy"], "validation"), ("cut", res["with_cut"], "validation"),
                            ("greedy", s_greedy, "E9 test"), ("cut", s_cut, "E9 test")):
        for ax in P.AXES:
            e = s[ax]
            lines.append(f"| {label} {ax} | {where} | {e['agree']:.1%} | {e['kappa']:.2f} | {e['decides']:.0%} | "
                         f"{e['flip']:.1%} ({e['flip_when_both_decide']:.0%}) | {e['miss']:.0%} | {e['invent']:.0%} |")
    lines += ["", "![cut](e12_cut.png)", "",
              "The dashed test curves are reported, never used to choose. E9's own analysis (reports/e9.md) has C4-T "
              "with intervals and the positions check (J3)."]
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return {"same_as_greedy": same, "greedy": s_greedy, "cut": s_cut}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["probe", "tune", "report"])
    a = ap.parse_args()
    if a.what == "probe":
        print(f"{probe()} validation pairs → {PROBS}")
    elif a.what == "tune":
        r = tune()
        print(json.dumps({k: r[k] for k in ("na_cut", "greedy", "with_cut")}, indent=1))
    else:
        report()


if __name__ == "__main__":
    main()
