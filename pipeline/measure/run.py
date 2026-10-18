"""3 · Measure: plan → judge → rate on real story pairs, until the budget is spent (A46–A48).

    uv run python -m pipeline.measure.run --dry-run                       # plan + show prompts + cost estimate, no calls
    uv run python -m pipeline.measure.run --judge simulated --max-calls 300   # full loop, no key, no spend
    uv run python -m pipeline.measure.run --budget-usd 1                  # real: OpenAI judge, stops at $1

Candidates: one article per outlet per story (latest 2.3 run), judged as they appear: a later article that joins a story
only adds new pairs (A58; `--settle-hours` can hold back articles younger than N hours) — pairs across outlets, like with
like — both full text or both the outlet's own summary (A21), both news or both opinion (A43); same headline + text pairs
are known ties (A44); a story pair already judged is never bought again. Each call asks both axes (A31) with the order randomised (A04).
After every batch the posterior is refitted (3.3) from all judgments of the current prompt version.
"""
from __future__ import annotations

import argparse
import datetime as dt
import uuid
from collections import Counter, defaultdict

import numpy as np

from pipeline import store
from pipeline.contracts import HeadlineVersion
from pipeline.ingest.feeds import utc_now_iso
from pipeline.judge import prompt as P
from pipeline.judge import calllog
from pipeline.judge.client import JudgeCallFailed, OpenAIJudge, SimulatedJudge
from pipeline.judge.render import render_article
from pipeline.plan.planner import Candidate, PlannerConfig, plan_joint_hier
from pipeline.rate.hierarchical import HierarchicalBT
from pipeline.rate.model import outlet_sds
from pipeline.settings import load_settings
from pipeline.sources.registry import load_outlets, reference_groups

A_FULL, A_SUMMARY = 1.0, 0.6                  # A30: sharpness of a full-text vs summary comparison (to be estimated, M7)
PRIOR_RELEVANCE = {"econ": 0.5, "social": 0.8}  # A32: P(story has an angle on the axis) before any verdicts
PROMPT_OVERHEAD_TOKENS, OUTPUT_TOKENS = 450, 250   # measured 2026-10-11, prompt v3: ~210 out (reasoning "low")
SETTLE_HOURS = 0    # A58: judge pairs as they appear (a dial: e.g. 96 holds back articles younger than 4 days)


def tokens(text: str) -> int:
    return len(text) // 4 + 1


