"""V2 external check: the history's positions (A74) against three outside references, no fitting — only reading.

    PYTHONPATH=. uv run python -m pipeline.history.external_check   → prints, and writes reports/external_check.md

  - Reuters Institute DNR 2017 (Fletcher, Cornia & Nielsen 2020, suppl. tables 3–14): each outlet's AUDIENCE leaning,
    self-placed left–right, relative to its own country (−.5 … +.5). One axis; compared within country (both sides
    centred per country, pooled over countries), against our latest positions (Axel: the current state, A78).
  - AllSides (2024 ratings, docs/experiments/e4_prereg.json): US/UK outlets, −2 … +2, one scale for both countries;
    against our latest positions.
  - CHES 2019 (Chapel Hill Expert Survey, trend file 1999–2024 V2): PARTY positions, averaged per country weighted by
    vote share (lrecon = economic, galtan = social, 0–10) — the electorate, not its media; against our latest country
    positions. A78.
Spearman ρ with a 95 % bootstrap interval (resampling countries for the pooled within-country DNR test and CHES,
outlets for AllSides). Our axes: econ (+ = right), social (+ = conservative), and their sum.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import numpy as np

DB = Path("data/history/history.sqlite")
REF = Path("docs/reference")
OUT = Path("reports/external_check.md")
DNR_YEAR, CHES_YEAR = 2017, 2019                     # the references' survey years (labels only)
LATEST_DAYS = 1                                      # ours = the latest day the history has (A78)
BOOT, SEED = 4000, 0


def rank(x: np.ndarray) -> np.ndarray:
    """Average ranks (ties share their mean rank)."""
    order = x.argsort(kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    for v in np.unique(x):
        m = x == v
        r[m] = r[m].mean()
    return r


def spearman(a, b) -> float:
    a, b = rank(np.asarray(a, float)), rank(np.asarray(b, float))
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def boot_ci(groups: list[list[tuple[float, float]]], stat=spearman) -> tuple[float, float, float]:
    """ρ over all pairs, and a 95 % interval from resampling whole groups (a group = a country, or one outlet)."""
    rng = np.random.default_rng(SEED)
    flat = [p for g in groups for p in g]
    rho = stat([p[0] for p in flat], [p[1] for p in flat])
    sims = []
    for _ in range(BOOT):
        pick = [p for i in rng.integers(0, len(groups), len(groups)) for p in groups[i]]
        s = stat([p[0] for p in pick], [p[1] for p in pick])
        if s == s:
            sims.append(s)
    lo, hi = np.percentile(sims, [2.5, 97.5])
    return rho, float(lo), float(hi)


def latest(conn) -> tuple[str, str]:
    """The last LATEST_DAYS days the history has positions for."""
    last = conn.execute("SELECT max(day) FROM history_positions").fetchone()[0]
    return conn.execute("SELECT date(?, ?)", (last, f"-{LATEST_DAYS - 1} days")).fetchone()[0], last


def window(start: str, last: str) -> str:
    return f"on {last}" if start == last else f"averaged {start} → {last}"


def judged(conn, end: str) -> dict[str, int]:
    """outlet → history verdicts it is in, up to `end` (an outlet with none sits at its prior: 0 ± 1, not a position)."""
    n: dict[str, int] = {}
    for a, b, k in conn.execute("SELECT first_outlet, second_outlet, count(*) FROM judge_calls WHERE purpose = 'history' "
                                "AND substr(event_id, 1, 10) <= ? GROUP BY 1, 2", (end,)):
        n[a] = n.get(a, 0) + k
        n[b] = n.get(b, 0) + k
    return n


def averaged(conn, table: str, key: str, start: str, end: str) -> dict[str, dict[str, float]]:
    """{id: {econ, social, sum}} — each axis's daily mean averaged over [start, end]; outlets without a verdict by `end`
    left out."""
    out: dict[str, dict[str, float]] = {}
    for i, axis, m in conn.execute(f"SELECT {key}, axis, avg(mean) FROM {table} WHERE day BETWEEN ? AND ? GROUP BY 1, 2",
                                   (start, end)):
        out.setdefault(i, {})[axis] = m
    for v in out.values():
        if "econ" in v and "social" in v:
            v["sum"] = v["econ"] + v["social"]
    if table == "history_positions":
        n = judged(conn, end)
        out = {i: v | {"n": n[i]} for i, v in out.items() if n.get(i)}
    return {i: v for i, v in out.items() if "sum" in v}


def dnr(conn) -> tuple[list[str], list[str]]:
    rows = list(csv.DictReader((REF / "dnr2017_audience_leaning.csv").open()))
    start, last = latest(conn)
    ours = averaged(conn, "history_positions", "outlet", start, last)
    rows = [r for r in rows if r["outlet"] in ours]
    by_c: dict[str, list[dict]] = {}
    for r in rows:
        by_c.setdefault(r["country"], []).append(r)
    L = [f"## Reuters Institute DNR {DNR_YEAR} — audience leaning, within country",
         "", f"{len(rows)} outlets in {len(by_c)} countries; ours = position {window(start, last)}. Both sides centred per "
         "country, pooled; interval resamples countries.", "",
         "| DNR score | vs econ | vs social | vs econ+social |", "|---|---|---|---|"]
    summary = []
    for ref in ("cross_platform", "online"):
        cells = []
        for ax in ("econ", "social", "sum"):
            groups = []
            for rs in by_c.values():
                a = np.array([float(r[ref]) for r in rs]); b = np.array([ours[r["outlet"]][ax] for r in rs])
                groups.append(list(zip(a - a.mean(), b - b.mean())))
            rho, lo, hi = boot_ci(groups)
            cells.append(f"{rho:+.2f} [{lo:+.2f}, {hi:+.2f}]")
            summary.append(f"DNR {ref} vs {ax}: ρ {rho:+.2f} [{lo:+.2f}, {hi:+.2f}] (n {len(rows)})")
        L.append(f"| {ref.replace('_', '-')} | " + " | ".join(cells) + " |")
    L += ["", "Per country (cross-platform score; Spearman, n is tiny — read the pooled row):", "",
          "| country | n | vs econ | vs social | vs econ+social |", "|---|---:|---:|---:|---:|"]
    for c, rs in sorted(by_c.items()):
        a = [float(r["cross_platform"]) for r in rs]
        L.append(f"| {c} | {len(rs)} | " + " | ".join(f"{spearman(a, [ours[r['outlet']][ax] for r in rs]):+.2f}"
                                                    for ax in ("econ", "social", "sum")) + " |")
    missing = sorted(r["dnr_name"] for r in csv.DictReader((REF / "dnr2017_audience_leaning.csv").open())
                     if r["outlet"] not in ours)
    L += ["", f"Left out (no history verdict by {last}): {', '.join(missing) or 'none'}.", "",
          "| outlet | country | DNR cross | DNR online | econ | social | verdicts |", "|---|---|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda r: (r["country"], float(r["cross_platform"]))):
        o = ours[r["outlet"]]
        L.append(f"| {r['dnr_name']} | {r['country']} | {float(r['cross_platform']):+.2f} | {float(r['online']):+.2f} | "
                 f"{o['econ']:+.2f} | {o['social']:+.2f} | {o['n']:,} |")
    return L, summary


def allsides(conn) -> tuple[list[str], list[str]]:
    rated = json.loads(Path("docs/experiments/e4_prereg.json").read_text())["allsides"]["ratings"]
    start, last = latest(conn)
    ours = averaged(conn, "history_positions", "outlet", start, last)
    rs = sorted((o, x) for o, x in rated.items() if o in ours)
    L = ["## AllSides (2024 ratings) — US/UK outlets", "",
         f"{len(rs)} outlets; ours = position {window(start, last)}. Interval resamples outlets.", "",
         "| vs econ | vs social | vs econ+social |", "|---|---|---|"]
    cells, summary = [], []
    for ax in ("econ", "social", "sum"):
        rho, lo, hi = boot_ci([[(x, ours[o][ax])] for o, x in rs])
        cells.append(f"{rho:+.2f} [{lo:+.2f}, {hi:+.2f}]")
        summary.append(f"AllSides vs {ax}: ρ {rho:+.2f} [{lo:+.2f}, {hi:+.2f}] (n {len(rs)})")
    L += ["| " + " | ".join(cells) + " |", "", f"Left out (no history verdict): {', '.join(sorted(set(rated) - set(ours))) or 'none'}.",
          "", "| outlet | AllSides | econ | social | verdicts |", "|---|---:|---:|---:|---:|"]
    L += [f"| {o} | {x:+d} | {ours[o]['econ']:+.2f} | {ours[o]['social']:+.2f} | {ours[o]['n']:,} |"
          for o, x in sorted(rs, key=lambda t: t[1])]
    return L, summary


def ches(conn) -> tuple[list[str], list[str]]:
    rows = list(csv.DictReader((REF / "ches2019_country_means.csv").open()))
    start, last = latest(conn)
    ours = averaged(conn, "history_country_positions", "country", start, last)
    rows = [r for r in rows if r["country"] in ours]
    L = [f"## CHES {CHES_YEAR} — vote-weighted party positions per country", "",
         f"{len(rows)} countries; ours = the country's position {window(start, last)}. Interval resamples countries.", "",
         "| CHES | vs econ | vs social | vs econ+social |", "|---|---|---|---|"]
    summary = []
    for ref in ("lrecon", "galtan"):
        cells = []
        for ax in ("econ", "social", "sum"):
            rho, lo, hi = boot_ci([[(float(r[ref]), ours[r["country"]][ax])] for r in rows])
            cells.append(f"{rho:+.2f} [{lo:+.2f}, {hi:+.2f}]")
            summary.append(f"CHES {ref} vs {ax}: ρ {rho:+.2f} [{lo:+.2f}, {hi:+.2f}] (n {len(rows)})")
        L.append(f"| {ref} | " + " | ".join(cells) + " |")
    L += ["", "| country | lrecon | galtan | econ | social |", "|---|---:|---:|---:|---:|"]
    L += [f"| {r['country']} | {float(r['lrecon']):.2f} | {float(r['galtan']):.2f} | {ours[r['country']]['econ']:+.2f} | "
          f"{ours[r['country']]['social']:+.2f} |" for r in sorted(rows, key=lambda r: float(r["galtan"]))]
    return L, summary


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    last = conn.execute("SELECT max(day) FROM history_positions").fetchone()[0]
    parts = [dnr(conn), allsides(conn), ches(conn)]
    L = ["# External check (V2): history positions vs DNR, AllSides, CHES", "",
         f"History positions through {last}; generated by `python -m pipeline.history.external_check`. Spearman ρ "
         f"[95 % bootstrap, {BOOT} resamples]. No thresholds set in advance.", ""]
    for p, _ in parts:
        L += p + [""]
    OUT.write_text("\n".join(L))
    for _, s in parts:
        print("\n".join(s))
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
