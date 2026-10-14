"""Reuters Institute Digital News Report 2026: the "online brands, used last week" chart per market (A29).

    PYTHONPATH=. uv run python -m pipeline.sources.dnr netherlands belgium ...   → data/dnr2026/<market>.csv + a printout

Each country page embeds Datawrapper charts; the online-brands chart is the one whose rows are brands with weekly use
and that names web brands ("online", ".nl", …) rather than TV, radio or print. The raw CSV is kept for provenance
(git-ignored; re-run to regenerate). A market without a DNR page prints "not covered".
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from pipeline.net import fetch

PAGE = "https://reutersinstitute.politics.ox.ac.uk/digital-news-report/2026/{market}"
CSV = "https://datawrapper.dwcdn.net/{chart}/dataset.csv"
OUT = Path("data/dnr2026")
VERBOSE = "-v" in sys.argv
OFFLINE = re.compile(r"\b(TV|radio|newspaper|print|broadcaster\)|\(TV)", re.I)


def online_chart(market: str) -> tuple[str, list[tuple[str, int]]] | None:
    try:
        html = fetch(PAGE.format(market=market), timeout=30)[0].decode("utf-8", "replace")
    except Exception:                                        # 404: the market is not in the report
        return None
    charts = list(dict.fromkeys(re.findall(r"datawrapper\.dwcdn\.net/(\w+/\d+)/", html)))   # page order
    best = None
    for c in charts:
        time.sleep(0.3)
        text = fetch(CSV.format(chart=c), timeout=30)[0].decode("utf-8", "replace")
        sep = "\t" if "\t" in text.splitlines()[0] else ","          # most charts are tab-separated, some comma
        lines = [l.split(sep) for l in text.strip().splitlines()]
        if not lines or "Used last week" not in " ".join(lines[0]):
            continue
        rows = [(l[0].strip(), int(l[1].strip().rstrip("%"))) for l in lines[1:] if len(l) > 1 and l[1].strip().rstrip("%").isdigit()]
        offline = sum(bool(OFFLINE.search(n)) for n, _ in rows)
        score = len(rows) - 2 * offline                      # the online chart names few TV/radio/print brands
        if VERBOSE:
            print(f"  chart {c} score {score}: " + " · ".join(f"{n} {p}%" for n, p in rows[:8]))
        if best is None or score > best[0]:
            best = (score, c, text, rows)
    if best is None:
        return None
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{market}.csv").write_text(best[2])
    return best[1], best[3]


def main(markets: list[str]):
    for m in markets:
        res = online_chart(m)
        if res is None:
            print(f"{m}: not covered")
            continue
        chart, rows = res
        print(f"{m} ({chart}): " + " · ".join(f"{n} {p}%" for n, p in rows[:12]))
        time.sleep(1)


if __name__ == "__main__":
    main([a for a in sys.argv[1:] if a != "-v"])
