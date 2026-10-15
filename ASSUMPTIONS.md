# Assumptions register

> 2026-10-11: A01–A29 reviewed and accepted. New assumptions start as `open` again.

Every non-trivial assumption lives here so it can be judged later. Format per entry:
**ID · Assumption · Why · Depends on it · How to test · If wrong (blast radius)** · Status: `open` / `accepted` / `rejected`.
Reference IDs in code where the dependency is non-obvious (e.g. `# A07`).

---

### A01 — Two axes: economic left–right and liberal–conservative (GAL–TAN)
- **Why:** standard in political science (e.g. Chapel Hill Expert Survey); one axis conflates economics and values.
- **Depends:** M7 prompt, M8 (one model per axis), M9, frontend plane.
- **Test:** check whether the two axes come out strongly correlated on real data (if ρ > 0.9, one axis may suffice).
- **If wrong:** axes are a config list; adding/removing an axis = new prompt + one more model fit. Low cost.
- **Status:** accepted (2026-10-11)

### A02 — Compare articles about the *same event* only
- **Why:** comparing different topics confounds topic choice with framing.
- **Depends:** M5 → M6 candidate pairs.
- **Test:** small experiment: judge random cross-topic pairs vs same-event pairs; compare judge consistency.
- **If wrong:** planner candidate generator is one function; widen it. Low cost.
- **Status:** accepted (2026-10-11)

### A03 — Outlet = one latent position per axis (articles are noisy samples of it) — *superseded in time by A22*
- **Why:** Bradley–Terry at outlet level is simple and data-efficient.
- **Depends:** M8 model, M6 information-gain formula.
- **Test:** residual analysis — do some outlets' articles disagree with their outlet position systematically (e.g. op-ed vs news sections)?
- **If wrong:** model sections/authors as sub-entities or add article-level random effects inside M8 only.
- **Status:** accepted (2026-10-11)

### A04 — The LLM judge has a first-position bias, modelled as one parameter β per axis
- **Why:** LLM pairwise judges are known to prefer one position; estimating β is cheaper than asking both orders.
- **Depends:** M7 (must randomise order and record it), M8 (β term).
- **Test:** M7 eval: ask a fixed set in both orders; compare measured flip rate with fitted β.
- **Evidence (P1):** simulated β = 0.30 recovered as 0.29–0.31 with randomised order. Real judge still untested.
- **If wrong:** switch M7 to ask both orders (2× cost) — config flag. Low cost.
- **Status:** accepted (2026-10-11)

### A05 — Logistic (Bradley–Terry) link with Gaussian prior; Laplace approximation for the posterior
- **Why:** closed-form gradients/Hessian, fast, gives a covariance matrix for information gain.
- **Depends:** M8, M6.
- **Test:** simulator: credible-interval coverage ≈ nominal (e.g. 90 % intervals contain truth ~90 % of the time).
- **Evidence (P1, 2026-10-10):** 90 % CI coverage 0.89–0.91 on the simulator *with* article noise (A03 violated on purpose). Holds in simulation; must be re-checked on real judgments.
- **If wrong:** swap in MCMC / variational inference behind the same `fit()` interface.
- **Status:** accepted (2026-10-11)

### A06 — "Ties"/abstentions carry no information and are dropped
- **Why:** keeps the likelihood binary. Judge may abstain when articles aren't comparable.
- **Depends:** M7 output schema, M8.
- **Test:** count abstention rate; if high, model ties (Davidson extension).
- **If wrong:** extend likelihood in M8; M7 already records abstain. Low cost.
- **Status:** accepted (2026-10-11)

### A07 — A single global scale across countries is meaningful
- **Why:** needed for the world map. Achieved by cross-country comparisons of the same international event.
- **Depends:** M5 (cross-lingual events), M6 (cross-country edges), M8, M9, map.
- **Risk:** "left" in one country may be "centre" in another; the LLM's frame may be US-centric.
- **Test:** compare global-scale country ordering with external references (e.g. party-system positions); check stability when adding countries.
- **If wrong:** M9 offers a *country-relative* mode (positions normalised within country); map shows that instead. Keep both modes possible.
- **Status:** accepted (2026-10-11)

### A08 — Country position = audience-weighted mean of its outlets
- **Why:** a country's "media lean" is what people actually read.
- **Depends:** M1 (needs reach/weight per outlet), M9.
- **MVP simplification:** equal weights until reach data is added.
- **If wrong:** weights are data in the outlet registry. Low cost.
- **Status:** accepted (2026-10-11)

### A09 — Stop criterion: posterior sd ≤ target (default 0.15 on a unit-variance scale) or daily budget hit
- **Why:** "spend until sure enough" is the USP. 0.15 is a placeholder.
- **Depends:** M6.
- **Test:** simulator shows what sd target gives stable country colours/rankings.
- **Evidence (P1):** with 40 outlets, max outlet sd 0.3 took 1,766 calls (variance-targeted); country sd 0.15 not reached in 3,000. Target likely needs to depend on LLM $/call.
- **If wrong:** config value.
- **Status:** accepted (2026-10-11)

### A10 — Article text is processed internally but never republished
- **Why:** copyright. Public pages show headline, link, short snippet. EU DSM Directive Art. 4 allows text-and-data mining unless the rightsholder opts out (e.g. robots.txt / TDM reservation).
- **Depends:** M2 (must honour robots.txt/TDM opt-outs), API/frontend (never serve full text).
- **Test:** M2 eval reports outlets with opt-outs; API contract test asserts no full text in responses.
- **If wrong:** store summaries/embeddings only; drop full text after M4/M7. Medium cost.
- **Implementation (P2):** M1 checks robots.txt for our UA and W3C TDMRep (`/.well-known/tdmrep.json` + `tdm-reservation`
  header). `full_text_allowed = robots not disallowing AND no TDM reservation`; otherwise M2 keeps headline + RSS summary only.
  First run: Le Figaro, France 24, Le Parisien declare TDM reservations; WSJ/CNN disallowed by robots (since replaced).
- **Open legal question:** "robots.txt unreadable" and "no TDMRep file" are treated as *allowed*. Is that acceptable?
- **Status:** accepted (2026-10-11)

### A11 — A cheap, capable HOSTED model is a good-enough judge — provider: OpenAI (A45)
- **Why:** cheap; one AI vendor for judge and embeddings (2026-10-11).
- **Depends:** M7.
- **Test:** M7 eval: agreement between cheap and strong model on a fixed set; if low, use strong model for uncertain pairs only.
- **Decision (2026-10-11):** hosted models only — no local/self-hosted judges.
- **If wrong:** model name is config; provider behind an interface.
- **Status:** accepted (2026-10-11)

### A12 — Multilingual MiniLM embeddings are good enough for cross-lingual event matching
- **Why:** already used in `match_algo.ipynb`; small and fast.
- **Depends:** M4, M5.
- **Test:** M5 eval on labelled cross-country pairs.
- **If wrong:** swap model (e.g. multilingual-e5) via config; re-run M4–M5.
- **Status:** accepted (2026-10-11); **superseded 2026-10-12** as the active model by OpenAI `text-embedding-3-small@512`
  (A56, ADR-011). On the unbiased pooled test (E7b) MiniLM was good but behind (AUC 0.925 vs 0.965); it stays as the
  local baseline.

### A13 — Stack: Python pipeline · Postgres(+pgvector) · Spring Boot API · Angular SSR
- **Why:** matches the existing repo and familiar tooling.
- **If wrong:** modules talk via tables, so any tier can be replaced independently.
- **Status:** accepted (2026-10-11); for deployment superseded by ADR-010 the same day: no Spring Boot API
  (static JSON on a CDN), SQLite-in-S3 instead of Postgres while the hot database is small (A52), static Angular
  instead of SSR.

### A14 — Name "BasedNews"
- **Why:** existing name.
- **Risk:** "based" reads as right-wing slang to many users, which can undermine trust in a neutral bias tool.
- **If wrong:** rename before buying the domain/launching (cheap now, expensive later).
- **Decision (2026-10-10):** keep "BasedNews". Consequence: the methodology page and UI copy must work
  harder to signal neutrality (show uncertainty, publish prompts, no editorialising).
- **Status:** accepted

### A15 — MVP map: 10 countries (SE, NO, DK, FI, US, UK, DE, FR, ES, IT), ~5 major outlets each
- **Why:** Chosen 2026-10-10: recognisable map, languages well covered by the multilingual model.
- **Depends:** M1 registry, budget (A16), M9/map.
- **If wrong:** countries are rows in the registry; adding/removing one doesn't touch code.
- **Status:** accepted

