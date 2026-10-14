# Proposal: Europe and North America (41 countries) — not started

Status: **proposal** (2026-10-13). Scope decided 2026-10-14: the 41 countries listed below, without Turkey, Russia and
Belarus ("this selection is good"). Order, judge budget and the rule outside DNR are still open. Nothing in the pipeline has changed. The design
prototypes already show 41 countries with demo data (`frontend/prototypes`); this note is what it would take to measure
them for real.

## What changes with ~3× the outlets

Today: 10 countries, 73 outlets. Target: 41 countries (the EU 27, UK, Norway, Switzerland, Iceland, the Western
Balkans, Ukraine, Moldova; US, Canada, Mexico), about 230–260 outlets at 5–7 per country.

| | Today (73 outlets) | 41 countries (≈236 outlets) | How the estimate is made |
|---|---|---|---|
| Articles in the 7-day story window | 10.5k (first refresh) | ≈34k | linear in outlets |
| Story clustering memory (condensed float64 pairs) | ≈0.44 GB | **≈4.6 GB — does not fit the 2 GB task** | n²/2 × 8 bytes |
| Judge, same precision per outlet | $0.15/day | ≈$0.50/day (≈$15/month) | calls per outlet held fixed |
| Embeddings (new articles) | ≈$0.01/day | ≈$0.03/day | linear |
| Fargate (hourly refresh) | 9.5 min (first run, with backlog) | up to ≈30 min/run, ≈$2/month | ingest dominates, linear |
| Total running cost | ≈$8/month at full judge budget | ≈$20/month | within A16's $25, above today's caps |

The judge figure assumes the planner needs the same number of calls per outlet to reach the same sd (A09); cross-country
pairs could make it cheaper per outlet (more informative pairs) or dearer (more languages, more abstentions) — unknown.

## Blockers before any new country

1. **Clustering must scale.** The all-pairs distance matrix grows with the square of the window. Options: float32
   (halves it, not enough), nearest neighbours only (an ANN index, then average linkage on the sparse graph), or
   clustering per day-slice with a merge step. Needs an equivalence test against today's partitions (as when the
   block-built version was introduced).
2. **The reference tier rule (A29) only works where the Digital News Report covers the country.** DNR covers roughly
   half of the 31 new countries (to verify against the DNR 2026 market list: most of Western/Central Europe, Canada,
   Mexico; not Iceland, Malta, Luxembourg, the Baltics, most of the Western Balkans, Moldova). Elsewhere a different
   audience source is needed, or the country waits.
3. **New languages are unmeasured.** Cross-lingual matching (threshold 0.56, E7b) and the judge were tested on the
   current 7 languages. A small blind eval per new language family is a paid run.

## Suggested order

- Phase 0 — clustering that scales (free, code + test).
- Phase 1 — DNR-covered countries: sources and feeds, robots/TDM checks (A10), a one-language judge eval sample (paid,
  small), then switch on.
- Phase 2 — the rest, once there is a reference-tier rule for countries outside DNR.

## Decisions needed

- Which countries, and in which order (all 41, or Phase 1 first)?
- The judge budget per day (today $0.15) and the OpenAI monthly caps.
- The reference-tier rule for countries outside DNR.
- ~~Turkey, Russia and Belarus~~ — out (2026-10-14).

The candidate outlet names in `frontend/prototypes/make_demo_data.py` are from memory and unverified — a starting list,
not a source list.
