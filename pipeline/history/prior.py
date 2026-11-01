"""The history's final positions as live's prior (A79, A82): every outlet's mean and sd per axis on the history's last
day, its number of history comparisons and its centre worth (A08: comparisons decayed by HALF_LIFE_DAYS).

    PYTHONPATH=. uv run python -m pipeline.history.prior --to <live.sqlite>        # into prior_positions (A79)
    PYTHONPATH=. uv run python -m pipeline.history.prior --json                    # → pipeline/measure/prior.json (A82)

Retention reads prior_positions to value articles (pipeline.retention.run.trash: the chance two outlets are in the
wrong order); live judging starts from prior.json (pipeline.measure.live), which ships with the code — aggregates only.
Foreign editions' verdicts are not counted (A81). Eval: both print what they wrote.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

from pipeline import store
from pipeline.history import positions as HP

HISTORY_DB = "data/history/history.sqlite"
PRIOR_JSON = Path("pipeline/measure/prior.json")


def final_positions(history_db: str = HISTORY_DB) -> tuple[str, list[tuple[str, str, float, float]]]:
    """(day, [(outlet, axis, mean, sd)]) of the history's last day of positions."""
    h = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True, timeout=60)
    day = h.execute("SELECT max(day) FROM history_positions").fetchone()[0]
    if day is None:
        raise SystemExit("the history has no positions yet")
    rows = h.execute("SELECT outlet, axis, mean, sd FROM history_positions WHERE day = ? ORDER BY outlet, axis",
                     (day,)).fetchall()
    return day, rows


def comparisons(history_db: str, day: str) -> dict[str, tuple[int, float]]:
    """outlet → (history comparisons it is in, their worth on `day`: each decayed by its age, A08), A81 left out."""
    h = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True, timeout=60)
    t = dt.date.fromisoformat(day)
    out: dict[str, list] = {}
    for a, b, d, k in h.execute(f"""SELECT c.first_outlet, c.second_outlet, substr(c.event_id, 1, 10), count(*)
                                    FROM judge_calls c WHERE c.purpose = 'history' AND {HP.not_foreign_call('c')}
                                    GROUP BY 1, 2, 3"""):
        w = k * 0.5 ** ((t - dt.date.fromisoformat(d)).days / HP.HALF_LIFE_DAYS)
        for o in (a, b):
            s = out.setdefault(o, [0, 0.0])
            s[0] += k
            s[1] += w
    return {o: (n, w) for o, (n, w) in out.items()}


def write(conn, day: str, rows, source: str = "history") -> int:
    with conn:
        conn.execute("DELETE FROM prior_positions")
        conn.executemany("INSERT INTO prior_positions VALUES (?,?,?,?,?,?)",
                         [(o, ax, m, sd, day, source) for o, ax, m, sd in rows])
    return len(rows)


def write_json(path: Path, history_db: str = HISTORY_DB) -> dict:
    day, rows = final_positions(history_db)
    h = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True, timeout=60)
    first = h.execute("SELECT min(day) FROM history_positions").fetchone()[0]
    n = comparisons(history_db, day)
    outlets: dict[str, dict] = {}
    for o, ax, m, sd in rows:
        k, w = n.get(o, (0, 0.0))
        outlets.setdefault(o, {"n": k, "worth": round(w, 3)})[ax] = [round(m, 6), round(sd, 6)]
    data = {"as_of": day, "first": first, "source": "history (A74, A81)", "outlets": outlets}
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", help="the live database to write prior_positions into")
    ap.add_argument("--json", nargs="?", const=str(PRIOR_JSON), help=f"write the prior file (default {PRIOR_JSON})")
    ap.add_argument("--history", default=HISTORY_DB)
    a = ap.parse_args()
    if not a.to and not a.json:
        ap.error("give --to and/or --json")
    if a.to:
        day, rows = final_positions(a.history)
        n = write(store.connect(a.to), day, rows)
        sds = sorted(r[3] for r in rows)
        print(f"prior_positions ← history {day}: {n} rows, {len({r[0] for r in rows})} outlets · "
              f"sd median {sds[len(sds) // 2]:.3f}, max {sds[-1]:.3f}")
    if a.json:
        d = write_json(Path(a.json), a.history)
        ns = sorted(v["n"] for v in d["outlets"].values())
        print(f"{a.json} ← history {d['first']} → {d['as_of']}: {len(ns)} outlets · comparisons median "
              f"{ns[len(ns) // 2]:,}, total {sum(ns) // 2:,}")


if __name__ == "__main__":
    main()
