"""A51 call log: every paid judge call keeps its exact request, the full response, timing, retries and the planner's
context — also when the call fails. Fake clients only (no network, no key, no spend)."""
import json
from types import SimpleNamespace as NS

import pytest

from pipeline import store
from pipeline.contracts import ComparisonRequest
from pipeline.judge import calllog, client as C
from pipeline.judge.client import JudgeCallFailed, OpenAIJudge, Verdict
from pipeline.measure.run import pick_context, record, record_failure
from pipeline.rate.hierarchical import HierarchicalBT
from pipeline.settings import Settings

KEYED = Settings(openai_api_key="test-key-not-real")
REQ = ComparisonRequest("ev1:full", "both", "o1", "o2", "a1", "a2", 0.0123)


def response(content):
    return NS(id="chatcmpl-1", model="gpt-5.4-mini-2026-09-01", system_fingerprint="fp_x", created=1,
              choices=[NS(index=0, finish_reason="stop", message=NS(role="assistant", content=content))],
              usage=NS(prompt_tokens=2000, completion_tokens=100,
                       prompt_tokens_details=NS(cached_tokens=1024), completion_tokens_details=NS(reasoning_tokens=64)))


class Chat:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def create(self, **kw):
        self.calls.append(kw)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return response(o)


def judge_with(outcomes, monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    chat = Chat(outcomes)
    return OpenAIJudge(KEYED, client=NS(chat=NS(completions=chat)), model="gpt-5.4-mini"), chat


def test_successful_call_is_logged_in_full(monkeypatch):
    judge, chat = judge_with([RuntimeError("rate limit, try again"), json.dumps({"econ": "A", "social": "na"})], monkeypatch)
    v = judge.compare("Article text A", "Article text B", "negative", "v4")
    assert v.log["attempts"] == 2 and len(v.log["errors"]) == 1 and v.log["latency_ms"] >= 0
    conn = store.connect(":memory:")
    cid = record(conn, "measure", REQ, "full", v, "negative", "ev1:full", "v4", context={"run_id": "r1", "batch": 3})
    got = calllog.load(conn, cid)
    assert got["request"] == json.loads(json.dumps(chat.calls[-1]))          # the exact request that was sent
    assert "Article text A" in got["request"]["messages"][1]["content"]
    assert got["response_model"] == "gpt-5.4-mini-2026-09-01" and got["finish_reason"] == "stop"
    assert got["cached_tokens"] == 1024 and got["reasoning_tokens"] == 64 and got["status"] == "ok"
    assert got["run_id"] == "r1" and got["context"]["batch"] == 3 and got["context"]["direction"] == "negative"
    assert got["response"]["choices"][0]["message"]["content"] == v.raw


def test_failed_call_is_logged_and_never_stores_a_key(monkeypatch):
    judge, _ = judge_with([RuntimeError("Incorrect API key provided: sk-abcdef1234567890")], monkeypatch)
    with pytest.raises(JudgeCallFailed) as e:
        judge.compare("A", "B", "positive", "v4")
    conn = store.connect(":memory:")
    cid = record_failure(conn, "measure", REQ, e.value, {"run_id": "r1"})
    got = calllog.load(conn, cid)
    assert got["status"] == "failed" and got["response"] is None and got["context"]["first_article"] == "a1"
    blob = json.dumps(got["errors"]) + str(e.value)
    assert "sk-abcdef" not in blob and "sk-***" in blob
    assert conn.execute("SELECT COUNT(*) FROM judge_calls").fetchone()[0] == 0      # no verdict, nothing to fit


def test_simulated_verdicts_write_no_log():
    conn = store.connect(":memory:")
    record(conn, "simulation", REQ, "full", Verdict({"econ": {"verdict": "A", "reason": ""}}, model="simulated"))
    assert conn.execute("SELECT COUNT(*) FROM judge_call_log").fetchone()[0] == 0


def test_pick_context_has_positions_of_outlets_and_known_articles():
    comps = [("o1", "o2", "a1", "b1", 1.0), ("o2", "o1", "b2", "a1", 1.0)] * 3
    posts = {ax: HierarchicalBT(["o1", "o2"], ax).fit(comps, [True, False] * 3, sigma_a=0.5, tau=1.0)
             for ax in ("econ", "social")}
    c = NS(cost=0.002, discrimination=1.0, relevance=(0.5, 0.8))
    ctx = pick_context(REQ, c, posts, {"econ": (1.0, 0.5), "social": (1.0, 0.5)}, run_id="r1")
    assert ctx["planner_score"] == 0.0123 and ctx["est_cost_usd"] == 0.002
    assert len(ctx["social_first_outlet"]) == 2 and "social_first_article" in ctx      # a1 was judged before
    assert "social_second_article" not in ctx                                           # a2 is new
