"""The history replay (A61): a day's stories, the judgeable pairs it offers, the daily budget as a fraction of them,
the free count-only dry run and its summary, and that one failed judge call never stops the replay. Synthetic day,
real registry outlets, live's own candidate and planner code."""
import datetime as dt
import json

import numpy as np
import pytest

from pipeline import store
from pipeline.history import replay
from pipeline.judge import prompt as P
from pipeline.judge.client import JudgeCallFailed, SimulatedJudge

OUTLETS = ["nrk", "vg", "dagbladet", "svt", "aftonbladet", "dn"]
MODEL = "test-model"
DAY = dt.date(2016, 3, 10)


@pytest.fixture
def conn(tmp_path):
    """Six stories on DAY, each covered by four of the six outlets (headline + summary, like history pages)."""
    c = replay.prepare(store.connect(tmp_path / "h.sqlite"))
    rng = np.random.default_rng(3)
    centres = rng.normal(size=(6, 24))
    ids, vecs = [], []
    for s in range(6):
        for k in range(4):
            aid = f"s{s}o{k}"
            c.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, summary, text_source) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (aid, OUTLETS[(s + k) % 6], "x", f"https://x/{aid}", f"Story {s} by outlet {k}",
                       f"{DAY.isoformat()}T{8 + s:02d}:{10 * k:02d}:00Z", f"What happened in story {s}.", "history-meta"))
            ids.append(aid)
            vecs.append(centres[s] + 0.05 * rng.normal(size=24))
    V = np.array(vecs); V /= np.linalg.norm(V, axis=1, keepdims=True)
    store.save_embeddings(c, MODEL, ids, V.astype(np.float32))
    c.commit()
    replay.regroup(c, DAY, MODEL, threshold=0.8)
    return c


def test_budget_is_a_rounded_up_fraction_never_above_the_cap():
    assert replay.budget(100, 60, 0.05) == 5
    assert replay.budget(101, 60, 0.05) == 6
    assert replay.budget(10_000, 60, 0.05) == 60
    assert replay.budget(37, 60, None) == 60
    assert replay.budget(10_000, None, 0.05) == 500          # no cap given: the share alone, however busy the day
    assert replay.budget(37, None, None) == replay.CALLS_PER_DAY


def test_the_day_offers_cross_outlet_pairs_within_stories(conn):
    cands, ctx = replay.day_candidates(conn, DAY)
    assert len(cands) == 6 * 6                              # 4 articles per story → 6 pairs, 6 stories
    assert all(c.outlet_a != c.outlet_b for c in cands) and ctx["run"].startswith(DAY.isoformat())


def test_count_only_records_counts_without_marking_the_day_done(conn):
    assert replay.count_day(conn, DAY) == 36
    r = conn.execute("SELECT * FROM history_pair_counts").fetchone()
    assert r["pairs"] == 36 and r["outlets"] == 6 and r["articles"] == 24
    assert sum(json.loads(r["by_outlet"]).values()) == 72      # each pair counts for both outlets
    assert json.loads(r["by_country"]) == {"Norway": 28, "Sweden": 28}   # once per country it touches: 8 within each,
                                                                       # 20 across (8 + 20 = 28)
    assert conn.execute("SELECT count(*) FROM history_progress").fetchone()[0] == 0
    assert r["new_pairs"] == 36                                 # every article is from DAY: all pairs are new
    out = replay.summary(conn, 0.5)
    assert "36 judgeable pairs" in out and "≈ 18 calls" in out


def test_judge_day_spends_the_fraction_and_never_rebuys_a_pair(conn):
    judge = SimulatedJudge(sorted(OUTLETS), seed=0)
    rng = np.random.default_rng(0)
    calls, hyper = replay.judge_day(conn, DAY, judge, max_calls=60, rng=rng, fraction=0.25, min_per_week=0)
    assert calls == 9 and set(hyper) == {"econ", "social"}                                  # ceil(0.25 × 36)
    pairs = conn.execute("SELECT first_article, second_article FROM judge_calls WHERE purpose = 'history'").fetchall()
    assert len({frozenset(p) for p in pairs}) == 9
    cands, _ = replay.day_candidates(conn, DAY)
    assert len(cands) == 36 - 9                             # judged pairs leave the pool (A58)