### A16 — Total running budget ≈ $25/month (hosting + LLM)
- **Why:** Chosen 2026-10-10.
- **Implication:** hosting ≈ $5–10 (one small Lightsail/EC2 box, Docker Compose, Caddy TLS; CloudFront free tier),
  LLM ≈ $15/month hard cap → cheapest capable judge model, strict daily cap, cache everything, judge both axes
  in one call, and convergence for new countries will be slow (days–weeks, not hours).
- **Depends:** M7 budget guard, A09 stop target, PLAN §4 hosting choice (Phase A only; no RDS/Fargate).
- **If wrong:** budget is a config value; raising it only changes speed of convergence.
- **Status:** accepted

### A17 — (superseded for the reference tier by A29) Outlet selection: "major national outlets spanning the political spectrum", ~5 per country
- **Why:** A15. Picked by Claude from general knowledge on 2026-10-10, then filtered by M1 (dead/blocked feeds replaced
  with outlets of similar political position: WSJ→NY Post, CNN→Washington Post, il Giornale→Libero, Corriere→Il Sole 24 Ore;
  The Independent and RTVE dropped).
- **Risk:** *selection bias decides the country result.* A country's position is only as fair as its outlet list.
- **Test:** compare the list against audience-reach data (e.g. Reuters Institute Digital News Report) per country.
- **If wrong:** the list is data (`data/outlets.json`); re-run M1→M8. No code changes.
- **Status:** accepted (2026-10-11)

### A18 — SQLite in development, Postgres in production, behind `pipeline/store.py`
- **Why:** zero-setup locally; one module holds all SQL.
- **If wrong:** only `store.py` changes.
- **Status:** accepted (2026-10-11)

### A19 — Extracted text shorter than 300 characters counts as a failed extraction (fall back to RSS summary)
- **Why:** short extractions are usually paywall teasers or cookie banners.
- **Depends:** M2, and indirectly what the judge (M7) sees.
- **Test:** M2 eval `--sample` spot-checks; per-outlet text% column.
- **If wrong:** constant `MIN_TEXT_CHARS` in `pipeline/ingest/run.py`.
- **Status:** accepted (2026-10-11)

### A20 — Event = cluster of articles under a time-aware cosine similarity (72 h window, 96 h decay), average linkage
- **Why:** prototype (`match_algo.ipynb`) used connected components, which chains unrelated stories; average linkage is
  the challenger. Window/decay are the notebook's values.
- **Depends:** M5 → M6 candidate pool (A02), cross-country edges (A07).
- **Test:** `pipeline.events.eval` threshold sweep against the labelled pairs; "largest cluster" chaining check.
- **If wrong:** method/threshold/window are `ClusterConfig` fields; re-run M5 only (M4 vectors unchanged).
- **Evidence (2026-10-10, 2,460 articles, threshold 0.60):** components → largest cluster 403 articles (chaining
  confirmed); average linkage → largest 31, 206 multi-outlet events, 111 cross-country. Average linkage is the default.
  Same-topic-different-event pairs score up to ~0.55–0.62 cosine, so the threshold needs the labelled set.
- **Decision (maintainer, 2026-10-15): a pair is a pair.** Once two articles are in one story, match strength
  and time gap do not weight the pair, neither in the planner nor in the fit. Outlets that frame an event
  differently write less similar articles, so weighting by similarity would discount the most slanted coverage —
  the signal being measured. The threshold stays a yes/no gate for "same event".
- **Status:** accepted (2026-10-11)

### A21 — The judge compares like with like: both articles as full text, or both as headline + summary
- **Why:** M2 eval (2026-10-10): NYT, Washington Post, Daily Mail, Sky News, Le Monde, Libération, Le Figaro, France 24 give
  0 % full text (paywall / JS / TDM). Comparing a full article with a two-line summary could bias the verdict.
- **Depends:** M7 prompt builder; M6 could prefer pairs where both sides have full text.
- **Test:** M7 eval: same pairs judged with full text vs summary-only → agreement rate.
- **If wrong:** if summaries agree with full text, use the richest available text per side. One function in M7.
- **Status:** accepted (2026-10-11)

### A22 — Positions drift over time: dynamic Bradley–Terry (random walk), monthly windows as the first version
- **Why:** Trends over time are a product requirement (2026-10-10). A random-walk prior θ_o(t) = θ_o(t−1) + N(0, q²) lets uncertainty
  regrow when an outlet isn't measured, so the planner (M6) automatically spends budget where positions may have moved.
- **First version:** fit M8 independently per rolling 30-day window (no new maths); then replace with the
  random-walk model behind the same `fit()` interface.
- **Depends:** Judgment contract now carries `event_time` + `judged_at`; M8; M6 (budget split between "converge" and "track");
  M9 trend aggregates; frontend charts.
- **Test:** simulator with drifting true positions: does the dynamic model detect a planted shift, and how many calls/month
  does tracking cost at a given sd?
- **If wrong / too expensive:** fall back to monthly snapshots (first version). Drift rate q is a config value.
- **Decision (2026-10-11):** the outlet set is allowed to change over time. Historical gaps are fine; new outlets
  appearing is a *feature* (the landscape really changed). Per-outlet trends tolerate gaps (uncertainty widens);
  aggregate trends at time t use the outlets active at t. Joint fit across time keeps the scale linked via overlapping outlets.
- **Possible later:** decompose a country's trend into "outlets drifted" vs "landscape changed" (composition).
- **Status:** accepted (2026-10-11)

### A23 — History: sparse, synchronised Wayback sample (2026-10-10)
- **What:** dense live data decides current positions; a sparse 10-year sample per outlet (k≈4 articles per outlet per
  sample date, coarse-to-fine yearly → quarterly → monthly on the 15th) drives the trend analysis (A22).
- **Why synchronised:** all outlets of a country sampled at the same date so their front pages share events → historical
  same-event pairs exist (A02).
- **Published date = Wayback capture time** of the article (captures within ±45 days of the grid date for feeds/homepages,
  ±60 for articles). Close for news, but not exact.
- **Known noise:** the homepage link heuristic also picks service/promo pages ("contact us"); they are excluded downstream
  because they never cluster with other outlets in M5. Sport/entertainment over-represented → topic filter (BACKLOG).
- **Text policy:** the publisher's *current* robots/TDM status (A10) also governs archived copies.
- **If wrong:** grid density, k and windows are CLI flags; jobs table makes any re-run cheap. Historical articles are
  marked `text_source = wayback-*`, so they can be excluded or re-weighted anywhere.
- **Status:** accepted (2026-10-11)

### A24 — Internet Archive access via plain snapshot redirects at ≈1 req/s
- **Why:** on 2026-10-10 the CDX search API returned 403 ("requires authorization") and the availability API returned
  empty results; `web.archive.org/web/<date>id_/<url>` redirects still work.
- **Risk:** archive.org may restrict further or be offline (it was "Temporarily Offline" earlier the same evening). The run is
  resumable and waits 10 min on outages. If access stays blocked, alternatives: GDELT (2015+), Common Crawl News (2016+).
- **Status:** accepted (2026-10-11)

### A25 — Ovals: radii = k × sd per axis, k = 1 by default (spec, 2026-10-11)
- **What:** horizontal radius = econ sd, vertical = social sd. Axis-aligned is exact because the axes are fitted as separate
  models (no cross-axis covariance, A01). Country ovals = uncertainty of the country *average* (full covariance), not the
  spread of its outlets.
- **Caveat:** a 1-sd 2-D oval contains the truth only ≈39 % of the time (1 − e^(−k²/2)); k = 2.45 gives ≈95 %.
  Users tend to read ovals as "almost certainly in here" — consider k = 2.45 for the public site.
- **If wrong:** `OVAL_SD_MULTIPLIER` in `pipeline/aggregate/plane.py` (exported in the JSON meta; frontend uses it).
- **Status:** accepted (2026-10-11)

### A26 — The plane's origin (0, 0) is the average of all outlets
- **Why:** Bradley–Terry identifies only differences; without an anchor, dots carry an arbitrary offset (the first preview put
  only 12 % of country truths inside their ovals because of this). Positions and sds are computed for the contrast
  "group − mean of all outlets", so ovals stay correct.
- **Implication:** "centre" means "the average outlet in our sample", not an absolute political centre; adding outlets
  can shift the origin. Methodology page must say so.
- **Decision (2026-10-11):** relative-only is the *point* of the product, not a limitation. No absolute anchoring.
- **Remaining care:** show "relative to N outlets in M countries" on the site. No fixed reference set for trends
  (2026-10-11): composition changes are real changes — see A22.
- **Status:** accepted

