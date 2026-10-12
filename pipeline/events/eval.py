"""M5 eval: event matching quality against the hand labels + the comparable-pair pool that feeds the planner (M6).

    uv run --group ml python -m pipeline.events.eval
1) Pair-level precision / recall / F1 for both clustering methods over a threshold sweep (needs labels).
2) Pool stats for the latest run: events with ≥2 outlets, cross-country events, comparable pairs per day
   — the real number that caps the active-learning savings measured in P1.
"""
from __future__ import annotations

import json
from collections import defaultdict


from pipeline import store
from pipeline.events.cluster import ClusterConfig, cluster
from pipeline.events.label import OUT
from pipeline.events.run import load_window


def prf(labels_by_id, gold):
    tp = fp = fn = 0
    for a, b, same in gold:
        if a not in labels_by_id or b not in labels_by_id:
            continue
        pred = labels_by_id[a] == labels_by_id[b]
        tp += pred and same; fp += pred and not same; fn += (not pred) and same
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0), tp + fp + fn


def main():
    conn = store.connect()
    rows, V, hours = load_window(conn, 7)
    ids = [r["id"] for r in rows]

    if OUT.exists():
        gold = [(d["a"], d["b"], d["same"]) for d in map(json.loads, OUT.read_text().splitlines())]
        print(f"Labelled pairs: {len(gold)} ({sum(g[2] for g in gold)} same-event)\n")
        print(f"{'method':<12}{'thr':>6}{'precision':>11}{'recall':>8}{'F1':>7}")
        for method in ("components", "average"):
            for t in (0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
                lab = cluster(V, hours, ClusterConfig(method=method, threshold=t))
                p, r, f, _ = prf(dict(zip(ids, lab)), gold)
                print(f"{method:<12}{t:>6.2f}{p:>11.2f}{r:>8.2f}{f:>7.2f}")
        print()
    else:
        print("No labels yet — run `python -m pipeline.events.label` (≈15 min) to get precision/recall.\n")

    run = store.latest_event_run(conn)
    if not run:
        print("No event run yet — run pipeline.events.run"); return
    members = defaultdict(list)
    info = {r["id"]: r for r in rows}
    for r in conn.execute("SELECT event_id, article_id FROM event_members WHERE run_id=?", (run,)):
        if r["article_id"] in info:
            members[r["event_id"]].append(info[r["article_id"]])
    multi = {e: m for e, m in members.items() if len({x["outlet_id"] for x in m}) >= 2}
    cross = {e: m for e, m in multi.items() if len({x["country"] for x in m}) >= 2}
    pairs = sum(len({x["outlet_id"] for x in m}) * (len({x["outlet_id"] for x in m}) - 1) // 2 for m in multi.values())
    xpairs = sum(1 for m in cross.values() for i, a in enumerate({x["outlet_id"]: x for x in m}.values())
                 for b in list({x["outlet_id"]: x for x in m}.values())[i + 1:] if a["country"] != b["country"])
    span_days = max(1.0, (hours.max() - hours.min()) / 24) if len(hours) else 1.0
    sizes = sorted((len(m) for m in members.values()), reverse=True)
    print(f"Latest run: {run}")
    print(f"  articles {len(ids)} · events {len(members)} · events with ≥2 outlets {len(multi)} · cross-country {len(cross)}")
    print(f"  largest clusters (articles): {sizes[:5]}   ← a huge first number means chaining")
    print(f"  comparable outlet pairs (A02): {pairs} total ≈ {pairs / span_days:.0f}/day; cross-country {xpairs} ≈ {xpairs / span_days:.0f}/day")


if __name__ == "__main__":
    main()