class Flaky:
    """A local judge that fails every second call; thread-safe, since the replay judges in parallel."""

    def __init__(self):
        import threading
        self.n, self.lock = 0, threading.Lock()

    def compare(self, a, b, direction):
        from pipeline.judge.client import Verdict
        with self.lock:
            self.n += 1
            fail = self.n % 2
        if fail:
            raise JudgeCallFailed("server down", {"request": {"model": "judge"}, "errors": []})
        return Verdict({"econ": {"verdict": "A", "reason": ""}, "social": {"verdict": "B", "reason": ""}},
                       100, 10, 0.0, "judge", "{}", {})


def test_a_failed_call_is_recorded_and_the_day_goes_on(conn):
    calls, _ = replay.judge_day(conn, DAY, Flaky(), max_calls=6, rng=np.random.default_rng(0), parallel=4, min_per_week=0)
    assert calls == 6
    ok = conn.execute("SELECT count(*) FROM judge_calls WHERE purpose = 'history'").fetchone()[0]
    failed = conn.execute("SELECT count(*) FROM judge_call_log WHERE status = 'failed'").fetchone()[0]
    assert ok == 3 and failed == 3


def test_held_spreads_are_used_and_returned(conn):
    held = {"econ": (1.0, 0.5), "social": (0.5, 0.3)}
    calls, used = replay.judge_day(conn, DAY, SimulatedJudge(sorted(OUTLETS), seed=1), max_calls=5,
                                   rng=np.random.default_rng(1), hyper=held, min_per_week=0)
    assert calls == 5 and used == held


def test_the_budget_counts_only_pairs_new_today(conn):
    """Yesterday's pairs are still in the pool but must not be budgeted again (else 5 % a day ≈ 30 % over a window)."""
    yesterday = DAY - dt.timedelta(days=1)
    conn.execute("UPDATE articles SET published = ? WHERE id LIKE 's0o%' OR id LIKE 's1o%'",
                 (f"{yesterday.isoformat()}T09:00:00Z",))      # stories 0 and 1 came out yesterday
    conn.commit()
    replay.regroup(conn, DAY, MODEL, threshold=0.8)
    cands, _ = replay.day_candidates(conn, DAY)
    assert len(cands) == 36 and len(replay.new_pairs(conn, cands, DAY)) == 24
    calls, _ = replay.judge_day(conn, DAY, SimulatedJudge(sorted(OUTLETS), seed=0), max_calls=60,
                                rng=np.random.default_rng(0), fraction=0.25, min_per_week=0)
    assert calls == 6                                          # ceil(0.25 × 24), chosen from all 36


def test_a_day_reads_its_own_story_run_not_the_newest(conn):
    """After a dry run over all days the newest run is the last day's; judging an early day must still use its own."""
    later = f"{(DAY + dt.timedelta(days=30)).isoformat()}_history_0.8"
    store.save_events(conn, later, {"s0o0": "later:ev0", "s0o1": "later:ev0"})
    conn.commit()
    cands, ctx = replay.day_candidates(conn, DAY)
    assert ctx["run"].startswith(DAY.isoformat()) and len(cands) == 36
    assert replay.day_candidates(conn, DAY - dt.timedelta(days=1)) == ([], {"run": None, "texts": {}})


def test_regroup_keeps_only_the_days_run(conn):
    replay.regroup(conn, DAY + dt.timedelta(days=1), MODEL, threshold=0.8)
    runs = [r[0] for r in conn.execute("SELECT DISTINCT run_id FROM event_members")]
    assert runs == [f"{(DAY + dt.timedelta(days=1)).isoformat()}_history_0.8"]


def test_target_sd_buys_only_until_outlets_are_that_certain(conn):
    """A68: a target looser than the prior buys nothing; a strict one buys up to the ceiling; the window filters."""
    from pipeline.measure.run import fit
    judge = SimulatedJudge(sorted(OUTLETS), seed=0)
    calls, _ = replay.judge_day(conn, DAY, judge, max_calls=60, rng=np.random.default_rng(0), target_sd=10.0,
                                min_per_week=0)
    assert calls == 0
    calls, _ = replay.judge_day(conn, DAY, judge, max_calls=12, rng=np.random.default_rng(0), target_sd=0.01,
                                min_per_week=0)
    assert calls == 12
    inside, _ = fit(conn, OUTLETS, "history", since=DAY.isoformat())
    outside, _ = fit(conn, OUTLETS, "history", since=(DAY + dt.timedelta(days=1)).isoformat())
    assert inside["econ"].n_obs > 0 and outside["econ"].n_obs == 0


