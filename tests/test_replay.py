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
