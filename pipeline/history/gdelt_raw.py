"""H1b discover, without BigQuery: GDELT's own 15-minute GKG files for the outlets the first query left out (A75).

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.history.gdelt_raw --start 2019-01 --end 2019-12 \
        [--outlets id …]          → data/history/gdelt_more/YYYY-MM.parquet (resumable: a month on disk is skipped)
    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.history.gdelt_raw --day 2019-03-15   (eval: hits per outlet)

GDELT publishes every GKG record as a zipped file per 15 minutes (data.gdeltproject.org/gdeltv2/<YYYYMMDDHHMMSS>
.gkg.csv.zip, ≈ 9 MB in 2019, and .translation.gkg.csv.zip for the articles it machine-translated: most non-English
outlets): the same rows BigQuery's gkg_partitioned holds, free to download. Each file is read
in memory and only the rows of the asked outlets' domains are kept, with the columns gdelt.py keeps (seen, url,
persons, orgs, places, outlet, country) — the history's later stages read both. Default outlets: the registry's
outlets with no row in data/history/gdelt (the first query did not ask for them, or they had other addresses).
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline.history.gdelt import OUT as FIRST, SUFFIX2, assign, outlet_rules, reg_domain

OUT = Path("data/history/gdelt_more")
BASE = "http://data.gdeltproject.org/gdeltv2/"
WORKERS = 8
DEADLINE = 180                                                # seconds for one file (≈ 1 s normally)
# GKG 2.1 columns (tab-separated): 1 date, 4 DocumentIdentifier, 9 V1Locations, 11 V1Persons, 13 V1Organizations
DATE, URL, LOCS, PERSONS, ORGS = 1, 4, 9, 11, 13


def uncovered() -> list[str]:
    """Registry outlets with no row in the first query's files."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from pipeline.sources.registry import load_outlets
    have = set()
    for f in FIRST.glob("year=*.parquet"):
        have |= set(pc.unique(pq.read_table(f, columns=["outlet"]).column("outlet")).to_pylist())
    return [o.id for o in load_outlets() if o.id not in have]


def stamps(day: dt.date):
    t = dt.datetime(day.year, day.month, day.day)
    for k in range(96):
        yield (t + dt.timedelta(minutes=15 * k)).strftime("%Y%m%d%H%M%S")


def fetch(name: str) -> bytes | None:
    """One file, whole, within DEADLINE seconds (a download that trickles is cut and asked again)."""
    for attempt in range(5):
        t0, parts = time.monotonic(), []
        try:
            with urllib.request.urlopen(f"{BASE}{name}", timeout=30) as r:
                while chunk := r.read1(1 << 20):           # what has arrived, so the deadline is checked often
                    parts.append(chunk)
                    if time.monotonic() - t0 > DEADLINE:
                        raise TimeoutError(name)
            data = b"".join(parts)
            if zipfile.is_zipfile(io.BytesIO(data)):
                return data
            if attempt >= 2:                                    # GDELT's file itself is broken: skip it, counted
                BAD.append(name)
                return None
        except urllib.error.HTTPError as e:
            if e.code == 404:                                   # GDELT skipped that quarter-hour
                return None
        except Exception:                                       # noqa: BLE001 — resets, timeouts
            pass
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"{name}: not downloaded")              # a month is written only when complete


BAD: list[str] = []                                             # files GDELT serves broken (not a zip, 3 times)


def _domain(host: bytes) -> bytes:
    """reg_domain on bytes, for the fast scan."""
    h = host.lower().split(b":")[0]
    h = h[4:] if h.startswith(b"www.") else h
    parts = h.split(b".")
    two = b".".join(parts[-2:])
    return b".".join(parts[-3:]) if two.decode("ascii", "replace") in SUFFIX2 else two


