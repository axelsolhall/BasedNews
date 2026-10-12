"""M2 eval: per outlet — volume, freshness, text extraction success, duplicates, date parsing.

    uv run python -m pipeline.ingest.eval [--sample OUTLET_ID]
Pass criteria (per active outlet): >=5 articles in last 72 h; text OR summary on >=90 % and dated >=95 % of live-ingest articles.
"""
from __future__ import annotations

import argparse
import datetime as dt

from pipeline import store
from pipeline.sources.registry import load_outlets

# Pass criteria per active outlet (shared with pipeline.ingest.report). Text and date checks look at live-ingest
# articles only: they judge the ingest that runs from now on, not archive or pre-pipeline data, and not articles
# whose text retention has already deleted (A52).
LIVE_SINCE = "2026-10-01"     # pipeline RSS ingest started 2026-10-10; everything ingested earlier is legacy
MIN_RECENT_72H = 5
MIN_ANY_TEXT_PCT = 90
MIN_DATED_PCT = 95


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", help="print 3 recent articles for one outlet")
    args = ap.parse_args()
    conn = store.connect()
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = {r["outlet_id"]: r for r in conn.execute("""
        SELECT outlet_id, COUNT(*) n,
               SUM(published >= ?) recent,
               SUM(full_text IS NOT NULL) has_text,
               SUM(live) n_live,
               SUM(live AND (full_text IS NOT NULL OR summary IS NOT NULL)) has_any,
               SUM(live AND published LIKE '____-__-__T%Z') dated,
               COUNT(DISTINCT lower(title)) uniq_titles,
               AVG(LENGTH(full_text)) avg_len
        FROM (SELECT *, (text_source NOT LIKE 'wayback-%' AND ingested_at >= ? AND pruned_at IS NULL) live FROM articles
              WHERE canonical_id IS NULL AND kind IS NULL)          -- copies and non-articles excluded (A34, A38)
        GROUP BY outlet_id""", (since, LIVE_SINCE))}

    fails = []
    print(f"{'outlet':<16}{'total':>7}{'72h':>6}{'text%':>7}{'any%':>6}{'dated%':>8}{'dup%':>6}{'avg_chars':>10}  status")
    for o in load_outlets():
        r = rows.get(o.id)
        if r is None:
            print(f"{o.id:<16}{0:>7}  no articles"); fails.append(o.id); continue
        n = r["n"]
        pct = lambda k: 100 * (r[k] or 0) / n
        lpct = lambda k: 100 * (r[k] or 0) / r["n_live"] if r["n_live"] else 0.0
        dup = 100 * (1 - r["uniq_titles"] / n)
        ok = ((r["recent"] or 0) >= MIN_RECENT_72H and lpct("has_any") >= MIN_ANY_TEXT_PCT
              and lpct("dated") >= MIN_DATED_PCT)
        if not ok:
            fails.append(o.id)
        print(f"{o.id:<16}{n:>7}{r['recent'] or 0:>6}{pct('has_text'):>7.0f}{lpct('has_any'):>6.0f}{lpct('dated'):>8.0f}"
              f"{dup:>6.1f}{(r['avg_len'] or 0):>10.0f}  {'OK' if ok else 'CHECK'}")
    print(f"\nOutlets needing attention: {len(fails)} {fails}")

    if args.sample:
        for a in conn.execute("SELECT published, title, text_source, substr(coalesce(full_text, summary, ''), 1, 200) t "
                              "FROM articles WHERE outlet_id = ? ORDER BY published DESC LIMIT 3", (args.sample,)):
            print(f"\n[{a['published']}] {a['title']}  ({a['text_source']})\n  {a['t']}…")


if __name__ == "__main__":
    main()
