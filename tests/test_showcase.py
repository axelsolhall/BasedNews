"""A85: the site's daily example — the story spanning the most countries, the two drawn outlets of different countries
farthest apart, one call with reasons (A/B → outlet names), kept as shown, never measured, never the same story twice.
A fake judge (no network, no key, no spend)."""
import datetime as dt
import json
import sys

import pytest

from pipeline import store
from pipeline.judge.client import Verdict
from pipeline.measure import showcase as S

NOW = dt.datetime.now(dt.UTC)


def iso(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeJudge:
    calls = []

    def __init__(self, settings):
        pass

    def compare(self, text_a, text_b, direction="positive", version="v4"):
        FakeJudge.calls.append(version)
        return Verdict({"econ": {"verdict": "A", "reason": "Article A stresses taxes while Article B stresses wages."},
                        "social": {"verdict": "na", "reason": "No social angle."}}, 10, 2, 0.001, "fake", "{}")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEDNEWS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("BASEDNEWS_MONTHLY_AI_USD", "5")
    prior = {"as_of": NOW.date().isoformat(), "first": "2015-11-01", "source": "test",
             "outlets": {o: {"n": n, "worth": 50.0, "econ": [0, 0.3], "social": [0, 0.3]}
                         for o, n in (("nrk", 9000), ("svt", 9000), ("aftonbladet", 9000), ("vg", 10))}}
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(prior))
    monkeypatch.setattr(S, "PRIOR", path)
    monkeypatch.setattr(S, "OpenAIJudge", FakeJudge)
    conn = store.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS same_text_pairs (run_id TEXT, a TEXT, b TEXT, jaccard REAL)")
    stories = {"wide": ("nrk", "svt", "aftonbladet", "vg"), "narrow": ("aftonbladet", "nrk")}
    for ev, outs in stories.items():
        for k, o in enumerate(outs):
            aid = f"{ev}-{o}"
            country = {"nrk": "Norway", "vg": "Norway"}.get(o, "Sweden")
            conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, full_text, ingested_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (aid, o, country, f"https://x/{aid}", f"T {aid}", iso(5 + k),
                                                       "text " * 100, iso(5 + k)))
            store.save_events(conn, "run1", {aid: ev})
    # today's positions: svt farthest from nrk; vg is not drawn (10 comparisons), however far it sits
    conn.executemany("INSERT INTO prior_positions VALUES (?,?,?,?,?,?)",
                     [(o, ax, m, 0.1, NOW.date().isoformat(), "live") for o, m in
                      (("nrk", 0.0), ("svt", 1.0), ("aftonbladet", 0.2), ("vg", 9.0)) for ax in ("econ", "social")])
    conn.commit()
    return conn


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["showcase", *args])
    S.main()


def test_one_example_a_day_from_the_widest_story_shown_not_measured(db, monkeypatch, tmp_path):
    FakeJudge.calls = []
    run(monkeypatch, "--dry-run")
    assert not FakeJudge.calls
    run(monkeypatch, "--budget-usd", "1")
    assert FakeJudge.calls == ["v4r"]
    c = db.execute("SELECT purpose, prompt_version, event_id, first_outlet, second_outlet FROM judge_calls").fetchall()
    assert len(c) == 1 and c[0]["purpose"] == "showcase" and c[0]["prompt_version"] == "v4r"
    assert c[0]["event_id"].startswith("wide") and {c[0]["first_outlet"], c[0]["second_outlet"]} == {"nrk", "svt"}
    assert not db.execute("SELECT COUNT(*) FROM live_positions").fetchone()[0]          # never measured
    ex = json.loads(db.execute("SELECT example FROM showcase").fetchone()[0])
    first = ex["articles"][0]["outlet"]
    assert ex["axes"]["econ"]["further"] == 0 and ex["axes"]["econ"]["reason"].startswith(f"{first} stresses taxes")
    assert not ex["axes"]["social"]["decided"]
    data = S.export_json(db, tmp_path / "site")
    assert json.loads((tmp_path / "site" / S.OUT_NAME).read_text()) == data and len(data["examples"]) == 1
    run(monkeypatch, "--budget-usd", "1")                     # the wide story was shown: the next one, not it again
    events = [r[0] for r in db.execute("SELECT event_id FROM judge_calls ORDER BY rowid")]
    assert events[1].startswith("narrow")


def test_the_budget_skips_the_call(db, monkeypatch):
    FakeJudge.calls = []
    run(monkeypatch, "--budget-usd", "0")
    assert not FakeJudge.calls and not db.execute("SELECT COUNT(*) FROM showcase").fetchone()[0]
