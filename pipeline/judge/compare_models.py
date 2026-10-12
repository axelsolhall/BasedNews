"""Does a cheaper judge model answer like the current one? (A11)

    uv run python -m pipeline.judge.compare_models --model gpt-5.4-nano [--pairs 40]

Re-asks the challenger on the same pairs, same order, same question as the reference model's first ask in the latest
v3 reliability eval, and compares with the reference model's own retest (same pair asked twice) as the ceiling.
Writes reports/e4_nano.json.
"""
from __future__ import annotations

import argparse
import json
import random

from pipeline import store
from pipeline.contracts import ComparisonRequest
from pipeline.judge import prompt as P
from pipeline.judge.client import OpenAIJudge
from pipeline.measure.run import load_items, record, rendered
from pipeline.settings import load_settings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.4-nano")
    ap.add_argument("--reference", default="gpt-5.4-mini")
    ap.add_argument("--pairs", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    conn = store.connect()
    calls = conn.execute("""SELECT call_id, first_article, second_article, event_id, text_kind, at FROM judge_calls
                            WHERE purpose = 'eval' AND prompt_version = 'v3' AND model = ? AND direction = 'positive'
                            AND event_id IS NOT NULL AND first_article != second_article ORDER BY at""",
                         (args.reference,)).fetchall()
    by_pair = {}
    for c in calls:                                        # first and second ask of the same pair, same order
        by_pair.setdefault((c["first_article"], c["second_article"]), []).append(c)
    pairs = [v for v in by_pair.values() if len(v) >= 2]
    pairs = random.Random(args.seed).sample(pairs, min(args.pairs, len(pairs)))
    ans = lambda cid: {r["axis"]: r["verdict"] for r in conn.execute("SELECT axis, verdict FROM judgments WHERE call_id = ?", (cid,))}
    _, ev, hist, _ = load_items(conn)
    arts = {r["id"]: r for a in ev.values() for r in a.values()}
    judge = OpenAIJudge(load_settings(), model=args.model)
    agree_new, agree_ref, cost, n = {ax: [] for ax in P.AXES}, {ax: [] for ax in P.AXES}, 0.0, 0
    for first, second in pairs:
        a, b = arts.get(first["first_article"]), arts.get(first["second_article"])
        if not a or not b:
            continue
        v = judge.compare(rendered(a, hist, first["text_kind"]), rendered(b, hist, first["text_kind"]), "positive", "v3")
        record(conn, "eval-model", ComparisonRequest(first["event_id"], "both", a["outlet_id"], b["outlet_id"], a["id"], b["id"]),
               first["text_kind"], v, "positive", first["event_id"], "v3")
        cost += v.cost_usd; n += 1
        r1, r2 = ans(first["call_id"]), ans(second["call_id"])
        for ax in P.AXES:
            agree_new[ax].append(v.answers[ax]["verdict"] == r1.get(ax))
            agree_ref[ax].append(r2.get(ax) == r1.get(ax))
    conn.commit()
    out = {"challenger": args.model, "reference": args.reference, "pairs": n, "cost_usd": round(cost, 4),
           "agreement_with_reference": {ax: round(sum(x) / len(x), 3) for ax, x in agree_new.items() if x},
           "reference_retest_agreement": {ax: round(sum(x) / len(x), 3) for ax, x in agree_ref.items() if x}}
    json.dump(out, open("reports/e4_nano.json", "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
