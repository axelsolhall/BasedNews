# ADR-008 — Ten years of history from the Wayback Machine, sparse and synchronised
**Standard options:** commercial news archives / GDELT.
**Explored:** coarse-to-fine sampling (yearly → quarterly → monthly) of archived feeds and front pages, all outlets of a
country on the same date so historical front pages share events (comparable pairs exist in the past too).
**Evidence:** 933 historical articles overnight; 2016–2020 complete for nearly all outlets. archive.org restricted its search
API mid-project — switched to plain snapshot redirects; one bad URL stalling the run was found and fixed.
**Decision:** keep, with GDELT / Common Crawl News as fallbacks.
