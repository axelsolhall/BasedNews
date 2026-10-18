"""E11's training data (pipeline.experiments.e11): batches of whole days that never overlap and never reach E9's test
month, pairs within stories spread over outlets, every choice stored before any call, judging that resumes and stops at
its budget, and an export with exactly the judge's v4 messages and gpt-5.4-mini's compact answer."""
import datetime as dt
import json
import sqlite3

import numpy as np
import pytest

from pipeline.experiments import e11
from pipeline.judge import prompt as P
from pipeline.judge.client import Verdict

OUTLETS = [("nrk", "Norway"), ("vg", "Norway"), ("svt", "Sweden"), ("dn", "Sweden"), ("bbc", "United Kingdom")]


class Encoder:
    """Articles about the same story get the same direction (the story is the first word of the headline)."""
    def encode(self, texts):
        out = []
        for t in texts:
            v = np.zeros(16); v[hash(t.split()[0]) % 16] = 1.0; v[(hash(t.split()[0]) // 16) % 16] += 0.5
            out.append(v / np.linalg.norm(v))
        return np.array(out)


@pytest.fixture
def hist(tmp_path):
    """Three stories a day, each covered by every outlet, on the days the test asks for."""
    conn = sqlite3.connect(tmp_path / "h.sqlite"); conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE articles (id TEXT PRIMARY KEY, outlet_id TEXT, country TEXT, url TEXT, title TEXT, "
                 "summary TEXT, published TEXT)")
    for day in (dt.date(2016, 3, d) for d in range(1, 32)):
        for s, word in enumerate(("Budget", "Storm", "Election")):
            for k, (o, c) in enumerate(OUTLETS):
                aid = f"{day}{s}{o}"
                conn.execute("INSERT INTO articles VALUES (?,?,?,?,?,?,?)",
                             (aid, o, c, f"https://x/{aid}", f"{word}{day.day} news by {o}", f"What {o} says.",
                              f"{day.isoformat()}T{8 + s:02d}:{10 * k:02d}:00Z"))
    conn.commit()
    return conn


def test_days_are_seeded_disjoint_and_before_the_test_month():
    a = e11.pick_days("b1", set())
    assert a == e11.pick_days("b1", set()) and len(a) == 2 * 131               # 2015-11 → 2026-09, two a month
    assert max(a) <= dt.date(2026, 9, 30) and min(a) >= dt.date(2015, 11, 1)
    b = e11.pick_days("b2", {d.isoformat() for d in a})
    assert not set(a) & set(b)
    share = np.mean([e11.split_of(d.isoformat(), "b1") == "val" for d in e11.pick_days("x", set(), per_month=20)])
    assert 0.05 < share < 0.15


class Judge:
    def __init__(self):
        self.n = 0

    def compare(self, a, b, direction):
        self.n += 1
        raw = json.dumps({"econ": "na", "social": "A" if self.n % 2 else "B"})
        return Verdict(P.parse(raw), 600, 150, 0.001, "gpt-5.4-mini", raw, {})


def test_sample_judge_export(tmp_path, hist):
    conn = e11.connect(tmp_path / "d.sqlite")
    days = [dt.date(2016, 3, d) for d in (2, 9, 16, 23)]
    n = e11.sample("b1", 24, conn=conn, hist=hist, encoder=Encoder(), days=days)
    assert n == 24
    pairs = conn.execute("SELECT * FROM e11_pairs").fetchall()
    assert all(p["first_outlet"] != p["second_outlet"] for p in pairs)
    assert {p["story"].split(":")[0] for p in pairs} == {d.isoformat() for d in days}
    use = {o: sum(o in (p["first_outlet"], p["second_outlet"]) for p in pairs) for o, _ in OUTLETS}
    assert max(use.values()) - min(use.values()) <= 2                          # spread over outlets
    with pytest.raises(SystemExit):
        e11.sample("b1", 5, conn=conn, hist=hist, encoder=Encoder(), days=days)  # a batch name is never reused
    j = Judge()
    first = e11.judge("b1", budget_usd=0.01, conn=conn, judge_=j, parallel=2)
    assert first["judged"] == 24 or first["usd"] >= 0.01                       # stops at the budget (in steps of 40)
    again = e11.judge("b1", budget_usd=1.0, conn=conn, judge_=j)
    assert first["judged"] + again["judged"] == 24 and e11.judge("b1", 1.0, conn=conn, judge_=j)["judged"] == 0
    counts = e11.export(tmp_path / "sft", conn=conn)
    assert sum(counts.values()) == 24
    rows = [json.loads(x) for x in (tmp_path / "sft" / "train.jsonl").read_text().splitlines()]
    rows += [json.loads(x) for x in (tmp_path / "sft" / "val.jsonl").read_text().splitlines()]
    ex = rows[0]["messages"]
    assert [m["role"] for m in ex] == ["system", "user", "assistant"]
    assert ex[0]["content"] == P.messages("a", "b", "positive", "v4")[0]["content"]
    assert ex[2]["content"] in ('{"econ":"na","social":"A"}', '{"econ":"na","social":"B"}')
    assert "status" not in e11.status(conn) and "b1: 24 pairs" in e11.status(conn)
