"""What the history holds, per outlet and month: the articles fetched (2015-11 → now) and the pilot's comparisons — so
the countries and times that need more fetching are visible (coverage.html).

    PYTHONPATH=. uv run python frontend/prototypes/make_coverage_data.py   → frontend/prototypes/coverage.json

The article counts do not change while the pilot runs and take a scan of every article, so they are kept in
coverage-articles.json and counted again only with --recount (not counting rows whose title is not a headline, A76);
the comparisons are counted each time.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_demo_data import COUNTRIES, REGIONS          # noqa: E402

from pipeline.sources.registry import load_outlets   # noqa: E402

DB = Path("data/history/history.sqlite")
OUT = Path(__file__).with_name("coverage.json")
CACHE = Path(__file__).with_name("coverage-articles.json")


def months(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    out = []
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}")
        y, m = (y + (m == 12), m % 12 + 1)
    return out


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    if "--recount" in sys.argv or not CACHE.exists():
        from pipeline.history import quality                 # A76: rows whose title is not a headline do not count
        junk = quality.junk_titles(conn)
        n: dict[tuple[str, str], int] = {}
        for o, m, t, k in conn.execute("SELECT outlet_id, substr(published, 1, 7), title, count(*) FROM articles "
                                       "GROUP BY 1, 2, 3"):
            if (o, t) not in junk and not quality.is_wall(t):
                n[(o, m)] = n.get((o, m), 0) + k
        CACHE.write_text(json.dumps([[o, m, k] for (o, m), k in n.items()]))
    arts = {(o, m): n for o, m, n in json.loads(CACHE.read_text())}
    comp: dict[tuple[str, str], int] = {}
    for o, m, n in conn.execute("""SELECT o, m, count(*) FROM (
            SELECT c.first_outlet AS o, substr(max(a.published, b.published), 1, 7) AS m FROM judge_calls c
            JOIN articles a ON a.id = c.first_article JOIN articles b ON b.id = c.second_article
            UNION ALL SELECT c.second_outlet, substr(max(a.published, b.published), 1, 7) FROM judge_calls c
            JOIN articles a ON a.id = c.first_article JOIN articles b ON b.id = c.second_article) GROUP BY 1, 2"""):
        comp[(o, m)] = n
    span = sorted(m for _, m in arts if m and m >= "2015-11")
    ms = months("2015-11", span[-1])
    last_day = conn.execute("SELECT max(day) FROM history_progress").fetchone()[0]
    by_country: dict[str, list] = {}
    for o in load_outlets():
        by_country.setdefault(o.country, []).append(
            {"id": o.id, "name": o.name, "tier": o.tier, "articles": [arts.get((o.id, m), 0) for m in ms],
             "compared": [comp.get((o.id, m), 0) for m in ms]})
    data = {"meta": {"months": ms, "pilot_through": last_day, "regions": REGIONS},
            "countries": [{"id": c, "region": r, "outlets": by_country.get(c, [])} for c, _, r in COUNTRIES]}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.replace(tmp, OUT)
    print(f"{OUT}: {sum(len(c['outlets']) for c in data['countries'])} outlets × {len(ms)} months "
          f"(pilot through {last_day})")


if __name__ == "__main__":
    main()
