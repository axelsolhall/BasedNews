"""Day-by-day positions (pipeline.history.positions, A74): yesterday's posterior is today's prior, only today's verdicts
move it, the state stays the same size; without the floor the answer matches one fit over all the verdicts, and with
it a position never freezes."""
import datetime as dt

import numpy as np

from pipeline.history import positions as HP
from pipeline.rate.hierarchical import HierarchicalBT
from pipeline.rate.model import sigmoid

OUTLETS = ["a", "b", "c", "d", "e", "f"]
TRUE = dict(zip(OUTLETS, [-1.2, -0.6, 0.0, 0.3, 0.8, 1.5]))
DAY0 = dt.date(2019, 1, 1)


def days_of_verdicts(n_days=20, per_day=40, seed=0, sigma_a=0.4):
    """Per day: verdicts between articles published that day or the day before (so articles recur across days)."""
    rng = np.random.default_rng(seed)
    eps, out = {}, []
    for d in range(n_days):
        day = DAY0 + dt.timedelta(days=d)
        arts = [(f"{o}{d}_{k}", o, day.isoformat()) for o in OUTLETS for k in range(2)]
        if d:
            arts += [(f"{o}{d - 1}_{k}", o, (day - dt.timedelta(days=1)).isoformat()) for o in OUTLETS for k in range(2)]
        rows = []
        for _ in range(per_day):
            i, j = rng.choice(len(arts), 2, replace=False)
            (fa, oa, pa), (fb, ob, pb) = arts[i], arts[j]
            if oa == ob:
                continue
            for a in (fa, fb):
                eps.setdefault(a, rng.normal(0, sigma_a))
            p = sigmoid(TRUE[oa] + eps[fa] - TRUE[ob] - eps[fb])
            rows.append((oa, ob, fa, fb, pa, pb, 1.0, bool(rng.random() < p)))
        out.append((day, rows))
    return out


def test_day_by_day_matches_one_fit_over_everything():
    days = days_of_verdicts()
    s = HP.start(OUTLETS, "econ", tau=1.0, sigma_a=0.4)
    for _, rows in days:
        HP.update(s, rows, floor=0.0)                 # no floor: one fixed position per outlet, as the batch fit
    allrows = [r for _, rows in days for r in rows]
    batch = HierarchicalBT(OUTLETS, "econ").fit([r[:4] + (r[6],) for r in allrows], [r[7] for r in allrows],
                                                sigma_a=0.4, tau=1.0)
    n = len(OUTLETS)
    assert np.allclose(s.mean[:n], batch.mean[:n], atol=0.05)
    assert np.allclose(np.sqrt(np.diag(s.cov)[:n]), np.sqrt(np.diag(batch.cov)[:n]), rtol=0.1)
    assert list(np.argsort(s.mean[:n])) == list(np.argsort(batch.mean[:n]))
    assert np.corrcoef(s.mean[:n], [TRUE[o] for o in OUTLETS])[0, 1] > 0.95


def test_without_the_floor_more_verdicts_only_sharpen_and_the_state_stays_the_same_size():
    s = HP.start(OUTLETS, "econ", tau=1.0, sigma_a=0.4)
    sd = np.sqrt(np.diag(s.cov)[:len(OUTLETS)])
    sizes = []
    for day, rows in days_of_verdicts(n_days=30):
        HP.update(s, rows, floor=0.0)
        HP.drop_before(s, HP.window_start(day))
        now = np.sqrt(np.diag(s.cov)[:len(OUTLETS)])
        assert np.all(now <= sd + 1e-12)
        sd = now
        sizes.append(len(s.mean))
    window_most = len(OUTLETS) + 1 + 7 * 2 * len(OUTLETS)          # outlets, β, and at most a week of articles
    assert max(sizes) <= window_most and max(sizes[20:]) <= max(sizes[5:10])   # it does not grow with the days
    assert all(b >= HP.window_start(DAY0 + dt.timedelta(days=29)) for b in s.born)


def test_dropping_old_articles_keeps_the_outlets_exactly():
    s = HP.start(OUTLETS, "econ", tau=1.0, sigma_a=0.4)
    for _, rows in days_of_verdicts(n_days=3):
        HP.update(s, rows)
    n = len(OUTLETS) + 1
    mean, cov = s.mean[:n].copy(), s.cov[:n, :n].copy()
    HP.drop_before(s, "2099-01-01")
    assert s.articles == [] and np.array_equal(s.mean, mean) and np.array_equal(s.cov, cov)


def test_saved_state_comes_back_the_same(tmp_path):
    s = HP.start(OUTLETS, "econ", tau=1.0, sigma_a=0.4)
    HP.update(s, days_of_verdicts(n_days=1)[0][1])
    HP.save(tmp_path / "p.npz", "2019-01-01", 42, {"econ": s})
    day, rowid, states = HP.load(tmp_path / "p.npz")
    t = states["econ"]
    assert (day, rowid) == ("2019-01-01", 42)
    assert np.array_equal(t.mean, s.mean) and np.array_equal(t.cov, s.cov)
    assert (t.articles, t.born, t.n_obs, t.sigma_a) == (s.articles, s.born, s.n_obs, s.sigma_a)


def test_the_planner_reads_the_state_as_a_fit():
    s = HP.start(OUTLETS, "econ", tau=1.0, sigma_a=0.4)
    HP.update(s, days_of_verdicts(n_days=1)[0][1])
    h = s.posterior()
    assert h.outlets == OUTLETS and h.articles == s.articles and h.sigma_a == 0.4
    assert h.article_index()[s.articles[0]] == len(OUTLETS) + 1


def test_positions_never_freeze():
    """After a month of verdicts, three upsets still move the top outlet's score; without the floor the same three
    barely move it. (Less than three full Elo steps: the update shares each surprise with the other outlet, the two
    articles and the order bias.)"""
    moved = {}
    for floor in (0.0, HP.FLOOR_VAR):
        # τ 0.3: six outlets' common level is then known to ±0.12 (only differences are measured), so a month of
        # verdicts takes an outlet's sd below the floor's 0.40 — the case the floor is for
        s = HP.start(OUTLETS, "econ", tau=0.3, sigma_a=0.1)
        for _, rows in days_of_verdicts(n_days=30, per_day=80, sigma_a=0.1):
            HP.update(s, rows, floor=floor)
        k = OUTLETS.index("f")                                   # the top outlet loses three times to the bottom one
        if floor == 0.0:
            assert s.cov[k, k] < HP.FLOOR_VAR / 4
        before = s.mean[k]
        upsets = [("f", "a", f"u{i}", f"v{i}", "2019-02-01", "2019-02-01", 0.6, False) for i in range(3)]
        HP.update(s, upsets, floor=floor)
        moved[floor] = before - s.mean[k]
    assert moved[HP.FLOOR_VAR] > 3 * moved[0.0]                 # measured 2026-10-19: 0.062 against 0.010
