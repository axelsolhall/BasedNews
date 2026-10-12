"""3.2 eval: is the judge reliable enough to measure with? (A46; tests A04, A06, A11, A28, A41)

    uv run python -m pipeline.judge.eval [--pairs 30] [--model gpt-5.4-nano] [--judge simulated]

Fixed sample of real story pairs (seeded), each asked four ways: A|B, swapped B|A, A|B again, and the mirror question
("further LEFT/LIBERAL?"), plus null pairs that must come out "same": an article against itself and same-text pairs
(A44). ~4 calls per pair (≈ $0.16 for 30 pairs on gpt-5.4-mini). Calls are stored with purpose "eval" and never used
in the fit. Writes reports/m7_judge_eval.md (metrics only, no article text).

Targets (first version — to be revisited with the numbers): swap consistency ≥ 0.80, retest agreement ≥ 0.80,
mirror agreement ≥ 0.80, first-shown share of decisive verdicts 0.40–0.60, longer-text share 0.40–0.60, null pairs
"same" or "na" ≥ 0.80.
"""
from __future__ import annotations

import argparse
import random
from collections import defaultdict

import numpy as np

from pipeline import store
from pipeline.contracts import ComparisonRequest
from pipeline.judge import prompt as P
from pipeline.judge.client import OpenAIJudge, SimulatedJudge
from pipeline.measure.run import load_items, record, rendered
from pipeline.settings import load_settings

FLIP = {"A": "B", "B": "A", "same": "same", "na": "na"}