def load_items(conn, settle_hours: float = 0, now: dt.datetime | None = None, run: str | None = None):
    """A story run (the latest unless given) → {story: {outlet: article}}. With settle_hours, only articles at least
    that old (A58)."""
    cutoff = ((now or dt.datetime.now(dt.UTC)) - dt.timedelta(hours=settle_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    run = run or store.latest_event_run(conn)
    rows = conn.execute("""SELECT e.event_id, a.id, a.outlet_id, a.title, a.summary, a.full_text, a.genre, a.published
                           FROM event_members e JOIN articles a ON a.id = e.article_id
                           WHERE e.run_id = ? AND a.canonical_id IS NULL AND a.kind IS NULL""", (run,)).fetchall()
    ev, n_all = defaultdict(dict), len(rows)
    rows = [r for r in rows if settle_hours <= 0 or (r["published"] or "9") <= cutoff]   # ISO strings compare in time order
    for r in rows:
        ev[r["event_id"]].setdefault(r["outlet_id"], r)        # one article per outlet per story
    hist = store.headline_history(conn, [r["id"] for r in rows])
    ties = {frozenset((t["a"], t["b"])) for t in conn.execute("SELECT a, b FROM same_text_pairs WHERE run_id = ?", (run,))}
    return run, ev, hist, ties, {"articles": n_all, "settled": len(rows)}


def judged_pairs(conn, purpose="measure") -> set[frozenset]:
    """Pairs already bought with the CURRENT prompt version (a new prompt re-measures from scratch)."""
    return {frozenset((r["first_article"], r["second_article"])) for r in
            conn.execute("SELECT first_article, second_article FROM judge_calls WHERE purpose = ? AND prompt_version = ?",
                         (purpose, P.PROMPT_VERSION))}


def rendered(art, hist, kind: str) -> str:
    versions = hist.get(art["id"]) or [HeadlineVersion(art["id"], art["title"] or "", art["published"], "", "original")]
    body = art["full_text"] if kind == "full" else art["summary"]
    return render_article(versions, body or "", outlet_id=art["outlet_id"])


def candidates(conn, settings, relevance, purpose="measure", countries=None, settle_hours: float = SETTLE_HOURS,
               now: dt.datetime | None = None, run: str | None = None,
               published_after: str | None = None) -> tuple[list[Candidate], dict]:
    run, ev, hist, ties, stats = load_items(conn, settle_hours, now, run)
    done = judged_pairs(conn, purpose)
    country = {o.id: o.country for o in load_outlets()}
    pin, pout = settings.price(settings.judge_model)
    cands, texts = [], {}
    for eid, arts in ev.items():
        items = list(arts.values())
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                key = frozenset((a["id"], b["id"]))
                if key in ties or key in done or (a["genre"] or "news") != (b["genre"] or "news"):
                    continue
                if countries and not (country[a["outlet_id"]] in countries and country[b["outlet_id"]] in countries):
                    continue
                if published_after and min(a["published"] or "", b["published"] or "") < published_after:
                    continue                                            # out-of-sample checks: only fresh articles
                for kind, ok, disc in (("summary", a["summary"] and b["summary"], A_SUMMARY),
                                       ("full", a["full_text"] and b["full_text"], A_FULL)):
                    if not ok:
                        continue
                    ta, tb = rendered(a, hist, kind), rendered(b, hist, kind)
                    usd = ((PROMPT_OVERHEAD_TOKENS + tokens(ta) + tokens(tb)) * pin + OUTPUT_TOKENS * pout) / 1e6
                    c = Candidate(f"{eid}:{kind}", a["outlet_id"], b["outlet_id"], a["id"], b["id"], cost=max(usd, 1e-6),
                                  discrimination=disc, relevance=(relevance["econ"], relevance["social"]))
                    cands.append(c)
                    texts[(c.event_id, a["id"])], texts[(c.event_id, b["id"])] = ta, tb
    return cands, {"run": run, "texts": texts, **stats}


def with_angle(conn, cands: list, purpose: str, embed_model: str) -> tuple[list, int]:
    """A70: each candidate's own predicted chance of an answer per axis, learned from this purpose's verdicts and the
    articles' stored vectors (pipeline.plan.angle). Returns the candidates and how many verdicts taught it (0 = the
    average is kept: fewer than MIN_LABELS verdicts, or no vectors)."""
    from pipeline.plan import angle as AN
    vec_of = lambda ids: store.load_embeddings(conn, embed_model, ids)       # noqa: E731
    model = AN.learn(conn, purpose, vec_of)
    return (AN.apply(cands, model, vec_of), model.n_labels) if model else (cands, 0)


def fit(conn, outlet_ids, purpose="measure", hyper=None, since: str | None = None):
    """Hierarchical fit per axis (A49): {axis: HierPosterior}. hyper={axis: (τ, σ_a)} fixes the spreads (fast, used
    inside the loop); None averages over the grid by evidence (used at the start and for the final positions).
    since: only verdicts on articles both published from that date on (the history's planner window)."""
    window = "" if since is None else (" AND c.first_article IN (SELECT id FROM articles WHERE published >= :since)"
                                       " AND c.second_article IN (SELECT id FROM articles WHERE published >= :since)")
    rows = conn.execute("""SELECT c.first_outlet, c.second_outlet, c.first_article, c.second_article, c.text_kind,
                                  j.axis, j.first_wins FROM judgments j JOIN judge_calls c ON c.call_id = j.call_id
                           WHERE c.purpose = :purpose AND c.prompt_version = :version AND j.first_wins IS NOT NULL"""
                        + window, {"purpose": purpose, "version": P.PROMPT_VERSION, "since": since}).fetchall()
    known = set(outlet_ids)
    posts = {}
    for ax in P.AXES:
        rs = [r for r in rows if r["axis"] == ax and r["first_outlet"] in known and r["second_outlet"] in known]
        comps = [(r["first_outlet"], r["second_outlet"], r["first_article"], r["second_article"],
                  A_FULL if r["text_kind"] == "full" else A_SUMMARY) for r in rs]
        t, s_ = hyper[ax] if hyper else (None, None)
        posts[ax] = HierarchicalBT(outlet_ids, ax).fit(comps, [bool(r["first_wins"]) for r in rs], sigma_a=s_, tau=t)
    return posts, len(rows)


def best_hyper(hposts) -> dict:
    """The single (τ, σ_a) with the highest evidence per axis — held fixed inside the loop for speed."""
    out = {}
    for ax, h in hposts.items():
        out[ax] = max(h.evidence, key=h.evidence.get) if h.evidence else (h.tau, h.sigma_a)
    return out


def relevance_so_far(conn, purpose="measure") -> dict:
    """A32: share of calls where the judge found an angle on each axis, shrunk toward the prior (10 pseudo-calls)."""
    out = {}
    for ax in P.AXES:
        n, ans = conn.execute("""SELECT COUNT(*), SUM(j.verdict != 'na') FROM judgments j JOIN judge_calls c
                                 ON c.call_id = j.call_id WHERE c.purpose = ? AND j.axis = ?""", (purpose, ax)).fetchone()
        out[ax] = ((ans or 0) + 10 * PRIOR_RELEVANCE[ax]) / ((n or 0) + 10)
    return out


def record(conn, purpose, req, kind, verdict, direction="positive", event_id=None, version=P.PROMPT_VERSION,
           context=None):
    call_id = uuid.uuid4().hex
    conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (call_id, utc_now_iso(), purpose, verdict.model, version, direction, event_id,
                  req.first_article_id, req.second_article_id, req.first_outlet, req.second_outlet, kind,
                  verdict.input_tokens, verdict.output_tokens, verdict.cost_usd, verdict.raw))
    for ax, a in verdict.answers.items():
        conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)",
                     (call_id, ax, a["verdict"], P.first_wins(a["verdict"], direction), a["reason"]))
    if verdict.cost_usd:
        store.log_usage(conn, "openai", verdict.model, "judge", verdict.input_tokens, verdict.output_tokens,
                        verdict.cost_usd)
    calllog.save(conn, call_id, verdict.log, {**(context or {}), "purpose": purpose, "direction": direction})   # A51
    return call_id


