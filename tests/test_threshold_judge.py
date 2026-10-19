"""A71 (pipeline.judge.client.ThresholdJudge): the local judge read from its next-token probabilities. It must find
each verdict's position from the answer's own tokens, read social after the econ answer it chose, and say "na" only
past the cut."""
import json
from types import SimpleNamespace

import numpy as np

from pipeline.judge.client import ThresholdJudge, choose, decide
from pipeline.settings import load_settings

VOCAB = ['{"', "econ", '":"', "A", "B", "na", '","', "social", '"}']


def tokens(text: str) -> list[int]:
    out, k = [], 0
    while k < len(text):
        tok = max((t for t in VOCAB if text.startswith(t, k)), key=len)
        out.append(VOCAB.index(tok)); k += len(tok)
    return out


class Http:
    def post(self, url, json=None, **kw):
        body = {"tokens": [100, 101, 102]} if "messages" in json else {"tokens": tokens(json["prompt"])}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


class Completions:
    """Econ: P(na) 0.6, A 0.3, B 0.1. Social: after econ "A" leans B, otherwise "na"."""
    def __init__(self):
        self.prompts = []

    def create(self, prompt, **kw):
        self.prompts.append(prompt)
        after = [VOCAB[i] for i in prompt[3:]]
        if after[-2:] == ["econ", '":"']:
            p = {"na": 0.6, "A": 0.3, "B": 0.1}
        elif after[3] == "A":
            p = {"na": 0.2, "A": 0.1, "B": 0.7}
        else:
            p = {"na": 0.9, "A": 0.05, "B": 0.05}
        top = {k: float(np.log(v * 0.9)) for k, v in p.items()} | {"x": float(np.log(0.1))}
        return SimpleNamespace(choices=[SimpleNamespace(logprobs=SimpleNamespace(top_logprobs=[top]))])


def test_rule():
    assert choose({"A": 0.3, "B": 0.1, "na": 0.6}, 0.5) == "na"
    assert choose({"A": 0.3, "B": 0.1, "na": 0.6}, 0.7) == "A"
    assert choose({"A": 0.2, "B": 0.4, "na": 0.4}, 0.5) == "B"


def test_reads_each_verdict_after_the_answer_before_it(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://pc:8000/v1"); monkeypatch.setenv("LOCAL_LLM_API_KEY", "k")
    comp = Completions()
    j = ThresholdJudge({"econ": 0.7, "social": 0.5}, client=SimpleNamespace(completions=comp), settings=load_settings(),
                       served="c4", http=Http())
    pre = j.prefixes()
    assert [VOCAB[i] for i in pre["econ"]] == ['{"', "econ", '":"']
    assert [VOCAB[i] for i in pre["social"]["B"]] == ['{"', "econ", '":"', "B", '","', "social", '":"']
    v = j.compare("a", "b")
    probs = v.log["response"]["probabilities"]
    assert abs(sum(probs["econ"].values()) - 1) < 1e-9 and abs(probs["econ"]["na"] - 0.6) < 1e-9   # renormalised
    assert json.loads(v.raw) == {"econ": "A", "social": "B"}             # P(na) 0.6 < 0.7 → A; social read after A
    assert decide(probs, {"econ": 0.5, "social": 0.5}) == {"econ": "na", "social": "na"}
    assert v.answers["econ"]["verdict"] == "A" and v.cost_usd == 0.0
    assert all(p[:3] == [100, 101, 102] for p in comp.prompts) and len(comp.prompts) == 4


def test_swap_average_cancels_order():
    """A72: an answer that follows the position (always "A") cancels out; a lean that follows the article survives."""
    from pipeline.judge.client import averaged, likeliest, swapped
    pos = {"econ": {"A": 0.6, "B": 0.3, "na": 0.1},
           "social": {e: {"A": 0.5, "B": 0.4, "na": 0.1} for e in ("A", "B", "na")}}
    back = swapped(pos)                                   # the same "first position" preference, the other order
    assert back["econ"] == {"B": 0.6, "A": 0.3, "na": 0.1} and back["social"]["B"] == {"B": 0.5, "A": 0.4, "na": 0.1}
    avg = averaged(pos, back)
    assert abs(avg["econ"]["A"] - avg["econ"]["B"]) < 1e-12                     # pure position bias → a tie
    lean = {"econ": {"A": 0.7, "B": 0.2, "na": 0.1}, "social": {e: {"A": 0.1, "B": 0.1, "na": 0.8} for e in ("A", "B", "na")}}
    lean_back = {"econ": {"A": 0.25, "B": 0.65, "na": 0.1}, "social": lean["social"]}   # read in the other order
    assert likeliest(averaged(lean, swapped(lean_back))) == {"econ": "A", "social": "na"}
