"""M2b eval: historical coverage grid — articles per outlet per year, and job status counts.

    uv run python -m pipeline.backfill.eval
Good: every outlet has ≥ ~6 articles in most years; same-country outlets share dates (A23).
"""
from __future__ import annotations

from collections import defaultdict

from pipeline import store
from pipeline.backfill.run import JOBS_SCHEMA
from pipeline.sources.registry import load_outlets


def main():
    conn = store.connect()
    conn.execute(JOBS_SCHEMA)
    counts = defaultdict(lambda: defaultdict(int))
    for r in conn.execute("SELECT outlet_id, substr(published,1,4) y, COUNT(*) n FROM articles "
                          "WHERE text_source LIKE 'wayback-%' GROUP BY 1, 2"):
        counts[r["outlet_id"]][r["y"]] = r["n"]
    years = sorted({y for c in counts.values() for y in c})
    print(f"{'outlet':<16}" + "".join(f"{y:>6}" for y in years) + "   text%")
    for o in load_outlets():
        row = counts.get(o.id, {})
        tx = conn.execute("SELECT AVG(full_text IS NOT NULL)*100 FROM articles WHERE outlet_id=? AND text_source LIKE 'wayback-%'",
                          (o.id,)).fetchone()[0]
        print(f"{o.id:<16}" + "".join(f"{row.get(y, 0):>6}" for y in years) + f"   {tx or 0:>4.0f}")
    print()
    for r in conn.execute("SELECT status, COUNT(*) n, SUM(n_articles) a FROM backfill_jobs GROUP BY status"):
        print(f"jobs {r['status']:<6} {r['n']:>6}  articles {r['a'] or 0}")


if __name__ == "__main__":
    main()
