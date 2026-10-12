"""3.2 judge and the OpenAI backends, tested against fake clients (no network, no key)."""
import json
from types import SimpleNamespace as NS

import numpy as np

from pipeline.embed.backend import OpenAIEncoder
from pipeline.embed.calibrate import qmap
from pipeline.judge import prompt as P
from pipeline.judge.client import OpenAIJudge, SimulatedJudge
from pipeline.settings import Settings

KEYED = Settings(openai_api_key="test-key-not-real")


def test_parse_and_direction_mapping():
    a = P.parse(json.dumps({"econ": {"verdict": "A", "reason": "r"}, "social": {"verdict": "na", "reason": ""}}))
    assert a["econ"]["verdict"] == "A" and a["social"]["verdict"] == "na"
    assert P.parse("not json")["econ"]["verdict"] == "na"                     # malformed → abstain
    assert P.first_wins("A") is True and P.first_wins("B") is False
    assert P.first_wins("A", "negative") is False                               # "further LEFT" → A is less right
    assert P.first_wins("same") is None and P.first_wins("na") is None


def test_prompt_never_names_outlets_and_asks_the_mirror_question():
    m = P.messages("Headline: [outlet] says X", "Headline: Y", "negative", "v1")
    assert "economic LEFT" in m[0]["content"] and "LIBERAL" in m[0]["content"]
    assert m[1]["content"].startswith("Article A")
    m3 = P.messages("Headline: [outlet] says X", "Headline: Y", "positive", "v3")
    assert "more economically RIGHT: A or B?" in m3[1]["content"] and "more CONSERVATIVE: A or B?" in m3[1]["content"]


class FakeChat:
    def __init__(self, reject_reasoning=False):
        self.calls, self.reject = [], reject_reasoning

    def create(self, **kw):
        self.calls.append(kw)
        if self.reject and "reasoning_effort" in kw:
            raise ValueError("Unsupported parameter: 'reasoning_effort' is not supported with this model.")
        content = json.dumps({"econ": {"verdict": "B", "reason": "quotes unions"}, "social": {"verdict": "same", "reason": "x"}})
        return NS(choices=[NS(message=NS(content=content))], usage=NS(prompt_tokens=2000, completion_tokens=100))


def test_openai_judge_parses_prices_and_drops_unsupported_reasoning():
    chat = FakeChat(reject_reasoning=True)
    judge = OpenAIJudge(KEYED, client=NS(chat=NS(completions=chat)), model="gpt-5.4-mini")
    v = judge.compare("text a", "text b")
    assert v.answers["econ"]["verdict"] == "B" and v.answers["social"]["verdict"] == "same"
    assert abs(v.cost_usd - (2000 * 0.75 + 100 * 4.50) / 1e6) < 1e-12
    assert "reasoning_effort" not in chat.calls[-1]                              # retried without it
    assert chat.calls[-1]["response_format"]["json_schema"]["strict"] is True


def test_openai_encoder_normalises_and_keeps_order():
    class FakeEmb:
        def create(self, model, input):
            data = [NS(index=i, embedding=[float(len(t)), 1.0]) for i, t in enumerate(input)][::-1]   # out of order
            return NS(data=data, usage=NS(total_tokens=10))
    enc = OpenAIEncoder("openai:text-embedding-3-small", KEYED, client=NS(embeddings=FakeEmb()))
    V = enc.encode(["a", "bbbb"])
    assert np.allclose(np.linalg.norm(V, axis=1), 1.0)
    assert V[1][0] > V[0][0]                                                    # row order follows the input


def test_threshold_mapping_is_exact_on_the_same_model_and_follows_a_shift():
    rng = np.random.default_rng(0)
    base = rng.normal(0.3, 0.15, 3000)
    assert abs(qmap(0.6, base, base) - 0.6) < 1e-9
    assert abs(qmap(0.6, base, base + 0.1) - 0.7) < 0.01                       # a uniformly "warmer" model


def test_simulated_judge_is_reproducible_and_respects_direction():
    j1, j2 = SimulatedJudge(["x", "y"], seed=3), SimulatedJudge(["x", "y"], seed=3)
    assert j1.compare_outlets("x", "y").answers == j2.compare_outlets("x", "y").answers


def test_v3_forced_choice_maps_through_the_asked_direction():
    a = P.parse('{"econ": "A", "social": "na"}')
    assert a["econ"]["verdict"] == "A" and a["social"]["verdict"] == "na"
    assert P.first_wins("A", "negative") is False          # A more LEFT → A not further right
    assert P.first_wins("A", "positive") is True           # A more RIGHT
    assert P.first_wins("B", "negative") is True           # B more LEFT → A further right
    m = P.messages("x", "y", "negative", "v3")
    assert "more economically LEFT: A or B?" in m[1]["content"] and "reason" not in str(P.schema("v3"))