### A27 — Gaussian (Laplace) uncertainty is adequate even for extreme outlets
- **Context (question raised 2026-10-11, "is ±10 at 1700 the same as at 1000?"):** yes — Bradley–Terry is translation-
  invariant (only differences matter), so an sd means the same anywhere on the scale. But information per comparison is
  p(1−p): pairs far apart carry little, so extreme outlets keep wider ovals (correct) — and their true posterior can be
  *skewed* (we know "far right", not how far), which a symmetric Laplace oval can misrepresent.
- **Units for the public:** a difference of 1.0 = the judge picks the further-right side 73 % of the time (2.0 → 88 %).
- **Test:** on real data, check coverage separately for the outermost 20 % of outlets; compare Laplace sds with MCMC.
- **If wrong:** asymmetric intervals via MCMC/profile likelihood behind the same `fit()` interface; ovals become
  asymmetric shapes in the frontend.
- **Status:** accepted (2026-10-11)

### A28 — The scale is symmetric: −d is as far left as +d is right (question raised 2026-10-11)
- **True inside the model:** logistic link is symmetric, σ(−d) = 1 − σ(d); centring (A26) changes no differences.
- **Units:** log-odds of the judge's choice — NOT standardised N(0,1). 1.0 ≈ 174 Elo points (Elo: 400 per 10:1 odds).
  The N(0,1) prior only shrinks weakly-measured outlets; the spread of positions comes from the data.
- **Can break in three ways:**
  1. ~~Origin = average of our outlets, not the political centre~~ — *by design* (A26 accepted: everything is relative).
     What still matters: a lopsided outlet list changes what "relative to the average" means → A17 review.
  2. Units measure *judge-distinguishability*, not ideology; 1.0 among centrists may ≠ 1.0 among radicals; saturation at extremes.
  3. **Judge asymmetry:** the LLM may treat one side's framing as the neutral default and stretch the other side.
- **Note:** for a relative product, judge asymmetry (3) cannot change the *ordering* of outlets (it is monotone) but it
  can stretch distances on one side — which still distorts how far apart outlets and countries look.
- **Test (M7 eval):** ask a fixed set in both framings ("more right?" and "more left?"); a symmetric judge gives
  P(left) = 1 − P(right). Report the gap overall and per region of the scale. Optionally compare with an external scale.
- **If wrong:** ask both framings in production for uncertain pairs and average them; or model a framing term in M8.
- **Status:** accepted (2026-10-11)

