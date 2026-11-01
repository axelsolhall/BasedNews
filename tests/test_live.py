"""A82: live judging carries the history's state on — the prior seeds day one, the planner's picks stop at the budget,
each verdict moves its two outlets, the day's positions are saved, and the site frames follow. A fake judge (no
network, no key, no spend)."""
import datetime as dt
import json
import sys

import numpy as np
import pytest

from pipeline import store
from pipeline.export import frames as F
from pipeline.judge.client import Verdict
from pipeline.measure import live as L

NOW = dt.datetime.now(dt.UTC)
OUTLETS = ("nrk", "vg", "aftenposten")          # one country (Norway): a reference group of the registry


def iso(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeJudge:
    calls = 0

    def __init__(self, settings):
        pass

    def compare(self, text_a, text_b, direction="positive"):
        FakeJudge.calls += 1
        return Verdict({"econ": {"verdict": "A", "reason": "r"}, "social": {"verdict": "B", "reason": "r"}},
                       10, 2, 0.001, "fake")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEDNEWS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("BASEDNEWS_MONTHLY_AI_USD", "5")
    prior = {"as_of": (NOW.date() - dt.timedelta(days=10)).isoformat(), "first": "2015-11-01", "source": "test",
             "outlets": {o: {"econ": [0.1 * k, 0.3], "social": [-0.1 * k, 0.3], "n": 6000, "worth": 50.0}
                         for k, o in enumerate(OUTLETS)}}
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(prior))
    monkeypatch.setattr(L, "PRIOR", path)
    monkeypatch.setattr(F, "PRIOR", path)
    monkeypatch.setattr(L, "OpenAIJudge", FakeJudge)
    conn = store.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS same_text_pairs (run_id TEXT, a TEXT, b TEXT, jaccard REAL)")
    for ev in ("e1", "e2", "e3"):
        for k, o in enumerate(OUTLETS):
            aid = f"{ev}-{o}"
            conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, full_text, ingested_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (aid, o, "Norway", f"https://x/{aid}", f"T {aid}", iso(5 + k),
                                                       "text " * 100, iso(5 + k)))
            store.save_events(conn, "run1", {aid: ev})
    conn.commit()
    return conn, prior


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["live", "--no-angle", *args])
    L.main()


def test_the_first_day_starts_from_the_prior_grown(db):
    conn, prior = db
    day, states = L.load_state(conn)
    assert day == prior["as_of"]
    k = states["econ"].outlets.index("vg")
    assert states["econ"].mean[k] == pytest.approx(0.1) and states["econ"].cov[k, k] == pytest.approx(0.09)


def test_a_day_judges_within_budget_moves_positions_and_writes_frames(db, monkeypatch, tmp_path):
    conn, prior = db
    FakeJudge.calls = 0
    run(monkeypatch, "--dry-run", "--budget-usd", "1")
    assert FakeJudge.calls == 0 and not conn.execute("SELECT COUNT(*) FROM live_positions").fetchone()[0]
    run(monkeypatch, "--budget-usd", "1")
    n = FakeJudge.calls
    assert n == conn.execute("SELECT COUNT(*) FROM judge_calls WHERE purpose = 'live'").fetchone()[0] > 0
    today = NOW.date().isoformat()
    pos = {(ax, o): m for ax, o, m in conn.execute("SELECT axis, outlet, mean FROM live_positions WHERE day = ?", (today,))}
    assert {o for _, o in pos} >= set(OUTLETS)
    moved = [abs(pos[("econ", o)] - prior["outlets"][o]["econ"][0]) for o in OUTLETS]
    assert max(moved) > 1e-3                                           # the verdicts moved the judged outlets
    assert conn.execute("SELECT COUNT(*) FROM live_country_positions WHERE day = ? AND country = 'Norway'",
                        (today,)).fetchone()[0] == 2
    assert tuple(conn.execute("SELECT count(*), min(as_of), min(source) FROM prior_positions").fetchone()) == (2 * len(
        L.load_state(conn)[1]["econ"].outlets), today, "live")                 # A79 values articles by today's positions
    run(monkeypatch, "--budget-usd", "1")                              # same day again: every pair is judged already
    assert FakeJudge.calls == n

    meta = F.live(conn, tmp_path / "site", threshold=1000)
    assert meta["days"] == [today] and set(OUTLETS) <= set(meta["entities"]) and "Norway" in meta["entities"]
    f = F.decode((tmp_path / "site" / meta["file"]).read_bytes(), len(meta["entities"]))
    assert f.shape == (1, len(meta["entities"]), 4)
    k = meta["entities"].index("Norway")
    assert np.allclose(f[0, k, :2], 0, atol=2 * F.SCALE)               # the only shown country is the world's centre
    assert "live after" in F.check(tmp_path / "site")


def test_the_budget_stops_the_picks(db, monkeypatch):
    conn, _ = db
    FakeJudge.calls = 0
    run(monkeypatch, "--budget-usd", "0")
    assert FakeJudge.calls == 0
    assert conn.execute("SELECT COUNT(*) FROM live_positions").fetchone()[0]     # the day is still recorded (grown)