def test_an_outlet_known_well_is_still_compared_every_week(tmp_path, monkeypatch):
    """A69: four outlets were pinned down by 1,200 verdicts long ago, so the planner, which keeps all it knows, would
    spend today's calls on the two it knows least. The floor still gives every outlet its minimum comparisons, picked
    as the best of its own pairs, even past the day's budget — no outlet is ever so well known that it stops being
    compared, so a move from left to right can show (A67). (The answer share is held fixed: it counts all years.)"""
    import pipeline.measure.run as MR
    monkeypatch.setattr(MR, "relevance_so_far", lambda conn, purpose="measure": {"econ": 0.5, "social": 0.8})
    def plan(floor: int):
        conn = replay.prepare(store.connect(tmp_path / f"h{floor}.sqlite"))
        rng = np.random.default_rng(3)
        centres = rng.normal(size=(6, 24))
        ids, vecs = [], []
        for s in range(6):
            for k in range(4):
                aid = f"s{s}o{k}"
                conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, summary, text_source) "
                             "VALUES (?,?,?,?,?,?,?,?)", (aid, OUTLETS[(s + k) % 6], "x", f"https://x/{aid}", f"S{s} {k}",
                                                          f"{DAY.isoformat()}T{8 + s:02d}:{10 * k:02d}:00Z", "s", "history-meta"))
                ids.append(aid); vecs.append(centres[s] + 0.05 * rng.normal(size=24))
        V = np.array(vecs); V /= np.linalg.norm(V, axis=1, keepdims=True)
        store.save_embeddings(conn, MODEL, ids, V.astype(np.float32))
        if True:                                         # four of the six, pinned down long ago
            old = (DAY - dt.timedelta(days=700)).isoformat()
            known = [("nrk", "vg"), ("nrk", "svt"), ("vg", "dn"), ("svt", "dn"), ("nrk", "dn"), ("vg", "svt")]
            for k in range(1200):
                oa, ob = known[k % 6]
                for aid, o in ((f"old{k}a", oa), (f"old{k}b", ob)):
                    conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, summary) "
                                 "VALUES (?,?,?,?,?,?,?)", (aid, o, "x", f"https://x/{aid}", "t", f"{old}T12:00:00Z", "s"))
                conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (f"c{k}", "t", "history", "judge", P.PROMPT_VERSION, "positive", f"old:ev{k}:summary",
                              f"old{k}a", f"old{k}b", oa, ob, "summary", 0, 0, 0.0, ""))
                for ax in P.AXES:
                    v = "A" if k % 3 else "B"
                    conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)", (f"c{k}", ax, v, P.first_wins(v), None))
        conn.commit()
        replay.regroup(conn, DAY, MODEL, threshold=0.8)
        calls, _ = replay.judge_day(conn, DAY, SimulatedJudge(sorted(OUTLETS), seed=0), max_calls=4,
                                    rng=np.random.default_rng(0), hyper={"econ": (1.0, 0.5), "social": (1.0, 0.5)},
                                    min_per_week=floor)
        return calls, replay.week_counts(conn, DAY)
    calls, per = plan(floor=0)
    pinned = ["nrk", "vg", "svt", "dn"]
    assert calls == 4 and min(per.get(o, 0) for o in pinned) == 0     # without a floor some pinned outlet waits
    calls, per = plan(floor=3)
    assert all(per[o] >= 3 for o in OUTLETS)                          # with it, every outlet gets three this week
    assert calls > 4                                                   # the floor outranks the day's budget


