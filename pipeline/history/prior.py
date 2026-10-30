"""The history's final positions as live's prior (A79): every outlet's mean and sd per axis on the history's last day,
written into a live database's `prior_positions` (one row per outlet and axis, replaced as a whole).

    PYTHONPATH=. uv run python -m pipeline.history.prior --to <live.sqlite>        # eval: prints what it wrote

Retention reads it to value articles (pipeline.retention.run.trash: the chance two outlets are in the wrong order).
"""
from __future__ import annotations

import argparse
import sqlite3

from pipeline import store

HISTORY_DB = "data/history/history.sqlite"


def final_positions(history_db: str = HISTORY_DB) -> tuple[str, list[tuple[str, str, float, float]]]:
    """(day, [(outlet, axis, mean, sd)]) of the history's last day of positions."""
    h = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True, timeout=60)
    day = h.execute("SELECT max(day) FROM history_positions").fetchone()[0]
    if day is None:
        raise SystemExit("the history has no positions yet")
    rows = h.execute("SELECT outlet, axis, mean, sd FROM history_positions WHERE day = ? ORDER BY outlet, axis",
                     (day,)).fetchall()
    return day, rows


def write(conn, day: str, rows, source: str = "history") -> int:
    with conn:
        conn.execute("DELETE FROM prior_positions")
        conn.executemany("INSERT INTO prior_positions VALUES (?,?,?,?,?,?)",
                         [(o, ax, m, sd, day, source) for o, ax, m, sd in rows])
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="the live database to write prior_positions into")
    ap.add_argument("--history", default=HISTORY_DB)
    a = ap.parse_args()
    day, rows = final_positions(a.history)
    n = write(store.connect(a.to), day, rows)
    sds = sorted(r[3] for r in rows)
    print(f"prior_positions ← history {day}: {n} rows, {len({r[0] for r in rows})} outlets · "
          f"sd median {sds[len(sds) // 2]:.3f}, max {sds[-1]:.3f}")


if __name__ == "__main__":
    main()
