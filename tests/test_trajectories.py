"""H5 (pipeline.history.trajectories): the site's lines are the replay's own day-by-day positions (A74), read back —
one point per bin (its last day), outlets from their first verdict, countries with the sd the replay wrote."""
import datetime as dt

import numpy as np

from pipeline import store
from pipeline.history import positions as HP
from pipeline.history import trajectories as H5
from pipeline.judge import prompt as P

OUTLETS = ["a1", "a2", "b1", "c1"]
GROUPS = {"A": {"a1": 2.0, "a2": 1.0}, "B": {"b1": 1.0}, "C": {"c1": 1.0}}
START = dt.date(2019, 1, 1)


def db(tmp_path):
    """14 days of positions; a1 and b1 judged from day 0, a2 from day 9, c1 never."""
    conn = store.connect(tmp_path / "h.sqlite")
    states = {ax: HP.start(OUTLETS, ax, 1.0, 0.5) for ax in P.AXES}
    for d in range(14):
        day = (START + dt.timedelta(days=d)).isoformat()
        rows = [("a1", "b1", f"x{d}", f"y{d}", day, day, 1.0, d % 3 != 0)]
        if d >= 9:
            rows.append(("a2", "b1", f"z{d}", f"y{d}", day, day, 1.0, True))
        for k, r in enumerate(rows):
            for art, o in ((r[2], r[0]), (r[3], r[1])):
                conn.execute("INSERT OR IGNORE INTO articles (id, outlet_id, country, url, title, published, summary) "
                             "VALUES (?,?,?,?,?,?,?)", (art, o, "x", f"https://x/{art}", "t", f"{day}T12:00:00Z", "s"))
            conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"c{d}_{k}", "2026-10-19T00:00:00Z", "history", "judge", P.PROMPT_VERSION, "positive",
                          f"ev{d}:summary", r[2], r[3], r[0], r[1], "summary", 100, 10, 0.0, ""))
            for ax in P.AXES:
                v = "A" if r[7] else "B"
                conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)", (f"c{d}_{k}", ax, v, P.first_wins(v, "positive"), None))
        for st in states.values():
            HP.update(st, rows)
        HP.record_day(conn, day, states, GROUPS)
    conn.commit()
    return conn, states


def test_lines_are_the_stored_positions_one_per_week(tmp_path):
    conn, states = db(tmp_path)
    out = H5.build(conn, GROUPS, bin_days=7)
    econ = out["axes"]["econ"]
    assert out["start"] == START.isoformat() and set(econ["outlets"]) == {"a1", "a2", "b1"}      # c1: never judged
    assert [p[0] for p in econ["outlets"]["a1"]] == [0, 1] and [p[0] for p in econ["outlets"]["a2"]] == [1]
    k = OUTLETS.index("a1")                         # the last bin's point is the last day's stored position
    assert np.isclose(econ["outlets"]["a1"][-1][1], states["econ"].mean[k], atol=1e-4)
    assert np.isclose(econ["outlets"]["a1"][-1][2], np.sqrt(states["econ"].cov[k, k]), atol=1e-4)


def test_countries_carry_the_sd_of_their_weighted_mean(tmp_path):
    conn, states = db(tmp_path)
    s = states["social"]
    w = np.array([2 / 3, 1 / 3])
    m, sd = w @ s.mean[:2], np.sqrt(w @ s.cov[:2, :2] @ w)
    social = H5.build(conn, GROUPS)["axes"]["social"]["countries"]
    assert np.isclose(social["A"][-1][1], m, atol=1e-4) and np.isclose(social["A"][-1][2], sd, atol=1e-4)
    assert "C" not in social                                             # no outlet of C judged yet


