"""3 · Showcase (A85): one judgment a day for the site's example card — the day's top story, two outlets that disagree,
the judge's verdicts and its reasons. Shown only, never measured: purpose "showcase", which no position reads.

    uv run python -m pipeline.measure.showcase --dry-run            # the story and the pair it would ask about; no call
    uv run python -m pipeline.measure.showcase --budget-usd 0.02    # real: one OpenAI call (prompt v4r)

The story: in the newest story run, the event whose drawn outlets (A83: ≥ THRESHOLD history comparisons) span the most
countries, then the most outlets; never one shown before. The pair: two drawn outlets of different countries in it whose
positions today (prior_positions) are farthest apart, both with full text if the story has such a pair. The question is
the live prompt's (v4) with one short reason per axis (v4r). The card is kept in `showcase` as shown — headline, link,
date, verdict, reason; never article text (A10) — so it outlives its articles; export_json writes the newest N.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from pipeline import store
from pipeline.export import frames as F
from pipeline.judge import prompt as P
from pipeline.judge.client import JudgeCallFailed, OpenAIJudge
from pipeline.measure.live import PRIOR, today
from pipeline.measure.run import candidates, record, record_failure, relevance_so_far
from pipeline.sources.registry import load_outlets

PURPOSE = "showcase"
VERSION = "v4r"
N = 10                                  # examples on the card: today's first
OUT_NAME = "live-judgments.json"        # data/live*: the job's prefix (CI owns data/judgments.json, the seed examples)


def drawn(prior: Path | None = None) -> set[str]:
    pr = json.loads((prior or PRIOR).read_text())
    ents, _ = F.entities({o: v["n"] for o, v in pr["outlets"].items()})
    return {e["id"] for e in ents if e["kind"] == "o"}


def pick(conn, settings, prior: Path | None = None):
    """(candidate, texts, countries) for today's example, or None when no story has two drawn outlets of two countries."""
    shown = drawn(prior)
    country = {o.id: o.country for o in load_outlets()}
    pos = defaultdict(dict)
    for o, ax, m in conn.execute("SELECT outlet_id, axis, mean FROM prior_positions"):
        pos[o][ax] = m
    before = {e.rsplit(":", 1)[0] for (e,) in conn.execute(
        "SELECT event_id FROM judge_calls WHERE purpose = ? AND event_id IS NOT NULL", (PURPOSE,))}
    cands, ctx = candidates(conn, settings, relevance_so_far(conn, PURPOSE), PURPOSE)
    by_event = defaultdict(list)
    for c in cands:
        eid = c.event_id.rsplit(":", 1)[0]
        if eid in before or c.outlet_a not in shown or c.outlet_b not in shown:
            continue
        if country[c.outlet_a] == country[c.outlet_b]:
            continue
        by_event[eid].append(c)
    if not by_event:
        return None

    def span(eid):
        outs = {o for c in by_event[eid] for o in (c.outlet_a, c.outlet_b)}
        return len({country[o] for o in outs}), len(outs)

    eid = max(by_event, key=span)

    def apart(c):
        a, b = pos.get(c.outlet_a, {}), pos.get(c.outlet_b, {})
        d = math.dist([a.get(ax, 0.0) for ax in P.AXES], [b.get(ax, 0.0) for ax in P.AXES])
        return (c.event_id.endswith(":full"), d)                 # full text first, then the farthest apart

    best = max(by_event[eid], key=apart)
    return best, ctx["texts"], span(eid)[0]


def example(conn, call_id: str, c, verdict) -> dict:
    """The card as the page shows it (judgment.js): outlets, headlines, links, dates, verdicts, reasons (A/B → names)."""
    reg = {o.id: o for o in load_outlets()}
    side = []
    for aid in (c.article_a, c.article_b):
        a = conn.execute("SELECT outlet_id, country, title, url, published FROM articles WHERE id = ?", (aid,)).fetchone()
        o = reg.get(a["outlet_id"])
        side.append({"outlet": o.name if o else a["outlet_id"], "outlet_id": a["outlet_id"], "country": a["country"],
                     "title": a["title"], "url": a["url"], "published": (a["published"] or "")[:10]})
    axes = {}
    for ax, ans in verdict.answers.items():
        fw = P.first_wins(ans["verdict"], "positive")
        axes[ax] = {"decided": ans["verdict"] in ("A", "B"), "same": False, "further": None if fw is None else (0 if fw else 1),
                    "reason": re.sub(r"\b(?:Article |article )?([AB])\b", lambda m: side[m.group(1) == "B"]["outlet"],
                                     ans["reason"])}
    return {"id": call_id[:8], "at": today(), "featured": True, "source": "daily", "model": verdict.model,
            "articles": side, "axes": axes}


def export_json(conn, out: Path, n: int = N) -> dict:
    """The newest n daily examples → out/live-judgments.json (the page puts them before the seed examples)."""
    rows = conn.execute("SELECT example FROM showcase ORDER BY day DESC, rowid DESC LIMIT ?", (n,)).fetchall()
    data = {"meta": {"source": f"judge_calls purpose={PURPOSE}, prompt {VERSION}", "note": "one a day, shown not measured (A85)"},
            "examples": [json.loads(r[0]) for r in rows]}
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / (OUT_NAME + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(out / OUT_NAME)
    return data


def main():
    from pipeline.settings import load_settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-usd", type=float, default=0.02, help="the call is skipped if its estimated cost is above")
    ap.add_argument("--dry-run", action="store_true", help="the story and the pair; no call, nothing saved")
    args = ap.parse_args()
    settings = load_settings()
    conn = store.connect()
    got = pick(conn, settings)
    if got is None:
        print("showcase: no story with two drawn outlets of two countries today — nothing to show")
        return
    c, texts, n_countries = got
    print(f"showcase {today()}: {c.event_id} ({n_countries} countries) · {c.outlet_a} vs {c.outlet_b} · est ${c.cost:.4f}")
    if args.dry_run:
        return
    budget = min(args.budget_usd, max(0.0, settings.monthly_ai_usd - store.spent_this_month(conn)))
    if c.cost > budget:
        print(f"skipped: estimated ${c.cost:.4f} > budget ${budget:.4f}")
        return
    req = SimpleNamespace(first_article_id=c.article_a, second_article_id=c.article_b, first_outlet=c.outlet_a,
                          second_outlet=c.outlet_b, event_id=c.event_id)
    context = {"live_day": today(), "countries": n_countries, "prompt_version": VERSION}
    try:
        v = OpenAIJudge(settings).compare(texts[(c.event_id, c.article_a)], texts[(c.event_id, c.article_b)],
                                          "positive", version=VERSION)
    except JudgeCallFailed as e:
        record_failure(conn, PURPOSE, req, e, context)
        conn.commit()
        raise SystemExit(f"showcase call failed: {e}")
    call_id = record(conn, PURPOSE, req, c.event_id.rsplit(":", 1)[1], v, "positive", event_id=c.event_id,
                     version=VERSION, context=context)
    ex = example(conn, call_id, c, v)
    conn.execute("INSERT INTO showcase VALUES (?,?,?)", (call_id, today(), json.dumps(ex, ensure_ascii=False)))
    conn.commit()
    print(f"spent ${v.cost_usd:.4f} · " + " · ".join(
        f"{ax}: {'—' if not d['decided'] else ex['articles'][d['further']]['outlet']}" for ax, d in ex["axes"].items()))


if __name__ == "__main__":
    main()