def test_the_day_is_planned_from_the_carried_positions_and_moves_them(conn, tmp_path):
    """A74: the planner reads the carried state; the day's verdicts update it; every outlet gets the day's position;
    a run that stopped between its commit and its save catches up from the verdicts on reopening."""
    path = tmp_path / "positions.npz"
    last_day, rowid, states = replay.open_positions(conn, path)
    assert last_day is None and rowid == 0
    calls, _ = replay.judge_day(conn, DAY, SimulatedJudge(sorted(OUTLETS), seed=0), max_calls=8,
                                rng=np.random.default_rng(0), min_per_week=0,
                                posts={ax: s.posterior() for ax, s in states.items()})
    assert calls == 8
    rowid = replay.advance(conn, states, rowid, DAY)
    n_obs = {ax: s.n_obs for ax, s in states.items()}
    assert sum(n_obs.values()) > 0
    k = states["econ"].outlets.index("nrk")
    assert states["econ"].cov[k, k] < 1.0                   # judged: sharper than its prior (τ = 1)
    got = conn.execute("SELECT count(*), count(DISTINCT outlet) FROM history_positions WHERE day = ?",
                       (DAY.isoformat(),)).fetchone()
    assert got[0] == 2 * len(states["econ"].outlets) and got[1] == len(states["econ"].outlets)
    conn.execute("INSERT INTO history_progress (day) VALUES (?)", (DAY.isoformat(),))
    conn.commit()
    from pipeline.history import positions as HP
    HP.save(path, DAY.isoformat(), rowid, states)
    replay.judge_day(conn, DAY, SimulatedJudge(sorted(OUTLETS), seed=1), max_calls=4, rng=np.random.default_rng(1),
                     min_per_week=0, posts={ax: s.posterior() for ax, s in states.items()})   # recorded, not saved
    _, _, again = replay.open_positions(conn, path)
    assert sum(s.n_obs for s in again.values()) > sum(n_obs.values())


def test_judging_on_a_grouping_process_runs_gives_the_same_verdicts(tmp_path, monkeypatch):
    """--ahead / --grouped: a grouping process groups and counts the days ahead of the judge (so the GPU is not idle
    while a day is grouped), keeping each day's run until the judge has read it. The judge then asks exactly what it
    asks when it groups each day itself — same pairs, same order of reading, same verdicts — and deletes each run once
    its day is judged."""
    import sys
    import pipeline.embed.backend as EB
    monkeypatch.setattr(EB, "get_encoder", lambda name: None)       # every article has its vector already
    days = [DAY + dt.timedelta(days=k) for k in range(3)]

    def world(name: str):
        path = tmp_path / name / "h.sqlite"
        path.parent.mkdir()
        c = replay.prepare(store.connect(path))
        rng = np.random.default_rng(3)
        ids, vecs = [], []
        for d, day in enumerate(days):
            centres = rng.normal(size=(4, 24))
            for s in range(4):
                for k in range(4):
                    aid = f"d{d}s{s}o{k}"
                    c.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, summary, text_source) "
                              "VALUES (?,?,?,?,?,?,?,?)", (aid, OUTLETS[(s + k + d) % 6], "x", f"https://x/{aid}", f"S{d}.{s} {k}",
                                                           f"{day.isoformat()}T{8 + s:02d}:{10 * k:02d}:00Z", "s", "history-meta"))
                    ids.append(aid)
                    vecs.append(centres[s] + 0.05 * rng.normal(size=24))
        V = np.array(vecs); V /= np.linalg.norm(V, axis=1, keepdims=True)
        store.save_embeddings(c, MODEL, ids, V.astype(np.float32))
        c.commit()
        return path, c

    def run(path, *extra):
        monkeypatch.setattr(sys, "argv", ["replay", "--db", str(path), "--embed-model", MODEL, "--threshold", "0.8",
                                          "--start", days[0].isoformat(), "--end", days[-1].isoformat(), "--prefetched",
                                          "--judge-fraction", "0.5", "--no-angle", *extra])
        replay.main()

    def asked(c):
        return c.execute("SELECT c.first_article, c.second_article, c.direction, group_concat(j.axis || j.verdict) FROM judge_calls c "
                         "JOIN judgments j USING (call_id) GROUP BY c.call_id ORDER BY c.rowid").fetchall()

    a, ca = world("alone")
    run(a)
    b, cb = world("behind")
    since = replay.utc_now_iso()
    run(b, "--count-only", "--recount", "--ahead", "10")
    assert cb.execute("SELECT count(DISTINCT run_id) FROM event_members").fetchone()[0] == 3     # all kept for the judge
    run(b, "--grouped", since)
    assert [tuple(r) for r in asked(cb)] == [tuple(r) for r in asked(ca)] and len(asked(ca)) > 0
    assert cb.execute("SELECT count(*) FROM event_members").fetchone()[0] == 0                  # each deleted once judged
    assert [r[0] for r in cb.execute("SELECT day FROM history_progress ORDER BY day")] == [d.isoformat() for d in days]


