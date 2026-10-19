"""H5: trajectories for the site — the positions the replay computed, day by day (A74), read back, never refitted.

    PYTHONPATH=. uv run python -m pipeline.history.trajectories [--db data/history/history.sqlite] \\
        [--bin-days 7] [--out data/history/trajectories.json]

Every outlet's and country's Bradley–Terry score per axis with its sd, as written by the replay each day
(history_positions, history_country_positions; pipeline.history.positions), one point per bin (its last day). An
outlet's line starts at its first verdict on that axis (before it, the score is only the prior); a country's at the
first verdict of any of its outlets. Only aggregates leave: no text, no titles (A10).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from pipeline.judge import prompt as P

DB = Path("data/history/history.sqlite")
BIN_DAYS = 7


def first_seen(conn, purpose: str = "history") -> dict[str, dict[str, str]]:
    """{axis: {outlet: the day of its first verdict}} (a verdict's day: its later article's publication day)."""
    rows = conn.execute("""SELECT j.axis, o.outlet, min(o.day) AS day FROM (
                               SELECT c.call_id, c.first_outlet AS outlet,
                                      substr(max(a.published, b.published), 1, 10) AS day FROM judge_calls c
                               JOIN articles a ON a.id = c.first_article JOIN articles b ON b.id = c.second_article
                               WHERE c.purpose = :p AND c.prompt_version = :v
                               UNION ALL
                               SELECT c.call_id, c.second_outlet,
                                      substr(max(a.published, b.published), 1, 10) FROM judge_calls c
                               JOIN articles a ON a.id = c.first_article JOIN articles b ON b.id = c.second_article
                               WHERE c.purpose = :p AND c.prompt_version = :v) o
                           JOIN judgments j ON j.call_id = o.call_id AND j.first_wins IS NOT NULL
                           GROUP BY j.axis, o.outlet""", {"p": purpose, "v": P.PROMPT_VERSION}).fetchall()
    out: dict[str, dict[str, str]] = {ax: {} for ax in P.AXES}
    for r in rows:
        out[r["axis"]][r["outlet"]] = r["day"]
    return out


def lines(rows, start: dt.date, bin_days: int, since: dict[str, str]) -> dict[str, list]:
    """{id: [[bin, mean, sd], …]} from (day, id, mean, sd) rows: each bin's last day, from the id's first day on."""
    last: dict[tuple[str, int], tuple] = {}
    for day, key, mean, sd in rows:
        if key in since and day >= since[key]:
            b = (dt.date.fromisoformat(day) - start).days // bin_days
            if (key, b) not in last or day > last[(key, b)][0]:
                last[(key, b)] = (day, mean, sd)
    out: dict[str, list] = {}
    for (key, b), (_, mean, sd) in sorted(last.items()):
        out.setdefault(key, []).append([b, round(mean, 4), round(sd, 4)])
    return out


def build(conn, groups: dict[str, dict[str, float]], bin_days: int = BIN_DAYS) -> dict:
    first = conn.execute("SELECT min(day) FROM history_positions").fetchone()[0]
    if first is None:
        raise SystemExit("no positions yet (pipeline.history.replay writes them day by day)")
    start = dt.date.fromisoformat(first)
    seen = first_seen(conn)
    out = {"start": start.isoformat(), "bin_days": bin_days, "positions": "the replay's day-by-day scores (A74)",
           "axes": {}}
    for ax in P.AXES:
        outl = conn.execute("SELECT day, outlet, mean, sd FROM history_positions WHERE axis = ?", (ax,)).fetchall()
        ctry = conn.execute("SELECT day, country, mean, sd FROM history_country_positions WHERE axis = ?", (ax,)).fetchall()
        c_since = {c: min(seen[ax][o] for o in g if o in seen[ax]) for c, g in groups.items()
                   if any(o in seen[ax] for o in g)}
        out["axes"][ax] = {"outlets": lines([tuple(r) for r in outl], start, bin_days, seen[ax]),
                           "countries": lines([tuple(r) for r in ctry], start, bin_days, c_since)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--bin-days", type=int, default=BIN_DAYS)
    ap.add_argument("--out", default="data/history/trajectories.json")
    args = ap.parse_args()
    from pipeline import store
    from pipeline.sources.registry import load_outlets, reference_groups
    conn = store.connect(args.db)
    out = build(conn, reference_groups(load_outlets()), args.bin_days)
    Path(args.out).write_text(json.dumps(out))
    for ax, a in out["axes"].items():
        print(f"{ax}: {len(a['outlets'])} outlets, {len(a['countries'])} countries")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
