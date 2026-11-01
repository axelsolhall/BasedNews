"""3 · Measure, live (A82): the history's model carried on, one day per run, on today's news — same planner, same update.

    uv run python -m pipeline.measure.live --dry-run              # the state, today's candidates and first picks; no calls
    uv run python -m pipeline.measure.live --judge simulated      # the whole day, no key, no spend (stored as "simulation")
    uv run python -m pipeline.measure.live --budget-usd 0.15      # real: the OpenAI judge, up to the day's budget

Each run: the positions so far — the newest live_positions day, else the history's last day (prior.json) — grow by
the days since (positions.grow); the planner takes the newest story run's pairs not judged before, first for outlets
below MIN_PER_WEEK comparisons over the last 7 days (A69), then the pairs most likely in the wrong order
(planner.iter_plan_independent), until their estimated cost reaches the budget; every verdict moves only its two
outlets (positions.update_independent); today's positions go to live_positions and live_country_positions (A08 centre
weights: the history's comparisons and live's, decayed by HALF_LIFE_DAYS) and replace prior_positions (A79's trash
rule values articles by them). The judge is OpenAI's, not the history's local model (the maintainer, 2026-11-01);
prompt, planner and update are the history's.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.history import positions as HP
from pipeline.ingest.feeds import utc_now_iso
from pipeline.judge import prompt as P
from pipeline.judge.client import JudgeCallFailed, OpenAIJudge, SimulatedJudge
from pipeline.measure.run import (A_FULL, A_SUMMARY, candidates, record, record_failure, relevance_so_far,
                                  with_angle)
from pipeline.plan.planner import PlannerConfig, iter_plan_independent
from pipeline.rate.model import BTConfig
from pipeline.settings import load_settings
from pipeline.sources.registry import load_outlets, reference_groups

PURPOSE = "live"
PRIOR = Path("pipeline/measure/prior.json")
MIN_PER_WEEK = 5                 # A69, as the history
SIGMA_A = 0.5                    # carried in the state only; the independent model never reads it
WORKERS = 6


def today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


def load_state(conn, prior: Path | None = None) -> tuple[str, dict[str, HP.AxisState]]:
    """(the state's day, {axis: state}) — the newest live day, or the history's last day."""
    last = conn.execute("SELECT max(day) FROM live_positions").fetchone()[0]
    if last is None:
        pr = json.loads((prior or PRIOR).read_text())
        day = pr["as_of"]
        src = {(o, ax): tuple(v[ax]) for o, v in pr["outlets"].items() for ax in P.AXES if ax in v}
    else:
        day = last
        src = {(o, ax): (m, s) for ax, o, m, s in
               conn.execute("SELECT axis, outlet, mean, sd FROM live_positions WHERE day = ?", (last,))}
    outlets = sorted(o.id for o in load_outlets())
    tau = BTConfig().prior_sd
    states = {}
    for ax in P.AXES:
        s = HP.start(outlets, ax, tau, SIGMA_A)
        for k, o in enumerate(outlets):
            m, sd = src.get((o, ax), (0.0, tau))         # an outlet new since the history: the prior
            s.mean[k], s.cov[k, k] = m, sd * sd
        states[ax] = s
    return day, states


def worth(conn, day: str, prior: Path | None = None) -> dict[str, float]:
    """A08 centre worth on `day`: the history's (on its last day) decayed since, plus live's comparisons at their age."""
    pr = json.loads((prior or PRIOR).read_text())
    t = dt.date.fromisoformat(day)
    f = 0.5 ** ((t - dt.date.fromisoformat(pr["as_of"])).days / HP.HALF_LIFE_DAYS)
    w = Counter({o: v.get("worth", 0.0) * f for o, v in pr["outlets"].items()})
    for a, b, d in conn.execute("SELECT first_outlet, second_outlet, substr(at, 1, 10) FROM judge_calls WHERE purpose = ?",
                                (PURPOSE,)):
        k = 0.5 ** ((t - dt.date.fromisoformat(d)).days / HP.HALF_LIFE_DAYS)
        w[a] += k
        w[b] += k
    return w


def weights(conn, day: str, prior: Path | None = None) -> dict[str, dict[str, float]]:
    w = worth(conn, day, prior)
    return {c: {o: (math.log(w[o]) if w[o] > 1.0 else 0.0) for o in g} for c, g in reference_groups(load_outlets()).items()}


def week_counts(conn, day: str) -> Counter:
    """Live comparisons per outlet over the 7 days ending on `day` (when they were judged)."""
    since = (dt.date.fromisoformat(day) - dt.timedelta(days=6)).isoformat()
    return Counter(o for a, b in conn.execute("SELECT first_outlet, second_outlet FROM judge_calls WHERE purpose = ? "
                                              "AND substr(at, 1, 10) >= ?", (PURPOSE, since)) for o in (a, b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", choices=["openai", "simulated"], default="openai")
    ap.add_argument("--budget-usd", type=float, default=0.15, help="the day's budget: picks stop at their estimated cost")
    ap.add_argument("--dry-run", action="store_true", help="state, candidates and the first picks; no calls, nothing saved")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-angle", action="store_true", help="A70 off: one average chance of an answer per axis")
    args = ap.parse_args()
    settings = load_settings()
    conn = store.connect()
    rng = np.random.default_rng(args.seed)
    purpose = PURPOSE if args.judge == "openai" else "simulation"     # simulated verdicts never mix with real ones
    day = today()
    since, states = load_state(conn)
    gap = (dt.date.fromisoformat(day) - dt.date.fromisoformat(since)).days
    for st in states.values():
        HP.grow(st, gap)

    cands, ctx = candidates(conn, settings, relevance_so_far(conn, purpose), purpose)
    taught = 0
    if not args.no_angle:
        cands, taught = with_angle(conn, cands, purpose, settings.embed_model)
    known = set(states[P.AXES[0]].outlets)
    cands = [c for c in cands if c.outlet_a in known and c.outlet_b in known]
    have = week_counts(conn, day)
    quota = {o: MIN_PER_WEEK - have[o] for o in {c.outlet_a for c in cands} | {c.outlet_b for c in cands}
             if have[o] < MIN_PER_WEEK}
    month = store.spent_this_month(conn)
    budget = min(args.budget_usd, max(0.0, settings.monthly_ai_usd - month)) if args.judge == "openai" else args.budget_usd
    print(f"live {day} · state of {since} grown {gap} day(s) · story run {ctx['run']} · {len(cands):,} candidates · "
          f"{len(quota)} outlets below {MIN_PER_WEEK}/week · angle {'from %d verdicts' % taught if taught else 'off'} · "
          f"budget ${budget:.2f} (month so far ${month:.2f} of ${settings.monthly_ai_usd:.2f})")

    by_key = {(c.event_id, frozenset((c.outlet_a, c.outlet_b))): c for c in cands}
    posts = {ax: s.posterior() for ax, s in states.items()}
    cfg = PlannerConfig(mode="variance_per_cost", target_sd=0.0, batch_size=len(cands))
    picks, est = [], 0.0
    for r in iter_plan_independent(posts, cands, cfg, rng=rng, quota=quota):
        c = by_key[(r.event_id, frozenset((r.first_outlet, r.second_outlet)))]
        if est + c.cost > budget:
            break
        est += c.cost
        picks.append((r, c, "positive" if rng.random() < 0.5 else "negative"))
    print(f"{len(picks)} picks · estimated ${est:.4f}")
    if args.dry_run:
        for r, c, _ in picks[:10]:
            print(f"  {r.first_outlet:>16} vs {r.second_outlet:<16} {r.event_id}  P(wrong order) {r.expected_info_gain:.2f}")
        return

    judge = OpenAIJudge(settings) if args.judge == "openai" else SimulatedJudge(sorted(known), seed=args.seed)

    def ask(item):
        r, c, direction = item
        try:
            if args.judge == "openai":
                return judge.compare(ctx["texts"][(r.event_id, r.first_article_id)],
                                     ctx["texts"][(r.event_id, r.second_article_id)], direction)
            return judge.compare_outlets(r.first_outlet, r.second_outlet, c.discrimination, direction)
        except JudgeCallFailed as e:
            return e

    run_id = f"{utc_now_iso()}_{uuid.uuid4().hex[:6]}"                 # A51: groups the calls of one run
    context = {"run_id": run_id, "story_run": ctx["run"], "prompt_version": P.PROMPT_VERSION, "live_day": day}
    after = conn.execute("SELECT coalesce(max(rowid), 0) FROM judge_calls").fetchone()[0]
    with ThreadPoolExecutor(WORKERS) as pool:
        answers = list(pool.map(ask, picks))
    spent, failed = 0.0, 0
    for (r, c, direction), v in zip(picks, answers):
        if isinstance(v, JudgeCallFailed):
            record_failure(conn, purpose, r, v, context)
            failed += 1
        else:
            record(conn, purpose, r, r.event_id.rsplit(":", 1)[1], v, direction, event_id=r.event_id, context=context)
            spent += v.cost_usd
    conn.commit()
    rows, _ = HP.new_verdicts(conn, purpose, after, A_FULL, A_SUMMARY)
    for ax, st in states.items():
        HP.update_independent(st, rows[ax])
    if purpose == PURPOSE:                                              # a simulated day is not a live day
        HP.record_day(conn, day, states, weights(conn, day), prefix="live")
        # A79's trash rule values articles by these positions: keep them today's
        conn.execute("DELETE FROM prior_positions")
        conn.executemany("INSERT INTO prior_positions VALUES (?,?,?,?,?,?)",
                         [(o, ax, float(st.mean[k]), float(np.sqrt(st.cov[k, k])), day, "live")
                          for ax, st in states.items() for k, o in enumerate(st.outlets)])
        conn.commit()
    print(f"{len(picks) - failed} verdicts{f' (+{failed} failed, logged)' if failed else ''} · spent ${spent:.4f} · "
          f"{sum(len(v) for v in rows.values())} axis answers moved the positions"
          f"{' · live_positions ' + day if purpose == PURPOSE else ' (simulation: not saved)'}")


if __name__ == "__main__":
    main()
