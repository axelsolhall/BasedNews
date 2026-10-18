"""E9 (docs/experiments/e9_prereg.json): the local-judge harness against a fake OpenAI-compatible server — the
requests it sends, what counts as a failure, resuming, and the pre-registered analysis and decision rule."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline import store
from pipeline.experiments import e9
from pipeline.judge import prompt as P

OUTLETS = ["o1", "o2", "o3", "o4"]


class Resp:
    def __init__(self, content, finish):
        self.id, self.model, self.object = "r", "judge", "chat.completion"
        self.choices = [SimpleNamespace(index=0, finish_reason=finish, message=SimpleNamespace(content=content, role="assistant"))]
        self.usage = SimpleNamespace(prompt_tokens=100, completion_tokens=12)


class FakeServer:
    """models.list() and chat.completions.create() like vLLM; `answer(kw)` → (content, finish_reason)."""

    def __init__(self, answer, root="Qwen/Qwen3-14B-AWQ"):
        self.sent, self.answer = [], answer
        self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="judge", root=root)]))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.sent.append(kw)
        return Resp(*self.answer(kw))


def add_call(conn, cid, purpose, a, b, oa, ob, direction, econ, social, at):
    conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, at, purpose, "gpt-5.4-mini", "v4", direction, f"ev:{cid}", a, b, oa, ob, "full", 900, 150, 0.0005, ""))
    for ax, v in (("econ", econ), ("social", social)):
        conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)", (cid, ax, v, P.first_wins(v, direction), None))


@pytest.fixture
def conn(tmp_path, monkeypatch):
    prereg = tmp_path / "e9_prereg.json"
    prereg.write_text(json.dumps({"written_at": "2026-10-17T07:29:51Z"}))
    monkeypatch.setattr(e9, "PREREG", prereg)
    c = store.connect(tmp_path / "t.sqlite")
    rng = np.random.default_rng(1)
    for k in range(12):
        o = OUTLETS[k % 4]
        c.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, full_text) VALUES (?,?,?,?,?,?,?)",
                  (f"a{k}", o, "SE", f"https://x/{k}", f"Headline {k}" + (" LONG" if k == 5 else ""),
                   "2026-10-11T10:00:00Z", "Body text " * 40))
    for k in range(10):                                    # live calls between outlets, gpt's verdicts mixed
        i, j = k % 12, (k + 1) % 12
        add_call(c, f"m{k}", "measure", f"a{i}", f"a{j}", OUTLETS[i % 4], OUTLETS[j % 4],
                 "positive" if k % 2 else "negative", rng.choice(["A", "B"]), rng.choice(["A", "B", "na"]),
                 f"2026-10-11T14:{k:02d}:00Z")
    add_call(c, "e1", "eval", "a1", "a2", "o2", "o3", "positive", "A", "B", "2026-10-11T13:00:00Z")   # asked twice
    add_call(c, "e2", "eval", "a1", "a2", "o2", "o3", "positive", "A", "A", "2026-10-11T13:05:00Z")
    c.commit()
    return c


def agree_always(kw):
    return json.dumps({"econ": "A", "social": "B"}), "stop"


def test_requests_carry_sampling_and_thinking_but_no_token_cap():
    j = e9.LocalJudge(FakeServer(agree_always), e9.CANDIDATES["C2"])
    kw = j._request(P.messages("a", "b"), "v4")
    assert "max_tokens" not in kw and "reasoning_effort" not in kw                # harness 2
    assert kw["temperature"] == 0.6 and kw["top_p"] == 0.95
    assert kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}
    assert kw["response_format"]["json_schema"]["strict"] is True


def test_run_records_verdicts_under_the_harness_and_resumes(conn):
    server = FakeServer(agree_always)
    counts = e9.run("C1", client=server, conn=conn)
    assert counts == {"ok": 12, "failed": 0} and len(server.sent) == 12
    n = conn.execute("SELECT count(*) FROM judge_calls WHERE purpose = ?", (e9.purpose_of("C1"),)).fetchone()[0]
    assert n == 12
    ctx = json.loads(conn.execute("SELECT context FROM judge_call_log LIMIT 1").fetchone()[0])
    assert ctx["harness"] == e9.HARNESS and ctx["candidate"] == "C1"
    assert e9.run("C1", client=server, conn=conn) == {"ok": 0, "failed": 0}       # nothing left to ask


def test_an_answer_cut_off_at_the_token_limit_is_a_failure_not_a_verdict(conn):
    def answer(kw):
        long = "LONG" in kw["messages"][1]["content"]
        return (json.dumps({"econ": "na"}) + "\n" * 50, "length") if long else agree_always(kw)
    counts = e9.run("C2", client=FakeServer(answer), conn=conn)
    assert counts["failed"] >= 1
    rows = conn.execute("""SELECT status, finish_reason FROM judge_call_log
                           WHERE json_extract(context, '$.candidate') = 'C2' AND status != 'ok'""").fetchall()
    assert rows and all(r["status"] == "failed" for r in rows)
    assert conn.execute("SELECT count(*) FROM judge_calls WHERE purpose = ?", (e9.purpose_of("C2"),)).fetchone()[0] \
        == counts["ok"]


def test_run_refuses_a_server_running_another_model(conn):
    with pytest.raises(SystemExit, match="Qwen3-8B-AWQ"):
        e9.run("C3", client=FakeServer(agree_always, root="Qwen/Qwen3-14B-AWQ"), conn=conn)


def test_old_harness_rows_are_never_analysed(conn, tmp_path):
    e9.run("C1", client=FakeServer(agree_always), conn=conn)
    conn.execute("UPDATE judge_calls SET purpose = 'e9-C1' WHERE purpose = ?", (e9.purpose_of("C1"),))
    conn.execute("UPDATE judge_call_log SET context = json_set(context, '$.harness', 1)")
    conn.commit()
    out = e9.analyse(conn, tmp_path / "reports", boot_positions=5)
    assert out["candidates"] == {}


def test_analyse_reports_agreement_ceiling_positions_and_the_decision(conn, tmp_path):
    e9.run("C1", client=FakeServer(agree_always), conn=conn)
    out = e9.analyse(conn, tmp_path / "reports", boot_positions=10)
    assert out["J2_ceiling"]["pairs"] == 1 and out["J2_ceiling"]["econ"][0] == 1.0 and out["J2_ceiling"]["social"][0] == 0.0
    r = out["candidates"]["C1"]
    assert r["asked"] == 12 and r["failed"] == 0
    ref = {c: v for c, v in e9.verdicts(conn, "c.model = 'gpt-5.4-mini'").items()}
    want = np.mean([ref[c]["econ"] == "A" for c in ref])            # the fake always answers econ A
    assert r["J1_econ"][0] == pytest.approx(round(float(want), 3))
    assert r["J1_econ"][1] <= r["J1_econ"][0] <= r["J1_econ"][2]
    assert set(r["J3_positions_spearman"]) >= {"calls", "outlets", "econ", "social"}
    assert out["decision"]["history_judge"] == "C1"
    assert (tmp_path / "reports/e9.md").read_text().startswith("# E9")


def test_the_decision_rule_needs_the_ceiling_overlapped_on_both_axes():
    base = {"J2_ceiling": {"econ": [0.8, 0.7, 0.9], "social": [0.7, 0.6, 0.8]}}
    cands = {"C1": {"J1_econ": [0.75, 0.72, 0.78], "J1_social": [0.65, 0.61, 0.69], "mean_output_tokens": 12},
             "C2": {"J1_econ": [0.6, 0.55, 0.65], "J1_social": [0.5, 0.45, 0.55], "mean_output_tokens": 500}}
    d = e9.decide({**base, "candidates": cands})
    assert d["history_judge"] == "C1" and d["valid"]
    cands["C1"]["J1_social"] = [0.5, 0.45, 0.55]                          # below the ceiling on one axis
    cands["C2"]["J1_social"] = [0.4, 0.35, 0.45]
    d = e9.decide({**base, "candidates": cands})
    assert d["history_judge"] == "C1" and not d["valid"] and d["overlaps_ceiling"] == {"econ": True, "social": False}
