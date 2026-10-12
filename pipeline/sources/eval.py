"""M1 eval: is every outlet usable? Prints a table and an overall verdict.

    uv run python -m pipeline.sources.eval
Pass criteria (per outlet): >=1 feed OK, newest item < 48 h old. Text policy is reported, not failed.
"""
from __future__ import annotations

from collections import defaultdict

from pipeline import store
from pipeline.sources.registry import load_outlets


def main():
    outlets = load_outlets()
    health = store.latest_health(store.connect())
    by_country = defaultdict(list)
    for o in outlets:
        by_country[o.country].append(o)

    fails, text_blocked, n = [], [], 0
    print(f"{'outlet':<14}{'feeds':>7}{'items':>7}{'newest_h':>10}  {'robots':<8}{'tdm':<8}{'text':<6} status")
    for country, os_ in by_country.items():
        print(f"── {country}")
        for o in os_:
            n += 1
            h = health.get(o.id)
            if h is None:
                print(f"{o.id:<14}{'—':>7}  not checked yet"); fails.append(o.id); continue
            fresh = h.newest_item_age_h is not None and h.newest_item_age_h < 48
            ok = h.feeds_ok > 0 and fresh
            if not ok:
                fails.append(o.id)
            if not h.full_text_allowed:
                text_blocked.append(o.id)
            age = f"{h.newest_item_age_h:.1f}" if h.newest_item_age_h is not None else "—"
            fmt = lambda v: "?" if v is None else ("yes" if v else "no")
            print(f"{o.id:<14}{h.feeds_ok}/{h.feeds_total:>5}{h.items_seen:>7}{age:>10}  {fmt(h.robots_allows_articles):<8}"
                  f"{fmt(h.tdm_reserved):<8}{'yes' if h.full_text_allowed else 'NO':<6} {'OK' if ok else 'FAIL ' + '; '.join(h.errors)[:70]}")
    print()
    print(f"Usable outlets: {n - len(fails)}/{n}")
    print(f"Full-text processing blocked by robots/TDM (headline+summary only, A10): {len(text_blocked)} {text_blocked}")
    if fails:
        print(f"FAILING: {fails}  → fix feed URLs in data/outlets.json and re-run pipeline.sources.run")


if __name__ == "__main__":
    main()