def record_failure(conn, purpose, req, error: JudgeCallFailed, context=None) -> str:
    """A call whose attempts all failed: no verdict, but the request, errors and context are kept (A51)."""
    call_id = uuid.uuid4().hex
    calllog.save(conn, call_id, error.log, {**(context or {}), "purpose": purpose, "event_id": req.event_id,
                                            "first_article": req.first_article_id,
                                            "second_article": req.second_article_id}, status="failed")
    return call_id


def pick_context(r, c, posts, hyper, **extra) -> dict:
    """What the planner knew when it picked this pair (A51): its score, the estimated cost, and the current position
    and sd of both outlets and both articles per axis (articles not judged before have no entry)."""
    out = {**extra, "planner_score": r.expected_info_gain, "est_cost_usd": c.cost, "discrimination": c.discrimination,
           "relevance": list(c.relevance), "spreads": {ax: [float(v) for v in hyper[ax]] for ax in hyper}}
    for ax, h in posts.items():
        aidx = h.article_index()
        for side, o, a in (("first", r.first_outlet, r.first_article_id), ("second", r.second_outlet, r.second_article_id)):
            k = h.outlets.index(o)
            out[f"{ax}_{side}_outlet"] = [float(h.mean[k]), float(np.sqrt(h.cov[k, k]))]
            if a in aidx:
                j = aidx[a]
                out[f"{ax}_{side}_article"] = [float(h.mean[j]), float(np.sqrt(h.cov[j, j]))]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", choices=["openai", "simulated"], default="openai")
    ap.add_argument("--budget-usd", type=float, default=1.0, help="stop when this run has spent this much")
    ap.add_argument("--max-calls", type=int, default=500)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true", help="plan one batch, print a prompt and the cost estimate")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--countries", default="", help='only pairs where both outlets are in these countries, e.g. "United States,United Kingdom"')
    ap.add_argument("--workers", type=int, default=6, help="parallel judge calls within a batch")
    ap.add_argument("--settle-hours", type=float, default=SETTLE_HOURS, help="judge only articles at least this old (A58)")
    ap.add_argument("--purpose", default="measure", help="kept apart from live's verdicts, e.g. an experiment's name")
    ap.add_argument("--published-after", help="only pairs whose two articles were published at or after this (ISO)")
    ap.add_argument("--no-angle", action="store_true", help="A70 off: one average chance of an answer per axis")
    args = ap.parse_args()
    settings = load_settings()
    countries = {c.strip() for c in args.countries.split(",") if c.strip()}
    conn = store.connect()
    outlets = load_outlets()
    groups = reference_groups(outlets)
    rng = np.random.default_rng(args.seed)
    purpose = args.purpose if args.judge == "openai" else "simulation"  # simulated verdicts never mix with real ones

    cands, ctx = candidates(conn, settings, relevance_so_far(conn, purpose), purpose, countries, args.settle_hours,
                            published_after=args.published_after)
    taught = 0
    if not args.no_angle:
        cands, taught = with_angle(conn, cands, purpose, settings.embed_model)
    outlet_ids = sorted({c.outlet_a for c in cands} | {c.outlet_b for c in cands} | {o for g in groups.values() for o in g})
    groups = {c: {o: w for o, w in g.items() if o in outlet_ids} for c, g in groups.items()}
    posts, n_obs = fit(conn, outlet_ids, purpose)
    hyper = best_hyper(posts)
    held = f" · {ctx['settled']:,} of {ctx['articles']:,} articles ≥ {args.settle_hours:g} h old (A58)" if args.settle_hours else ""
    print(f"story run {ctx['run']}{held} · {len(cands):,} candidate comparisons ({sum(c.event_id.endswith(':full') for c in cands):,}"
          f" full text) · {n_obs} verdicts so far (prompt {P.PROMPT_VERSION}) · judge {args.judge}"
          f"{' ' + settings.judge_model if args.judge == 'openai' else ''} · angle model "
          f"{f'from {taught:,} verdicts' if taught else 'off (the average per axis)'}")

    if args.dry_run:
        reqs = plan_joint_hier(posts, cands, PlannerConfig(mode="variance_per_cost", target_sd=0.0, batch_size=args.batch),
                               groups=groups, rng=rng)
        by_ev = {(c.event_id, frozenset((c.outlet_a, c.outlet_b))): c for c in cands}
        est = [by_ev[(r.event_id, frozenset((r.first_outlet, r.second_outlet)))].cost for r in reqs]
        if not reqs:
            print("nothing to judge: no settled story has an unjudged pair (A58)"); return
        r0 = reqs[0]
        print(f"\nfirst planned call ({r0.event_id}, {r0.first_outlet} vs {r0.second_outlet}):\n")
        for m in P.messages(ctx["texts"][(r0.event_id, r0.first_article_id)], ctx["texts"][(r0.event_id, r0.second_article_id)]):
            print(f"[{m['role']}]\n{m['content'][:1800]}{' …' if len(m['content']) > 1800 else ''}\n")
        print(f"batch of {len(reqs)}: estimated ${sum(est):.4f} (mean ${np.mean(est):.5f}/call at {settings.judge_model} prices)"
              f" · cheapest-first plan of all candidates would cost ${sum(c.cost for c in cands):.2f}")
        return

    month = store.spent_this_month(conn)
    budget = min(args.budget_usd, max(0.0, settings.monthly_ai_usd - month))
    if args.judge == "openai":
        if budget <= 0:
            print(f"monthly AI cap reached (${month:.2f} of ${settings.monthly_ai_usd:.2f}) — nothing to do"); return
        judge = OpenAIJudge(settings)
    else:
        judge = SimulatedJudge(outlet_ids, seed=args.seed)

    spent, calls, failed, by_key = 0.0, 0, 0, {}
    for c in cands:
        by_key[(c.event_id, frozenset((c.outlet_a, c.outlet_b)))] = c
    pool = list(cands)
    run_id, batch_no = f"{utc_now_iso()}_{uuid.uuid4().hex[:6]}", 0      # A51: groups the calls of one measure run
    while calls < args.max_calls and pool:
        left = budget - spent if args.judge == "openai" else None
        reqs = plan_joint_hier(posts, pool, PlannerConfig(mode="variance_per_cost", target_sd=0.0, batch_size=args.batch),
                               groups=groups, budget_left=left, rng=rng)
        if not reqs:
            break
        batch_no += 1
        jobs = []
        for r in reqs:
            c = by_key[(r.event_id, frozenset((r.first_outlet, r.second_outlet)))]
            direction = "positive" if rng.random() < 0.5 else "negative"     # v3: alternate the question (A46)
            context = pick_context(r, c, posts, hyper, run_id=run_id, batch=batch_no, story_run=ctx["run"],
                                   budget_left_usd=left, pool_size=len(pool), prompt_version=P.PROMPT_VERSION)
            jobs.append((r, c, r.event_id.rsplit(":", 1)[1], direction, context))

        def ask(job):
            r, c, kind, direction, _ = job
            if args.judge == "openai":
                try:
                    return judge.compare(ctx["texts"][(r.event_id, r.first_article_id)],
                                         ctx["texts"][(r.event_id, r.second_article_id)], direction)
                except JudgeCallFailed as e:
                    return e                                           # recorded below; the rest of the batch is kept
            return judge.compare_outlets(r.first_outlet, r.second_outlet, c.discrimination, direction)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool_ex:
            verdicts = list(pool_ex.map(ask, jobs))                    # API calls in parallel, writes in order
        batch_failed = 0
        for (r, c, kind, direction, context), v in zip(jobs, verdicts):
            if isinstance(v, JudgeCallFailed):
                record_failure(conn, purpose, r, v, context)
                batch_failed += 1
            else:
                record(conn, purpose, r, kind, v, direction, event_id=r.event_id, context=context)
                spent += v.cost_usd; calls += 1
            pair = frozenset((r.first_article_id, r.second_article_id))   # a story pair is bought once, either variant
            pool = [p for p in pool if frozenset((p.article_a, p.article_b)) != pair]   # (a failed one waits for the next run)
        conn.commit()
        failed += batch_failed
        if batch_failed == len(jobs):
            print(f"every call in batch {batch_no} failed — stopping (errors in judge_call_log, status 'failed')")
            break
        posts, n_obs = fit(conn, outlet_ids, purpose, hyper)
        if args.judge == "openai" and spent >= budget:
            break

    posts, n_obs = fit(conn, outlet_ids, purpose)                       # final: average over the spreads
    opost = {ax: posts[ax].outlet_posterior() for ax in P.AXES}
    sd = {ax: outlet_sds(opost[ax]) for ax in P.AXES}
    fitted = utc_now_iso()
    counts = Counter()
    for r in conn.execute("""SELECT first_outlet, second_outlet FROM judge_calls WHERE purpose = ?
                             AND prompt_version = ?""", (purpose, P.PROMPT_VERSION)):
        counts[r["first_outlet"]] += 1; counts[r["second_outlet"]] += 1
    conn.executemany("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                     [(fitted, f"{P.PROMPT_VERSION}{'' if purpose == 'measure' else '-sim'}", o, ax, float(opost[ax].mean[k]), float(sd[ax][k]), counts[o])
                      for ax in P.AXES for k, o in enumerate(opost[ax].outlets)])
    conn.commit()
    verdicts = Counter(r[0] for r in conn.execute("""SELECT j.verdict FROM judgments j JOIN judge_calls c
                       ON c.call_id = j.call_id WHERE c.purpose = ?""", (purpose,)))
    print(f"\ncalls this run: {calls}{f' (+{failed} failed, logged)' if failed else ''} · spent ${spent:.4f} (month so far ${store.spent_this_month(conn):.2f} of "
          f"${settings.monthly_ai_usd:.2f}) · verdicts so far {dict(verdicts)}")
    for ax in P.AXES:
        h = posts[ax]
        measured = [k for k, o in enumerate(opost[ax].outlets) if counts[o]]
        print(f"  {ax}: median sd of measured outlets {np.median(sd[ax][measured]) if measured else float('nan'):.2f} · "
              f"order bias β {h.mean[h.n]:+.2f} · article spread σ_a {h.sigma_a:.2f}, outlet spread τ {h.tau:.2f}, "
              f"ICC {h.icc:.2f} ({len(h.articles)} articles)")
    print(f"positions saved (fitted_at {fitted}, {'real' if purpose == 'measure' else 'SIMULATED — kept apart from real verdicts'}).")


if __name__ == "__main__":
    main()