def share(xs):
    return (float(np.mean(xs)), len(xs)) if xs else (float("nan"), 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=30)
    ap.add_argument("--nulls", type=int, default=5, help="self pairs and same-text pairs, each")
    ap.add_argument("--model", default=None, help="OpenAI judge model (default from settings)")
    ap.add_argument("--judge", choices=["openai", "simulated"], default="openai")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--prompt", choices=P.VERSIONS, default=P.PROMPT_VERSION)
    args = ap.parse_args()
    settings = load_settings()
    conn = store.connect()
    run, ev, hist, ties = load_items(conn)

    rng = random.Random(args.seed)
    pairs = []
    for eid, arts in ev.items():
        items = list(arts.values())
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if frozenset((a["id"], b["id"])) in ties or (a["genre"] or "news") != (b["genre"] or "news"):
                    continue
                kind = "full" if a["full_text"] and b["full_text"] else "summary" if a["summary"] and b["summary"] else None
                if kind:
                    pairs.append((eid, a, b, kind))
    pairs = rng.sample(pairs, min(args.pairs, len(pairs)))
    every = {r["id"]: r for arts in ev.values() for r in arts.values()}
    selfs = [(eid, a, a, "full" if a["full_text"] else "summary") for eid, a, _, _ in rng.sample(pairs, min(args.nulls, len(pairs)))
             if a["full_text"] or a["summary"]]
    wires = [(None, every[x], every[y], "full" if every[x]["full_text"] and every[y]["full_text"] else "summary")
             for x, y in (tuple(t) for t in list(ties)[:args.nulls]) if x in every and y in every]

    outlet_ids = sorted({r["outlet_id"] for r in every.values()})
    judge = OpenAIJudge(settings, model=args.model) if args.judge == "openai" else SimulatedJudge(outlet_ids, seed=args.seed)
    model_name = getattr(judge, "model", "simulated")

    def ask(eid, a, b, kind, direction="positive"):
        req = ComparisonRequest(eid or "null", "both", a["outlet_id"], b["outlet_id"], a["id"], b["id"])
        if args.judge == "openai":
            v = judge.compare(rendered(a, hist, kind), rendered(b, hist, kind), direction, args.prompt)
        else:
            v = judge.compare_outlets(a["outlet_id"], b["outlet_id"], 1.0, direction)
        record(conn, "eval", req, kind, v, direction, eid, args.prompt)
        return v

    res = defaultdict(lambda: defaultdict(list))
    cost, calls, tin, tout = 0.0, 0, 0, 0
    for eid, a, b, kind in pairs:
        ab, ba, ab2, neg = ask(eid, a, b, kind), ask(eid, b, a, kind), ask(eid, a, b, kind), ask(eid, a, b, kind, "negative")
        longer_is_a = len(rendered(a, hist, kind)) >= len(rendered(b, hist, kind))
        for v in (ab, ba, ab2, neg):
            cost += v.cost_usd; calls += 1; tin += v.input_tokens; tout += v.output_tokens
        for ax in P.AXES:
            x, y, z, m = (v.answers[ax]["verdict"] for v in (ab, ba, ab2, neg))
            res[ax]["decisive"].append(x in ("A", "B"))
            res[ax]["na"].append(x == "na")
            if x in ("A", "B") and y in ("A", "B"):
                res[ax]["swap"].append(y == FLIP[x])
            res[ax]["retest"].append(x == z)
            if x in ("A", "B") and m in ("A", "B"):
                res[ax]["mirror"].append(m == FLIP[x])
            for verdict, first_longer in ((x, longer_is_a), (y, not longer_is_a)):
                if verdict in ("A", "B"):
                    res[ax]["first"].append(verdict == "A")
                    res[ax]["longer"].append((verdict == "A") == first_longer)
    for eid, a, b, kind in selfs + wires:
        v = ask(eid, a, b, kind)
        cost += v.cost_usd; calls += 1; tin += v.input_tokens; tout += v.output_tokens
        for ax in P.AXES:
            if args.prompt in ("v3", "v4"):         # identical texts, forced choice: no preference for the first-shown
                res[ax]["null_na"].append(v.answers[ax]["verdict"] == "na")
                if v.answers[ax]["verdict"] in ("A", "B"):
                    res[ax]["null_first"].append(v.answers[ax]["verdict"] == "A")
            else:
                res[ax]["null_same"].append(v.answers[ax]["verdict"] in ("same", "na"))
    conn.commit()

    targets = {"swap": (0.80, None), "retest": (0.80, None), "mirror": (0.80, None), "first": (0.40, 0.60),
               "longer": (0.40, 0.60), "null_same": (0.80, None), "null_first": (0.30, 0.70)}
    names = {"decisive": "decisive (A or B)", "na": "no angle (na)", "swap": "swap consistency", "retest": "retest agreement",
             "mirror": "mirror agreement (A28)", "first": "picks the first-shown (A04)", "longer": "picks the longer text (A41)",
             "null_same": "null pairs → same/na", "null_na": "identical pair → na",
             "null_first": "identical pair → picks the first-shown"}
    lines = [f"# 3.2 judge reliability — {model_name}, prompt {args.prompt}", "",
             f"{len(pairs)} story pairs × 4 asks + {len(selfs)} self pairs + {len(wires)} same-text pairs = {calls} calls · "
             f"${cost:.4f} (${cost / max(calls, 1):.5f}/call) · {tin / max(calls, 1):.0f} tokens in / {tout / max(calls, 1):.0f} out "
             f"per call · story run `{run}`", "",
             "| check | econ | social | target |", "|---|---|---|---|"]
    ok = True
    for key, label in names.items():
        cells = []
        for ax in P.AXES:
            m, n = share(res[ax][key])
            cells.append(f"{m:.2f} (n={n})" if n else "–")
            if key in targets and n:
                lo, hi = targets[key]
                ok &= m >= lo and (hi is None or m <= hi)
        lo, hi = targets.get(key, (None, None))
        t = "" if lo is None else (f"≥ {lo:.2f}" if hi is None else f"{lo:.2f}–{hi:.2f}")
        lines.append(f"| {label} | {cells[0]} | {cells[1]} | {t} |")
    lines += ["", f"**Verdict:** {'all targets met' if ok else 'some targets missed — read before measuring'}."]
    report = "\n".join(lines)
    out = f"reports/m7_judge_eval_{args.prompt}{'' if args.judge == 'openai' else '_simulated'}.md"
    open(out, "w").write(report + "\n")
    print(report + f"\n\nwrote {out}")


if __name__ == "__main__":
    main()
