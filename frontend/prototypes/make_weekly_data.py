"""What the site's data section shows (story-guided.html, data.js): per outlet and country and week, the articles we hold
and the pairs that can be compared — the history (2015-11 → the live start) and then the live pipeline (A77).

    PYTHONPATH=. uv run python frontend/prototypes/make_weekly_data.py [--live-db prod.sqlite]  → frontend/prototypes/weekly.json

Articles: rows that are articles (kind IS NULL: not tagged as junk, A76), by the week of their publish time. Pairs: two
articles of different outlets in one story, counted in the week of the later one — the history's dry run
(history_pair_counts: per outlet, and per country once per pair) and live's candidates. A week is history before the
first week of live articles and live from then on, so nothing is counted twice; the week in progress is left out.
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_demo_data import COUNTRIES, REGIONS          # noqa: E402

from pipeline.sources.registry import load_outlets   # noqa: E402

DB = Path("data/history/history.sqlite")
OUT = Path(__file__).with_name("weekly.json")
FIRST = dt.date(2015, 10, 26)                        # the Monday of the history's first week (it starts 2015-11-01)


def week(day: str) -> int:
    return (dt.date.fromisoformat(day[:10]) - FIRST).days // 7


def articles(conn) -> Counter:
    """(outlet, week) → articles."""
    n = Counter()
    for o, d, k in conn.execute("SELECT outlet_id, substr(published, 1, 10), count(*) FROM articles "
                                "WHERE kind IS NULL AND published >= ? GROUP BY 1, 2", (FIRST.isoformat(),)):
        n[(o, week(d))] += k
    return n


def history_pairs(conn) -> tuple[Counter, Counter]:
    """(outlet, week) → pairs and (country, week) → pairs, from the dry run."""
    by_o, by_c = Counter(), Counter()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(history_pair_counts)")}
    if "by_country" not in cols:                      # the dry run has not run yet (replay.prepare adds it)
        return by_o, by_c
    days = Counter()
    for day, bo, bc in conn.execute("SELECT day, by_outlet, by_country FROM history_pair_counts WHERE new_pairs IS NOT NULL"):
        w = week(day)
        days[w] += 1
        for o, k in json.loads(bo or "{}").items():
            by_o[(o, w)] += k
        for c, k in json.loads(bc or "{}").items():
            by_c[(c, w)] += k
    whole = {w for w, n in days.items() if n == 7}     # a week still being counted would show as a false drop
    return Counter({k: v for k, v in by_o.items() if k[1] in whole}), Counter({k: v for k, v in by_c.items() if k[1] in whole})


def live_pairs(conn, country: dict) -> tuple[Counter, Counter]:
    """The same counts from live's database: its candidates (one per article pair), in the week of the later article."""
    from pipeline.measure.run import candidates, relevance_so_far
    from pipeline.settings import load_settings
    cands, _ = candidates(conn, load_settings(), relevance_so_far(conn, "measure"), "measure")
    pairs = {}
    for c in cands:                                   # a pair can come once per text kind: count it once
        pairs[frozenset((c.article_a, c.article_b))] = (c.outlet_a, c.outlet_b)
    pub = dict(conn.execute("SELECT id, published FROM articles").fetchall())
    by_o, by_c = Counter(), Counter()
    for arts, (a, b) in pairs.items():
        w = week(max(pub[x] for x in arts))
        by_o[(a, w)] += 1
        by_o[(b, w)] += 1
        for k in {country.get(a), country.get(b)} - {None}:
            by_c[(k, w)] += 1
    return by_o, by_c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--live-db", help="a copy of live's database (prod/db/basednews.sqlite)")
    a = ap.parse_args()
    outlets = load_outlets()
    country = {o.id: o.country for o in outlets}
    hist = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=60)
    h_art, (h_po, h_pc) = articles(hist), history_pairs(hist)
    l_art, l_po, l_pc, live_from = Counter(), Counter(), Counter(), None
    if a.live_db:
        from pipeline import store
        live = store.connect(a.live_db)
        l_art = articles(live)
        live_from = min((w for _, w in l_art), default=None)
        l_po, l_pc = live_pairs(live, country)
    now = (dt.date.today() - FIRST).days // 7            # the week in progress: left out
    n = now
    pick = lambda h, l, key, w: (l if live_from is not None and w >= live_from else h).get((key, w), 0)  # noqa: E731
    series = lambda h, l, key: [pick(h, l, key, w) for w in range(n)]                                    # noqa: E731
    by_country: dict[str, list] = {}
    for o in outlets:
        by_country.setdefault(o.country, []).append(
            {"id": o.id, "name": o.name, "tier": o.tier,
             "articles": series(h_art, l_art, o.id), "pairs": series(h_po, l_po, o.id)})
    rows = []
    for c, _, r in COUNTRIES:
        os_ = by_country.get(c, [])
        rows.append({"id": c, "region": r, "pairs": series(h_pc, l_pc, c),
                     "articles": [sum(o["articles"][w] for o in os_) for w in range(n)], "outlets": os_})
    weeks = sorted({w for _, w in h_pc})                 # the weeks whose seven days are all counted
    counted = ((FIRST + dt.timedelta(weeks=weeks[0])).isoformat(), (FIRST + dt.timedelta(weeks=weeks[-1] + 1)).isoformat()) \
        if weeks else (None, None)
    data = {"meta": {"first_week": FIRST.isoformat(), "weeks": n, "regions": REGIONS, "updated": dt.date.today().isoformat(),
                     "live_from": None if live_from is None else (FIRST + dt.timedelta(weeks=live_from)).isoformat(),
                     "history_pairs_counted": list(counted)},
            "countries": rows}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.replace(tmp, OUT)
    print(f"{OUT}: {sum(len(c['outlets']) for c in rows)} outlets × {n} weeks · history pairs counted {counted[0]} → "
          f"{counted[1]} · live from {data['meta']['live_from']} · {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