def test_a_countrys_centre_is_weighted_by_its_outlets_recent_comparisons(tmp_path):
    """A08 (2026-10-23): an outlet's weight in its country's centre is the log of its comparisons, each worth half as
    much a year later; one comparison or none weighs nothing, so an outlet without verdicts cannot drag the centre
    to its prior. The log's base cancels when the weights are normalised. A country with no judged outlet keeps the
    plain mean of its outlets."""
    import math
    conn = store.connect(tmp_path / "h.sqlite")
    day = dt.date(2020, 6, 1)
    def judged(n, when, a, b):
        for k in range(n):
            conn.execute("INSERT INTO judge_calls (call_id, at, purpose, model, prompt_version, direction, event_id, "
                         "first_article, second_article, first_outlet, second_outlet, text_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"{a}{b}{when}{k}", "t", "history", "judge", P.PROMPT_VERSION, "positive",
                          f"{when.isoformat()}:ev{k}:summary", "x", "y", a, b, "summary"))
    judged(10, day, "a1", "x9")                                   # 10 today
    judged(10, day - dt.timedelta(days=365), "a2", "x9")          # 10 a year ago: worth 5
    judged(1, day, "b1", "x9")                                    # one: log 0
    judged(4, day + dt.timedelta(days=1), "b1", "x9")             # judged after the day: not counted
    w = HP.centre_weights(conn, day.isoformat(), GROUPS, "history")
    assert math.isclose(w["A"]["a1"], math.log(10)) and math.isclose(w["A"]["a2"], math.log(5))
    assert w["B"]["b1"] == 0.0 and w["C"]["c1"] == 0.0
    assert math.isclose(w["A"]["a1"] / w["A"]["a2"], math.log10(10) / math.log10(5))   # any base: the same ratio

    states = {ax: HP.start(OUTLETS, ax, 1.0, 0.5) for ax in P.AXES}
    for st in states.values():
        st.mean[[st.outlets.index(o) for o in ("a1", "a2", "b1", "c1")]] = [1.0, -1.0, 0.4, 0.7]
    HP.record_day(conn, day.isoformat(), states, w)
    got = dict(conn.execute("SELECT country, mean FROM history_country_positions WHERE axis = 'econ'").fetchall())
    share = math.log(10) / (math.log(10) + math.log(5))
    assert math.isclose(got["A"], share * 1.0 + (1 - share) * -1.0)
    assert math.isclose(got["B"], 0.4) and math.isclose(got["C"], 0.7)  # unjudged: their own plain mean


def test_the_running_centre_weights_equal_the_recount(tmp_path):
    """CentreWorth (the run's running sum) gives the weights centre_weights recounts, day after day — with a day's
    late calls recorded the next day."""
    import math
    conn = store.connect(tmp_path / "h.sqlite")
    def judged(k, when, a, b):
        conn.execute("INSERT INTO judge_calls (call_id, at, purpose, model, prompt_version, direction, event_id, first_article, "
                     "second_article, first_outlet, second_outlet, text_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"c{k}", "t", "history", "judge", P.PROMPT_VERSION, "positive", f"{when.isoformat()}:ev{k}:summary",
                      "x", "y", a, b, "summary"))
    run, k = HP.CentreWorth("history"), 0
    for d in range(40):
        day = START + dt.timedelta(days=10 * d)
        for _ in range(d % 4 + 1):
            judged(k, day, "a1" if k % 3 else "a2", "b1"); k += 1
        if d:
            judged(k, day - dt.timedelta(days=10), "c1", "a1"); k += 1             # yesterday's late call
        got, want = run.weights(conn, day.isoformat(), GROUPS), HP.centre_weights(conn, day.isoformat(), GROUPS, "history")
        assert all(math.isclose(got[c][o], want[c][o], abs_tol=1e-9) for c in GROUPS for o in GROUPS[c])


def test_each_outlet_its_own_score_a_verdict_moves_only_its_two(tmp_path):
    """The maintainer, 2026-10-25: a verdict moves A and B only — the winner up, the loser down, both more certain —
    and no other outlet; no floor, so their variance keeps falling with more verdicts."""
    s = HP.start(OUTLETS, "econ", 1.0, 0.5)
    before = s.cov.copy()
    HP.update_independent(s, [("a1", "b1", "x", "y", "d", "d", 1.0, True)])
    k = {o: i for i, o in enumerate(OUTLETS)}
    assert s.mean[k["a1"]] > 0 > s.mean[k["b1"]]
    assert s.cov[k["a1"], k["a1"]] < 1.0 and s.cov[k["b1"], k["b1"]] < 1.0
    assert s.mean[k["a2"]] == 0 and s.cov[k["a2"], k["a2"]] == before[k["a2"], k["a2"]]       # nobody else moves
    assert np.count_nonzero(s.cov - np.diag(np.diag(s.cov))) == 0                             # nothing shared
    for i in range(400):                                                                        # close, mixed results
        HP.update_independent(s, [("a1", "b1", "x", "y", "d", "d", 1.0, i % 2 == 0)])
    assert s.cov[k["a1"], k["a1"]] < 0.05                                                       # no floor


def test_uncertainty_grows_a_tiny_multiple_a_day_unless_an_outlet_has_plenty_of_close_outlets():
    """× GROWTH a day per axis, proportional to the variance; not for an outlet with more than CLOSE_MAX others whose
    order with it is CLOSE_P likely inverted."""
    crowd = [f"c{k}" for k in range(7)]
    s = HP.start(crowd + ["far"], "econ", 0.3, 0.5)                    # seven outlets at 0, one far away
    s.mean[len(crowd)] = 50.0
    HP.grow(s, 10)
    v = np.diag(s.cov)
    assert np.allclose(v[:7], 0.09)                                   # each has six close ones: no growth
    assert np.isclose(v[7], 0.09 * HP.GROWTH ** 10)                   # alone: it grows