def test_an_unreachable_judge_server_is_waited_for_never_recorded(conn, monkeypatch):
    """The PC off or asleep: every attempt fails to connect. That is not a bad call — the day waits and asks again, and
    nothing is recorded as failed (2026-10-23: 52,244 such calls were recorded as failures and 98 days were judged
    without verdicts). A real bad answer is still recorded and the day goes on."""
    monkeypatch.setattr(replay, "UNREACHABLE_WAIT", 0)
    sim = SimulatedJudge(sorted(OUTLETS), seed=0)

    class Flaky:
        down = 3                                                            # the first three calls find no server

        def compare_outlets(self, *a, **k):
            if Flaky.down > 0:
                Flaky.down -= 1
                raise JudgeCallFailed("Connection error.", {"errors": [{"type": "APIConnectionError"}] * 5})
            return sim.compare_outlets(*a, **k)

    calls, _ = replay.judge_day(conn, DAY, Flaky(), max_calls=6, rng=np.random.default_rng(0), min_per_week=0)
    assert calls == 6 and Flaky.down == 0
    assert conn.execute("SELECT count(*) FROM judge_call_log WHERE status = 'failed'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM judge_calls").fetchone()[0] == 6
    assert replay.unreachable(JudgeCallFailed("x", {"errors": [{"type": "APITimeoutError"}]}))
    assert not replay.unreachable(JudgeCallFailed("x", {"errors": [{"type": "APIConnectionError"}, {"type": "BadRequestError"}]}))
    assert not replay.unreachable(JudgeCallFailed("cut off", {"errors": []}))


def test_the_planner_takes_a_block_of_picks_from_one_scoring(conn, tmp_path):
    """The maintainer, 2026-10-24: score all candidates only as often as the GPU needs, at most every 100 picks. A
    block takes its picks from one scoring (each pick's fantasised update still applied, a pair still bought once);
    without a block every pick is scored afresh, as before."""
    from pipeline.plan.planner import PlannerConfig, plan_joint_hier
    _, _, states = replay.open_positions(conn, tmp_path / "p.npz")
    posts = {ax: s.posterior() for ax, s in states.items()}
    cands, _ = replay.day_candidates(conn, DAY)
    cfg = PlannerConfig(mode="variance_per_cost", target_sd=0.0, batch_size=8)
    scorings = []
    blocked = plan_joint_hier(posts, cands, cfg, rng=np.random.default_rng(0), block=lambda: scorings.append(1) or 100)
    each = plan_joint_hier(posts, cands, cfg, rng=np.random.default_rng(0))
    assert len(blocked) == len(each) == 8 and len(scorings) == 1
    pairs = [frozenset((r.first_article_id, r.second_article_id)) for r in blocked]
    assert len(set(pairs)) == len(pairs)
    sizes = iter(range(1, 100))                                       # 1, 2, 3 picks per scoring: 3 scorings for 6
    scorings.clear()
    plan_joint_hier(posts, cands, PlannerConfig(mode="variance_per_cost", target_sd=0.0, batch_size=6),
                    rng=np.random.default_rng(0), block=lambda: scorings.append(1) or next(sizes))
    assert len(scorings) == 3


def test_the_feed_keeps_the_gpu_supplied_with_at_most_a_hundred_picks_a_scoring():
    """k = parallel + rate × time per scoring − calls waiting, between 1 and 100: a full GPU's worth at the start, one
    pick a scoring while enough wait, and never more than BLOCK_MAX."""
    f = replay.Feed(parallel=64)
    assert f() == 64                                                  # nothing waiting yet: fill the GPU
    for _ in range(64):
        f.sent()
    assert f() == 1                                                   # 64 waiting, nothing answered: rescore after one
    f.t0 -= 10.0
    for _ in range(300):
        f.answered()
        f.sent()                                                       # 30 answers a second, 64 still waiting
    f.last -= 2.0                                                      # a scoring takes 2 s: 60 more needed
    assert 55 <= f() <= 65
    assert replay.Feed(parallel=500)() == replay.BLOCK_MAX