def rows_of(data: bytes | None, domains: set[str], rules, country: dict) -> list[dict]:
    if not data:
        return []
    want = {d.encode() for d in domains}
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            for raw in z.read(name).split(b"\n"):
                head = raw.split(b"\t", URL + 1)                  # only the first fields: most rows are not ours
                if len(head) <= URL + 1 or b"://" not in head[URL]:
                    continue
                try:
                    if _domain(head[URL].split(b"/", 3)[2]) not in want:
                        continue
                except IndexError:
                    continue
                f = raw.decode("utf-8", errors="replace").rstrip("\r").split("\t")
                if len(f) <= ORGS:
                    continue
                oid = assign(f[URL], rules)
                if not oid:
                    continue
                places = ";".join(p.split("#")[1] for p in f[LOCS].split(";") if p.count("#") >= 1)
                seen = dt.datetime.strptime(f[DATE][:14], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
                out.append({"seen": seen, "url": f[URL],
                            "persons": f[PERSONS], "orgs": f[ORGS], "places": places,
                            "outlet": oid, "country": country[oid]})
    return out


def day_rows(day: dt.date, domains, rules, country, pool) -> list[dict]:
    out = []
    names = [f"{s}{kind}" for s in stamps(day) for kind in (".gkg.csv.zip", ".translation.gkg.csv.zip")]
    for data in pool.map(fetch, names):
        out += rows_of(data, domains, rules, country)
    return out


def run(start: str, end: str, ids: list[str] | None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    ids = ids or uncovered()
    outlets, rules = outlet_rules(ids)
    country = {o["id"]: o["country"] for o in outlets}
    domains = {reg_domain(h) for _, h, _ in rules}
    print(f"{len(ids)} outlets: {' '.join(ids)}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    y, m = map(int, start.split("-"))
    with ThreadPoolExecutor(WORKERS) as pool:
        while f"{y}-{m:02d}" <= end:
            path = OUT / f"{y}-{m:02d}.parquet"
            if not path.exists():
                t0, rows, day = time.monotonic(), [], dt.date(y, m, 1)
                while day.month == m and day <= dt.date.today():
                    rows += day_rows(day, domains, rules, country, pool)
                    day += dt.timedelta(days=1)
                tmp = path.with_suffix(".part")
                pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp)
                tmp.rename(path)
                c = Counter(r["outlet"] for r in rows)
                mins = (time.monotonic() - t0) / 60
                bad = f" · {len(BAD)} broken files skipped" if BAD else ""
                BAD.clear()
                print(f"{y}-{m:02d}: {len(rows):,} articles from {len(c)} outlets in {mins:.0f} min{bad}"
                      f" · {', '.join(f'{o} {n}' for o, n in c.most_common(8))}", flush=True)
            y, m = (y + (m == 12), m % 12 + 1)


def eval_day(day: str, ids: list[str] | None):
    ids = ids or uncovered()
    outlets, rules = outlet_rules(ids)
    country = {o["id"]: o["country"] for o in outlets}
    domains = {reg_domain(h) for _, h, _ in rules}
    t0 = time.monotonic()
    with ThreadPoolExecutor(WORKERS) as pool:
        rows = day_rows(dt.date.fromisoformat(day), domains, rules, country, pool)
    c = Counter(r["outlet"] for r in rows)
    print(f"{day}: {len(rows):,} articles in {time.monotonic() - t0:.0f} s")
    for o in ids:
        print(f"  {o:28s} {c[o]:5d}")


def _schema():
    import pyarrow as pa
    text = [(c, pa.string()) for c in ("url", "persons", "orgs", "places", "outlet", "country")]
    return pa.schema([("seen", pa.timestamp("us", tz="UTC"))] + text)


SCHEMA = None


def main():
    global SCHEMA
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--day")
    ap.add_argument("--outlets", nargs="*")
    a = ap.parse_args()
    from pipeline import net  # noqa: F401  — BASEDNEWS_IPV4=1 takes effect on import
    if a.day:
        return eval_day(a.day, a.outlets)
    SCHEMA = _schema()
    run(a.start, a.end, a.outlets)


if __name__ == "__main__":
    main()
