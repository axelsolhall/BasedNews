"""Replay the stored E4 verdicts as plane frames for the site (free: no API calls).

    uv run python -m pipeline.experiments.e4_frames [--every 40]

Writes frontend/src/assets/plane-real.json: positions refitted after every N calls, in the order the judge made them
(first the 338 US/UK calls, then all countries). Relative scale, ±1 sd per axis (A25, A26). Real judge, no truth.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline import store
from pipeline.aggregate.plane import _contrast_stats, dumps_site
from pipeline.experiments.e4 import A
from pipeline.rate.model import BradleyTerryLaplace
from pipeline.sources.registry import load_outlets, reference_groups

OUT = Path("frontend/src/assets/plane-real.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=40)
    ap.add_argument("--since", default=json.loads(Path("reports/e4_run.json").read_text())["e4_start"])
    args = ap.parse_args()
    conn = store.connect()
    reg = load_outlets()
    names = {o.id: o for o in reg}
    calls = conn.execute("""SELECT call_id, first_outlet f, second_outlet s, text_kind k, cost_usd c, input_tokens i,
                                   output_tokens o FROM judge_calls WHERE purpose = 'measure' AND prompt_version = 'v3'
                            AND at >= ? ORDER BY at, rowid""", (args.since,)).fetchall()
    verdicts = {}
    for r in conn.execute("SELECT call_id, axis, first_wins FROM judgments WHERE first_wins IS NOT NULL"):
        verdicts.setdefault(r["call_id"], {})[r["axis"]] = bool(r["first_wins"])
    groups = reference_groups(reg)
    outlets = sorted({c["f"] for c in calls} | {c["s"] for c in calls} | {o for g in groups.values() for o in g})
    model = BradleyTerryLaplace(outlets)
    frames, cost, tokens = [], 0.0, 0
    stops = list(range(args.every, len(calls), args.every)) + [len(calls)]
    k = 0
    for stop in [0] + stops:
        for c in calls[k:stop]:
            cost += c["c"] or 0.0; tokens += (c["i"] or 0) + (c["o"] or 0)
        k = stop
        upto = calls[:stop]
        posts = {}
        for ax in ("econ", "social"):
            rs = [(c, verdicts.get(c["call_id"], {}).get(ax)) for c in upto]
            rs = [(c, w) for c, w in rs if w is not None]
            posts[ax] = model.fit([(c["f"], c["s"]) for c, _ in rs], [w for _, w in rs], [A[c["k"]] for c, _ in rs])
        o_pts = []
        for o in outlets:
            x, sx = _contrast_stats(posts["econ"], {o: 1.0}); y, sy = _contrast_stats(posts["social"], {o: 1.0})
            o_pts.append({"id": o, "label": names[o].name, "country": names[o].country, "tier": names[o].tier,
                          "x": x, "y": y, "sd_x": sx, "sd_y": sy})
        c_pts = []
        for country, w in groups.items():
            x, sx = _contrast_stats(posts["econ"], w); y, sy = _contrast_stats(posts["social"], w)
            c_pts.append({"id": country, "label": country, "country": country, "x": x, "y": y, "sd_x": sx, "sd_y": sy})
        frames.append({"tokens": tokens, "calls": stop, "cost_usd": round(cost, 4), "outlets": o_pts, "countries": c_pts})
    OUT.write_text(dumps_site({"meta": {"source": "real", "experiment": "E4", "model": "gpt-5.4-mini", "prompt": "v3",
                                        "since": args.since, "calls": len(calls), "cost_usd": round(cost, 2)},
                               "real": frames}))
    print(f"wrote {OUT}: {len(frames)} frames, {len(calls)} calls, ${cost:.2f}")


if __name__ == "__main__":
    main()