def test_a_days_last_block_is_recorded_the_next_day_and_never_bought_twice(conn):
    """The maintainer, 2026-10-24: the calls still out when a day's plan is done run while the next day is prepared, so
    the GPU does not wait for them. Its verdicts are recorded during the next day (after the positions that day is planned from: they
    first count two days on), and its pairs are not offered again meanwhile."""
    from concurrent.futures import ThreadPoolExecutor
    sim = SimulatedJudge(sorted(OUTLETS), seed=0)

    import itertools
    import time
    asked = itertools.count()

    class Local:                                                       # a judge read like the real one (no outlets);
        def compare(self, text_a, text_b, direction="positive"):      # from the fifth call on it is busy
            if next(asked) >= 4:
                time.sleep(0.5)
            return sim.compare_outlets("nrk", "vg", 1.0, direction)

    pool, carry = ThreadPoolExecutor(2), []
    calls = lambda: conn.execute("SELECT count(*) FROM judge_calls").fetchone()[0]     # noqa: E731
    n1, _ = replay.judge_day(conn, DAY, Local(), max_calls=8, rng=np.random.default_rng(0), min_per_week=0,
                             parallel=2, pool=pool, carry=carry, keep_last=True)
    assert n1 == 8 and 0 < len(carry) < 8 and calls() == 8 - len(carry)          # the last block is still out
    out = {frozenset((it[0].first_article_id, it[0].second_article_id)) for it, _, _ in carry}
    n2, _ = replay.judge_day(conn, DAY, Local(), max_calls=4, rng=np.random.default_rng(1), min_per_week=0,
                             parallel=2, pool=pool, carry=carry, keep_last=False)
    assert not carry and calls() == n1 + n2                                       # recorded, nothing lost
    pairs = [frozenset(r) for r in conn.execute("SELECT first_article, second_article FROM judge_calls")]
    assert len(set(pairs)) == len(pairs) and out <= set(pairs)                    # none bought twice


def test_vectors_and_dates_worked_out_a_day_ahead_give_the_same_day(conn):
    """Option a (the maintainer, 2026-10-24): the pairs' vectors and the articles' dates are worked out while the day
    before is judged; once the pairs judged by then are cut out, the answer shares the model predicts and the day's
    new pairs are the same as worked out on the day."""
    from pipeline.plan import angle as AN
    vec = lambda ids: store.load_embeddings(conn, MODEL, ids)                       # noqa: E731
    cands, ctx = replay.day_candidates(conn, DAY)
    prep_c = list(cands)
    prep_ctx = {**ctx, "_pairs": AN.pair_vectors([(c.article_a, c.article_b) for c in prep_c], vec),
                "_pub": dict(conn.execute("SELECT id, published FROM articles").fetchall())}
    a, b = prep_c[3].article_a, prep_c[3].article_b                                 # judged meanwhile: cut out
    conn.execute("INSERT INTO judge_calls (call_id, at, purpose, model, prompt_version, direction, event_id, first_article, "
                 "second_article, first_outlet, second_outlet, text_kind) VALUES ('x','t','history','judge',?,'positive',"
                 "?,?,?,?,?,'summary')", (P.PROMPT_VERSION, prep_c[3].event_id, a, b, prep_c[3].outlet_a, prep_c[3].outlet_b))

    class Model:                                                                   # any model: the mean vector's first two values
        def predict(self, X):
            return 1 / (1 + np.exp(-X[:, :2]))

    got, gctx = replay.finish_candidates(conn, (prep_c, prep_ctx))
    want, _ = replay.day_candidates(conn, DAY)
    assert [(c.event_id, c.article_a, c.article_b) for c in got] == [(c.event_id, c.article_a, c.article_b) for c in want]
    assert all(frozenset((c.article_a, c.article_b)) != frozenset((a, b)) for c in got)
    assert [c.relevance for c in AN.apply(got, Model(), vec, gctx["_pairs"])] == [c.relevance for c in AN.apply(want, Model(), vec)]
    assert len(replay.new_pairs(conn, got, DAY, gctx["_pub"])) == len(replay.new_pairs(conn, want, DAY))


