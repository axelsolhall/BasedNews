"""Pilot data for the prototypes: the outlets' and countries' positions the history replay computed (A74), one point per
week (its last processed day), up to the last day processed — no fitting here, only reading.

    PYTHONPATH=. uv run python frontend/prototypes/make_pilot_data.py   → frontend/prototypes/pilot-trends.json

Same shape as demo-trends.json (make_demo_data.py), plus each country's own series ("s", from
history_country_positions) and meta.updated, which the page polls to pick up new days while the pilot runs.
"""
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_demo_data import COUNTRIES, REGIONS          # noqa: E402  (the page's country list: names, ISO ids, regions)

from pipeline.sources.registry import load_outlets   # noqa: E402

DB = Path("data/history/history.sqlite")
OUT = Path(__file__).with_name("pilot-trends.json")
BIN_DAYS = 7


def series(rows, days: list[str], key: str) -> dict[str, list]:
    """{id: [[econ, social, sd econ, sd social] per day in `days`]} from (day, axis, id, mean, sd) rows."""
    rows = list(rows)
    got: dict[tuple[str, str, str], tuple[float, float]] = {(r[0], r[1], r[2]): (r[3], r[4]) for r in rows}
    ids = sorted({r[2] for r in rows})
    out = {}
    for i in ids:
        pts = []
        for d in days:
            e, s = got.get((d, "econ", i)), got.get((d, "social", i))
            if e is None or s is None:
                break
            pts.append([round(e[0], 4), round(s[0], 4), round(e[1], 4), round(s[1], 4)])
        if len(pts) == len(days):
            out[i] = pts
    return out


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    done = [r[0] for r in conn.execute("SELECT DISTINCT day FROM history_positions ORDER BY day")]
    if not done:
        raise SystemExit("no pilot positions yet")
    first = dt.date.fromisoformat(done[0])
    last_of_bin: dict[int, str] = {}
    for d in done:
        last_of_bin[(dt.date.fromisoformat(d) - first).days // BIN_DAYS] = d
    days = [last_of_bin[b] for b in sorted(last_of_bin)]
    q = ",".join("?" * len(days))
    outl = series(conn.execute(f"SELECT day, axis, outlet, mean, sd FROM history_positions WHERE day IN ({q})", days),
                  days, "outlet")
    ctry = series(conn.execute(f"SELECT day, axis, country, mean, sd FROM history_country_positions WHERE day IN ({q})",
                               days), days, "country")
    data = {"meta": {"demo": False, "pilot": True, "updated": done[-1], "first": done[0], "bin_days": BIN_DAYS,
                     "note": "2019 pilot: the replay's day-by-day positions (A74), one point per week"},
            "months": days, "regions": REGIONS,
            "countries": [{"id": c, "name": c, "iso": i, "region": r, "measured": c in ctry, "s": ctry.get(c)}
                          for c, i, r in COUNTRIES if c in ctry],
            "outlets": [{"id": o.id, "name": o.name, "country": o.country, "tier": o.tier, "s": outl[o.id]}
                        for o in load_outlets() if o.id in outl]}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.replace(tmp, OUT)
    print(f"{OUT}: {len(data['outlets'])} outlets, {len(data['countries'])} countries, {len(days)} weeks "
          f"({done[0]} → {done[-1]})")


if __name__ == "__main__":
    main()