### A29 — Reference tier = DNR 2026 top 5 measurable domestic news brands per country; everything else = extra tier
- **Decision (2026-10-11):** source = Reuters Institute Digital News Report 2026, "Weekly reach online" per market
  (fetched from the report's Datawrapper CSVs). Frozen — no re-indexing; the project won't span enough time to need it.
- **Rules applied (proposed by Claude, open to review):**
  1. drop categories that aren't brands ("Other regional/local newspaper", "Regional TV2 stations", "Commercial radio news");
  2. drop foreign brands (BBC/CNN outside their home market);
  3. drop pure aggregators (Yahoo! News, MSN, Web.de, GMX) — judging them = judging their sources; t-online kept (own newsroom);
  4. drop video-only brands without articles (Brut, HugoDécrypte, Konbini);
  5. ties broken by the DNR chart order;
  6. **measurability:** if a brand has no live machine-readable feed or blocks automated access, take the next-ranked
     brand — CNN (no RSS) → NBC News; Fanpage & SkyTG24 (403) → RaiNews, Il Fatto; Corriere (feeds stale);
     RTVE (feeds stale) → OKDiario; Le Parisien (titles-only, blocks bots) → BFMTV.
- **Effect:** origin (A26) and country positions use reference outlets only; extra outlets get dots/ovals relative to them.
- **Notable:** reach-based selection added GB News, Nettavisen, Ekstra Bladet, B.T., Bild, OKDiario — the hand-picked list
  (A17) under-represented high-reach right-leaning/tabloid brands. A17 is superseded for the reference tier.
- **If wrong:** tiers are data in `data/outlets.json` (`tier`, `dnr_2026_online_reach_pct`, `dnr_2026_rank`).
- **Status:** accepted (rules 1–6 open for review)

### A30 — Judge cost & sharpness model: full-text pair ≈ 2,700 tokens (a = 1.0); summary pair ≈ 540 tokens (a = 0.6)
- **Why:** needed for the cost-aware planner (E2). Token counts are rough (prompt 300 + 1,200 or 120 per side).
  a_summary = 0.6 is a placeholder.
- **Key derived fact:** information ∝ a², so summaries win per token iff a_summary > √(540/2700) ≈ 0.45.
  E2 sensitivity (a = 0.4 / 0.6 / 0.8) shows the planner adapts on both sides of that break-even.
- **Test (M7 eval):** judge the same pairs with full text vs summaries → estimate a_summary; log real token counts.
- **If wrong:** costs and a are per-candidate fields; the planner needs no code change.
- **Status:** open

### A31 — One judge call returns a verdict on BOTH axes
- **Why:** halves cost; the planner scores a candidate by the summed variance reduction over both axes ÷ cost (`plan_joint`).
- **Risk:** the two verdicts from one call may be correlated (judge "halo" — calling one article further right *and* more
  conservative). The model currently treats them as independent.
- **Test (M7):** compare agreement between axes from joint calls vs separate single-axis calls on a fixed set.
- **If wrong:** ask axes separately for some pairs, or add a correlated-verdict term in M8.
- **Status:** open

### A32 — Per-axis relevance: the judge abstains on an axis a story has no angle on
- **Why (every oval was a circle, noticed 2026-10-11):** with identical evidence on both axes the model *must* produce
  circles. Real stories differ: a stabbing or a football match has a values angle but no economic one. The judge answers
  "not applicable" per axis (A06); the planner weights each axis by the story's relevance probability.
- **Simulation:** ~30 % of events economic (econ relevance 0.9, else 0.2); social relevance uniform 0.5–0.95.
- **Production:** relevance per event from a cheap topic classifier (or the judge's own abstention rate per cluster).
- **Effect:** real ovals (width/height up to ~1.5); the planner self-balances the axes (median width/height 1.04 vs 1.21 random).
- **Test (M7):** measure per-axis abstention rates by topic.
- **If wrong:** relevance is a per-candidate field; set to 1 to recover the old behaviour.
- **Status:** open

### A33 — A blocked article page means "publisher declined automated access": we use the feed summary, never a workaround
- **Found by the M2 coverage report (2026-10-11):** live article pages return 403 Forbidden (Daily Mail, Libération) or a
  JavaScript bot challenge (Le Monde, title "Client Challenge"), while archive.org copies of the same sites extract fine.
- **Decision:** treat a block like a TDM opt-out (A10): keep the feed's headline + summary, never spoof a browser user
  agent or solve challenges. The bot identifies itself (`BasedNewsBot`, pipeline/net.py).
- **Effect:** these outlets are judged on summaries (a ≈ 0.6, A30), so their ovals shrink more slowly per dollar. France is
  hit hardest: of its 5 reference outlets only 20 Minutes and BFMTV give full text (TF1 Info and franceinfo opt out of TDM, Le Monde blocks).
- **Depends:** pipeline/ingest/run.py (fallback to summary), pipeline/ingest/report.py (text-available bars), M6 cost model.
- **Test:** `uv run python -m pipeline.ingest.report` → per-outlet "Full text" column; spot-fetch one URL per outlet.
- **If wrong** (e.g. a publisher grants access or offers an API/licence): add the outlet to an allow-list or a licensed
  source in M2; nothing downstream changes, since the cost model already handles both text kinds.
- **Status:** accepted 2026-10-11 ("respect bot-blocking … not for profit")

### A34 — One text under several headlines: one renamed article, or a container page (decided by content, not count)
- **History:** v1 (2026-10-11) split by headline count (≥ 3 = live blog, 2 = renamed). **Rejected:** an article
  can be renamed any number of times. v2 below replaced it the same day; all v1 changes were restored first.
- **Found:** live blogs give each entry its own URL (`?pinnedEntry=…`) but serve the whole blog, so 300 Aftonbladet "Direkt"
  entries carried 32 distinct texts; DR "seneste" and Dagbladet video pages carry a news list. Separately, outlets
  re-headline articles in place (same URL; M2 used to skip known URLs and lost these) or under a new URL (same text).
- **Rule** (`pipeline/normalize/rules.py`, M3): link two copies as one article if their feed summaries match (≥ 0.6) or
  their headlines are near-identical in meaning (≥ 0.75). Links covering the whole group → one article with n headline
  versions, any n. Otherwise a container: only a component whose headline clearly matches the page text (≥ 0.5 and 0.15
  ahead of the runner-up) keeps it; the rest keep headline + summary. Same-URL edits are recorded by M2 directly.
- **Measured (2026-10-11):** summary similarity between live-blog entries ≤ 0.16, between renamed copies 1.0; the top
  story's headline scores 0.57–0.80 against the page, other entries ≲ 0.4; four different car crashes scored 0.47–0.57
  against one crash text (hence the margin: a tie gives the text to nobody — a false split is safer than a false merge).
- **Result:** 121 groups → 11 renamed articles, 50 containers with an owner, 60 without; 27 copies merged, 536 entries stripped.
  Free-label checks: live-blog URL groups → container 52/52; identical-summary groups → renamed 8/8.
- **Depends:** M3 (`articles.canonical_id`, `headlines`), M4 (A35), M5 (canonical only), M7 render (A36), M2 report.
- **Test:** `uv run python -m pipeline.normalize.eval --samples 3` + `reports/m3_normalize.png`; `tests/test_headlines.py`.
- **If wrong:** thresholds live in `NormalizeConfig`; texts are in `articles_shared_text`, merges in `canonical_id` (both reversible).
- **Status:** accepted 2026-10-11 (stage 2 sign-off)

### A35 — An article with several headlines is embedded as the mean of its versions
- **Why:** M4's model (MiniLM) reads ~128 tokens, so concatenating headline versions would push the body out. Each version
  is embedded as headline + body and the article vector is the L2-normalised mean, every version weighted equally. With one
  headline the vector is exactly what it was before (tested).
- **Depends:** pipeline/embed/run.py; M5 clustering. A new version changes the input hash → re-embed.
- **Alternatives not taken:** latest headline only (loses what the story was called when other outlets covered it);
  weighting by how long each version was live (we rarely know when a version ended).
- **Test:** M5 eval once headline histories are common (today 25 articles have one).
- **If wrong:** change `mean_vectors` / `version_texts` in one file.
- **Status:** accepted for MiniLM (2026-10-11); kept for OpenAI (2026-10-12) — its 8,000-token context removes the reason
  for averaging, so concatenating the versions is an option to measure with E7b's pooled set (BACKLOG)

### A36 — The judge sees the latest headline plus the earlier versions with timestamps (capped at 6)
- **Why:** headline edits are framing evidence, and two outlets may be compared at different stages of the same story;
  the timestamps let the judge tell a factual update from a reframing. Capped (first + latest five) so tokens stay bounded;
  E3's cost model charges for exactly this rendered text.
- **Depends:** pipeline/judge/render.py (`HEADLINE_POLICY`, `MAX_VERSIONS`), M7 prompt, E3 costs.
- **Test (M7 eval):** verdict agreement between policies "history" / "latest" / "first" on articles with ≥ 2 versions; if
  "history" adds nothing, drop to "latest" to save tokens.
- **If wrong:** one constant.
- **Status:** accepted 2026-10-11 (stage 2 sign-off)

### A37 — History comes only from the synchronised archive.org sample; the pre-pipeline scraper's rows are retired
- **Why (2026-10-11):** the old scraper (ingested 2025-12-23 .. 2026-01-11) read 12 Nordic outlets densely for three
  weeks (8,306 articles, up to 55 per outlet per day) and nothing else. History is meant to be a sparse sample on the same
  dates for every outlet (A23); that window would make five countries ~100× denser than the rest, and the backfill (1.3)
  samples Dec 2025 / Jan 2026 for every outlet anyway.
- **Done:** `pipeline/ingest/retire_legacy.py` moved the rows to `articles_legacy`; their headlines and vectors were dropped.
- **Depends:** 1.2 report (no "old scraper" era), 2.1–2.3 (fewer historical articles), trends (4.1).
- **Test:** `sqlite3 data/basednews.sqlite "select count(*) from articles_legacy"` = 8306; the coverage chart shows one
  history pattern for all outlets.
- **If wrong:** `uv run python -m pipeline.ingest.retire_legacy --restore`, then re-run 2.1 normalize.
- **Status:** accepted 2026-10-11

### A38 — Not everything in a feed is one article: rundowns and site pages are excluded; notices, walls and paywalls are not text
- **Found by Claude's verification (2026-10-11):** of the 15 stored texts that matched their headline
  worst, 5 were cookie/consent walls, an expired gift link or a comment-section blurb; FAZ and DN prefix real articles
  with consent notices; 56 FAZ+ texts are a free first paragraph + subscription offer + other articles' teasers;
  TF1/CBS publish multi-topic show pages ("LCI Live du samedi 10 octobre 2026") that 2.3 matched to single stories;
  the backfill picked up cookie/privacy/terms pages. 974 feed summaries carried raw HTML.
- **Rules** (one place each):
  - `extract.clean_text` (1.2): strips HTML/entities/comments; drops paragraphs that open with a known site notice
    (`BOILERPLATE_OPENERS`); `is_paywalled` drops the whole text when a paywall/dead-link marker is present → the item
    keeps its summary (like any paywalled outlet, A21). `pipeline.ingest.reclean` re-applies this to stored rows.
  - `normalize/kinds.py` (2.1): `kind = 'rundown'` (show title ending in a full date, "dd/mm: … News", summary
    "Au sommaire") or `'page'` (cookie/privacy/terms/impressum titles) → not embedded, not clustered, not counted;
    `is_wall`: headline↔opening similarity < 0.15 AND a wall phrase → text dropped. Single-topic live blogs stay articles.
    Added the same day: `'page'` also covers section hubs and service pages (URL "startseite", "/home-123", titles
    "Aktuelle News…", job boards); `'headline_only'` = neither text nor summary (VG video clips, Le Parisien) —
    nothing a judge could read, so it is not embedded, clustered or counted. Outlet checks now count judgeable articles.
- **Measured:** 10 rundowns and 7 pages marked, all checked by hand; 0 false positives on the probe headlines in
  `tests/test_cleaning.py` ("Datenschutz: Meta muss zahlen", Sweden's consent law, "cookies and cream").
- **Risks:** phrase lists are site-specific and will miss new walls → the M2 report's boilerplate-suspect column and
  the stage-1 sign-off ("texts opening like a wall") are the alarms; extend the lists, then run `reclean`.
- **If wrong:** dropped texts are in `articles_shared_text` (reversible); `kind` can be reset to NULL.
- **Status:** open

### A39 — A feed item without a parseable date is dated by when we first saw it; zone abbreviations are real offsets
- **Why:** Dagbladet, Libero and Le Parisien publish some items without a date; RSS parsers only know US zone names, so
  Bild's "CEST" was read as UTC and its articles sat 2 h in the future (31 rows).
- **Rule:** `ingest.run`: undated → first-seen time (ingested_at); `feeds.to_utc_iso` maps CET/CEST/BST/EET… to offsets.
  Stored rows fixed once on 2026-10-11 (Bild live rows −2 h, all ingested during CEST; undated live rows → ingested_at).
- **Error bound:** first-seen lags publication by at most one ingest interval (≤ 12 h today), well inside 2.3's 72 h window.
- **Test:** stage-1 sign-off "live articles with a parseable date" (100 %) and "dated in the future: 0" in the M2 report.
- **Status:** open

### A40 — Boilerplate = a paragraph repeated across an outlet's articles, at the start/end, across different stories
- **Idea (2026-10-11):** text repeated in "too many" articles is header, footer, pop-up or navigation; repeats in
  the body are more likely story text, repeats at the start and end more likely boilerplate.
- **Rule** (`pipeline/normalize/boilerplate.py`, 2.1, texts and summaries): repeated verbatim in ≥ 3 articles of one
  outlet, AND at the edge (inside the run of repeated paragraphs touching the start or end — footers can be 20
  paragraphs) in most occurrences, AND across different stories (mean headline similarity < 0.5). Exception agreed in
  the data: ≥ 10 repeats across different stories count anywhere (all such were inline embed placeholders, affiliate
  notes, app promos, "Story continued below").
- **Measured:** 139 paragraphs in 29 outlets, 642 articles edited, 244 k characters removed (SVT policy box 106×,
  Guardian "Continue reading..." 183×, GB News subscription pop-up, Aftonbladet tip box, ANSA copyright). Kept: a
  Berlingske eyewitness quote repeated in 3 follow-ups (body); same-story openers. Removed as intended: SVT's Brattås
  series fact box (same 5 paragraphs at the end of 4 articles).
- **Test:** `tests/test_boilerplate.py`; `uv run python -m pipeline.normalize.eval` lists every paragraph with its count.
- **If wrong:** constants at the top of boilerplate.py; originals in `articles_text_backup`, found paragraphs in `boilerplate`.
- **Status:** accepted 2026-10-11 (stage 2 sign-off)

### A41 — Article content is never shortened or rewritten for the judge; length bias is measured and modelled instead
- **Why (2026-10-11):** "we cannot modify the actual content in a way that might destroy the intent." Cutting both
  articles to equal length could drop the paragraph that carries the framing.
- **Rule:** the only edits to article text are removing non-content (HTML, site notices, walls, boilerplate: A38, A40).
  The judge reads the whole article — the old 6,000-character cap in E3's cost model is removed (it cut 547 of 4,516
  texts, 12 %); long articles simply cost more and the cost-aware planner prices that in (A30).
- **Length bias:** M7's reliability eval measures whether verdicts lean with the length ratio of the two texts; if they
  do, M8 gets a length term (γ · log(len_A / len_B)), estimated from the data like the order bias β (ADR-003).
- **Depends:** pipeline/judge/render.py (no truncation), E3 costs, M7 eval, M8 model.
- **Status:** accepted 2026-10-11

### A42 — The judge is blind to the outlet: its own names are masked, site suffixes removed from titles
- **Why:** an LLM that recognises the brand ("© Copyright ANSA", "skriver Aftonbladet", "| Fox News") can answer from
  its stereotype of the outlet instead of the text — we would measure the model's prior, not the article.
- **Rule:** titles lose a trailing site name ("… | Kultur | Aftonbladet" → "…") in 2.1 (stored; 375 titles; originals
  in `articles_text_backup`). In the judge input (3.2, `pipeline/judge/blind.py`), the outlet's OWN names are replaced by
  "[outlet]"; mentions of other outlets and the article's meaning are untouched (A41). Patterns: `data/outlet_aliases.json`,
  safe forms only for names that are also words (BILD not Bild, SPIEGEL not Spiegel, WELT not die Welt, la Repubblica,
  IS:n not IS, 20minutos not 20 minutos). Measured: 3,250 self-references in 1,758 fields.
- **Test:** `uv run python -m pipeline.judge.blind` lists what was masked and where the name still appears;
  M7 eval: verdict agreement with vs without masking.
- **Status:** accepted 2026-10-11 (stage 2 sign-off)

### A43 — Genre: opinion pieces are tagged, so the planner can compare like with like
- **Why:** a column vs a news report measures genre, not outlet.
- **Rule:** `genre = 'opinion'` from URL sections (/opinion/, /debatt/, /kommentar/, /commentisfree/, /mielipide/,
  /editoriali/, …) or title prefixes ("Kommentar:", "Ledare:", "Analyse:"); else 'news'. 125 opinion, 5,931 news.
- **Next:** 3.1 pairs news with news and opinion with opinion (BACKLOG).
- **Status:** accepted 2026-10-11 (stage 2 sign-off)

### A44 — Same headline AND same text in two outlets = the same thing: a known tie, kept, never judged
- **Why (2026-10-11):** "if they publish the same thing then it's the same thing, nothing more or less."
- **v1 → v2 (Question raised: "will it really be identical enough to say tie just from the text?"):** v1 used text overlap
  only (word-5-gram Jaccard ≥ 0.8) → 29 pairs, but 15 had different headlines, several with real framing (JP "Trump
  tordner mod Zelenskyj" vs Berlingske "Trump: Ukraine har brug for en ny præsident"; Dagbladet "Slår tilbake mot USA" vs
  Aftenposten "Fredsprisvinneren om Trumps sanksjoner mot ICC: – Uhørt og uakseptabelt"), and up to 12 % of the words
  differed. Those are judged — same body, different headline isolates headline framing.
- **Rule (v2, `pipeline/events/wire.py`):** identical headline AND ≤ 2 % of words differ, after removing what is not
  editorial (agency tags NTB/TT/Ritzau/…, "Fortsæt med at læse", a leading copy of the headline, soft hyphens, case,
  punctuation). 11 pairs this week, all agency copy run unchanged (TT, NTB, Ritzau).
- **Open (A06):** M8 drops ties today; a tie-aware model (Davidson) would let these pull the two outlets together.
- **Status:** accepted 2026-10-11

### A45 — One AI vendor: OpenAI for the judge and the embeddings; AWS hosts
- **Decision (2026-10-11):** "I will host with AWS and use OpenAI for the judge and vectors" — one AI bill, less
  maintenance, fewer things that can go wrong. (An earlier draft of this entry kept Claude for the judge; replaced.)
- **Judge (3.2):** a cheap, capable OpenAI chat model (A11); model name is config.
- **Embeddings (2.1, 2.2):** an OpenAI multilingual embedding model replaces MiniLM. Every similarity threshold was
  calibrated on MiniLM (2.3 story 0.6; 2.1 wall 0.15, same-story guard 0.5, summary link 0.6, title link 0.75), so the
  switch re-runs the 2.1/2.2/2.3 evals on the 200 labelled pairs and recalibrates; MiniLM stays as the measured baseline.
- **Keys:** OPENAI_API_KEY in `.env` (untracked; added by hand); in production in SSM Parameter Store. Provider-side
  cap: prepaid credits with a $10 monthly reload limit (checked 2026-10-12).
- **Embeddings switched 2026-10-12** after E7/E7b: `text-embedding-3-small` at 512 dimensions (A56, ADR-011); the 2.3
  story threshold is 0.56 (E7b's pre-registered rule), the 2.1 thresholds quantile-mapped (A47).
- **Status:** accepted 2026-10-11; embeddings done 2026-10-12

### A46 — The judge protocol: one call answers both axes from the text alone; the prompt is versioned (v4 active)
- **Rule** (`pipeline/judge/prompt.py`, `client.py`): system prompt defines the axes and forbids using knowledge about
  publications (names are masked, A42); per axis the answer is A / B / same / na plus a one-sentence reason; strict JSON
  schema. Order randomised by the planner (A04). Every call is stored (`judge_calls`, `judgments`) with model, prompt
  version, tokens and cost; fits only use one prompt version.
- **Like with like:** both full text or both the outlet's own summary (A21); both news or both opinion (A43).
- **Test:** `uv run python -m pipeline.judge.eval` (swap, retest, mirror, first-shown and length lean, null pairs, cost);
  targets in the module docstring. Run it before the first measurement and after any prompt or model change.
- **Eval 2026-10-11, gpt-5.4-mini** (`reports/m7_judge_eval_v1.md`, `_v2.md`):
  | check (econ / social) | v1 "which is further right/conservative?" | v2 "score both −2…+2", length warning |
  |---|---|---|
  | identical pairs → same/na | 1.00 / 1.00 | 1.00 / 1.00 |
  | order swap flips | 0.86 / 0.89 | 0.80 / 0.80 |
  | mirror question agrees (A28) | 0.80 / **0.20** | 1.00 / 0.91 |
  | picks the longer text | **0.71 / 0.86** | 0.32 / 0.59 |
  | retest agrees | 0.83 / 0.73 | 0.82 / 0.68 |
  | picks the first-shown | 0.53 / 0.36 | 0.34 / 0.34 |
  | decisive share | 0.27 / 0.40 | 0.22 / 0.30 |
  | cost per call | $0.0018 | $0.0021 |
  v1 picked the article with more material whichever direction was asked; v2 removes that. v2 leans toward the
  second-shown article (~0.33) — the order bias β that the planner's random order and M8 already estimate (A04); and
  social retest is noisy (0.68), which the model treats as noise. Decisive pairs are rare, so swap/mirror rest on 6–11
  pairs: re-run with more pairs as data accumulates. Next improvement path: prompt optimization (H6, BACKLOG).
- **v3 (same day):** one forced choice per axis — "Which article's framing is more LIBERAL: A or B?" —
  "na" only when the story has no angle, no reasons, sharper axis definitions; the asked direction is random per call.
  (A yes/no draft "Is A more X than B?" was dropped before running: "no" also means "equal".)
  | check (econ / social) | v2 | v3 |
  |---|---|---|
  | decisive share | 0.22 / 0.30 | **0.38 / 0.63** |
  | order swap flips | 0.80 / 0.80 (n 10/10) | 0.67 / 0.68 (n 21/31) |
  | retest agrees | 0.82 / 0.68 | 0.80 / 0.78 |
  | mirror agrees | 1.00 / 0.91 (n 6/11) | 0.67 / 0.82 (n 18/22) |
  | picks the longer text | 0.32 / 0.59 | 0.54 / 0.60 |
  | picks the first-shown | 0.34 / 0.34 | 0.33 / 0.36 |
  | identical pair → na | 1.00 | 1.00 |
  | cost per call | $0.0021 | $0.0018 |
  Consistent verdicts per call (decisive × swap consistency): v2 ≈ 0.18 / 0.24, v3 ≈ 0.25 / 0.43 — v3 gives about
  1.4–1.8× more usable signal per dollar. The lower swap consistency is mostly close pairs settled by the judge's lean
  toward the second-shown article (~0.35), the order bias β the model estimates from randomised order (A04).
- **Status:** v4 (forced-choice design) active since 2026-10-11 (E4b). Open: social length lean 0.73 (VERIFY, E4)

- **E4 (2026-10-11, $2, 1,433 measurement calls):** signal at the extremes, but NYT reads conservative and Fox slightly
  liberal; AllSides Spearman 0.38. Hypothesis: the social definition's "trusts police, military and authorities" pushes
  official-source-heavy straight news to the conservative side. Economic axis: 90 % "na" (A32). See docs/experiments/e4_results.md.
- **v4 (after E4):** sourcing is not a leaning; tone, language and order ruled out; wider economic angle. Same
  338 US/UK pairs: AllSides 0.40 → 0.69; contrasts 3/5 → 4/5. Eval: social retest 0.83, swap 0.82; but picks the longer
  text 0.73 on social. **v4 active from 2026-10-11.** See docs/experiments/e4b_results.md.
### A47 — Similarity thresholds belong to the embedding model; a new model is calibrated, never assumed
- **Rule:** thresholds live in per-model profiles (`pipeline/embed/profiles.py`, `data/embedding_profiles.json`); a
  model without a profile refuses to run 2.1/2.3. `pipeline.embed.calibrate` sets the 2.3 story threshold at the
  baseline's precision on the labelled pairs (best recall) and maps each 2.1 threshold by interpolated quantile on the
  same texts (exact identity when the model is unchanged — tested).
- **Lesson (E7, E7b, F2):** the labelled pairs used for the story threshold were chosen with MiniLM, so they favour it.
  The OpenAI story threshold (0.56) comes from E7b's blind pooled set instead; the 2.1 thresholds come from quantile
  mapping, which uses no labels.
- **Status:** done for `openai:text-embedding-3-small@512` (2026-10-12)

### A48 — AI spend is capped per run and per month from our own usage log
- **Rule:** every embedding and judge call logs tokens and cost (`api_usage`, prices in `pipeline/settings.py`, checked
  2026-10-11 — verify on openai.com/api/pricing). `pipeline.measure.run --budget-usd X` stops at X and never exceeds
  `BASEDNEWS_MONTHLY_AI_USD` (default $12 ≈ the $25/month total minus hosting, A16). Also set a hard limit in the OpenAI
  dashboard — the log is our cap, the dashboard is the provider's.
- **Measured (dry run):** ≈ $0.0012 per call on gpt-5.4-mini; all 8,477 candidate comparisons of a week ≈ $12.6 —
  the planner buys the informative ones first, so a few dollars a week should go far (E1–E3, exploratory).
- **Layers since 2026-10-12:** (1) per run: `--budget-usd`, and a paid job step refuses to start without one (A53's
  runner); (2) per month: the usage log, now also checked by the embedding encoder before every paid batch (A56);
  (3) provider: OpenAI prepaid credits with a $10 monthly reload limit; (4) AWS: a $10 budget email and a $15 kill
  switch that turns every schedule off (infra/main/alerts.tf). In production the judge runs daily with $0.15 (A58).
- **Status:** open (the layers are built; the monthly numbers are checked after a month of production)

### A49 — An outlet is measured over its articles: one article's verdicts are not independent evidence
- **Found (E4c, 2026-10-11):** NYT's 23 decisive social verdicts came from 9 articles, one used in 10; Fox's 21 from 8,
  one in 9. The Bradley–Terry model treats each verdict as independent, so a single article's coverage of a single story
  dominates the outlet and the reported sd is too small.
- **Proposal:** hierarchical Bradley–Terry: θ_article = θ_outlet + ε_article, ε ~ N(0, σ_a²) (σ_a estimated); verdicts
  inform the article, articles inform the outlet. Re-judging the same article then yields diminishing outlet information,
  so the planner's information gain prefers new articles without any extra rule, and the uncertainty becomes honest.
- **Test:** simulator with article effects (does the flat model under-cover?); on real data, compare outlet sds and
  split-half stability flat vs hierarchical.
- **Implemented 2026-10-11:** `pipeline/rate/hierarchical.py` (outlet spread τ and article spread σ_a both
  estimated by Laplace evidence and averaged over a grid), `plan_joint_hier` (diminishing returns from the covariance),
  measuring loop switched over. Real data: ICC 0.13–0.19 (article-to-article variation dominates), outlet sds widen
  (± 0.46 → ± 0.64), split halves agree more (0.44 → 0.50, mean of 30 splits). Simulation E5: flat 90 % intervals cover
  0.79–0.83 under article reuse, hierarchical ≈ 0.85; no harm without article effects. ADR-009, docs/FINDINGS.md (F1).
- **Status:** accepted 2026-10-11

### A50 — What BasedNews measures: framing of the same event, not the whole outlet
- **Statement:** positions come from pairwise framing of articles about the same event, news with news and opinion with
  opinion (A02, A43). They do not capture story selection (what an outlet chooses to cover) or the volume of opinion.
  External outlet ratings (AllSides) include both, so agreement with them has a ceiling below 1; disagreement is a
  finding to explain, not a target to tune toward (E4c).
- **Status:** open

### A51 — Every paid call is archived in full, so later analyses never have to buy it again (2026-10-11)
- **Rule:** each judge call writes a `judge_call_log` row (`pipeline/judge/calllog.py`), also when every attempt failed:
  - the exact request (model, messages with the rendered articles, schema, reasoning effort), zlib-compressed + sha256;
  - the provider's full response (returned model snapshot, response id, system fingerprint, finish reason, cached and
    reasoning tokens);
  - timing, attempts and retry errors (API keys scrubbed);
  - context: measure run id, batch, the planner's score at pick time, estimated cost, the outlets' and articles'
    positions and sds when the pair was picked, story run, budget left, spreads (τ, σ_a);
  - a fingerprint of the judge's source files and the SDK version.
  `judge_calls` / `judgments` / `api_usage` stay the lean tables the fits read.
- **Why:** article texts and headline histories change after re-cleaning, so the stored request is the only exact
  record of what the judge saw. Re-analyses (length lean, model drift, a different rating model, a replay with another
  judge) then cost nothing. A failed call inside a batch no longer aborts the batch: it is logged and the rest is kept.
- **Depends:** 3.2 client, 3.3 measure loop, every caller of `record()` (judge eval, E4b, model comparison).
- **Cost:** measured on 400 real pairs: the compressed request is 3.7 kB median for full text (p90 6.7 kB) and 1.3 kB
  for summaries; response and context add ≈ 2 kB (estimate). 10,000 calls ≈ 50 MB. Text stays in the local, git-ignored database and
  is never served (A10).
- **Not recoverable:** the 2,126 measurement calls made before 2026-10-11 17:00 UTC have only the lean tables.
- **Test:** `uv run python -m pytest -q tests/test_calllog.py` (fake client); after the next paid run
  `uv run python -m pipeline.judge.calllog` → every paid call since the log started has a full row, hashes match.
- **If wrong / too big:** move `request_z` to files or object storage behind `calllog.save/load`; nothing else changes.
- **Status:** open (built 2026-10-11; first real check at the next paid run)

### A52 — A bounded data window: bulky inputs are deleted once processed; verdicts and aggregates are kept (2026-10-11)
- **Rule:**
  - **Kept forever (small):** verdicts (`judge_calls`, `judgments`, ≈ 50 MB/year at the AI cap), positions per period,
    article metadata (outlet, title, URL, date), `runs`.
  - **Deleted after ≈ 30 days, once no step will read them again:** article full text and summaries, embeddings, and
    story runs older than the latest.
  - **Moved to the S3 history lake after ≈ 30 days:** call-log requests (A51). Verdicts, positions and `runs` are
    also written there monthly as Parquet (ADR-010), the permanent copy.
  - Archive-sample articles keep their text until their period has been judged.
- **Why:** if everything were kept, the database would grow ≈ 5–7 GB/year, plus ≈ 10 GB of story runs if they were
  never pruned. Almost all of it is inputs that are only read inside the 72–96 h story window. With the window the hot
  database stays ≈ 0.5 GB, small enough for SQLite-in-S3 (ADR-010). Verdicts stay because a better model is refitted
  on them (as ADR-009 was). Holding publishers' text for a month at most also fits the text policy (A10).
- **Depends:** ADR-010, 2.2–2.3 (re-embedding needs text inside the window), 3.1 (candidates come only from the window),
  the trend model (A22, which needs verdicts per period, not text).
- **Test:** a retention job with a dry-run that reports what it would delete. Its eval: hot database size per week,
  and refits from the kept tables alone reproduce the stored positions.
- **If wrong:** if text is needed again (for example to re-judge with a new prompt), the window has to be longer, or
  re-judging is limited to recent stories. That is one config value.
- **Estimates:** from 2 days of live ingest (≈ 1,600 articles/day, which may include a first-pull burst); re-measure
  after a month.
- **Built 2026-10-12** (`pipeline/retention/run.py`, dry run unless `--apply`): window 30 days, the latest 2 story runs
  kept; pruned articles keep their row and get `pruned_at`, so "no text" is never mistaken for "not an article"
  (normalize skips them, the ingest eval leaves them out of its text share). The call log's request blobs leave the
  database only for closed months whose lake file was read back and every hash checked; the `lake_files` table is the
  index. The backup and legacy tables are reported, not touched (VERIFY.md). On a copy of today's database: 107 → 93 MB,
  lake 2.9 MB.
- **Status:** accepted in principle 2026-10-11; built 2026-10-12; window length open

### A56 — OpenAI embeddings at 512 dimensions, with a vector cache by text (2026-10-12)
- **Rule:** the maintainer chose the OpenAI API for embeddings (2026-10-12), so the job image carries no local model
  (0.43 GB instead of 2.2 GB). The model is `openai:text-embedding-3-small@512`: the API shortens the vectors to 512
  dimensions. 2.1 normalize keeps every vector it buys in `text_vectors` by text hash, so an hourly run pays only for
  new headlines and texts; entries unused for 30 days are dropped by retention (A52). Every batch is logged in
  `api_usage` and refused once the monthly cap is reached (A48).
- **Why:** normalize compares every headline on each run. Without the cache that is ≈ 9,000 titles an hour now and
  ≈ 50,000 once the 30-day window is full: up to ≈ $10/month spent re-buying the same vectors. 1,536 dimensions would
  make the 30-day window ≈ 0.3 GB of vectors alone; 512 keeps it ≈ 0.1 GB (hot database target 0.5 GB, A52), at a small
  loss in retrieval quality that the calibration (A47) measures on our own labelled pairs.
- **Depends:** 2.1, 2.2, 2.3 thresholds (A47 profile per model name), retention, the image.
- **Test:** `tests/test_judge.py` (cache pays once per text, size requested, usage logged, cap refusal); the calibration
  run (paid, ≈ $0.03) reports precision and recall against MiniLM.
- **If wrong:** another size or model is a new name (`@1024`, `-large`) and a new calibration; vectors are stored per
  model name, so nothing mixes.
- **E7 (2026-10-12, $0.045):** 512 matches 1536 (recall +0.015 [−0.015, +0.044]; the API's 512 = 1536 truncated). Against
  MiniLM at MiniLM's precision, recall 0.42 vs 0.83, but the label set was chosen with MiniLM; threshold-free on its
  least biased part the models tie. The A47 rule's threshold (0.62) would halve story recall; ≈ 0.44 trades precision
  for recall. Default not switched yet (docs/experiments/e7_results.md).
- **E7b (2026-10-12, free, the unbiased re-test):** 40 random anchors, both models' top 4 pooled, 270 pairs labelled
  blind. OpenAI @512 ahead on every measure (AUC 0.965 vs 0.925; clustering F1 0.71 vs 0.60 at threshold 0.56 vs
  0.60). E7's MiniLM comparison is withdrawn. Default switched; story threshold 0.56 (pre-registered rule).
- **Input limit (2026-10-15 incident):** every input is cut at the API's 8,192-token limit counted with the
  model's tokenizer (`cl100k_base`, baked into the image), not by characters: Greek costs ~5x the tokens of
  English, and one over-long Greek article failed the embed step every hour for a day. An input the API still
  rejects loses only itself (logged as `embedding_rejected`); the step goes on. Tests: tests/test_embed_limits.py.
- **Status:** accepted 2026-10-12 (size from E7, model and threshold from E7b)

### A53 — A job step's output goes on only if a few eval numbers are in range (2026-10-12)
- **Rule:** after each step the job runner (`pipeline/job/`) runs a gate: a few numbers from the module's eval, each
  against one threshold in `pipeline/job/gates.py`. A failed gate stops the job; in production the database is then
  not uploaded, so later steps and the site keep the last good data. Every step leaves a record (commit, arguments,
  table counts before and after, the gate's numbers, error and traceback) in `job_runs` and as JSON.
- **Thresholds** (measured on 2026-10-12 in brackets): usable outlets ≥ 80 % (99 %); newest live article ≤ 6 h old,
  outlets active in 72 h ≥ 75 % (99 %); normalize removes ≤ 5 % of articles in one run; embedding coverage of the story
  window ≥ 98 % (100 %); largest story ≤ 5 % of the window (0.8 %), ≥ 100 multi-outlet stories (701); measure: every
  paid call logged (A51), ≤ 20 % failed calls, spend ≤ budget + $0.02, positions saved; export: the payload check;
  retention: database ≤ 1 GB, integrity ok, refit reproduces the stored positions.
- **Why:** a failure should name the step and the number, and bad data should never reach the next step or the site.
- **Depends:** every module's output tables; ADR-010's "eval as a gate".
- **Test:** `tests/test_job.py`; on real data the gates passed on a copy of the database (VERIFY.md, 5 · Operate).
- **If wrong:** a threshold that blocks good runs, or passes bad ones, is a one-line change in `gates.py`; the record
  shows the measured value, so the new threshold can be set from data.
- **Status:** open (thresholds from one day of data; revisit after a month)

### A54 — The latest fit is exported every run but published only by the maintainer's decision (2026-10-12)
- **Rule:** the export (4.2) always writes `plane-latest.json`, labelled `validated: false`. The production job uploads
  it to the public site only when `BASEDNEWS_PUBLISH_POSITIONS=1`; locally it shows under "Latest fit".
- **Why:** positions are results only after validation (no claims during the pipeline phase). Building the file every
  run keeps the export tested without making anything public.
- **Depends:** 4.2 export, the job's storage wrapper, the site's "Latest fit" view.
- **Test:** `tests/test_storage.py` (not uploaded unless the flag is 1).
- **If wrong:** one environment value in the task definition.
- **Status:** open — the maintainer decides before the first deploy (VERIFY.md)

### A55 — One writer at a time for the SQLite file in S3 (2026-10-12)
- **Rule:** the four jobs are separate state machines, so they could overlap (a daily judge run and the
  hourly refresh). Each job therefore takes a lock object (`db/LOCK`, created only if absent) before it downloads the
  database and releases it after its uploads; a second job waits up to 45 minutes, then exits 5; a lock older than
  3 hours (a dead task) is taken over. As a second guard the upload is
  conditional: S3 accepts it only if the stored object is still the version the step downloaded (If-Match on the
  ETag); otherwise the step exits 4 and nothing is overwritten. The WAL is folded into the file and `quick_check` must
  pass before the upload; the first upload (`push-db`) never overwrites. S3 versioning keeps earlier versions.
- **Why:** two jobs that both download, change and upload would silently lose one job's work.
- **Depends:** `pipeline/job/storage.py`, the Step Functions concurrency setting (Terraform, 2.6), S3 conditional writes.
- **Test:** `tests/test_storage.py` (a second writer uploads mid-step → exit 4, theirs kept); the S3 adapter against a
  fake client. Not yet run against real S3 (first deploy, VERIFY.md).
- **If wrong:** if jobs must run in parallel or the file passes ~1 GB, move to Postgres behind `store.py` (ADR-010).
- **Status:** open (first real check at the first deploy)

### A57 — CI deploys code but never changes infrastructure (2026-10-12)
- **Rule:** GitHub Actions assumes one of two OIDC roles. `deploy` (main branch only) may push to the one ECR
  repository, write the site bucket except `data/` (denied explicitly), and invalidate the CloudFront cache. `plan`
  (pull requests) is read-only and is denied the private bucket's objects and the secrets. `terraform apply` is run
  by the maintainer with their own admin login.
- **Why:** a role that can apply Terraform can do anything in the account. Keeping apply manual means a compromised
  workflow or dependency can at worst ship a bad image or page, which the next push replaces.
- **Depends:** `infra/main/github.tf`, `.github/workflows/ci.yml`, ADR-010 (changed: it said CI applies).
- **Test:** after step 3, a pull request shows a plan; a push to main deploys; the deploy role cannot delete `data/`.
- **If wrong:** if manual applies become a bottleneck, add a protected-environment apply job with required approval.
- **Status:** accepted (design); first real check at the first deploy

### A58 — Story pairs are judged daily as they appear; nothing waits for a calendar week (2026-10-12)
- **Rule:** every job rolls: vectors each hour as articles arrive, stories re-grouped each hour over the last 7 days,
  and the judge runs daily on the latest story run with a daily budget. A story is not held back until it "settles":
  when another outlet covers it later, its article joins the story in the next hourly run and only adds **new** pairs;
  pairs already judged are never bought again.
- **Why:** the maintainer's call (2026-10-12). A first design held a story back until its newest article was 4 days
  old, but a big ongoing story keeps getting articles and would never be judged; settling per article fixed that but
  delayed every position by days for no gain in what is bought.
- **Dial:** `pipeline.measure.run --settle-hours N` (default 0, `SETTLE_HOURS`) holds back articles younger than N
  hours, so the planner chooses from a fuller pool at the cost of freshness. Tested in `tests/test_settled.py`.
- **Depends:** 2.3 (hourly re-grouping, 72 h max gap), 3 (judged pairs are never re-bought), the `judge` schedule.
- **If wrong:** if judging early buys clearly worse pairs (for example most of a story's pairs come a day later), set
  the dial to 24–96 h; one number.
- **Status:** accepted 2026-10-12


### A59 — The plane shows smoothed history and fits its view to the content, per axis (2026-10-13)
- **Rule:** each dot drags its whole history (first month → now) as one trail, fading and thinning with age. The
  monthly series is smoothed for display only (Gaussian, σ = 2 months, Catmull-Rom between months), so trails read as
  a string, not a random walk. The view is the box around every dot and trail at the chosen time, fitted per axis and
  eased (no fixed centre); one axis may be zoomed at most 3× the other. Ovals are not part of the fit and fade when
  their radius passes ~40 px (the less sure, the vaguer). Every page owns one nav item and the page never rests on a
  border: when scrolling stops with a border on screen it settles on the side of the pill's peak. The same holds for a
  half-folded masthead (round 3): it settles open or docked.
- **Why:** the maintainer's design review of round 1 (2026-10-13): trails "should go back to the beginning", be smooth
  "like dragging a string", and "the board should be fitted to see that in both axis"; the pill "should not allow
  stopping on the border between two pages".
- **Depends:** `frontend/prototypes/common.js` (`Plane` options `smooth`, `maxStretch`, `ovalSize`; `PillNav`), A22
  (monthly positions), A25 (ovals), A26 (origin). Applies to the Angular site once a design is ported.
- **Test:** `?selftest` on any round-2 page parks the scroll on and off each border and checks where it settles
  (`body[data-selftest]`, pass on b1/b2/b3 at 1440×900). Smoothing and fit: by eye, `?country=…&t=…`.
- **If wrong:** smoothing can hide a real sudden shift (an owner change shows as a 4–5-month glide): set `smooth: 0`
  or lower. A fitted, per-axis view exaggerates small spreads: set `maxStretch: 1` for equal axes, or fit once over
  all time for a fixed board. All three are single options.
- **Status:** design prototype, 2026-10-13

### A60 — With dozens of countries, colour means region; judgment examples come from the v2 eval run (2026-10-13)
- **Rule:** the world plane colours a country by its region (six hues in a fixed order, checked with a colour-blindness
  validator on the paper colour); a country's identity comes from its label and its table row, and labels that would
  overlap are hidden until hovered. In a country view the reference outlets take the six hues in order and the extra
  outlets share one muted ink, drawn hollow. Judgment examples on the site are real judge outputs with reasons from the
  prompt-v2 eval run (the live prompt v4 gives no reasons), chosen by a fixed rule, shown as outlet, headline, link,
  verdict and reason (A10).
- **Why:** the request for "all of Europe and NA" (41 countries) and for examples of judgments (2026-10-13). More than
  about eight categories cannot each get a hue that people can tell apart; reasons only exist for v1/v2 calls.
- **Depends:** `frontend/prototypes/paper.js` (`REGION`, `EXTRA`), `common.js` (`placeLabels`, `loadData` options),
  `make_judgment_examples.py`, `judgments.json` (git-ignored until publishing examples is decided).
- **Test:** the palette check passes (one adjacent pair in the 6–8 ΔE colour-blind band, legal with labels); examples:
  `PYTHONPATH=. uv run python frontend/prototypes/make_judgment_examples.py` prints the rule's picks.
- **If wrong:** v2 reasons may not describe how v4 judges — showing them could mislead. Then either buy reasons for a
  few v4 calls with `pipeline/judge/diagnose.py` (paid, needs a go) or show v4 verdicts without reasons.
- **Decision (maintainer, 2026-10-14):** no named judgments on the public site — examples are shown anonymized
  ("A frames it further left than B, because …"), without outlet names, headlines or links. The prototype's named
  examples stay local.
- **Status:** design prototype; anonymized examples decided 2026-10-14

### A61 — Daily history from 2010-01-01 for the five reference outlets of 41 countries, built locally (2026-10-14)
- **Rule:** the history is daily, 2010-01-01 to today, for the 5 reference outlets of each of the 41 countries
  (`data/history_outlets.json`, 205 outlets; review table `docs/decisions/history-reference-outlets.md`). It is
  crawled and judged on the maintainer's own machine, never deployed; only its verdicts and positions are merged into
  production. Every verdict carries the date of its articles, and positions are fitted per day over all days, so
  outlets' and countries' paths over time come out directly and history can be filled in after live judging starts.
- **Why:** the maintainer (2026-10-14): seeing how outlets and countries change from 2010 on is a main point; daily
  resolution keeps same-story matching valid and paths smooth; deploy within ~3 days, not after a month.
- **Choices inside (to confirm):** two-language markets split by population (BE 3+2, CH 3+2, CA 4+1); 14 countries
  without a DNR market use provisional outlets; brands younger than 2010 start when they did.
- **Depends:** `pipeline/sources/dnr.py`, `pipeline/backfill/coverage.py` (archive days per outlet and year), the backfill
  (A23, A24 politeness), A22 (dynamic positions), A29 (reference tier), A52 (retention must not delete unjudged text).
- **Test:** coverage report per outlet and year (`data/history_coverage.json`); the dynamic fit recovers simulated drift.
- **If wrong:** the outlet list is data; the start date is one parameter; the fetch can densify later (coarse to fine).
- **Update (maintainer, 2026-10-14, later):** start **2015-11-01** (a year before the 2016 US election), not 2010; the
  history judge is a **local model on the maintainer's RTX 5080** (after a blind check against gpt-5.4-mini); the 205-outlet
  list is accepted as drafted; coverage need not be complete — the top stories per country and day are enough if they
  give pairs.
- **Sources (researched 2026-10-14):** the Wayback Machine allows ≈15 requests/min and blocks bulk traffic (Sept 2026),
  so it only fills gaps. GDELT 2.0 records nearly every online article since Feb 2015 with domain, URL, time and
  English-normalised people/organisations/places/themes (page titles only in later years); probed: 16 of our outlets in
  one 15-minute file of Nov 2015. Plan: GDELT (BigQuery) to find each country's top stories per day → text from the
  live article pages (headline + description, parallel across publishers) → local judge → daily fit.
- **Coverage (accepted by the maintainer 2026-10-14, "good enough"):** live feeds for 172/205 reference outlets
  (`data/outlets.json` v4); history in GDELT for 186/205 (≥ 1,000 articles, Nov 2015 → Oct 2026, 60 M articles, 0.52 TB
  scanned, $0). Gaps: outlets without a readable feed (worst: Mexico, 1/5 live) and young or renamed outlets in the
  history; fixes later (homepage mode; old domains in November's free tier).
- **Status:** decided 2026-10-14

### A62 — The pipeline heals itself; an email means it could not (2026-10-15)
- **Rule:**
  - the lock is a lease (60 s heartbeat, 5 min takeover, 80 min hold limit, owner-only release, fencing before
    upload);
  - SIGTERM ends a step cleanly;
  - a failed step is retried once (not the paid one);
  - CI smoke-tests the image before it becomes `prod` and tags the outgoing one `prev`;
  - a Lambda rolls `prod` back to `prev` after two failed refreshes in a row with a new image that never succeeded;
  - the refresh alarms on 3 hours without success, not on single failures (ADR-012).
- **Why:** the maintainer (2026-10-15): "It's totally unacceptable for me to fix it". Three manual lock deletions in
  36 hours, all caused by a dead job's lock.
- **Depends:** `pipeline/job/storage.py`, `pipeline/job/selftest.py`, `infra/main/{pipeline,jobs,alerts,rollback}.tf`,
  `infra/main/rollback/handler.py`, `.github/workflows/ci.yml`.
- **Test:** `tests/test_lease.py`, `tests/test_rollback.py`, `selftest` in CI. In production, the next crash should
  show `lock_taken_over` in the logs and no email.
- **If wrong:** if 5 min is too short for a slow heartbeat (S3 latency), raise `LEASE_S` (one constant). If rollbacks
  hit good images (e.g. a source outage right after a deploy), raise `MIN_FAILURES` or require a failed step whose
  record shows a code error.
- **Status:** built 2026-10-15; live once pushed (code) and applied (infrastructure)
