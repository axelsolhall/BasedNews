"""H1 discover: every article GDELT saw from the history outlets since 2015-11-01, one Parquet file per year (A61).

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run --with 'google-cloud-bigquery[bqstorage]' python -m pipeline.history.gdelt \
        [--project basednews-history] [--years 2016 2017] [--dry-run]
    → data/history/gdelt/year=YYYY.parquet  (resumable: a year already on disk is skipped)
    PYTHONPATH=. uv run python -m pipeline.history.gdelt --report     (eval: articles per outlet per year, unmatched outlets)

Source: GDELT 2.0 GKG in BigQuery (`gdelt-bq.gdeltv2.gkg_partitioned`), queried with the maintainer's own Google login
(free sandbox, 1 TB scanned per month; this query scans ≈0.55 TB in total, measured by a dry run 2026-10-14). Kept per
article: time GDELT saw it, URL, and GDELT's English-normalised persons, organisations and place names — what the
top-story finder (H2) matches on across outlets and languages. No text, no titles (titles are only in later years
and cost another 0.6 TB; H3 fetches headlines for the articles it picks).

Output schema (one row per article): seen (timestamp, UTC), outlet (history_outlets id), country, url, persons,
orgs, places (each a ';'-joined string).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

OUT = Path("data/history/gdelt")
START = dt.date(2015, 11, 1)
SUFFIX2 = {"co.uk", "com.mx", "com.mk", "co.me", "com.cy", "com.al", "com.mt", "com.ua"}   # the multi-part suffixes we meet

# Outlets whose articles live on another or a shared domain. (outlet id, host or host suffix, path prefix or "")
# Shared domains: the outlet is decided by host and path; older addresses keep the early years.
ALIASES = {
    "bbc": [("bbc.co.uk", "/news"), ("bbc.com", "/news")],
    "abcnews": [("abcnews.go.com", "")],
    "skynews": [("news.sky.com", "")],
    "tv2-dk": [("tv2.dk", "")],
    "tgcom24": [("tgcom24.mediaset.it", "")],
    "tf1info": [("tf1info.fr", ""), ("lci.fr", "")],
    "mtv-fi": [("mtvuutiset.fi", ""), ("mtv.fi", "")],
}


def reg_domain(host: str) -> str:
    parts = host.lower().removeprefix("www.").split(".")
    return ".".join(parts[-3:]) if ".".join(parts[-2:]) in SUFFIX2 else ".".join(parts[-2:])


def outlet_rules() -> tuple[list[dict], list[tuple[str, str, str]]]:
    """(outlets, rules): a rule (outlet id, host suffix, path prefix); the longest matching rule wins."""
    outlets = json.loads(Path("data/history_outlets.json").read_text())["outlets"]
    rules = []
    for o in outlets:
        if o["id"] in ALIASES:
            rules += [(o["id"], h, p) for h, p in ALIASES[o["id"]]]
            continue
        u = urlsplit(o["homepage"])
        rules.append((o["id"], u.netloc.lower().removeprefix("www."), u.path.rstrip("/")))
    return outlets, rules


def assign(url: str, rules) -> str | None:
    u = urlsplit(url)
    host, path = u.netloc.lower().removeprefix("www."), u.path
    best = None
    for oid, h, p in rules:
        if (host == h or host.endswith("." + h)) and path.startswith(p):
            score = len(h) + len(p)
            if best is None or score > best[0]:
                best = (score, oid)
    return best[1] if best else None


SQL = """
SELECT PARSE_TIMESTAMP('%Y%m%d%H%M%S', CAST(DATE AS STRING)) AS seen, DocumentIdentifier AS url,
       Persons AS persons, Organizations AS orgs,
       ARRAY_TO_STRING(ARRAY(SELECT SPLIT(l, '#')[SAFE_OFFSET(1)] FROM UNNEST(SPLIT(Locations, ';')) l
                             WHERE l != ''), ';') AS places
FROM `gdelt-bq.gdeltv2.gkg_partitioned`
WHERE _PARTITIONTIME >= TIMESTAMP(@start) AND _PARTITIONTIME < TIMESTAMP(@end)
  AND NET.REG_DOMAIN(DocumentIdentifier) IN UNNEST(@domains)
"""


def run(project: str, years: list[int], dry: bool):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from google.cloud import bigquery

    from pipeline import net  # noqa: F401  — BASEDNEWS_IPV4=1 takes effect on import (broken IPv6 at home)

    outlets, rules = outlet_rules()
    country = {o["id"]: o["country"] for o in outlets}
    domains = sorted({reg_domain(h) for _, h, _ in rules})
    client = bigquery.Client(project=project)
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for y in years:
        path = OUT / f"year={y}.parquet"
        if path.exists() and not dry:
            print(f"{y}: on disk, skipped")
            continue
        start, end = max(dt.date(y, 1, 1), START), dt.date(y + 1, 1, 1)
        cfg = bigquery.QueryJobConfig(dry_run=dry, use_query_cache=False, query_parameters=[
            bigquery.ScalarQueryParameter("start", "STRING", start.isoformat()),
            bigquery.ScalarQueryParameter("end", "STRING", end.isoformat()),
            bigquery.ArrayQueryParameter("domains", "STRING", domains)])
        job = client.query(SQL, job_config=cfg)
        if dry:
            tb = job.total_bytes_processed / 1e12
            total += tb
            print(f"{y}: would scan {tb:.3f} TB")
            continue
        table = job.result().to_arrow(create_bqstorage_client=True)
        tb = (job.total_bytes_processed or 0) / 1e12              # known once the job has finished
        total += tb
        ids = [assign(u, rules) for u in table.column("url").to_pylist()]
        table = table.append_column("outlet", pa.array(ids)).append_column(
            "country", pa.array([country.get(i) for i in ids]))
        table = table.filter(pc.is_valid(table.column("outlet")))
        pq.write_table(table, path)
        print(f"{y}: {table.num_rows:,} articles from {len(set(table.column('outlet').to_pylist()))} outlets · scanned {tb:.3f} TB",
              flush=True)
    print(f"total scanned {total:.3f} TB")


def report():
    import pyarrow.parquet as pq

    outlets, _ = outlet_rules()
    counts = defaultdict(Counter)
    for f in sorted(OUT.glob("year=*.parquet")):
        y = f.stem.split("=")[1]
        for o in pq.read_table(f, columns=["outlet"]).column("outlet").to_pylist():
            counts[o][y] += 1
    years = sorted({y for c in counts.values() for y in c})
    print(f"{'outlet':<30}" + "".join(f"{y:>8}" for y in years))
    for o in outlets:
        c = counts.get(o["id"], {})
        print(f"{o['id']:<30}" + "".join(f"{c.get(y, 0):>8}" for y in years))
    missing = [o["id"] for o in outlets if o["id"] not in counts]
    print(f"\n{len(outlets) - len(missing)}/{len(outlets)} outlets found; none for: {', '.join(missing) or '-'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="basednews-history")
    ap.add_argument("--years", type=int, nargs="*", default=list(range(START.year, dt.date.today().year + 1)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        return report()
    run(args.project, args.years, args.dry_run)


if __name__ == "__main__":
    main()