def test_the_feed_reads_the_gpus_own_queue_when_it_can():
    """2026-10-25: calls handed to the sending threads can sit on the Mac while the planner runs; the feed then thought
    the GPU full and rescored after every pick. With the server's own count (running + waiting) it plans a big block
    when the GPU runs short, a small one when it is full — and counts its own calls when the server cannot be read."""
    f = replay.Feed(parallel=64, queue=lambda: 6.0)
    for _ in range(64):
        f.sent()                                                       # 64 sent, but only 6 reached the GPU
    assert f() >= 58
    assert replay.Feed(parallel=64, queue=lambda: 70.0)() == 1          # the GPU is full: rescore after one
    g = replay.Feed(parallel=64, queue=lambda: None)
    for _ in range(64):
        g.sent()
    assert g() == 1                                                    # unreadable: its own count, as before
    assert replay.server_queue("http://127.0.0.1:9/v1")() is None      # nothing listening: None, never an error


def test_rebuilding_from_the_verdicts_gives_the_positions_the_run_wrote(conn, tmp_path):
    """replay rebuild (the maintainer, 2026-10-25): the independent model replayed over the saved verdicts, day by day
    as the run saw them, gives the positions the run itself wrote — so the history judged before the switch and the
    days judged after it are one consistent series."""
    path = tmp_path / "positions.npz"
    _, rowid, states = replay.open_positions(conn, path)
    for k in range(2):
        day = DAY + dt.timedelta(days=k)
        replay.judge_day(conn, DAY, SimulatedJudge(sorted(OUTLETS), seed=k), max_calls=6, rng=np.random.default_rng(k),
                         min_per_week=0, posts={ax: s.posterior() for ax, s in states.items()})
        rowid = replay.advance(conn, states, rowid, day)
        conn.execute("INSERT INTO history_progress (day, done_at) VALUES (?, ?)", (day.isoformat(), replay.utc_now_iso()))
        conn.commit()
        import time
        time.sleep(1.1)                                     # the call log's times are to the second: days apart in it
    ran = conn.execute("SELECT day, axis, outlet, mean, sd FROM history_positions ORDER BY 1, 2, 3").fetchall()
    assert replay.rebuild(conn, path) == 2
    again = conn.execute("SELECT day, axis, outlet, mean, sd FROM history_positions ORDER BY 1, 2, 3").fetchall()
    assert len(ran) == len(again) > 0
    bad = [(tuple(a), tuple(b)) for a, b in zip(ran, again) if a[:3] != b[:3] or abs(a[3] - b[3]) > 1e-9 or abs(a[4] - b[4]) > 1e-9]
    assert not bad, bad[:3]


def test_trim_discards_an_unfinished_day_so_the_database_matches_the_saved_state(conn, tmp_path):
    """history_judge_stop.sh / start.sh: whatever was recorded after the last finished day (an unfinished day's calls,
    the day before's last batch) is deleted, so the database matches the saved positions and the run resumes after
    that day; the finished day's verdicts stay."""
    from pipeline.history import positions as HP
    path = tmp_path / "positions.npz"
    _, rowid, states = replay.open_positions(conn, path)
    replay.judge_day(conn, DAY, SimulatedJudge(sorted(OUTLETS), seed=0), max_calls=5, rng=np.random.default_rng(0),
                     min_per_week=0, posts={ax: s.posterior() for ax, s in states.items()})
    rowid = replay.advance(conn, states, rowid, DAY)
    conn.execute("INSERT INTO history_progress (day, done_at) VALUES (?, ?)", (DAY.isoformat(), replay.utc_now_iso()))
    conn.commit()
    HP.save(path, DAY.isoformat(), rowid, states)
    kept = conn.execute("SELECT count(*) FROM judge_calls").fetchone()[0]
    replay.judge_day(conn, DAY, SimulatedJudge(sorted(OUTLETS), seed=1), max_calls=4, rng=np.random.default_rng(1),
                     min_per_week=0, posts={ax: s.posterior() for ax, s in states.items()})   # the next day, unfinished
    assert conn.execute("SELECT count(*) FROM judge_calls").fetchone()[0] == kept + 4
    got = replay.trim(conn, path)
    assert got["calls"] == 4 and got["verdicts"] == 8
    assert conn.execute("SELECT count(*) FROM judge_calls").fetchone()[0] == kept
    assert conn.execute("SELECT count(*) FROM judgments").fetchone()[0] == 2 * kept
    assert replay.trim(conn, path)["calls"] == 0                       # nothing more to discard
